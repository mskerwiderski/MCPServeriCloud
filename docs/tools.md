# Tool reference

Times are in the person's time zone; dates as `2026-11-10`, times as
`2026-11-10T14:00`. Every tool answers with JSON; failures (not connected,
switched off, a calendar that is only readable) come back as a tool error the
model can read.

## whoami

Who the connector belongs to, the iCloud account, which services are on, the
time zone. No parameters.

## Mail

| Tool | Parameters | Answer |
|---|---|---|
| `mail_folders` | - | `folders: [{name, role}]` (role: inbox, sent, drafts, archive, trash, junk) |
| `mail_search` | `folder` (default INBOX), `sender`, `to`, `subject`, `text` (anywhere), `since`, `before` (YYYY-MM-DD), `unread`, `limit` (1-50, default 20) | `folder, found, shown, mails: [{uid, date, from, to, subject, unread, size}]`, newest first |
| `mail_read` | `uid`, `folder` | `date, from, to, cc, subject, unread, text` (cut at 20,000 characters, `cut`), `attachments: [{name, type, size}]` - nothing is marked read |
| `mail_draft` | `to`, `cc`, `subject`, `body`, `reply_to_uid`, `reply_folder` | `saved_in, to, cc, subject, note` - a draft in Drafts; a reply takes the original's recipient, subject and threading |

Only one search term with non-ASCII characters (umlauts) per search: IMAP
sends it as a literal.

## Calendars

| Tool | Parameters | Answer |
|---|---|---|
| `calendar_list` | - | `calendars: [{id, name, access}]` (read or write; calendars set to none are left out) |
| `calendar_events` | `start`, `end` (default: one day), `calendar_id` | `from, to, events: [{calendar_id, calendar, event_id, title, start, end, all_day, location, notes, series, with_attendees, organizer, attendees: [{email, name, answer}]}], cut` (organizer and attendees only with attendees; answer: accepted, declined, tentative, needs-action) |
| `calendar_create` | `calendar_id`, `title`, `start`, `end` (default one hour / one day), `all_day`, `location`, `notes`, `alarm_minutes`, `repeat` (RRULE), `attendees` (e-mail addresses, `Name <address>` allowed - without a name the one in the contacts, at most 50) | `created: <event>`, `note` with attendees |
| `calendar_update` | `calendar_id`, `event_id`, and only what changes: `title`, `start` (keeps the length), `end`, `all_day`, `location`, `notes` (empty removes), `whole_series`, `attendees` (the complete new list; `[]` removes all) | `updated: <event>`, `note` with attendees |
| `calendar_delete` | `calendar_id`, `event_id`, `whole_series` | `deleted, note` |

Writing needs write access to that calendar (account page). With attendees
the person is the organizer and iCloud mails invitations, updates (a new time
asks every guest again; guests who stay keep their answer) and cancellations
in the person's name. Invitations from someone else and single dates of a
series are never changed; a series only as a whole. Every change can be
undone on the account page (30 days).

## Contacts

| Tool | Parameters | Answer |
|---|---|---|
| `contacts_search` | `query` (every word must match name, nickname, organization, an e-mail or a phone number), `limit` (1-50, default 20) | `contacts: [{id, name, organization, emails, phones}], total` |
| `contacts_get` | `contact_id` | name, nickname, organization, title, `emails`/`phones` with labels, addresses, birthday (`--MM-DD` without a year), urls, related names, notes |
| `contacts_birthdays` | `days` (default 30) | `from, days, birthdays: [{id, name, date, in_days, age}]` |
