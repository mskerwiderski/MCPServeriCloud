"""SQLite storage. One connection per operation, no ORM.

Starlette runs sync endpoints (and the tools' IMAP and DAV calls) in a
threadpool, so a shared connection would be used across threads. Opening per
call sidesteps that entirely and costs nothing at this scale - this database
serves a handful of MyAuth users.

Nothing from iCloud is kept here: no mail, no event, no contact. Only what
the user chose (which services, which calendar may be written), the calendar
changes made through the tools (with the event as it was, for undo) and the
OAuth grants.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
-- One row per MyAuth user who has used this server; id IS the MyAuth uid
-- (0 in the standalone mode). mail, calendar, contacts: whether the tools may
-- use that service. home: the account's calendar home (found once, CalDAV).
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    username       TEXT NOT NULL,
    display_name   TEXT NOT NULL DEFAULT '',
    mail           INTEGER NOT NULL DEFAULT 1,
    calendar       INTEGER NOT NULL DEFAULT 1,
    contacts       INTEGER NOT NULL DEFAULT 1,
    home           TEXT NOT NULL DEFAULT '',
    created_at_ms  INTEGER NOT NULL,
    last_seen_ms   INTEGER
);

-- The user's iCloud calendars and what the tools may do in each: none, read
-- (every new calendar) or write. Found anew on every calendar_list.
CREATE TABLE IF NOT EXISTS calendars (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url     TEXT NOT NULL,
    name    TEXT NOT NULL,
    access  TEXT NOT NULL DEFAULT 'read',
    UNIQUE (user_id, url)
);

-- Every calendar change made through the tools (create, update, delete) with
-- the event as it was before (iCalendar text, empty for a create), so the
-- account page can undo it. Kept for store.LOG_DAYS days.
CREATE TABLE IF NOT EXISTS calendar_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,
    calendar    TEXT NOT NULL,
    href        TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    start       TEXT NOT NULL DEFAULT '',
    before      TEXT NOT NULL DEFAULT '',
    restored_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    access_token          TEXT PRIMARY KEY,
    refresh_token         TEXT NOT NULL,
    client_id             TEXT NOT NULL,
    user_id               INTEGER NOT NULL,
    scope                 TEXT,
    access_expires_at_ms  INTEGER NOT NULL,
    refresh_expires_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_oauth_tokens_refresh ON oauth_tokens(refresh_token);
CREATE INDEX IF NOT EXISTS ix_oauth_tokens_user ON oauth_tokens(user_id);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def db_path() -> Path:
    env = os.environ.get("MCP_DB")
    return Path(env).expanduser() if env else Path.home() / ".icloud-mcp" / "app.db"


@contextmanager
def conn():
    """Careful: an exception passing through the `yield` skips the commit, so
    writes that must survive a failure have to be committed before the
    exception is raised.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
