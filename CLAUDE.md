# MCPServeriCloud

MCP server for a person's iCloud mail, calendars and contacts (package
`icloud_mcp`, command `icloud-mcp`): standalone for one person
(`icloud-mcp stdio`) or hosted and multi-tenant behind MyAuth
(`icloud_mcp.server:build_http_app`). IMAP, CalDAV and CardDAV with an
app-specific password. Public repository.

**Read [docs/architecture.md](docs/architecture.md) before changing anything.**
It carries the design decisions (the password, the limits, undo, tenancy);
this file only adds what is specific to working in this repository. Overview:
[README.md](README.md).

Documentation, code comments, tool texts and the web UI are English. A
sibling of Libre MCP (MCPServerFreeStyleLibre) and Garmin MCP: same pattern
(MyAuth, OAuth, path on a public host, msk-ui). Mail and calendar came from
Ash (app/mail.py, app/events.py), where the same rules held for the family's
agents.

The author's own hosted instance is operated from a private infrastructure
repo; nothing about it (host names, addresses, keys, deploy commands) belongs
here. Examples use placeholders such as `https://mcp.example.com/icloud`.

## Rules that are easy to break

- **The limits are in the code, not in the prompt.** Mail: folders opened
  read-only, `BODY.PEEK`, the only write is `APPEND` to Drafts - never add
  sending, deleting, moving or flagging. Calendars: write only with access
  `write`, never events with attendees or single dates of a series, every
  change through `events._log` with the event as it was. Contacts: read only.
  A new write path needs the person's explicit decision first.
- **Nothing from iCloud on disk.** No mail, event or contact in the database
  or the logs (httpx is set to WARNING: URLs carry ids). The contacts cache is
  in memory (`contacts.CACHE_S`).
- **The password is never written in the hosted mode.** It comes from MyAuth
  (`account.MyAuthSource`, 30 s in memory). Standalone it is in the 0600 file
  of `local.py` - say so plainly wherever the docs mention it.
- **Vendored building blocks are never edited here.** `access/` and
  `mcp_oauth/` are copies from the author's private repo MyCommons: change the
  block there, run its tests, bump its version, then
  `MyCommons/sync.sh <block> icloud_mcp`. `tests/test_access_copy.py` fails
  locally when a copy is out of date (skipped without MyCommons next to this
  repo). `static/ui/` is msk-ui (its own `sync.sh`); app styles go into
  `static/app.css`, tokens only.
- **Shared pattern with Libre MCP and Garmin MCP:** page shell, `signed_in` in
  `web.py`, `_McpEndpoint` in `server.py`. Fix a bug there in all of them.
- **Every tool goes through `_guard` and `_run`** (`tools.py`): the
  connection for its service, the blocking call in a thread, our errors as a
  readable ToolError. Hints that differ between hosted and standalone
  ("account page" vs. the command) come from `account.hint`.
- **Every POST acting for the signed-in user goes through `web.signed_in`**
  (it also checks `Origin`: apps behind one MyAuth share one cookie host).
- **No real data in tests.** Made-up family "Muster", no exports (`*.eml`,
  `*.ics`, `*.vcf` are ignored). No test talks to MyAuth or iCloud: mock points
  `access.myauth._fetch_session`, `_fetch_access`, `_request` (conftest.py),
  `mail._imap`, `events._client`, `contacts._client`, `contacts._today`
  (fake_icloud.py, fake_carddav.py).
- **iCloud quirks** (checked against a real account, 2026-11): the calendar
  and contact homes live on `pNN-` hosts found by discovery; reminder lists
  are VTODO collections holding only Apple's placeholder since the upgraded
  Reminders (left out); Notes are in CloudKit (the IMAP folder "Notes" is
  empty); the whole address book comes in one `addressbook-query`; birthdays
  without a year are `1604-MM-DD` (shown as `--MM-DD`).

## Working on it

```bash
uv sync --extra dev
uv run pytest
uvx ruff check --select E9,F icloud_mcp tests
```

Running a hosted instance: [docs/hosted.md](docs/hosted.md).
