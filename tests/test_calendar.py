"""Calendars (icloud_mcp/events.py): the calendars with their access, events
with series expanded, writing only where allowed, invitations only as their
organizer, never single dates of a series, and undo from the change log.
CalDAV faked (fake_icloud.FakeCalDAV)."""

import httpx
import pytest
from fake_icloud import HOME, ic  # noqa: F401 - the fixture
from icalendar import Calendar

from icloud_mcp import events, store, users
from icloud_mcp.account import IcloudError


def _calendar_id(uid, name):
    return next(c["id"] for c in store.calendars(uid) if c["name"] == name)


def _allow_writing(conn, name="Privat"):
    events.calendar_list(conn)
    cal = _calendar_id(conn.uid, name)
    store.set_calendar_access(conn.uid, cal, "write")
    return cal


def test_calendar_list_finds_the_calendars_and_keeps_their_access(conn, ic):  # noqa: F811
    out = events.calendar_list(conn)
    assert {c["name"] for c in out["calendars"]} == {"Privat", "Familie"}   # no reminder lists
    assert all(c["access"] == "read" for c in out["calendars"])            # new: read only
    assert users.home(conn.uid) == HOME
    cal = _calendar_id(conn.uid, "Familie")
    store.set_calendar_access(conn.uid, cal, "none")
    out = events.calendar_list(conn)                                       # found anew
    assert {c["name"] for c in out["calendars"]} == {"Privat"}
    assert next(c for c in store.calendars(conn.uid) if c["id"] == cal)["access"] == "none"


def test_events_with_a_series_expanded(conn, ic):  # noqa: F811
    out = events.list_events(conn, "2026-11-09", "2026-11-17")            # finds the calendars
    seen = [(e["title"], e["start"]) for e in out["events"]]
    assert seen == [("Schwimmen", "2026-11-09T07:00+01:00"), ("Zahnarzt", "2026-11-10T14:00+01:00"),
                    ("Essen bei Oma", "2026-11-11T18:00+01:00"), ("Kino", "2026-11-13T20:00+01:00"),
                    ("Schwimmen", "2026-11-16T07:00+01:00")]
    swim, dentist, dinner = out["events"][0], out["events"][1], out["events"][2]
    assert swim["series"] and not dentist["series"] and dinner["with_attendees"]
    assert dinner["organizer"] == "oma@icloud.example" and "attendees" not in dentist
    assert dentist["event_id"] == "zahnarzt.ics" and dentist["calendar"] == "Privat"
    store.set_calendar_access(conn.uid, _calendar_id(conn.uid, "Familie"), "none")
    assert events.list_events(conn, "2026-11-11", "2026-11-12")["events"] == []
    with pytest.raises(IcloudError, match="400"):
        events.list_events(conn, "2026-11-01", "2028-01-01")
    with pytest.raises(IcloudError, match="a date like"):
        events.list_events(conn, "morgen")


def test_writing_needs_write_access(conn, ic):  # noqa: F811
    events.calendar_list(conn)
    cal = _calendar_id(conn.uid, "Privat")
    with pytest.raises(IcloudError, match="read-only"):
        events.create(conn, cal, "Friseur", "2026-11-12T10:00")
    with pytest.raises(IcloudError, match="read-only"):
        events.delete(conn, cal, "zahnarzt.ics")
    assert "/123/calendars/home/zahnarzt.ics" in ic.dav.objects


def test_create_update_delete_and_undo(conn, ic):  # noqa: F811
    cal = _allow_writing(conn)
    created = events.create(conn, cal, "Friseur", "2026-11-12T10:00", location="Markt 1",
                            alarm_minutes=30)["created"]
    assert (created["start"], created["end"]) == ("2026-11-12T10:00+01:00", "2026-11-12T11:00+01:00")
    path = "/123/calendars/home/" + created["event_id"]
    event = Calendar.from_ical(ic.dav.objects[path][0]).walk("VEVENT")[0]
    assert str(event["LOCATION"]) == "Markt 1" and event.walk("VALARM")
    assert "BEGIN:VTIMEZONE" in ic.dav.objects[path][0]

    # a new start keeps the length; the old state is logged
    out = events.update(conn, cal, "zahnarzt.ics", start="2026-11-10T16:30", notes="Karte mitnehmen")
    assert out["updated"]["end"] == "2026-11-10T17:30+01:00"
    assert out["updated"]["notes"] == "Karte mitnehmen"

    # all day, the end given as the last day
    out = events.create(conn, cal, "Urlaub", "2026-12-21", end="2026-12-23")
    assert out["created"]["all_day"] and out["created"]["end"] == "2026-12-23"
    urlaub = Calendar.from_ical(ic.dav.objects["/123/calendars/home/" + out["created"]["event_id"]][0])
    assert str(urlaub.walk("VEVENT")[0].decoded("DTEND")) == "2026-12-24"

    out = events.delete(conn, cal, "zahnarzt.ics")
    assert out["deleted"] == "Zahnarzt" and "/123/calendars/home/zahnarzt.ics" not in ic.dav.objects

    log = store.changes(conn.uid)
    assert [c["action"] for c in log] == ["delete", "create", "update", "create"]

    # undo: the deleted dentist comes back as it was before the delete (16:30), the
    # update goes back to 14:00, the hairdresser goes again
    events.restore(conn, log[0])
    assert "T163000" in ic.dav.objects["/123/calendars/home/zahnarzt.ics"][0]
    events.restore(conn, log[2])
    assert "T140000" in ic.dav.objects["/123/calendars/home/zahnarzt.ics"][0]
    events.restore(conn, log[3])
    assert path not in ic.dav.objects
    assert all(c["restored_at"] for i, c in enumerate(store.changes(conn.uid)) if i != 1)
    with pytest.raises(IcloudError, match="exists again"):
        events.restore(conn, log[0])                    # the dentist is back already


def test_series_only_as_a_whole_and_never_invitations_from_others(conn, ic):  # noqa: F811
    cal = _allow_writing(conn)
    with pytest.raises(IcloudError, match="whole_series"):
        events.delete(conn, cal, "schwimmen.ics")
    out = events.update(conn, cal, "schwimmen.ics", title="Schwimmen (Verein)", whole_series=True)
    assert out["updated"]["series"]
    assert "RRULE" in ic.dav.objects["/123/calendars/home/schwimmen.ics"][0]
    family = _allow_writing(conn, "Familie")
    before = ic.dav.objects["/123/calendars/family/essen.ics"]
    for change in (lambda: events.update(conn, family, "essen.ics", title="x"),
                   lambda: events.update(conn, family, "essen.ics", attendees=[]),
                   lambda: events.delete(conn, family, "essen.ics")):
        with pytest.raises(IcloudError, match="invitation from oma@icloud.example"):
            change()
    assert ic.dav.objects["/123/calendars/family/essen.ics"] == before


def _event(fakes, path):
    return Calendar.from_ical(fakes.dav.objects[path][0]).walk("VEVENT")[0]


def _guests(event):
    found = event.get("ATTENDEE")
    found = found if isinstance(found, list) else [found]
    return {str(a)[7:]: str(a.params.get("PARTSTAT")) for a in found}


def test_invite_from_the_preferred_address(conn, ic):  # noqa: F811
    cal = _allow_writing(conn)
    out = events.create(conn, cal, "Laufen", "2026-11-14T09:00",
                        attendees=["Max Muster <max@icloud.example>", "lena@muster.example",
                                   "MAX@icloud.example", f"mailto:{conn.address}"])
    assert "invitations" in out["note"]
    assert out["created"]["organizer"] == "anja@muster.example"
    assert [(a["email"], a["answer"]) for a in out["created"]["attendees"]] == [
        ("anja@muster.example", "accepted"), ("max@icloud.example", "needs-action"),
        ("lena@muster.example", "needs-action")]               # each once, never the person twice
    event = _event(ic, "/123/calendars/home/" + out["created"]["event_id"])
    assert str(event["ORGANIZER"]) == "mailto:anja@muster.example"
    assert event["ORGANIZER"].params["CN"] == "Anja Muster"
    max_ = next(a for a in event["ATTENDEE"] if "max@" in str(a))
    assert max_.params["CN"] == "Max Muster" and max_.params["RSVP"] == "TRUE"

    for bad in (["nobody"], ["max@icloud.example, lena"], [f"g{i}@x.example" for i in range(51)]):
        with pytest.raises(IcloudError, match="attendees"):
            events.create(conn, cal, "x", "2026-11-14T09:00", attendees=bad)
    plain = events.create(conn, cal, "Allein", "2026-11-14T12:00", attendees=[])
    assert "ATTENDEE" not in ic.dav.objects["/123/calendars/home/" + plain["created"]["event_id"]][0]


def test_change_and_cancel_own_invitations(conn, ic):  # noqa: F811
    family = _allow_writing(conn, "Familie")
    path = "/123/calendars/family/kino.ics"
    listed = next(e for e in events.list_events(conn, "2026-11-13")["events"] if e["title"] == "Kino")
    assert listed["organizer"] == "anja@muster.example"
    assert {a["email"]: a["answer"] for a in listed["attendees"]}["lena@muster.example"] == "declined"

    # new guests: Max keeps his answer, Lena is dropped, Paul is invited
    out = events.update(conn, family, "kino.ics",
                        attendees=["max@icloud.example", "Paul <paul@muster.example>"])
    assert "update" in out["note"]
    assert _guests(_event(ic, path)) == {"anja@muster.example": "ACCEPTED",
                                         "max@icloud.example": "ACCEPTED",
                                         "paul@muster.example": "NEEDS-ACTION"}
    assert _event(ic, path)["SEQUENCE"] == 1

    # a new time asks every guest again, the organizer stays accepted
    events.update(conn, family, "kino.ics", start="2026-11-13T20:30")
    assert _guests(_event(ic, path)) == {"anja@muster.example": "ACCEPTED",
                                         "max@icloud.example": "NEEDS-ACTION",
                                         "paul@muster.example": "NEEDS-ACTION"}

    # no guests: a private event again
    events.update(conn, family, "kino.ics", attendees=[])
    assert "ATTENDEE" not in _event(ic, path) and "ORGANIZER" not in _event(ic, path)

    # undo brings the guests back; deleting the invitation cancels it
    events.restore(conn, store.changes(conn.uid)[0])
    assert "paul@muster.example" in _guests(_event(ic, path))
    out = events.delete(conn, family, "kino.ics")
    assert "cancellation" in out["note"] and path not in ic.dav.objects

    # a plain event gets guests later
    cal = _allow_writing(conn)
    events.update(conn, cal, "zahnarzt.ics", attendees=["max@icloud.example"])
    assert set(_guests(_event(ic, "/123/calendars/home/zahnarzt.ics"))) == {
        "anja@muster.example", "max@icloud.example"}


def test_events_only_inside_their_calendar(conn, ic):  # noqa: F811
    cal = _allow_writing(conn)
    for bad in ("../family/essen.ics", "/123/calendars/family/essen.ics", ""):
        with pytest.raises(IcloudError):
            events.delete(conn, cal, bad)
    assert "/123/calendars/family/essen.ics" in ic.dav.objects
    with pytest.raises(IcloudError, match="999"):
        events.create(conn, 999, "x", "2026-11-12")


def test_a_changed_event_is_not_overwritten(conn, ic, monkeypatch):  # noqa: F811
    cal = _allow_writing(conn)
    real = ic.dav.handler

    def changed_meanwhile(request):
        if request.method == "PUT":
            return httpx.Response(412)
        return real(request)
    monkeypatch.setattr(ic.dav, "handler", changed_meanwhile)
    with pytest.raises(IcloudError, match="changed elsewhere"):
        events.update(conn, cal, "zahnarzt.ics", title="x")
    assert store.changes(conn.uid) == []


def test_the_calendars_are_found_once_not_on_every_call(conn, ic):  # noqa: F811
    events.list_events(conn, "2026-11-10")
    found = ic.dav.propfinds
    events.list_events(conn, "2026-11-11")
    assert ic.dav.propfinds == found


def test_a_rejected_sign_in_says_so(conn, ic):  # noqa: F811
    from icloud_mcp.account import Conn
    wrong = Conn(uid=conn.uid, address=conn.address, password="x" * 16, timezone=conn.timezone)
    with pytest.raises(IcloudError, match="rejected the sign-in"):
        events.calendar_list(wrong)
