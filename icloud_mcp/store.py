"""The user's calendars with what the tools may do in each, and the change
log (db.py has the tables). Taken over from Ash (app/store.py), where the
same rules held for the family's agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import conn

ACCESS = ("none", "read", "write")
CALENDAR_FIELDS = ("id", "user_id", "url", "name", "access")
LOG_FIELDS = ("id", "user_id", "at", "action", "calendar", "href", "title", "start", "before",
              "restored_at")
LOG_DAYS = 30


def now() -> datetime:
    return datetime.now(timezone.utc)


def calendars(uid: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(f"SELECT {', '.join(CALENDAR_FIELDS)} FROM calendars WHERE user_id=?"
                         " ORDER BY name COLLATE NOCASE", (uid,)).fetchall()
    return [dict(r) for r in rows]


def sync_calendars(uid: int, found: list[dict]) -> None:
    """The calendars iCloud lists now ({url, name}): new ones only readable,
    known ones keep their access, gone ones go."""
    with conn() as c:
        for cal in found:
            c.execute("INSERT INTO calendars (user_id, url, name) VALUES (?, ?, ?)"
                      " ON CONFLICT(user_id, url) DO UPDATE SET name = excluded.name",
                      (uid, cal["url"], cal["name"]))
        urls = [cal["url"] for cal in found]
        c.execute(f"DELETE FROM calendars WHERE user_id=? AND url NOT IN "
                  f"({', '.join('?' * len(urls)) or 'NULL'})", (uid, *urls))


def set_calendar_access(uid: int, calendar_id: int, access: str) -> None:
    if access not in ACCESS:
        raise ValueError(f"access must be one of {ACCESS}")
    with conn() as c:
        c.execute("UPDATE calendars SET access=? WHERE user_id=? AND id=?",
                  (access, uid, calendar_id))


def log_change(uid: int, action: str, calendar: str, href: str, title: str, start: str,
               before: str) -> None:
    """One change made through the tools (create | update | delete) with the
    event as it was before."""
    t = now()
    with conn() as c:
        c.execute("INSERT INTO calendar_log (user_id, at, action, calendar, href, title, start,"
                  " before) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (uid, t.isoformat(timespec="seconds"), action, calendar, href, title, start,
                   before))
        c.execute("DELETE FROM calendar_log WHERE at < ?",
                  ((t - timedelta(days=LOG_DAYS)).isoformat(timespec="seconds"),))


def changes(uid: int, limit: int = 30) -> list[dict]:
    """The calendar changes of the last LOG_DAYS days, newest first."""
    since = (now() - timedelta(days=LOG_DAYS)).isoformat(timespec="seconds")
    with conn() as c:
        rows = c.execute(f"SELECT {', '.join(LOG_FIELDS)} FROM calendar_log"
                         " WHERE user_id=? AND at >= ? ORDER BY id DESC LIMIT ?",
                         (uid, since, limit)).fetchall()
    return [dict(r) for r in rows]


def change(uid: int, change_id: int) -> dict | None:
    return next((ch for ch in changes(uid, 1000) if ch["id"] == change_id), None)


def mark_restored(change_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE calendar_log SET restored_at=? WHERE id=?",
                  (now().isoformat(timespec="seconds"), change_id))
