# Install for Claude

## Standalone (Claude Code, Claude Desktop)

See [standalone.md](standalone.md): `icloud-mcp login`, then
`claude mcp add --scope user icloud -- icloud-mcp stdio` or the Claude Desktop
configuration.

## Hosted (claude.ai in the browser and the Claude app)

1. **Connect iCloud once** in MyAuth: Connections → iCloud, with your Apple ID
   and an app-specific password (account.apple.com → Sign-In and Security →
   App-Specific Passwords).
2. **Open the account page** of the server and choose what the connector may
   use: mail, calendar, contacts, and per calendar none, read or write (all
   calendars start read-only). Copy the connector address shown there.
3. **claude.ai** → Settings → Connectors → Add custom connector: a name (e.g.
   "iCloud") and the address. Leave the advanced settings empty - there is no
   client id to enter; the server registers Claude itself.
4. **Connect:** a browser window opens the consent page. Sign in with MyAuth
   if asked and allow. Do this on a device that can reach MyAuth (it may be
   private); afterwards the connector works from anywhere, the phone included.

Claude asks before it uses a tool that changes something (draft, calendar
event), unless you allowed that tool permanently in the connector's settings.

## Troubleshooting

- **"iCloud is not connected yet":** step 1 is missing, or the connection in
  MyAuth was removed.
- **"... is switched off for this server":** switch the service on on the
  account page.
- **"The calendar ... is read-only for this server":** allow writing for that
  calendar on the account page.
- **"iCloud ... rejected the sign-in":** the app-specific password was revoked
  (changing the Apple ID password revokes all of them). Create a new one and
  connect again in MyAuth.
- **The consent window does not load:** the device cannot reach the private
  account page or MyAuth (VPN off, or iCloud Private Relay resolving around
  it). Add the connector on a device in the network.
