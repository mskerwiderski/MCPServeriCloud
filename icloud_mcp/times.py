"""The user's time zone. Events are shown and given in it.

Each user picks their zone in MyAuth, which hands it over with every answer
(`timezone`); the MCP endpoint and the pages put it into a ContextVar with
`use`. Without one the container's TZ applies, and Europe/Berlin without that.
"""

from __future__ import annotations

import contextvars
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_USER_ZONE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "icloud_mcp_zone", default=None)


def use(name: str | None) -> None:
    """The zone of the user this request is for."""
    _USER_ZONE.set(name or None)


def _zone(name: str | None) -> ZoneInfo | None:
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def zone() -> ZoneInfo:
    return (_zone(_USER_ZONE.get()) or _zone(os.environ.get("TZ"))
            or ZoneInfo("Europe/Berlin"))
