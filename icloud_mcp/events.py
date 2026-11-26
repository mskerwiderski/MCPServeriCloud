"""iCloud calendars over CalDAV (caldav.icloud.com), for the tools: list
calendars and events, create, change and delete events.

What the tools may do is set per calendar on the account page (none, read,
write; new calendars only read). Every change is logged with the event as it
was before (store.log_change), and the account page can undo it (`restore`).

Invitations: an event may get attendees, and then the person is its
organizer. iCloud does the scheduling itself (RFC 6638): when such an event is
saved, deleted or changed, iCloud mails the invitation, the update or the
cancellation in the person's name. So events the person organized can be
changed and deleted here; invitations from someone else never (a change would
send answers in the person's name). Whose addresses are the person's own,
iCloud says in the principal's calendar-user-address-set (`_me`).

Never touched either: single dates out of a series (iCloud keeps them as
exceptions) - the model is told to leave those to the calendar app. A series
as a whole can be changed and deleted, but only when the call says so
(`whole_series`). Taken over from Ash (app/events.py).

The CalDAV part is small: PROPFIND to find the calendars, REPORT for the
events of a period, GET/PUT/DELETE with ETags for one event. `_client` is the
mock point of the tests."""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
import recurring_ical_events
from icalendar import Alarm, Calendar, Event, vCalAddress, vRecur

from . import account, store, users
from .account import Conn, IcloudError, refused

ROOT = "https://caldav.icloud.com/"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
XML_HEAD = '<?xml version="1.0" encoding="utf-8"?>'
ACCESS = ("none", "read", "write")
MAX_DAYS = 400
MAX_EVENTS = 300
MAX_NOTES = 1000
MAX_ATTENDEES = 50


def _client(conn: Conn) -> httpx.Client:
    return httpx.Client(auth=(conn.address, conn.password), timeout=30, follow_redirects=True)


def _send(c: httpx.Client, method: str, url: str, body: str = "", **headers) -> httpx.Response:
    if body:
        headers.setdefault("Content-Type", "application/xml; charset=utf-8")
    try:
        r = c.request(method, url, content=body.encode() if body else None, headers=headers)
    except httpx.HTTPError as e:
        raise IcloudError(f"iCloud Calendar is not answering ({e}).") from e
    if r.status_code in (401, 403):
        raise refused("calendar")
    return r


def _multistatus(r: httpx.Response) -> list[tuple[str, ET.Element]]:
    """(absolute href, <prop> of the 200 propstat) per response."""
    if r.status_code != 207:
        raise IcloudError(f"iCloud Calendar: unexpected answer {r.status_code}.")
    out = []
    for resp in ET.fromstring(r.content).findall("d:response", NS):
        href = resp.findtext("d:href", "", NS).strip()
        for stat in resp.findall("d:propstat", NS):
            if " 200 " in f"{stat.findtext('d:status', '', NS)} ":
                prop = stat.find("d:prop", NS)
                if prop is not None:
                    out.append((urljoin(str(r.url), href), prop))
    return out


def _propfind(c: httpx.Client, url: str, props: str, depth: str = "0"):
    body = f'{XML_HEAD}<d:propfind xmlns:d="DAV:" xmlns:c="{NS["c"]}"><d:prop>{props}</d:prop></d:propfind>'
    return _multistatus(_send(c, "PROPFIND", url, body, Depth=depth))


def _href(prop: ET.Element, path: str, base: str) -> str:
    h = prop.findtext(path, "", NS).strip()
    if not h:
        raise IcloudError("iCloud Calendar: the account has no calendars.")
    return urljoin(base, h)


def _first(found: list, what: str) -> tuple[str, ET.Element]:
    if not found:
        raise IcloudError(f"iCloud Calendar: {what} not found.")
    return found[0]


def _principal(c: httpx.Client, props: str) -> tuple[str, ET.Element]:
    base, prop = _first(_propfind(c, ROOT, "<d:current-user-principal/>"), "the account")
    principal = _href(prop, "d:current-user-principal/d:href", base)
    return _first(_propfind(c, principal, props), "the account")


def discover(conn: Conn) -> str:
    """The calendar home of the account (https://pNN-caldav.icloud.com/<id>/calendars/)."""
    with _client(conn) as c:
        base, prop = _principal(c, "<c:calendar-home-set/>")
        return _href(prop, "c:calendar-home-set/d:href", base)


def _me(c: httpx.Client, conn: Conn) -> dict:
    """The person as organizer: {address, name, all} - the address invitations
    go out from (iCloud's preferred one, else the Apple ID, else the first),
    the name on the principal, and every address that is the person's own
    (lower case, without mailto:)."""
    _, prop = _principal(c, "<d:displayname/><c:calendar-user-address-set/>")
    hrefs = prop.findall("c:calendar-user-address-set/d:href", NS)
    mails = [(h.text or "").strip()[7:] for h in hrefs
             if (h.text or "").strip().lower().startswith("mailto:")]
    if not mails:
        raise IcloudError("iCloud Calendar did not say which address invitations go out from.")
    preferred = [(h.text or "").strip()[7:] for h in hrefs
                 if h.get("preferred") == "1" and (h.text or "").strip().lower().startswith("mailto:")]
    own = [m for m in mails if m.lower() == conn.address.lower()]
    return {"address": (preferred or own or mails)[0],
            "name": prop.findtext("d:displayname", "", NS).strip(),
            "all": {m.lower() for m in mails}}


def calendars(conn: Conn, home: str) -> list[dict]:
    """The account's calendars that hold events ({url, name}); reminder lists are left out."""
    with _client(conn) as c:
        found = _propfind(c, home, "<d:displayname/><d:resourcetype/>"
                          "<c:supported-calendar-component-set/>", depth="1")
    out = []
    for url, prop in found:
        if prop.find("d:resourcetype/c:calendar", NS) is None:
            continue
        comps = {e.get("name") for e in prop.findall("c:supported-calendar-component-set/c:comp", NS)}
        if comps and "VEVENT" not in comps:
            continue
        out.append({"url": url, "name": prop.findtext("d:displayname", "", NS).strip() or "Calendar"})
    return out


def refresh(conn: Conn) -> None:
    """Find the calendar home and the calendars anew (store.sync_calendars)."""
    home = discover(conn)
    users.set_home(conn.uid, home)
    store.sync_calendars(conn.uid, calendars(conn, home))


def ensure(conn: Conn) -> None:
    """The calendars are known: found once, after that on every calendar_list."""
    if not users.home(conn.uid) or not store.calendars(conn.uid):
        refresh(conn)


# ---------- times ----------

def _zone(conn: Conn) -> ZoneInfo:
    try:
        return ZoneInfo(conn.timezone)
    except (KeyError, ValueError):
        return ZoneInfo("Europe/Berlin")


def _moment(value: str, conn: Conn, field: str, all_day: bool | None = None) -> date | datetime:
    """'2026-11-10' -> a date (all day), '2026-11-10T14:00' -> 14:00 in the person's zone."""
    value = (value or "").strip()
    try:
        if all_day or (all_day is None and len(value) == 10):
            return date.fromisoformat(value[:10])
        t = datetime.fromisoformat(value)
    except ValueError as e:
        raise IcloudError(f"{field}: a date like 2026-11-10 or a time like "
                          "2026-11-10T14:00.") from e
    return t.replace(tzinfo=_zone(conn)) if t.tzinfo is None else t


def _show(t: date | datetime, conn: Conn) -> str:
    if isinstance(t, datetime):
        t = t if t.tzinfo else t.replace(tzinfo=_zone(conn))
        return t.astimezone(_zone(conn)).isoformat(timespec="minutes")
    return t.isoformat()


def _utc(t: date | datetime, conn: Conn) -> str:
    if not isinstance(t, datetime):
        t = datetime(t.year, t.month, t.day, tzinfo=_zone(conn))
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _span(component) -> tuple[date | datetime, date | datetime]:
    start = component.decoded("DTSTART")
    if "DTEND" in component:
        return start, component.decoded("DTEND")
    if "DURATION" in component:
        return start, start + component.decoded("DURATION")
    return start, start + (timedelta(days=1) if not isinstance(start, datetime) else timedelta(0))


# ---------- calendars of the person, with their access ----------

def _calendar(conn: Conn, calendar_id, write: bool = False) -> dict:
    ensure(conn)
    cal = next((c for c in store.calendars(conn.uid) if str(c["id"]) == str(calendar_id)), None)
    if cal is None or cal["access"] == "none":
        raise IcloudError(f"There is no calendar {calendar_id} - calendar_list lists the "
                          "ones the person opened.")
    if write and cal["access"] != "write":
        raise IcloudError(f'The calendar "{cal["name"]}" is read-only for this server. The '
                          f"person can allow writing {account.hint('access')}.")
    return cal


def _event_url(cal: dict, event_id: str) -> str:
    url = urljoin(cal["url"], event_id or "")
    path, home = urlsplit(url).path, urlsplit(cal["url"]).path
    if not event_id or ".." in event_id or not path.startswith(home) or path == home:
        raise IcloudError("There is no such event in this calendar.")
    return url


def calendar_list(conn: Conn) -> dict:
    refresh(conn)                       # a calendar added in iCloud shows up
    return {"calendars": [{"id": c["id"], "name": c["name"], "access": c["access"]}
                          for c in store.calendars(conn.uid) if c["access"] != "none"]}


# ---------- read ----------

def _report(c: httpx.Client, url: str, start: str, end: str) -> list[tuple[str, str]]:
    body = (f'{XML_HEAD}<c:calendar-query xmlns:d="DAV:" xmlns:c="{NS["c"]}">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{start}" end="{end}"/></c:comp-filter></c:comp-filter></c:filter>'
            "</c:calendar-query>")
    return [(href, prop.findtext("c:calendar-data", "", NS))
            for href, prop in _multistatus(_send(c, "REPORT", url, body, Depth="1"))
            if prop.findtext("c:calendar-data", "", NS)]


def _occurrence(o, cal: dict, event_id: str, series: bool, conn: Conn) -> dict:
    """One date as the tools show it - an all-day end is the last day, not iCalendar's day after."""
    start, end = _span(o)
    if not isinstance(start, datetime):
        end = max(start, end - timedelta(days=1))
    notes = str(o.get("DESCRIPTION", ""))
    out = {"calendar_id": cal["id"], "calendar": cal["name"], "event_id": event_id,
           "title": str(o.get("SUMMARY", "")), "start": _show(start, conn), "end": _show(end, conn),
           "all_day": not isinstance(start, datetime), "location": str(o.get("LOCATION", "")),
           "notes": notes[:MAX_NOTES], "series": series, "with_attendees": "ATTENDEE" in o}
    if "ATTENDEE" in o:
        out["organizer"] = _addr(o.get("ORGANIZER", ""))
        out["attendees"] = [{"email": _addr(a), "name": str(a.params.get("CN", "")),
                             "answer": str(a.params.get("PARTSTAT", "NEEDS-ACTION")).lower()}
                            for a in _all(o, "ATTENDEE")]
    return out


def list_events(conn: Conn, start: str, end: str = "", calendar_id=None) -> dict:
    t0 = _moment(start, conn, "start")
    t1 = _moment(end, conn, "end") if end else t0 + timedelta(days=1)
    if not isinstance(t0, datetime):
        t0 = datetime(t0.year, t0.month, t0.day, tzinfo=_zone(conn))
    if not isinstance(t1, datetime):
        t1 = datetime(t1.year, t1.month, t1.day, tzinfo=_zone(conn))
    if t1 <= t0 or (t1 - t0).days > MAX_DAYS:
        raise IcloudError(f"The period must end after start and be at most {MAX_DAYS} days "
                          "long.")
    ensure(conn)
    cals = ([_calendar(conn, calendar_id)] if calendar_id not in (None, "") else
            [c for c in store.calendars(conn.uid) if c["access"] != "none"])
    out = []
    with _client(conn) as c:
        for cal in cals:
            for href, ics in _report(c, cal["url"], _utc(t0, conn), _utc(t1, conn)):
                parsed = Calendar.from_ical(ics)
                vevents = parsed.walk("VEVENT")
                series = len(vevents) > 1 or any("RRULE" in v or "RDATE" in v for v in vevents)
                event_id = urlsplit(href).path.rsplit("/", 1)[-1]
                for o in recurring_ical_events.of(parsed).between(t0, t1):
                    out.append(_occurrence(o, cal, event_id, series, conn))
    out.sort(key=lambda e: e["start"])
    return {"from": _show(t0, conn), "to": _show(t1, conn), "events": out[:MAX_EVENTS],
            "cut": len(out) > MAX_EVENTS}


# ---------- write ----------

def _get(c: httpx.Client, url: str) -> tuple[str, str]:
    r = _send(c, "GET", url)
    if r.status_code == 404:
        raise IcloudError("This event does not exist (any more).")
    if r.status_code != 200:
        raise IcloudError(f"iCloud Calendar: reading the event failed ({r.status_code}).")
    return r.text, r.headers.get("etag", "")


def _put(c: httpx.Client, url: str, ics: bytes | str, etag: str = "", new: bool = False) -> None:
    headers = {"Content-Type": "text/calendar; charset=utf-8"}
    if new:
        headers["If-None-Match"] = "*"
    elif etag:
        headers["If-Match"] = etag
    try:
        r = c.request("PUT", url, content=ics if isinstance(ics, bytes) else ics.encode(),
                      headers=headers)
    except httpx.HTTPError as e:
        raise IcloudError(f"iCloud Calendar is not answering ({e}).") from e
    if r.status_code == 412:
        raise IcloudError("The event was changed elsewhere meanwhile - read it again.")
    if r.status_code not in (200, 201, 204):
        raise IcloudError(f"iCloud Calendar: saving failed ({r.status_code}).")


def _delete(c: httpx.Client, url: str, etag: str = "") -> None:
    r = _send(c, "DELETE", url, **({"If-Match": etag} if etag else {}))
    if r.status_code == 412:
        raise IcloudError("The event was changed elsewhere meanwhile - read it again.")
    if r.status_code not in (200, 204, 404):
        raise IcloudError(f"iCloud Calendar: deleting failed ({r.status_code}).")


def _master(c: httpx.Client, conn: Conn, parsed: Calendar, whole_series: bool):
    """The event to change, after the checks that apply to every change, and
    the person as organizer (`_me`) when it is an invitation they sent."""
    vevents = parsed.walk("VEVENT")
    if not vevents:
        raise IcloudError("This is not an event.")
    me = None
    if any("ATTENDEE" in v or "ORGANIZER" in v for v in vevents):
        me = _me(c, conn)
        for v in vevents:
            organizer = _addr(v.get("ORGANIZER", ""))
            if organizer.lower() not in me["all"]:
                raise IcloudError(f"This is an invitation from {organizer or 'someone else'} - "
                                  "a change would send an answer in the person's name. Please "
                                  "use the Calendar app.")
    master = next((v for v in vevents if "RECURRENCE-ID" not in v), vevents[0])
    series = len(vevents) > 1 or "RRULE" in master or "RDATE" in master
    if series and not whole_series:
        raise IcloudError("This is a series. Only the whole series can be changed or deleted "
                          "(whole_series=true) - single dates of it in the Calendar app, please.")
    return master, len(vevents) > 1, me


# ---------- invitations ----------

def _addr(value) -> str:
    """mailto:Max@Example.org -> Max@Example.org"""
    text = str(value or "").strip()
    return text[7:] if text.lower().startswith("mailto:") else text


def _all(component, name: str) -> list:
    value = component.get(name)
    return [] if value is None else value if isinstance(value, list) else [value]


def _guests(values) -> list[tuple[str, str]]:
    """'Max Muster <max@example.org>' or 'max@example.org' -> (address, name), each once."""
    if isinstance(values, str):
        values = [v for v in re.split(r"[,;\n]", values) if v.strip()]
    out: dict[str, tuple[str, str]] = {}
    for value in values or []:
        name, address = parseaddr(str(value))
        address = _addr(address)
        if not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", address):
            raise IcloudError(f"attendees: {value!r} is not an e-mail address.")
        out.setdefault(address.lower(), (address, name.strip()))
    if len(out) > MAX_ATTENDEES:
        raise IcloudError(f"attendees: at most {MAX_ATTENDEES} people.")
    return list(out.values())


def _address(address: str, name: str, **params) -> vCalAddress:
    value = vCalAddress(f"mailto:{address}")
    if name:
        value.params["CN"] = name
    value.params.update(params)
    return value


def _invite(event, me: dict, wanted: list[tuple[str, str]]) -> None:
    """Make `wanted` the guest list of `event`, with the person as organizer
    (and, as Apple's apps do, as the first attendee, accepted). Guests who
    stay keep their answer; an empty list makes it a private event again."""
    old = {_addr(a).lower(): a for a in _all(event, "ATTENDEE")}
    event.pop("ATTENDEE", None)
    event.pop("ORGANIZER", None)
    wanted = [(a, n) for a, n in wanted if a.lower() not in me["all"]]
    if not wanted:
        return
    event.add("ORGANIZER", _address(me["address"], me["name"]))
    own = next((old[k] for k in old if k in me["all"]), None)
    event.add("ATTENDEE", own or _address(me["address"], me["name"], CUTYPE="INDIVIDUAL",
                                          ROLE="CHAIR", PARTSTAT="ACCEPTED"))
    for address, name in wanted:
        event.add("ATTENDEE", old.get(address.lower()) or _address(
            address, name, CUTYPE="INDIVIDUAL", ROLE="REQ-PARTICIPANT",
            PARTSTAT="NEEDS-ACTION", RSVP="TRUE"))


def _ask_again(event, me: dict) -> None:
    """A new time: every guest is asked again, as Apple's apps do."""
    for a in _all(event, "ATTENDEE"):
        if _addr(a).lower() not in me["all"]:
            a.params["PARTSTAT"] = "NEEDS-ACTION"
            a.params["RSVP"] = "TRUE"


def _set(component, name: str, value) -> None:
    component.pop(name, None)
    if value not in (None, ""):
        component.add(name, value)


def _stamp(component) -> None:
    now = datetime.now(timezone.utc)
    _set(component, "DTSTAMP", now)
    _set(component, "LAST-MODIFIED", now)


def _rrule(repeat: str) -> vRecur | None:
    repeat = (repeat or "").strip().removeprefix("RRULE:")
    if not repeat:
        return None
    try:
        rule = vRecur.from_ical(repeat)
    except ValueError as e:
        raise IcloudError("repeat: a rule like FREQ=WEEKLY;BYDAY=MO;COUNT=10.") from e
    if "FREQ" not in rule:
        raise IcloudError("repeat: FREQ is missing (DAILY, WEEKLY, MONTHLY or YEARLY).")
    return rule


def _times(conn: Conn, start: str, end: str, all_day: bool | None,
           keep: tuple | None = None) -> tuple[date | datetime, date | datetime]:
    """Start and (iCalendar's exclusive) end. An all-day `end` is the last day;
    without `end` the event keeps the length of `keep` (its old start and end),
    or lasts an hour or a day."""
    t0 = _moment(start, conn, "start", all_day)
    day = not isinstance(t0, datetime)
    if end:
        t1 = _moment(end, conn, "end", day)
        t1 = t1 + timedelta(days=1) if day else t1
    elif keep and day == (not isinstance(keep[0], datetime)):
        t1 = t0 + (keep[1] - keep[0])
    else:
        t1 = t0 + (timedelta(days=1) if day else timedelta(hours=1))
    if t1 <= t0:
        raise IcloudError("end must be after start.")
    return t0, t1


def _log(conn: Conn, action: str, cal: dict, url: str, component, before: str) -> None:
    start, _ = _span(component)
    store.log_change(conn.uid, action, cal["url"], url, str(component.get("SUMMARY", "")),
                     _show(start, conn), before)


def create(conn: Conn, calendar_id, title: str, start: str, end: str = "",
           all_day: bool | None = None, location: str = "", notes: str = "",
           alarm_minutes: int | None = None, repeat: str = "",
           attendees: list[str] | None = None) -> dict:
    cal = _calendar(conn, calendar_id, write=True)
    if not (title or "").strip():
        raise IcloudError("title: the event needs a title.")
    t0, t1 = _times(conn, start, end, all_day)
    guests = _guests(attendees)
    event = Event()
    uid = str(uuid.uuid4()).upper()
    event.add("UID", uid)
    event.add("SUMMARY", title.strip())
    event.add("DTSTART", t0)
    event.add("DTEND", t1)
    _set(event, "LOCATION", location)
    _set(event, "DESCRIPTION", notes)
    if rule := _rrule(repeat):
        event.add("RRULE", rule)
    if alarm_minutes not in (None, ""):
        alarm = Alarm()
        alarm.add("ACTION", "DISPLAY")
        alarm.add("DESCRIPTION", title.strip())
        alarm.add("TRIGGER", timedelta(minutes=-abs(int(alarm_minutes))))
        event.add_component(alarm)
    _stamp(event)
    parsed = Calendar()
    parsed.add("PRODID", "-//icloud-mcp//EN")
    parsed.add("VERSION", "2.0")
    parsed.add_component(event)
    parsed.add_missing_timezones()
    url = urljoin(cal["url"], f"{uid}.ics")
    with _client(conn) as c:
        if guests:
            _invite(event, _me(c, conn), guests)
        _put(c, url, parsed.to_ical(), new=True)
    _log(conn, "create", cal, url, event, "")
    out = {"created": _occurrence(event, cal, f"{uid}.ics", rule is not None, conn)}
    if "ATTENDEE" in event:
        out["note"] = "iCloud mails the invitations now, from the organizer's address."
    return out


def update(conn: Conn, calendar_id, event_id: str, title: str | None = None,
           start: str | None = None, end: str | None = None, all_day: bool | None = None,
           location: str | None = None, notes: str | None = None,
           whole_series: bool = False, attendees: list[str] | None = None) -> dict:
    cal = _calendar(conn, calendar_id, write=True)
    url = _event_url(cal, event_id)
    guests = _guests(attendees) if attendees is not None else None
    with _client(conn) as c:
        before, etag = _get(c, url)
        parsed = Calendar.from_ical(before)
        master, exceptions, me = _master(c, conn, parsed, whole_series)
        if title is not None:
            if not title.strip():
                raise IcloudError("title: the event needs a title.")
            _set(master, "SUMMARY", title.strip())
        if location is not None:
            _set(master, "LOCATION", location)
        if notes is not None:
            _set(master, "DESCRIPTION", notes)
        if start or end or all_day is not None:
            if exceptions:
                raise IcloudError("The time of a series with changed single dates is not "
                                  "changed here - please use the Calendar app.")
            old = _span(master)
            if all_day is not None and not start:
                raise IcloudError("To set all_day, give start as well.")
            t0, t1 = _times(conn, start or _show(old[0], conn), end or "", all_day, keep=old)
            master.pop("DURATION", None)
            _set(master, "DTSTART", t0)
            _set(master, "DTEND", t1)
            if me:
                _ask_again(master, me)
        if guests is not None and (guests or me):
            if exceptions:
                raise IcloudError("The guests of a series with changed single dates are not "
                                  "changed here - please use the Calendar app.")
            _invite(master, me or _me(c, conn), guests)
        _set(master, "SEQUENCE", int(master.get("SEQUENCE", 0)) + 1)
        _stamp(master)
        parsed.add_missing_timezones()
        _put(c, url, parsed.to_ical(), etag=etag)
    _log(conn, "update", cal, url, master, before)
    series = "RRULE" in master or "RDATE" in master
    out = {"updated": _occurrence(master, cal, urlsplit(url).path.rsplit("/", 1)[-1], series, conn)}
    if me or "ATTENDEE" in master:
        out["note"] = "iCloud mails the guests the update (added ones an invitation, removed ones a cancellation)."
    return out


def delete(conn: Conn, calendar_id, event_id: str, whole_series: bool = False) -> dict:
    cal = _calendar(conn, calendar_id, write=True)
    url = _event_url(cal, event_id)
    with _client(conn) as c:
        before, etag = _get(c, url)
        master, _, me = _master(c, conn, Calendar.from_ical(before), whole_series)
        _delete(c, url, etag)
    _log(conn, "delete", cal, url, master, before)
    note = f"The person can undo this {account.hint('undo')}."
    if me:
        note = "iCloud mails the guests a cancellation. " + note
    return {"deleted": str(master.get("SUMMARY", "")), "note": note}


def restore(conn: Conn, change: dict) -> None:
    """Undo one logged change: a created event goes again, a changed or
    deleted one comes back as it was before. For an invitation iCloud mails
    the guests again (a cancellation, an update, the invitation)."""
    with _client(conn) as c:
        if change["action"] == "create":
            _delete(c, change["href"])
        elif change["action"] == "update":
            _put(c, change["href"], change["before"])
        else:
            try:
                _put(c, change["href"], change["before"], new=True)
            except IcloudError as e:
                raise IcloudError("The event exists again already.") from e
    store.mark_restored(change["id"])
