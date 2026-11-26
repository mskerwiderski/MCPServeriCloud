"""iCloud's IMAP and CalDAV as far as mail.py and events.py use them, and the
fixture `ic` that puts both in place (`mail._imap`, `events._client`). Taken
over from Ash's tests. Everything here is made up (the family "Muster")."""

import base64
import imaplib
from email.message import EmailMessage

import httpx
import pytest

from conftest import EMAIL, PASSWORD

HOME = "https://p01-caldav.icloud.com:443/123/calendars/"


def _mail(subject, sender, body, msgid, html=False, attachment=False):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, EMAIL, subject
    m["Date"], m["Message-ID"] = "Thu, 05 Nov 2026 09:15:00 +0000", msgid
    if html:
        m.set_content("<html><head><style>p{}</style></head><body><p>Hallo</p><p>Grüße</p></body></html>",
                      subtype="html")
    else:
        m.set_content(body)
    if attachment:
        m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="rechnung.pdf")
    return m.as_bytes()


MAILS = {101: _mail("Rechnung", "Stadtwerke Musterstadt <info@sw-muster.example>",
                    "Ihre Rechnung.", "<a@sw-muster.example>", attachment=True),
         102: _mail("Grüße", "Max Muster <max@icloud.example>", "", "<b@icloud.example>",
                    html=True)}


class FakeIMAP:
    def __init__(self):
        self.commands, self.appended, self.literal, self.selected = [], [], None, None

    def login(self, user, password):
        if (user, password) != (EMAIL, PASSWORD):
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"ok"]

    def logout(self):
        return "BYE", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"',
                      b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
                      b'(\\HasNoChildren) "/" "Gesch&AOQ-ft"',
                      b'(\\Noselect \\HasChildren) "/" "Archiv"']

    def select(self, name, readonly=False):
        self.selected = (name, readonly)
        return ("OK", [b"2"]) if name in ('"INBOX"', '"Gesch&AOQ-ft"') else ("NO", [b"nope"])

    def uid(self, command, *args):
        self.commands.append((command, args, self.literal))
        self.literal = None
        if command == "SEARCH":
            return "OK", [b"101 102"]
        uids = [int(u) for u in args[0].split(",")]
        if "HEADER.FIELDS" in args[1]:
            out = []
            for u in uids:
                head = MAILS[u].split(b"\n\n", 1)[0] + b"\n\n"
                flags = b"\\Seen" if u == 101 else b""
                out += [(b"%d (UID %d FLAGS (%s) RFC822.SIZE 900 BODY[HEADER.FIELDS (FROM)] {9}"
                         % (u, u, flags), head), b")"]
            return "OK", out
        u = uids[0]
        if u not in MAILS:
            return "OK", [None]
        return "OK", [(b"%d (UID %d FLAGS () BODY[] {9}" % (u, u), MAILS[u]), b")"]

    def append(self, mailbox, flags, date, message):
        self.appended.append((mailbox, flags, message))
        return "OK", [b"APPEND completed"]


def ics(uid, summary, start, end, extra=""):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261101T000000Z\r\nDTSTART;TZID=Europe/Berlin:{start}\r\n"
            f"DTEND;TZID=Europe/Berlin:{end}\r\nSUMMARY:{summary}\r\n{extra}"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


class FakeCalDAV:
    """iCloud's CalDAV: discovery, calendars, REPORT, GET/PUT/DELETE with ETags."""

    CALS = {"home": ("Privat", "VEVENT"), "family": ("Familie", "VEVENT"),
            "tasks": ("Erinnerungen", "VTODO")}

    def __init__(self):
        self.objects = {
            "/123/calendars/home/zahnarzt.ics": (ics("zahnarzt", "Zahnarzt", "20261110T140000",
                                                     "20261110T150000"), '"1"'),
            "/123/calendars/home/schwimmen.ics": (ics("schwimmen", "Schwimmen", "20261109T070000",
                                                      "20261109T080000",
                                                      "RRULE:FREQ=WEEKLY;BYDAY=MO\r\n"), '"2"'),
            "/123/calendars/family/essen.ics": (ics("essen", "Essen bei Oma", "20261111T180000",
                                                    "20261111T210000",
                                                    "ORGANIZER:mailto:oma@icloud.example\r\n"
                                                    f"ATTENDEE:mailto:{EMAIL}\r\n"), '"3"'),
            "/123/calendars/family/kino.ics": (ics("kino", "Kino", "20261113T200000",
                                                   "20261113T220000",
                                                   "ORGANIZER;CN=Anja Muster:mailto:anja@muster.example\r\n"
                                                   "ATTENDEE;PARTSTAT=ACCEPTED;ROLE=CHAIR:mailto:anja@muster.example\r\n"
                                                   "ATTENDEE;CN=Max Muster;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:max@icloud.example\r\n"
                                                   "ATTENDEE;CN=Lena;PARTSTAT=DECLINED:mailto:lena@muster.example\r\n"), '"4"')}
        self.etag = 10
        self.propfinds = 0

    @staticmethod
    def _ms(responses):
        body = "".join(f"<d:response><d:href>{h}</d:href><d:propstat><d:prop>{p}</d:prop>"
                       "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                       for h, p in responses)
        return httpx.Response(207, text='<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
                              f'xmlns:c="urn:ietf:params:xml:ns:caldav">{body}</d:multistatus>')

    def handler(self, request: httpx.Request) -> httpx.Response:
        expected = "Basic " + base64.b64encode(f"{EMAIL}:{PASSWORD}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401)
        path, m = request.url.path, request.method
        if m == "PROPFIND":
            self.propfinds += 1
        if m == "PROPFIND" and path == "/":
            return self._ms([("/", "<d:current-user-principal><d:href>/123/principal/</d:href>"
                                   "</d:current-user-principal>")])
        if m == "PROPFIND" and path == "/123/principal/":
            return self._ms([(path, f"<c:calendar-home-set><d:href>{HOME}</d:href></c:calendar-home-set>"
                                    "<d:displayname>Anja Muster</d:displayname>"
                                    "<c:calendar-user-address-set>"
                                    "<d:href>urn:uuid:0000-1111</d:href>"
                                    f"<d:href>mailto:{EMAIL}</d:href>"
                                    '<d:href preferred="1">mailto:anja@muster.example</d:href>'
                                    "</c:calendar-user-address-set>")])
        if m == "PROPFIND" and path == "/123/calendars/":
            rows = [(path, "<d:resourcetype><d:collection/></d:resourcetype>")]
            for key, (name, comp) in self.CALS.items():
                rows.append((f"/123/calendars/{key}/",
                             f"<d:displayname>{name}</d:displayname><d:resourcetype><d:collection/>"
                             f"<c:calendar/></d:resourcetype><c:supported-calendar-component-set>"
                             f'<c:comp name="{comp}"/></c:supported-calendar-component-set>'))
            return self._ms(rows)
        if m == "REPORT":
            assert b"time-range" in request.content
            return self._ms([(p, f"<d:getetag>{e}</d:getetag><c:calendar-data>"
                                 f"{text.replace('&', '&amp;').replace('<', '&lt;')}</c:calendar-data>")
                             for p, (text, e) in self.objects.items() if p.startswith(path)])
        if m == "GET":
            if path not in self.objects:
                return httpx.Response(404)
            text, etag = self.objects[path]
            return httpx.Response(200, text=text, headers={"etag": etag})
        if m == "PUT":
            if request.headers.get("if-none-match") == "*" and path in self.objects:
                return httpx.Response(412)
            match = request.headers.get("if-match")
            if match and self.objects.get(path, (None, None))[1] != match:
                return httpx.Response(412)
            self.etag += 1
            self.objects[path] = (request.content.decode(), f'"{self.etag}"')
            return httpx.Response(201)
        if m == "DELETE":
            match = request.headers.get("if-match")
            if path not in self.objects:
                return httpx.Response(404)
            if match and self.objects[path][1] != match:
                return httpx.Response(412)
            del self.objects[path]
            return httpx.Response(204)
        return httpx.Response(405)


@pytest.fixture
def ic(monkeypatch):
    """Fake IMAP and CalDAV in place; `ic.imap`, `ic.dav` to look inside."""
    from icloud_mcp import events, mail
    fake_imap, fake_dav = FakeIMAP(), FakeCalDAV()
    monkeypatch.setattr(mail, "_imap", lambda: fake_imap)
    monkeypatch.setattr(events, "_client", lambda conn: httpx.Client(
        transport=httpx.MockTransport(fake_dav.handler), auth=(conn.address, conn.password)))

    class Fakes:
        imap, dav = fake_imap, fake_dav
    return Fakes
