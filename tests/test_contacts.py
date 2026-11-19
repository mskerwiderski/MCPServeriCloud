"""Contacts (icloud_mcp/contacts.py): CardDAV discovery, the vCard parser,
search, one contact, upcoming birthdays, the cache - read-only. CardDAV faked
(fake_carddav.FakeCardDAV)."""

from datetime import date

import pytest
from fake_carddav import LENA, cards  # noqa: F401 - the fixture

from icloud_mcp import contacts
from icloud_mcp.account import Conn, IcloudError


def test_search_finds_people_not_groups(conn, cards):  # noqa: F811
    out = contacts.search(conn, "muster")
    assert out["total"] == 4                       # the group card "Familie" is no contact
    assert [c["name"] for c in out["contacts"]] == [
        "Erika Muster", "Lena Muster", "Max Muster", "Stadtwerke Musterstadt"]
    max_ = out["contacts"][2]
    assert max_ == {"id": "max-uid-1", "name": "Max Muster", "organization": "Muster GmbH",
                    "emails": ["max@muster.example", "max@verein.example"],
                    "phones": ["+49 171 1234567", "089 123456", "0800 5555"]}
    assert "emails" not in out["contacts"][1]       # empty fields are left out


def test_every_word_must_match(conn, cards):  # noqa: F811
    assert [c["name"] for c in contacts.search(conn, "Max GmbH")["contacts"]] == ["Max Muster"]
    assert [c["name"] for c in contacts.search(conn, "maxi")["contacts"]] == ["Max Muster"]
    assert [c["name"] for c in contacts.search(conn, "verein.example")["contacts"]] == ["Max Muster"]
    assert contacts.search(conn, "max lena")["total"] == 0


def test_phone_numbers_match_in_any_notation(conn, cards):  # noqa: F811
    for q in ("0171 1234567", "+491711234567", "0049 171-1234567", "1234567"):
        assert [c["name"] for c in contacts.search(conn, q)["contacts"]] == ["Max Muster"], q


def test_search_needs_a_query_and_caps_the_list(conn, cards):  # noqa: F811
    with pytest.raises(ValueError):
        contacts.search(conn, "  ")
    out = contacts.search(conn, "muster", limit=2)
    assert len(out["contacts"]) == 2 and out["total"] == 4
    assert len(contacts.search(conn, "muster", limit=0)["contacts"]) == 1


def test_one_contact_in_full(conn, cards):  # noqa: F811
    c = contacts.get(conn, "max-uid-1")
    assert c["name"] == "Max Muster" and c["first_name"] == "Max" and c["last_name"] == "Muster"
    assert c["nickname"] == "Maxi" and c["title"] == "Leiter Vertrieb"
    assert c["organization"] == "Muster GmbH" and c["department"] == "Vertrieb"
    assert c["emails"] == [{"label": "home", "value": "max@muster.example"},
                           {"label": "Verein", "value": "max@verein.example"}]
    assert c["phones"] == [{"label": "mobile", "value": "+49 171 1234567"},
                           {"label": "home fax", "value": "089 123456"},
                           {"label": "other", "value": "0800 5555"}]
    assert c["addresses"] == [{"label": "home", "street": "Musterweg 1, Hinterhaus",
                               "city": "München", "region": "Bayern", "postal_code": "80331",
                               "country": "Deutschland"}]
    assert c["birthday"] == "1985-04-12"
    assert c["related"] == [{"label": "mother", "value": "Erika Muster"}]
    assert c["urls"] == [{"label": "homepage", "value": "https://muster.example"}]
    assert c["notes"] == "Erste Zeile\nzweite Zeile, mit Komma"
    assert "photo" not in str(c).lower()


def test_ids_birthdays_without_year_names_and_long_notes(conn, cards):  # noqa: F811
    lena = contacts.get(conn, "lena-file-2")                  # no UID: the file name
    assert lena["birthday"] == "--11-25"                      # Apple's year 1604
    assert contacts.get(conn, "erika-uid-3")["birthday"] == "--03-02"
    assert len(contacts.get(conn, "erika-uid-3")["notes"]) == contacts.MAX_NOTE + 6
    sw = contacts.get(conn, "sw-uid-4")
    assert sw["name"] == "Stadtwerke Musterstadt" and "first_name" not in sw
    with pytest.raises(ValueError):
        contacts.get(conn, "nobody")
    with pytest.raises(ValueError):
        contacts.get(conn, "group-uid-5")


def test_upcoming_birthdays(conn, cards, monkeypatch):  # noqa: F811
    monkeypatch.setattr(contacts, "_today", lambda zone: date(2026, 11, 20))
    out = contacts.upcoming_birthdays(conn)
    assert out == {"from": "2026-11-20", "days": 30, "birthdays": [
        {"id": "lena-file-2", "name": "Lena Muster", "date": "2026-11-25", "in_days": 5}]}
    year = contacts.upcoming_birthdays(conn, days=365)["birthdays"]
    assert [(b["name"], b["date"], b.get("age")) for b in year] == [
        ("Lena Muster", "2026-11-25", None), ("Erika Muster", "2027-03-02", None),
        ("Max Muster", "2027-04-12", 42)]


def test_a_birthday_today_counts_and_29_february_falls_on_the_28th(conn, cards, monkeypatch):  # noqa: F811
    cards.cards["leap.vcf"] = LENA.replace("1604-11-25", "2000-02-29").replace(
        "Lena", "Leo") + ""
    monkeypatch.setattr(contacts, "_today", lambda zone: date(2027, 2, 28))
    out = contacts.upcoming_birthdays(conn, days=1)["birthdays"]
    assert out == [{"id": "leap", "name": "Leo Muster", "date": "2027-02-28", "in_days": 0,
                    "age": 27}]


def test_the_cards_are_cached_per_user(conn, cards, monkeypatch):  # noqa: F811
    contacts.search(conn, "muster")
    first = len(cards.requests)
    assert first == 4                                   # three PROPFINDs, one REPORT
    contacts.search(conn, "lena")
    contacts.get(conn, "max-uid-1")
    assert len(cards.requests) == first                 # from the cache
    contacts.forget(conn.uid)
    contacts.search(conn, "lena")
    assert len(cards.requests) == 2 * first
    t = contacts.time.monotonic()
    monkeypatch.setattr(contacts.time, "monotonic", lambda: t + contacts.CACHE_S + 1)
    contacts.search(conn, "lena")
    assert len(cards.requests) == 3 * first             # expired


def test_a_new_contact_is_found_without_waiting_for_the_cache(conn, cards):  # noqa: F811
    contacts.search(conn, "muster")
    cards.cards["new.vcf"] = LENA.replace("Lena", "Nina").replace(
        "END:VCARD", "UID:nina-uid\r\nEND:VCARD")
    assert contacts.get(conn, "nina-uid")["name"] == "Nina Muster"


def test_a_revoked_password_and_other_failures(conn, cards):  # noqa: F811
    wrong = Conn(uid=conn.uid, address=conn.address, password="zzzz", timezone=conn.timezone)
    with pytest.raises(IcloudError, match="rejected the sign-in"):
        contacts.search(wrong, "muster")
    cards.status = 503
    with pytest.raises(IcloudError, match="HTTP 503"):
        contacts.search(conn, "muster")
