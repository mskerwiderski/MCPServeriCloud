# icloud-mcp

**Your iCloud mail, calendars and contacts, for Claude.** An
[MCP](https://modelcontextprotocol.io) server that lets Claude search and read
your iCloud mail and write drafts, read your calendars and change events
where you allow it, and look up your contacts - with an **app-specific
password**, never your Apple ID password.

```
You:  What did the Stadtwerke write last week?
You:  What is on my calendar on Friday?
You:  Put the dentist on Tuesday at 2 pm into my private calendar.
You:  Invite Max and Lena to a run on Saturday at 9. Who has accepted?
You:  Draft a reply to Max: Saturday works for me.
You:  What is Lena's mobile number? Whose birthday is coming up?
```

What it can and cannot do:

| | Can | Cannot |
|---|---|---|
| **Mail** (IMAP) | list folders, search, read (without marking anything read), save drafts | send, delete, move, flag |
| **Calendars** (CalDAV) | list events (series expanded) with attendees and their answers; create, change, delete events **in calendars you opened for writing** - every change logged and undoable; invite people (iCloud mails the invitation, updates and cancellations in your name) | change or answer invitations from someone else, or single dates of a series |
| **Contacts** (CardDAV) | search, read, upcoming birthdays | change anything |

Each service can be switched off. Reminders and Notes are not there: Apple
keeps them in CloudKit, which an app-specific password does not open.

---

## Quick start: on your own computer

The standalone mode is for one person: Claude Code or Claude Desktop starts
the server on your machine. No server, no accounts. You need Python 3.12 or
newer, [uv](https://docs.astral.sh/uv/), and an app-specific password
(account.apple.com → Sign-In and Security → App-Specific Passwords).

```bash
uv tool install git+https://github.com/mskerwiderski/MCPServeriCloud
icloud-mcp login                                    # Apple ID and the app-specific password
icloud-mcp calendars                                # all read-only at first
icloud-mcp access "Privat" write                    # allow changes in one calendar
claude mcp add --scope user icloud -- icloud-mcp stdio
```

Then ask Claude "What is on my calendar today?". Claude Desktop, the files,
removing it again: [docs/standalone.md](docs/standalone.md).

## Hosted: many users, Claude on the web and the phone

The same tools also run as a remote MCP server (an HTTP app in a container)
with its own OAuth 2.1 authorization server, so claude.ai (browser and phone)
can add it as a connector. It is multi-tenant: each user's connector only ever
reaches that user's iCloud. Each user has an account page: which services the
connector may use, what it may do in each calendar, and the calendar changes
with undo.

Users sign in through **MyAuth**, the author's self-hosted login service. It
decides who may use the server and holds the iCloud connections. **MyAuth is
not published**, so the hosted mode will not run without it (or a service
answering the same few endpoints, listed in [docs/hosted.md](docs/hosted.md)).
It is documented here for transparency and for the author's own instance; for
yourself, use the standalone mode above.

## Documentation

| Guide | What is in it |
|---|---|
| [Standalone on your own machine](docs/standalone.md) | `icloud-mcp login`, Claude Desktop / Claude Code over stdio, the command line, the files |
| [Install for Claude](docs/install-claude.md) | Standalone and hosted, claude.ai, Claude Code, troubleshooting |
| [Tool reference](docs/tools.md) | Every tool and its parameters |
| [Hosted: running the server](docs/hosted.md) | MyAuth's endpoints, configuration, reverse proxy, container |
| [How it works](docs/architecture.md) | Design decisions: the password, the limits, calendar access and undo, tenancy |

## Safety

- **An app-specific password**, not your Apple ID password: it opens mail,
  calendars and contacts only, and you can revoke it at account.apple.com at
  any time. IMAP and DAV know no tokens, so the server needs the password
  itself (hosted: MyAuth hands it over inside the server's network and it is
  never written here; standalone: a file only you can read).
- **Mails are data, not instructions.** A mail can try to talk the model into
  something. That is why mail stays read-only and drafts are sent by you, why
  calendars start read-only, and why every calendar change can be undone.
- **Nothing from iCloud is stored**: no mail, no event, no contact. Contacts
  are kept in memory for five minutes so a search does not download the whole
  address book every time.

## Development

```bash
uv sync --extra dev
uv run pytest
```

No test needs the network - MyAuth and iCloud (IMAP, CalDAV, CardDAV) are
faked, and every name and address in the tests is made up. See
[docs/architecture.md](docs/architecture.md) for how the pieces fit together.
Some folders are vendored copies of shared building blocks and are not edited
here; the architecture document says which.

## Disclaimer

An independent, unofficial project, not affiliated with or endorsed by Apple.
iCloud is a trademark of Apple Inc. It uses Apple's standard interfaces for
mail, calendars and contacts (IMAP, CalDAV, CardDAV) with an app-specific
password, as any mail or calendar app does.

## License

MIT. See [LICENSE](LICENSE).
