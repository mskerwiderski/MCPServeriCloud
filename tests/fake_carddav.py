"""iCloud's CardDAV as far as contacts.py uses it (principal, address book
home on another host, one address book, addressbook-query with every card),
and the fixture `cards` that puts it in place (`contacts._client`) with an
empty cache. The cards look like what iCloud sends (vCard 3.0, item groups
with X-ABLabel, folded lines); everything in them is made up (the family
"Muster")."""

import httpx
import pytest

from conftest import EMAIL, PASSWORD

HOME = "https://p99-contacts.icloud.com:443/123/carddavhome/"
BOOK = HOME + "card/"
# httpx drops the default port in what it sends
_HOME, _BOOK = str(httpx.URL(HOME)), str(httpx.URL(BOOK))

MAX = "\r\n".join([
    "BEGIN:VCARD", "VERSION:3.0", "PRODID:-//Apple Inc.//iOS 26.1//EN",
    "N:Muster;Max;;Dr.;", "FN:Max Muster", "NICKNAME:Maxi",
    "ORG:Muster GmbH;Vertrieb;", "TITLE:Leiter Vertrieb",
    "EMAIL;type=INTERNET;type=HOME;type=pref:max@muster.example",
    "item1.EMAIL;type=INTERNET:max@verein.example", "item1.X-ABLabel:Verein",
    "TEL;type=CELL;type=VOICE;type=pref:+49 171 1234567",
    "TEL;type=HOME;type=FAX:089 123456",
    "item2.TEL:0800 5555", "item2.X-ABLabel:_$!<Other>!$_",
    "item3.ADR;type=HOME;type=pref:;;Musterweg 1\\, Hinterhaus;München;Bayern;80331;Deutschland",
    "item3.X-ABADR:de",
    "BDAY;value=date:1985-04-12",
    "item4.X-ABRELATEDNAMES:Erika Muster", "item4.X-ABLabel:_$!<Mother>!$_",
    "item5.URL;type=pref:https://muster.example", "item5.X-ABLabel:_$!<HomePage>!$_",
    "NOTE:Erste Zeile\\nzweite Zeile\\, mit Komma",
    "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBk",
    " SEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN",
    "UID:max-uid-1", "REV:2026-11-01T10:00:00Z", "END:VCARD"])
LENA = "\r\n".join([
    "BEGIN:VCARD", "VERSION:3.0", "N:Muster;Lena;;;", "FN:Lena Muster",
    "TEL;type=CELL:0172 7654321",
    "BDAY;X-APPLE-OMIT-YEAR=1604:1604-11-25", "END:VCARD"])           # no UID: the file name
ERIKA = "\r\n".join([
    "BEGIN:VCARD", "VERSION:3.0", "N:Muster;Erika;;;", "FN:Erika Muster",
    "EMAIL;type=INTERNET:erika@muster.example", "BDAY:--0302",
    "NOTE:" + "Lang " * 450, "UID:erika-uid-3", "END:VCARD"])
STADTWERKE = "\r\n".join([
    "BEGIN:VCARD", "VERSION:3.0", "N:;;;;", "FN:", "ORG:Stadtwerke Musterstadt;",
    "X-ABShowAs:COMPANY", "TEL;type=WORK:0800 111222",
    "EMAIL;type=INTERNET;type=WORK:kunden@stadtwerke.example", "UID:sw-uid-4", "END:VCARD"])
FAMILY = "\r\n".join([
    "BEGIN:VCARD", "VERSION:3.0", "N:Familie", "FN:Familie",
    "X-ADDRESSBOOKSERVER-KIND:group", "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:max-uid-1",
    "UID:group-uid-5", "END:VCARD"])
CARDS = {"max.vcf": MAX, "lena-file-2.vcf": LENA, "erika.vcf": ERIKA, "sw.vcf": STADTWERKE,
         "family.vcf": FAMILY}


def _ms(*responses: str) -> httpx.Response:
    return httpx.Response(207, content=(
        '<?xml version="1.0" encoding="UTF-8"?><multistatus xmlns="DAV:" '
        'xmlns:card="urn:ietf:params:xml:ns:carddav">' + "".join(responses)
        + "</multistatus>").encode())


def _resp(href: str, prop: str) -> str:
    return (f"<response><href>{href}</href><propstat><prop>{prop}</prop>"
            "<status>HTTP/1.1 200 OK</status></propstat></response>")


class FakeCardDAV:
    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        self.status: int | None = None              # answer every request with this
        self.cards = dict(CARDS)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        if request.headers.get("authorization") != httpx.BasicAuth(EMAIL, PASSWORD)._auth_header:
            return httpx.Response(401)
        if self.status:
            return httpx.Response(self.status)
        url = str(request.url)
        if request.method == "PROPFIND" and url == "https://contacts.icloud.com/":
            return _ms(_resp("/", "<current-user-principal><href>/123/principal/</href>"
                                  "</current-user-principal>"))
        if request.method == "PROPFIND" and url == "https://contacts.icloud.com/123/principal/":
            return _ms(_resp("/123/principal/", f"<card:addressbook-home-set><href>{HOME}"
                                                "</href></card:addressbook-home-set>"))
        if request.method == "PROPFIND" and url == _HOME:
            assert request.headers["depth"] == "1"
            return _ms(_resp("/123/carddavhome/", "<resourcetype><collection/></resourcetype>"),
                       _resp("/123/carddavhome/card/",
                             "<resourcetype><collection/><card:addressbook/></resourcetype>"))
        if request.method == "REPORT" and url == _BOOK:
            assert b"addressbook-query" in request.content
            return _ms(*(_resp(f"/123/carddavhome/card/{name}",
                               f'<getetag>"e{i}"</getetag><card:address-data>'
                               f"{text.replace('&', '&amp;').replace('<', '&lt;')}"
                               "</card:address-data>")
                         for i, (name, text) in enumerate(self.cards.items())))
        return httpx.Response(404)


@pytest.fixture
def cards(monkeypatch, conn):
    from icloud_mcp import contacts
    fake = FakeCardDAV()
    monkeypatch.setattr(contacts, "_client", lambda c: httpx.Client(
        auth=(c.address, c.password), transport=httpx.MockTransport(fake.handle),
        follow_redirects=True))
    contacts._cache.clear()
    yield fake
    contacts._cache.clear()
