"""Standalone mode: one person, their own machine, no MyAuth.

`icloud-mcp login` checks the Apple ID and an app-specific password with
iCloud once and writes both to a file only that user can read. Unlike the
Libre MCP there is no session to keep instead: IMAP and DAV sign in with the
password every time, so the password itself is stored. It is an app-specific
password - it opens mail, calendars and contacts, not the Apple ID - and can be
revoked at account.apple.com at any time. `icloud-mcp stdio` then serves the
MCP tools over stdio to Claude Code or Claude Desktop.

What the account page does in the hosted mode is on the command line here:
`icloud-mcp services`, `icloud-mcp calendars` (and `access`), `icloud-mcp
changes` (and `undo`).

Files:
  account   $ICLOUD_MCP_ACCOUNT, else $XDG_CONFIG_HOME/icloud-mcp/account.json
            (default ~/.config/icloud-mcp/), mode 0600 in a 0700 directory
  settings  $MCP_DB, else $XDG_DATA_HOME/icloud-mcp/icloud.db
            (default ~/.local/share/icloud-mcp/), in a 0700 directory:
            services, calendar access, the change log - no mail, no event
The person is user 0 in the database; the time zone is $TZ, else the
machine's.
"""

from __future__ import annotations

import getpass
import json
import os
import re
from pathlib import Path

import httpx

from . import db, times, users
from .account import IcloudError

USER_ID = 0
CALDAV = "https://caldav.icloud.com/"
NOT_SIGNED_IN = ("not signed in to iCloud yet - run `icloud-mcp login` in a terminal on this "
                 "machine first")
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.IGNORECASE)
_PROBE = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:">'
          '<d:prop><d:current-user-principal/></d:prop></d:propfind>')


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default).expanduser()


def account_path() -> Path:
    env = os.environ.get("ICLOUD_MCP_ACCOUNT")
    if env:
        return Path(env).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "icloud-mcp" / "account.json"


def data_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "icloud-mcp" / "icloud.db"


def normalize(email: str, password: str) -> tuple[str, str]:
    """The address in lower case, the password without the dashes and spaces
    Apple shows; ValueError when either cannot be right."""
    email = email.strip().lower()
    password = re.sub(r"[\s-]", "", password).lower()
    if not _ADDRESS.fullmatch(email):
        raise ValueError("That is not an e-mail address.")
    if not re.fullmatch(r"[a-z]{16}", password):
        raise ValueError("An app-specific password has 16 letters (Apple shows it as "
                         "xxxx-xxxx-xxxx-xxxx). Your Apple ID password does not work here.")
    return email, password


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True)


def check(email: str, password: str) -> None:
    """One CalDAV PROPFIND: the same password opens mail and contacts too."""
    try:
        with _client() as c:
            r = c.request("PROPFIND", CALDAV, auth=(email, password),
                          content=_PROBE.encode(),
                          headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"})
    except httpx.HTTPError as exc:
        raise IcloudError(f"iCloud is not answering ({exc.__class__.__name__}).") from exc
    if r.status_code in (401, 403):
        raise IcloudError("iCloud rejected the Apple ID or the app-specific password.")
    if r.status_code != 207:
        raise IcloudError(f"iCloud answered with HTTP {r.status_code}.")


def load() -> dict | None:
    """The stored account, None when nobody signed in yet."""
    path = account_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text("utf-8"))
    if not data.get("email") or not data.get("password"):
        raise ValueError(f"{path} holds no iCloud account - run `icloud-mcp login` again")
    return data


def save(email: str, password: str) -> Path:
    """Write atomically, readable by this user only."""
    path = account_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"email": email, "password": password}, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def forget() -> bool:
    """Delete the account file; True if there was one. The settings stay."""
    path = account_path()
    if not path.exists():
        return False
    path.unlink()
    return True


class LocalSource:
    """The connection from the local file instead of MyAuth (account.use_source)."""

    not_connected = NOT_SIGNED_IN
    switch_hint = "with `icloud-mcp services` in a terminal"
    access_hint = "with `icloud-mcp access` in a terminal"
    undo_hint = "with `icloud-mcp undo` in a terminal"

    async def credentials(self, uid: int) -> dict | None:
        a = load()
        return {**a, "account": a["email"]} if a else None

    async def state(self, uid: int) -> dict:
        a = load()
        return {"connected": a is not None, "account": (a or {}).get("email"),
                "last_error": None}

    def forget(self, uid: int) -> None:
        """Nothing cached."""


def system_zone() -> str | None:
    """The machine's time zone name (macOS and Linux link /etc/localtime)."""
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else None


def prepare() -> None:
    """Point the server at the local files and the local account, user 0."""
    from . import account

    if not os.environ.get("MCP_DB"):
        path = data_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ["MCP_DB"] = str(path)
    db.init()
    account.use_source(LocalSource())
    a = load()
    users.ensure_user(USER_ID, "local", (a or {}).get("email") or getpass.getuser())
    users.CURRENT_USER.set(USER_ID)
    times.use(os.environ.get("TZ") or system_zone())
