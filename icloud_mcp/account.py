"""A user's iCloud connection: where the Apple ID and the app-specific
password come from, and what the mail, calendar and contact functions get
(`Conn`).

Hosted, the connection lives in MyAuth (account page, Connections -> iCloud):
MyAuth checks the password once and hands it to this server on request
(`GET /api/internal/icloud/token`). IMAP and DAV know no token, so unlike the
other platforms the password itself comes over - only inside the Docker
network, kept in memory for CACHE_TTL_S, never written here. The standalone
mode swaps in its own source (local.LocalSource, `use_source`): a file only
that user can read.

Nothing here talks to iCloud; mail.py, calendar.py and contacts.py do, each
with the Conn they are given."""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import times, users
from .access import myauth

CACHE_TTL_S = 30
NOT_CONNECTED = ("iCloud is not connected yet - connect it on your MyAuth account page "
                 "(Connections, iCloud) with an app-specific password")
SERVICE_NAMES = {"mail": "Mail", "calendar": "Calendar", "contacts": "Contacts"}


class IcloudError(Exception):
    """A failure the user (or the model) may read: not connected, switched off,
    iCloud refused or did not answer, a calendar that may not be written."""


@dataclass(frozen=True)
class Conn:
    """What the mail, calendar and contact functions need of a connection."""

    uid: int
    address: str
    password: str
    timezone: str
    home: str = ""


class MyAuthSource:
    """The connection as MyAuth hands it out (hosted mode)."""

    not_connected = NOT_CONNECTED
    switch_hint = "on this server's account page"
    access_hint = "on this server's account page"
    undo_hint = "on this server's account page"

    def __init__(self):
        self._cache: dict[int, tuple[float, dict | None]] = {}

    async def credentials(self, uid: int) -> dict | None:
        """{email, password, account}, None when iCloud is not connected."""
        hit = self._cache.get(uid)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            status, body = await myauth.call("GET", "/api/internal/icloud/token", uid=uid,
                                             timeout=15)
        except myauth.Unavailable as exc:
            raise IcloudError("MyAuth, which holds the iCloud connection, is not answering. "
                              "Try again in a moment.") from exc
        if status == 404:
            found = None
        elif status == 200 and body.get("email") and body.get("password"):
            found = body
        else:
            raise IcloudError(f"MyAuth answered {status} ({body.get('detail', '')}).")
        self._cache[uid] = (time.monotonic() + CACHE_TTL_S, found)
        return found

    async def state(self, uid: int) -> dict:
        """{connected, account, last_error} without the password; connected is
        None when MyAuth does not answer."""
        try:
            status, body = await myauth.call("GET", "/api/internal/connections", uid=uid)
        except myauth.Unavailable:
            return {"connected": None}
        state = body.get("icloud") if status == 200 else None
        return state if isinstance(state, dict) else {"connected": None}

    def forget(self, uid: int) -> None:
        self._cache.pop(uid, None)


_source = MyAuthSource()


def use_source(source) -> None:
    """Take the connection from `source` instead of MyAuth."""
    global _source
    _source = source


def source():
    return _source


def forget(uid: int) -> None:
    _source.forget(uid)


def hint(kind: str) -> str:
    """Where the person changes things in this mode: kind switch, access or undo."""
    return getattr(_source, f"{kind}_hint")


async def state(uid: int) -> dict:
    return await _source.state(uid)


async def conn(uid: int, service: str) -> Conn:
    """The connection for one of users.SERVICES, or IcloudError saying why not."""
    if not users.services(uid).get(service, False):
        raise IcloudError(f"{SERVICE_NAMES[service]} is switched off for this server - the "
                          f"person can switch it on {hint('switch')}.")
    found = await _source.credentials(uid)
    if found is None:
        raise IcloudError(_source.not_connected)
    return Conn(uid=uid, address=found["email"], password=found["password"],
                timezone=times.zone().key, home=users.home(uid))


def refused(service: str) -> IcloudError:
    """iCloud said no to the sign-in: the app-specific password was revoked
    (or the Apple ID changed)."""
    return IcloudError(f"iCloud {SERVICE_NAMES[service]} rejected the sign-in - the "
                       "app-specific password may have been revoked. Connect iCloud again "
                       "with a new one.")
