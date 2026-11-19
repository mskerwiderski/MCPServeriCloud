"""The small web surface: the account page (the iCloud connection's state
from MyAuth, which services the tools may use, what they may do in each
calendar, the calendar changes with undo) and the page shell every HTML
response shares (msk-ui, the author's design system). Plain HTML strings, no
template engine - this is a handful of pages.

Nobody signs in here. `signed_in` asks MyAuth who the browser belongs to; the
sign-in page, the user's account, the iCloud connection and the log-out are
MyAuth's.

The shell, the address helpers and `signed_in` are copied from
MCPServerFreeStyleLibre (libre_mcp/web.py, itself from Garmin MCP); fixes go to
all of them.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import os
from pathlib import Path
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import __version__, account, events, store, times, users
from .access import myauth
from .access import web as access_web
from .account import IcloudError
from .mcp_oauth import addresses

STATIC_DIR = Path(__file__).parent / "static"
_asset_cache: dict[str, str] = {}

APP_NAME = "iCloud MCP"

LOGO = """<svg class="brand-logo" viewBox="0 0 64 64" aria-hidden="true">
<defs><linearGradient id="hdrGrad" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#4f8cff"/><stop offset="1" stop-color="#7b6cff"/></linearGradient></defs>
<rect x="4.5" y="4.5" width="55" height="55" rx="15" fill="#0e141d" stroke="url(#hdrGrad)" stroke-width="3"/>
<path d="M21 42h23a8 8 0 0 0 .8-15.96A11 11 0 0 0 23.6 24.5 9 9 0 0 0 21 42Z"
 fill="#0e141d" stroke="#0bb4a6" stroke-width="3.6" stroke-linejoin="round"/>
<path d="M25.5 31.5h13v8h-13zM25.5 31.5l6.5 4.6 6.5-4.6" fill="none" stroke="#7b6cff"
 stroke-width="2.2" stroke-linejoin="round"/></svg>"""

_ICON_CLOUD = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor"'
               ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="M7 18.5h10a4 4 0 0 0 .4-7.98A5.5 5.5 0 0 0 6.8 9.3 4.6 4.6 0 0 0 7 18.5Z"/>'
               '</g></svg>')
_ICON_USER = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor"'
              ' stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="8.5" r="3.8"/>'
              '<path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></g></svg>')
_ICON_LOGOUT = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor"'
                ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M9.5 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3.5"/>'
                '<path d="M15 8l4 4-4 4M19 12H9.5"/></g></svg>')


def esc(s: object) -> str:
    return html_lib.escape(str(s), quote=True)


def asset(rel: str) -> str:
    """Static path with a content hash as ?v= (cache busting)."""
    if rel not in _asset_cache:
        try:
            v = hashlib.md5((STATIC_DIR / rel).read_bytes()).hexdigest()[:8]
        except OSError:
            v = "0"
        _asset_cache[rel] = f"/static/{rel}?v={v}"
    return _asset_cache[rel]


# --- addresses ---------------------------------------------------------------
#
# The server's addresses (issuer, connector, consent page) are the MyCommons
# block's: mcp_oauth.addresses (env PUBLIC_URL / MCP_URL / UI_URL, docs/hosted.md).
# The sign-in core and the cross-site check are access.web's.


def page(request: Request, title: str, body: str, status: int = 200,
         user: dict | None = None) -> HTMLResponse:
    nav = tabbar = banner = ""
    if user:
        acc = access_web.account(request, user)
        account = esc(acc["account_url"])
        logout = esc(acc["logout_url"])
        name = esc(acc["name"] or "")
        nav = (f'<span class="nav-user">{name} <a href="{account}">Account</a>'
               f' <a href="{logout}">Log out</a></span>')
        if acc["impersonated_by"]:
            # an admin signed in as this user ("sign in as" in MyAuth) - also on
            # the consent page, where "Allow" would count for this user. After a
            # POST the way back is the account page, not the form's target.
            admin = esc(acc["impersonated_by"])
            if request.method == "GET":
                stop = esc(acc["stop_url"])
            else:
                here = access_web.current_url(request)
                back = urlsplit(here)._replace(path="/account", query="").geturl()
                stop = esc(myauth.stop_impersonating_url(here, back))
            banner = (f'<div class="imp-banner" role="status"><span>You are signed in as '
                      f'{name} - on behalf of {admin}.</span>'
                      f'<a class="btn btn-sm" href="{stop}">Sign out as {name}</a></div>\n')
        here = "active" if request.url.path.startswith("/account") else ""
        tabbar = (f'<nav class="tabbar-bottom" aria-label="Main navigation">'
                  f'<a class="tab {here}" href="/account">{_ICON_CLOUD}<span>{APP_NAME}</span></a>'
                  f'<a class="tab" href="{account}">{_ICON_USER}<span>Account</span></a>'
                  f'<a class="tab" href="{logout}">{_ICON_LOGOUT}<span>Log out</span></a></nav>')
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#0a0e13" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#f3f5f9" media="(prefers-color-scheme: light)">
<title>{APP_NAME} · {esc(title)}</title>
<link rel="icon" type="image/svg+xml" href="{asset('favicon.svg')}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{APP_NAME}">
<link rel="apple-touch-icon" href="{asset('apple-touch-icon.png')}">
<link rel="manifest" href="{asset('site.webmanifest')}">
<link rel="stylesheet" href="{asset('ui/tokens.css')}">
<link rel="stylesheet" href="{asset('ui/components.css')}">
<link rel="stylesheet" href="{asset('app.css')}">
</head>
<body>
{banner}<header><a href="/account" class="brand">{LOGO}{APP_NAME}</a><nav>{nav}</nav></header>
<main>{body}</main>
{tabbar}
<footer>{APP_NAME} v{__version__}{_footer_note()}</footer>
</body>
</html>""", status_code=status)


def _footer_note() -> str:
    """FOOTER_NOTE (e.g. "Private service · Owner: ...") follows the version."""
    note = os.environ.get("FOOTER_NOTE", "").strip()
    return f" · {esc(note)}" if note else ""


# --- who is asking -----------------------------------------------------------


async def signed_in(request: Request) -> tuple[dict | None, Response | None]:
    """The MyAuth user behind this browser, with their local row brought up to
    date - or the response to send instead: MyAuth's sign-in page, "no access",
    or "sign-in unavailable". Apps behind one MyAuth share a host name and
    cookies do not separate ports, so a POST from another site is refused
    (access.web.cross_site)."""
    if request.method == "POST" and access_web.cross_site(request):
        return None, page(request, "Refused", "<h1>Refused</h1><p>This form was sent "
                          "from another site.</p>", 403)
    gate = await access_web.check(request)
    if gate.outcome == access_web.OK and "uid" in gate.user:
        body = gate.user
        users.ensure_user(int(body["uid"]), str(body.get("username") or ""),
                          str(body.get("display_name") or ""))
        times.use(body.get("timezone"))
        return body, None
    if gate.outcome == access_web.UNAVAILABLE:
        return None, page(request, "Sign-in unavailable",
                          "<h1>Sign-in unavailable</h1><p>MyAuth, the sign-in service, "
                          "is not answering. Please try again in a moment.</p>", 503)
    if gate.outcome == access_web.NO_ACCESS:
        account = esc(access_web.myauth_page(request, "/"))
        return None, page(request, "No access",
                          "<h1>No access</h1><p>Your MyAuth account is not enabled for "
                          f"{APP_NAME}. An administrator can change that in "
                          f'<a href="{account}">MyAuth</a>.</p>', 403)
    target = access_web.login_target(
        request, password_change=gate.outcome == access_web.CHANGE_PASSWORD)
    if request.method in ("GET", "HEAD"):
        return None, RedirectResponse(target, status_code=303)
    return None, page(request, "Signed out", "<h1>Signed out</h1><p>Your MyAuth session "
                      f'has ended. <a href="{esc(target)}">Sign in again</a>.</p>', 401)


# --- account -----------------------------------------------------------------


_COPY_JS = """<script>
function copyText(id, btn) {
  const field = document.getElementById(id);
  navigator.clipboard.writeText(field.value).then(function () {
    const before = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(function () { btn.textContent = before; }, 1500);
  }, function () { field.select(); });
}
</script>"""

ACCESS_LABEL = {"none": "no access", "read": "read", "write": "read and write"}
ACTION_LABEL = {"create": "created", "update": "changed", "delete": "deleted"}
NOTICES = {
    "saved": "Saved.",
    "calendars": "The calendars were read from iCloud again.",
    "undone": "The change was undone.",
    "gone": "That change can no longer be undone.",
}


def _icloud_card(request: Request, state: dict) -> str:
    """The connection is MyAuth's; here its state and the way there."""
    manage = esc(access_web.myauth_page(request, "/connections#pf-icloud"))
    if state.get("connected") is None:
        rows = ['<div class="status-line"><span class="badge muted">unknown</span>'
                ' MyAuth is not answering right now.</div>']
    elif state.get("connected"):
        rows = ['<div class="status-line"><span class="badge ok">connected</span>'
                f' in MyAuth as <b>{esc(state.get("account") or "")}</b></div>']
        if state.get("last_error"):
            rows.append(f'<p class="error">MyAuth: {esc(state["last_error"])}</p>')
    else:
        rows = ['<div class="status-line"><span class="badge warn">not connected</span></div>'
                '<p class="muted">iCloud is connected once in MyAuth, with your Apple ID and an '
                '<b>app-specific password</b> (account.apple.com → Sign-In and Security → '
                'App-Specific Passwords) - never your Apple ID password.</p>']
    label = "Manage in MyAuth" if state.get("connected") else "Connect iCloud in MyAuth"
    css = "btn-ghost" if state.get("connected") else "btn"
    rows.append(f'<a class="{css}" href="{manage}">{label} ↗</a>')
    return "".join(rows)


def _services_card(uid: int) -> str:
    on = users.services(uid)
    labels = {"mail": "Mail - search and read, save drafts (never send, delete or move)",
              "calendar": "Calendars - read, and change events where you allow it below",
              "contacts": "Contacts - search and read"}
    boxes = "".join(f'<label class="check"><input type="checkbox" name="{s}" value="1"'
                    f'{" checked" if on[s] else ""}> {esc(labels[s])}</label>'
                    for s in users.SERVICES)
    return f"""<form method="post" action="/account/services">{boxes}
<div class="actions"><button class="btn-ghost">Save</button></div></form>"""


async def _calendars_card(uid: int, state: dict) -> str:
    if not users.services(uid)["calendar"]:
        return '<p class="muted">Calendars are switched off above.</p>'
    if not state.get("connected"):
        return '<p class="muted">Your calendars show here once iCloud is connected.</p>'
    error = ""
    if not store.calendars(uid):                    # the first visit: ask iCloud once
        try:
            conn = await account.conn(uid, "calendar")
            await asyncio.to_thread(events.refresh, conn)
        except IcloudError as exc:
            error = f'<p class="error">{esc(exc)}</p>'
    rows = []
    for cal in store.calendars(uid):
        options = "".join(f'<option value="{a}"{" selected" if a == cal["access"] else ""}>'
                          f'{ACCESS_LABEL[a]}</option>' for a in store.ACCESS)
        rows.append(f'<tr><td>{esc(cal["name"])}</td><td><select name="access_{cal["id"]}"'
                    f' aria-label="Access to {esc(cal["name"])}">{options}</select></td></tr>')
    table = (f'<table class="kv"><tbody>{"".join(rows)}</tbody></table>' if rows else
             '<p class="muted">No calendars found.</p>')
    return f"""{error}<p class="muted">New calendars start as read-only. Events with
attendees and single dates out of a series are never changed - that would send
invitations or answers in your name.</p>
<form method="post" action="/account/calendars">{table}
<div class="actions"><button class="btn-ghost" name="do" value="save">Save</button>
<button class="btn-ghost" name="do" value="refresh">Read calendars again</button></div></form>"""


def _changes_card(uid: int) -> str:
    changes = store.changes(uid)
    if not changes:
        return (f'<p class="muted">No calendar changes in the last {store.LOG_DAYS} days.</p>')
    rows = []
    for ch in changes:
        when = esc(ch["at"][:16].replace("T", " ")) + " UTC"
        what = f'{esc(ch["title"] or "(no title)")} <span class="muted small">{esc(ch["start"])}</span>'
        if ch["restored_at"]:
            act = '<span class="badge muted">undone</span>'
        else:
            act = (f'<form method="post" action="/account/undo" class="inline">'
                   f'<input type="hidden" name="id" value="{ch["id"]}">'
                   f'<button class="btn-ghost btn-sm">Undo</button></form>')
        rows.append(f'<tr><td class="small muted">{when}</td><td>{ACTION_LABEL.get(ch["action"], esc(ch["action"]))}</td>'
                    f'<td>{what}</td><td>{act}</td></tr>')
    return (f'<p class="muted">Every change made through the connector, kept for '
            f'{store.LOG_DAYS} days. Undo puts the event back as it was.</p>'
            f'<div class="table-wrap"><table class="changes"><tbody>{"".join(rows)}</tbody></table></div>')


async def get_account(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    uid = int(user["uid"])
    state = await account.state(uid)
    url = addresses.mcp_url(request)
    notice = NOTICES.get(request.query_params.get("done", ""))
    flash = f'<p class="flash" role="status">{esc(notice)}</p>' if notice else ""
    return page(request, "Account", f"""<h1>{APP_NAME}</h1>{flash}
<div class="card"><h2 class="first">iCloud</h2>{_icloud_card(request, state)}</div>
<div class="card"><h2 class="first">What the connector may use</h2>{_services_card(uid)}</div>
<div class="card" id="calendars"><h2 class="first">Calendars</h2>{await _calendars_card(uid, state)}</div>
<div class="card" id="changes"><h2 class="first">Calendar changes</h2>{_changes_card(uid)}</div>
<div class="card"><h2 class="first">Use it in Claude</h2>
<p>Add a custom connector with this address, exactly as shown:</p>
<textarea id="mcp_url" class="connector-url" rows="1" readonly>{esc(url)}</textarea>
<div class="actions"><button type="button" onclick="copyText('mcp_url', this)">Copy address</button></div>
<p class="muted small">Adding it asks you once to confirm access, signed in with MyAuth. Do
that on a device that can reach MyAuth (it may be private). Afterwards the
connector works from anywhere. There is nothing else to enter: no API key, no token.</p>
</div>{_COPY_JS}
<div class="card"><h2 class="first">Your data on this server</h2>
<p class="muted">Your choices above, the change log and the connectors you allowed - no
mail, event or contact is stored here. Deleting it disconnects every connector; your
MyAuth account and its iCloud connection stay as they are.</p>
<form method="post" action="/account/delete"
onsubmit="return confirm('Delete your settings, the change log and all connector authorizations?')">
<button class="btn-danger">Delete my data</button></form></div>""", user=user)


def _back(done: str, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/account?done={done}{anchor}", status_code=303)


async def post_services(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    form = await request.form()
    for service in users.SERVICES:
        users.set_service(int(user["uid"]), service, form.get(service) == "1")
    return _back("saved")


async def post_calendars(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    uid = int(user["uid"])
    form = await request.form()
    if form.get("do") == "refresh":
        try:
            conn = await account.conn(uid, "calendar")
            await asyncio.to_thread(events.refresh, conn)
        except IcloudError as exc:
            return page(request, "Calendars", f'<h1>Calendars</h1><p class="error">{esc(exc)}</p>'
                        '<p><a href="/account">Back</a></p>', 502, user=user)
        return _back("calendars", "#calendars")
    for cal in store.calendars(uid):
        chosen = str(form.get(f"access_{cal['id']}") or "")
        if chosen in store.ACCESS:
            store.set_calendar_access(uid, cal["id"], chosen)
    return _back("saved", "#calendars")


async def post_undo(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    uid = int(user["uid"])
    form = await request.form()
    try:
        change = store.change(uid, int(str(form.get("id") or "")))
    except ValueError:
        change = None
    if change is None or change["restored_at"]:
        return _back("gone", "#changes")
    try:
        conn = await account.conn(uid, "calendar")
        await asyncio.to_thread(events.restore, conn, change)
    except IcloudError as exc:
        return page(request, "Undo", f'<h1>Undo</h1><p class="error">{esc(exc)}</p>'
                    '<p><a href="/account#changes">Back</a></p>', 409, user=user)
    return _back("undone", "#changes")


async def post_delete_account(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    users.delete_user(int(user["uid"]))
    return RedirectResponse("/account", status_code=303)
