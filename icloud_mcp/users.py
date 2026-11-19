"""The local side of a MyAuth user.

Who may use this server is decided in MyAuth, not here. A row in `users` only
exists to hang the user's choices (services, calendar access), the change log
and the OAuth grants on, and its id IS the MyAuth uid.

The username is kept for the one case where that could go wrong: a MyAuth
database rebuilt from scratch hands the same uid to somebody else. When the
name behind a uid changes, everything stored for the previous owner is dropped
before the new one can see it.
"""

from __future__ import annotations

import contextvars

from .db import conn, now_ms

SERVICES = ("mail", "calendar", "contacts")

# Set by the HTTP endpoint before it hands the request to the MCP session
# manager; the tools read it. That is the whole tenancy mechanism.
CURRENT_USER: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "icloud_mcp_user", default=None)


def current_user_id() -> int:
    uid = CURRENT_USER.get()
    if uid is None:
        raise RuntimeError("no user bound to this request")
    return uid


def ensure_user(uid: int, username: str, display_name: str = "") -> bool:
    """Create or refresh the row for this MyAuth user. True when the uid used
    to belong to a different username and its data was just dropped."""
    with conn() as c:
        row = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    reset = row is not None and row["username"] != username
    if reset:
        delete_user(uid)
    with conn() as c:
        if row is None or reset:
            c.execute("INSERT INTO users (id, username, display_name, created_at_ms,"
                      " last_seen_ms) VALUES (?,?,?,?,?)",
                      (uid, username, display_name or "", now_ms(), now_ms()))
        else:
            c.execute("UPDATE users SET display_name=?, last_seen_ms=? WHERE id=?",
                      (display_name or "", now_ms(), uid))
    return reset


def get_user(user_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def delete_user(user_id: int) -> None:
    """Everything this server holds for that person: choices, calendars, the
    change log, connector grants, the row itself. The MyAuth account and its
    iCloud connection are not touched."""
    from . import account, contacts
    account.forget(user_id)
    contacts.forget(user_id)
    with conn() as c:
        for table in ("calendars", "calendar_log", "oauth_tokens"):
            c.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


def services(user_id: int) -> dict[str, bool]:
    """Which services the tools may use ({mail, calendar, contacts}); all of
    them for a user without a row yet."""
    user = get_user(user_id) or {}
    return {s: bool(user.get(s, 1)) for s in SERVICES}


def set_service(user_id: int, service: str, on: bool) -> None:
    if service not in SERVICES:
        raise ValueError(f"service must be one of {SERVICES}")
    with conn() as c:
        c.execute(f"UPDATE users SET {service}=? WHERE id=?", (int(on), user_id))


def home(user_id: int) -> str:
    return (get_user(user_id) or {}).get("home") or ""


def set_home(user_id: int, url: str) -> None:
    with conn() as c:
        c.execute("UPDATE users SET home=? WHERE id=?", (url, user_id))
