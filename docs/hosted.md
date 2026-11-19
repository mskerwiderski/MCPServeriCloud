# Hosted: running the server

The hosted mode is the multi-user server: an HTTP app
(`uvicorn --factory icloud_mcp.server:build_http_app`, what the container runs)
with one connector per user in claude.ai and an account page per user. For one
person on their own computer, the [standalone mode](standalone.md) is simpler
and needs none of this.

## What it needs: MyAuth

The hosted server keeps no accounts and no passwords. It signs users in
through **MyAuth**, the author's self-hosted login service, which also holds
each user's iCloud connection (Apple ID and app-specific password, checked
once with CalDAV). **MyAuth is not published**; this guide describes how the
author's own instance is set up, for transparency. To run the hosted mode
elsewhere you would need a service that answers the same endpoints:

| Endpoint | Asked by | Answer |
|---|---|---|
| `GET /api/session?app=<key>` with the `sso_session` cookie | account and consent pages | 200 with the user (`uid`, `username`, `display_name`, `timezone`, optional `impersonated_by`), 401 not signed in or password change pending, 403 not allowed |
| `GET /api/internal/access?uid=&app=<key>` | every MCP call, every token refresh | 200 with the user while they may use the app, else 403 |
| `GET /api/internal/icloud/token?uid=&app=<key>`, bearer = app key | every tool call (cached 30 s) | `{email, password, account}`; 404 not connected |
| `GET /api/internal/connections?uid=&app=<key>`, bearer = app key | account page, consent page, `whoami` | the state without the password: `{"icloud": {"connected", "account", "last_error"}}` |
| `/login`, `/logout`, `/connections`, `/impersonate/stop` (browser pages, each with `?next=`) | links from this server's pages | sign-in, sign-out, connecting iCloud, ending "sign in as" |

The internal endpoints must only be reachable from the server's own network,
never through the reverse proxy - one of them hands out the password. The code
that asks them is in `icloud_mcp/access/myauth.py` and `icloud_mcp/account.py`.

In MyAuth the app is registered with a key (`mcp-icloud` by default), the port
of its pages and the platform `icloud`, and it gets an app key: the same
random value stands in MyAuth's `APP_KEYS` (`{"mcp-icloud": "..."}`) and in
this server's `MYAUTH_APP_KEY`. Users need the app enabled in MyAuth.

## Two faces

| Address (example) | Who | Reachable |
|---|---|---|
| `https://mcp.example.com/icloud` | AI clients (claude.ai) | public - the AI provider calls it from its data centres |
| `https://home.example.com:1033` | your browser: account page, consent page | may stay private (LAN or VPN), next to MyAuth |

Only the endpoints the AI clients call have to be public: the MCP endpoint,
OAuth token and registration, and the metadata. The account and consent pages
are browser pages behind the MyAuth sign-in; they can stay on a private host,
and the OAuth metadata points the clients' browsers there
(`authorization_endpoint` = `UI_URL`). Unset, everything collapses onto the
host of the request.

## Configuration

Everything is an environment variable; [.env.example](../.env.example) lists them.

| Variable | Example | Used for |
|---|---|---|
| `MYAUTH_APP_KEY` | random (`openssl rand -hex 32`), secret | this app's key in MyAuth: fetching the iCloud connection. The server refuses to start without it |
| `MYAUTH_URL` | `http://myauth:8000` | MyAuth as the container reaches it (internal network) |
| `MYAUTH_PUBLIC_PORT` | `1000` | port of MyAuth's browser pages, on the host the browser used |
| `MYAUTH_APP` | `mcp-icloud` | this app's key in MyAuth |
| `PUBLIC_URL` | `https://mcp.example.com/icloud` | OAuth issuer, token and registration endpoints |
| `MCP_URL` | the same | connector address, protected-resource metadata |
| `UI_URL` | `https://home.example.com:1033` | `authorization_endpoint` - the consent page, a browser page |
| `FOOTER_NOTE` | `Private service · Owner: ...` | optional text after the version in the page footer |
| `TZ` | `Europe/Berlin` | fallback only; each user's time zone comes from MyAuth |

Also set by the image: `MCP_DB=/data/app.db`, the SQLite file in the volume.

## Container

[docker-compose.yml](../docker-compose.yml) builds the image and keeps `/data`
in a volume. It has no host port: put it behind your reverse proxy, in the
same Docker network as MyAuth, with a `docker-compose.override.yml` next to it
(compose reads it automatically; it is git-ignored):

```yaml
services:
  mcp-icloud:
    networks:
      - myauth_net

networks:
  myauth_net:
    external: true
```

Then:

```bash
cp .env.example .env        # fill in MYAUTH_APP_KEY and the addresses
docker compose up -d --build
```

## Reverse proxy

Private site (account and consent pages), Caddy as an example:

```
home.example.com:1033 {
	reverse_proxy mcp-icloud:8000
	encode gzip
}
```

Public site, when the server lives under a path prefix (`/icloud`) on a host
shared with other MCP servers:

| Public path | To the app | Why |
|---|---|---|
| `/icloud` | `/mcp` | the MCP endpoint (the connector address) |
| `/icloud/oauth/token`, `/icloud/oauth/register` | same, prefix stripped | code exchange, refresh, client registration |
| `/icloud/.well-known/oauth-*` | same, prefix stripped | metadata, for clients that append |
| `/.well-known/oauth-protected-resource/icloud` | `/.well-known/oauth-protected-resource` | RFC 9728 location (path inserted) |
| `/.well-known/oauth-authorization-server/icloud` | `/.well-known/oauth-authorization-server` | RFC 8414 location (path inserted) |

Everything else - account page, consent page - should answer 404 on the
public host. Check afterwards, from anywhere:
`https://mcp.example.com/.well-known/oauth-protected-resource/icloud` shows the
metadata, `https://mcp.example.com/icloud` answers 401 (no token),
`https://mcp.example.com/icloud/account` answers 404.

## Data and backup

One SQLite file in the volume (`/data/app.db`): users and their choices,
calendars with their access, the change log, OAuth clients and tokens. No
mail, no event, no contact; the iCloud connection is MyAuth's. Back up the
volume and the `.env` with `MYAUTH_APP_KEY`.

## Who may use it

Only MyAuth decides: a user needs the app enabled there. Revoking it or
blocking the user cuts their connectors off within the 30-second cache. Each
user can delete their own data on the account page.

## Troubleshooting

- **Account page says "Sign-in unavailable", MCP answers 503:** MyAuth is down
  or not in the server's network (`MYAUTH_URL`).
- **"iCloud ... rejected the sign-in":** the app-specific password was revoked
  at account.apple.com (or the Apple ID password was changed, which revokes
  all of them). Create a new one and connect iCloud again in MyAuth.
- **A calendar is missing:** "Read calendars again" on the account page;
  reminder lists are left out on purpose.
- **The consent page does not load when adding a connector:** the browser
  cannot reach `UI_URL` or MyAuth - in a private setup, the device is not on
  the LAN or VPN.
- **Logs:** `docker logs --tail 50 mcp-icloud`.
