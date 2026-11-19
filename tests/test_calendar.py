"""Calendars (icloud_mcp/events.py): the calendars with their access, events
with series expanded, writing only where allowed, never events with attendees
or single dates of a series, and undo from the change log. CalDAV faked
(fake_icloud.FakeCalDAV)."""

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
                    ("Essen bei Oma", "2026-11-11T18:00+01:00"),
                    ("Schwimmen", "2026-11-16T07:00+01:00")]
    swim, dentist, dinner = out["events"][0], out["events"][1], out["events"][2]
    assert swim["series"] and not dentist["series"] and dinner["with_attendees"]
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


def test_series_only_as_a_whole_and_never_events_with_attendees(conn, ic):  # noqa: F811
    cal = _allow_writing(conn)
    with pytest.raises(IcloudError, match="whole_series"):
        events.delete(conn, cal, "schwimmen.ics")
    out = events.update(conn, cal, "schwimmen.ics", title="Schwimmen (Verein)", whole_series=True)
    assert out["updated"]["series"]
    assert "RRULE" in ic.dav.objects["/123/calendars/home/schwimmen.ics"][0]
    family = _allow_writing(conn, "Familie")
    with pytest.raises(IcloudError, match="attendees"):
        events.update(conn, family, "essen.ics", title="x")


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
