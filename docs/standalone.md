# Standalone on your own machine

No server, no MyAuth, no accounts: the server runs on your own computer for
one person, and Claude Code or Claude Desktop starts it there.

```
Claude Code / Claude Desktop ── starts ──▶ icloud-mcp stdio ──▶ iCloud (IMAP, CalDAV, CardDAV)
                                               │
                                               ├─ ~/.config/icloud-mcp/account.json
                                               └─ ~/.local/share/icloud-mcp/icloud.db
```

## 1. Install

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/mskerwiderski/MCPServeriCloud
icloud-mcp --help
```

`uv tool install` puts `icloud-mcp` into `~/.local/bin` (run
`uv tool update-shell` once if that is not on your `PATH`). To update, run the
same command with `--reinstall`.

## 2. An app-specific password

At [account.apple.com](https://account.apple.com): Sign-In and Security →
App-Specific Passwords → add one (e.g. "Claude"). Apple shows it once, as
`xxxx-xxxx-xxxx-xxxx`. Your Apple ID password does not work here, on purpose.

```bash
icloud-mcp login       # Apple ID (e-mail) and the app-specific password
icloud-mcp status      # who, which services are on, which calendars may be written
icloud-mcp logout      # delete the stored account (the settings stay)
```

**The app-specific password is stored** in `account.json`, readable only by
you: IMAP and DAV sign in with it every time, there is no token to keep
instead. It opens mail, calendars and contacts, not your Apple ID. Revoke it
at account.apple.com when you stop using this.

## 3. Choose what Claude may do

```bash
icloud-mcp services                    # mail, calendar, contacts: on or off
icloud-mcp services mail off
icloud-mcp calendars                   # every calendar starts read-only
icloud-mcp access "Privat" write       # by name or by the id the list shows
icloud-mcp changes                     # what Claude changed in the last 30 days
icloud-mcp undo 12                     # put that event back as it was
```

## 4. Add it to Claude

**Claude Code:**

```bash
claude mcp add --scope user icloud -- icloud-mcp stdio
claude mcp list              # icloud ... Connected
```

**Claude Desktop:** Settings → Developer → Edit Config, then in
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "icloud": {
      "command": "/Users/<you>/.local/bin/icloud-mcp",
      "args": ["stdio"]
    }
  }
}
```

Claude Desktop needs the full path (`which icloud-mcp` prints it) and a
complete restart (Cmd+Q). Ask "What is on my calendar today?" to check.

## Files

| File | What | Override |
|---|---|---|
| `~/.config/icloud-mcp/account.json` | Apple ID and app-specific password (0600) | `ICLOUD_MCP_ACCOUNT` |
| `~/.local/share/icloud-mcp/icloud.db` | services, calendar access, change log - no mail, no event | `MCP_DB` |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honoured. The time zone is `TZ`,
else the machine's.

## Removing it

```bash
claude mcp remove --scope user icloud
icloud-mcp logout
uv tool uninstall icloud-mcp
rm -r ~/.local/share/icloud-mcp
```

and revoke the app-specific password at account.apple.com.
