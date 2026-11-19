"""OAuth for the remote MCP endpoint: the MyCommons building block
`mcp_oauth` (icloud_mcp/mcp_oauth/ is a copy - never edit it here; change it in
MyCommons, run its tests and sync it into every project using it). This
module re-exports its names, so server.py and the tests stay short, and keeps
what is this server's own: the consent page (with a warning when iCloud is not
connected) and the error page.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from . import account, web
from .mcp_oauth import oauth as _block
from .mcp_oauth.oauth import (  # noqa: F401 - re-exported for server.py and the tests
    ACCESS_TTL_MS,
    REFRESH_TTL_MS,
    SCOPE,
    _AuthorizeRequest,
    _mint_pair,
    _parse_authorize,
    access_token_user,
    get_authorization_server_metadata,
    get_protected_resource_metadata,
    post_authorize,
    post_register,
    post_token,
    prune_expired,
)
from .web import esc, page


async def get_authorize(request: Request) -> Response:
    user, err = await web.signed_in(request)
    if err is not None:
        return err
    req, err = _parse_authorize(request, dict(request.query_params))
    if err is not None:
        return err
    assert req is not None
    connected = (await account.state(int(user["uid"]))).get("connected") is not False
    return page(request, "Authorize access", _consent_html(req, user, connected), user=user)


def _consent_html(req: _AuthorizeRequest, user: dict, connected: bool = True) -> str:
    name = user.get("display_name") or user.get("username") or ""
    warn = ("" if connected else
            '<p class="error">Your iCloud is not connected yet, so the tools will have '
            'nothing to answer with. You can connect it on your MyAuth account page (see '
            'this server\'s <a href="/account">account page</a>) - this authorization '
            'stays valid.</p>')

    def hidden(k: str, v: str | None) -> str:
        return "" if v is None else f'<input type="hidden" name="{k}" value="{esc(v)}">'

    return f"""<h1>Authorize access</h1>
<div class="card">
<p class="muted">Signed in as {esc(name)}</p>
<h2>{esc(req.client_name)}</h2>
<p class="muted small">Redirect: <code>{esc(req.redirect_uri)}</code></p>
<p>This app is requesting access to <b>your</b> iCloud: it may <b>read</b> your mail
and save drafts, read your calendars and change events in the calendars you opened for
writing, and read your contacts - only the services switched on on the account page.
It cannot send, delete or move mail, and it cannot see any other account on this
server.</p>
{warn}
<form method="post" action="/oauth/authorize">
{hidden("response_type", "code")}{hidden("client_id", req.client_id)}
{hidden("redirect_uri", req.redirect_uri)}{hidden("code_challenge", req.code_challenge)}
{hidden("code_challenge_method", "S256")}{hidden("state", req.state)}
{hidden("scope", req.scope)}
<div class="actions"><button name="decision" value="allow">Allow</button>
<button name="decision" value="deny" class="btn-ghost">Deny</button></div></form>
</div>"""


def _error_html(request: Request, msg: str, status: int = 400) -> Response:
    return page(request, "Error", f"<h1>Error</h1><p>{esc(msg)}</p>", status=status)


_block.configure(error_page=_error_html)
