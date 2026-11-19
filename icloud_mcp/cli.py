"""icloud-mcp: standalone on your own machine, without a server and MyAuth.

    icloud-mcp login              Apple ID and an app-specific password, checked once
    icloud-mcp status             who is signed in, which services are on
    icloud-mcp logout             delete the stored account (the settings stay)
    icloud-mcp stdio              MCP over stdio for Claude Code / Claude Desktop
    icloud-mcp services [S on|off]   mail, calendar, contacts on or off
    icloud-mcp calendars          the calendars with their access
    icloud-mcp access CAL none|read|write   what the tools may do in a calendar
    icloud-mcp changes            the calendar changes of the last 30 days
    icloud-mcp undo ID            put an event back as it was before a change

Hosted, the server runs as an HTTP app (`icloud_mcp.server:build_http_app`)
and takes the connection from MyAuth. See local.py for the files.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys


def _login() -> int:
    from . import local
    from .account import IcloudError

    print("Your Apple ID and an app-specific password - create one at account.apple.com →")
    print("Sign-In and Security → App-Specific Passwords. Your Apple ID password does not work.")
    try:
        email, password = local.normalize(input("Apple ID (e-mail): "),
                                          getpass.getpass("App-specific password: "))
        local.check(email, password)
    except (ValueError, IcloudError) as exc:
        print(f"Not signed in: {exc}", file=sys.stderr)
        return 1
    path = local.save(email, password)
    print(f"Signed in as {email}. Saved to {path} (readable only by you). The app-specific "
          "password is stored there - revoke it at account.apple.com when you stop using this.")
    print("Add `icloud-mcp stdio` to Claude Code or Claude Desktop; see docs/standalone.md.")
    return 0


def _status() -> int:
    from . import local, store, users

    try:
        a = local.load()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if a is None:
        print(f"Not signed in (no account at {local.account_path()}). Run `icloud-mcp login`.")
        return 1
    local.prepare()
    on = users.services(local.USER_ID)
    print(f"Signed in as {a['email']} ({local.account_path()})")
    print("Services: " + ", ".join(f"{s} {'on' if v else 'off'}" for s, v in on.items()))
    cals = store.calendars(local.USER_ID)
    if cals:
        print(f"Calendars: {len(cals)}, writable: "
              + (", ".join(c["name"] for c in cals if c["access"] == "write") or "none"))
    return 0


def _logout() -> int:
    from . import local

    if local.forget():
        print(f"Deleted {local.account_path()}. Revoke the app-specific password at "
              "account.apple.com as well.")
    else:
        print("There was no account to delete.")
    return 0


def _services(service: str | None, value: str | None) -> int:
    from . import local, users

    local.prepare()
    if service:
        if service not in users.SERVICES or value not in ("on", "off"):
            print(f"Usage: icloud-mcp services [{'|'.join(users.SERVICES)} on|off]",
                  file=sys.stderr)
            return 2
        users.set_service(local.USER_ID, service, value == "on")
    for s, on in users.services(local.USER_ID).items():
        print(f"{s:9} {'on' if on else 'off'}")
    return 0


def _connected(service: str):
    from . import account, local
    from .account import IcloudError

    local.prepare()
    try:
        return asyncio.run(account.conn(local.USER_ID, service))
    except IcloudError as exc:
        print(exc, file=sys.stderr)
        return None


def _calendars() -> int:
    from . import events, store
    from .account import IcloudError

    conn = _connected("calendar")
    if conn is None:
        return 1
    try:
        events.refresh(conn)
    except IcloudError as exc:
        print(exc, file=sys.stderr)
        return 1
    for cal in store.calendars(conn.uid):
        print(f"{cal['id']:>3}  {cal['access']:5}  {cal['name']}")
    print("Change with: icloud-mcp access <id or name> none|read|write")
    return 0


def _access(which: str, value: str) -> int:
    from . import local, store

    local.prepare()
    if value not in store.ACCESS:
        print(f"Access must be one of {', '.join(store.ACCESS)}.", file=sys.stderr)
        return 2
    cals = store.calendars(local.USER_ID)
    cal = next((c for c in cals if str(c["id"]) == which), None) or next(
        (c for c in cals if c["name"].casefold() == which.casefold()), None)
    if cal is None:
        print(f"No calendar {which!r} - see `icloud-mcp calendars`.", file=sys.stderr)
        return 1
    store.set_calendar_access(local.USER_ID, cal["id"], value)
    print(f"{cal['name']}: {value}")
    return 0


def _changes() -> int:
    from . import local, store

    local.prepare()
    changes = store.changes(local.USER_ID)
    if not changes:
        print(f"No calendar changes in the last {store.LOG_DAYS} days.")
    for ch in changes:
        state = "undone" if ch["restored_at"] else ""
        print(f"{ch['id']:>4}  {ch['at'][:16].replace('T', ' ')} UTC  {ch['action']:6}  "
              f"{ch['title'] or '(no title)'}  {ch['start']}  {state}".rstrip())
    return 0


def _undo(change_id: str) -> int:
    from . import events, store
    from .account import IcloudError

    conn = _connected("calendar")
    if conn is None:
        return 1
    change = store.change(conn.uid, int(change_id)) if change_id.isdigit() else None
    if change is None or change["restored_at"]:
        print("That change can no longer be undone - see `icloud-mcp changes`.", file=sys.stderr)
        return 1
    try:
        events.restore(conn, change)
    except IcloudError as exc:
        print(f"Not undone: {exc}", file=sys.stderr)
        return 1
    print("Undone.")
    return 0


async def _run_stdio() -> None:
    from . import local
    from .server import build_server

    local.prepare()
    await build_server().run_stdio_async()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="icloud-mcp", description="MCP server for your iCloud mail, calendars and "
                                       "contacts, standalone on your own machine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="sign in with your Apple ID and an app-specific password")
    sub.add_parser("status", help="show who is signed in and which services are on")
    sub.add_parser("logout", help="delete the stored account (the settings stay)")
    sub.add_parser("stdio", help="serve MCP over stdio (Claude Code / Claude Desktop)")
    p = sub.add_parser("services", help="show or switch mail, calendar, contacts")
    p.add_argument("service", nargs="?")
    p.add_argument("value", nargs="?")
    sub.add_parser("calendars", help="read the calendars from iCloud and show their access")
    p = sub.add_parser("access", help="set what the tools may do in a calendar")
    p.add_argument("calendar")
    p.add_argument("value")
    sub.add_parser("changes", help="the calendar changes of the last 30 days")
    p = sub.add_parser("undo", help="undo one calendar change")
    p.add_argument("id")
    args = parser.parse_args(argv)
    if args.cmd == "login":
        return _login()
    if args.cmd == "status":
        return _status()
    if args.cmd == "logout":
        return _logout()
    if args.cmd == "services":
        return _services(args.service, args.value)
    if args.cmd == "calendars":
        return _calendars()
    if args.cmd == "access":
        return _access(args.calendar, args.value)
    if args.cmd == "changes":
        return _changes()
    if args.cmd == "undo":
        return _undo(args.id)
    asyncio.run(_run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
