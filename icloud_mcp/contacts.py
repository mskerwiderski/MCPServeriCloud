"""iCloud contacts over CardDAV (contacts.icloud.com), read-only: search,
one contact in full, upcoming birthdays. Nothing here writes to iCloud.

The CardDAV part is small: PROPFIND for the principal, the address book home
and the address books, then one REPORT (addressbook-query with address-data)
per address book that answers every card at once - iCloud does that for a
whole book (about 1 KB per card). The parsed cards are kept in memory per
user for CACHE_S, so a search does not download the book each time; no card
is ever written to disk.

iCloud sends vCard 3.0: folded lines, labels as item groups (`item1.TEL` plus
`item1.X-ABLabel:_$!<Mobile>!$_` or a label of the person's own), escaped
`\\,` `\\;` `\\n`, and birthdays without a year as year 1604
(`BDAY;X-APPLE-OMIT-YEAR=1604:1604-11-25`). Group cards
(`X-ADDRESSBOOKSERVER-KIND:group`) are not contacts and are left out.

`_client` is the mock point of the tests, `_today` the one for birthdays."""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import unquote, urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from . import account
from .account import Conn, IcloudError

ROOT = "https://contacts.icloud.com/"
NS = {"d": "DAV:", "a": "urn:ietf:params:xml:ns:carddav"}
XML_HEAD = '<?xml version="1.0" encoding="utf-8"?>'
CACHE_S = 300
MAX_NOTE = 2000
MAX_RESULTS = 50
NO_YEAR = 1604                      # Apple's year for "birthday without a year"

_cache: dict[int, tuple[float, str, list[dict]]] = {}
_lock = threading.Lock()


def _client(conn: Conn) -> httpx.Client:
    return httpx.Client(auth=(conn.address, conn.password), timeout=30, follow_redirects=True)


def forget(uid: int) -> None:
    """Drop the cached cards of `uid` (the next call fetches them again)."""
    _cache.pop(uid, None)


# ---------- CardDAV ----------

def _send(c: httpx.Client, method: str, url: str, body: str, depth: str) -> httpx.Response:
    try:
        r = c.request(method, url, content=body.encode(),
                      headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
    except httpx.HTTPError as e:
        raise IcloudError(f"iCloud Contacts is not answering ({e.__class__.__name__}).") from e
    if r.status_code in (401, 403):
        raise account.refused("contacts")
    if r.status_code != 207:
        raise IcloudError(f"iCloud Contacts answered with HTTP {r.status_code}.")
    return r


def _multistatus(r: httpx.Response) -> list[tuple[str, ET.Element]]:
    """(absolute href, <prop> of the 200 propstat) per response."""
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise IcloudError("iCloud Contacts sent an answer that cannot be read.") from e
    out = []
    for resp in root.findall("d:response", NS):
        href = resp.findtext("d:href", "", NS).strip()
        for stat in resp.findall("d:propstat", NS):
            if " 200 " in f"{stat.findtext('d:status', '', NS)} ":
                prop = stat.find("d:prop", NS)
                if prop is not None:
                    out.append((urljoin(str(r.url), href), prop))
    return out


def _propfind(c: httpx.Client, url: str, props: str, depth: str = "0"):
    body = (f'{XML_HEAD}<d:propfind xmlns:d="DAV:" xmlns:a="{NS["a"]}">'
            f'<d:prop>{props}</d:prop></d:propfind>')
    return _multistatus(_send(c, "PROPFIND", url, body, depth))


def _href(found: list, path: str, what: str) -> str:
    for base, prop in found:
        h = prop.findtext(path, "", NS).strip()
        if h:
            return urljoin(base, h)
    raise IcloudError(f"iCloud Contacts: {what} not found.")


def _books(c: httpx.Client) -> list[str]:
    principal = _href(_propfind(c, ROOT, "<d:current-user-principal/>"),
                      "d:current-user-principal/d:href", "the account")
    home = _href(_propfind(c, principal, "<a:addressbook-home-set/>"),
                 "a:addressbook-home-set/d:href", "the address book home")
    return [url for url, prop in _propfind(c, home, "<d:resourcetype/>", depth="1")
            if prop.find("d:resourcetype/a:addressbook", NS) is not None]


def _cards(c: httpx.Client, book: str) -> list[tuple[str, str]]:
    """(href, vCard text) of every card in one address book."""
    body = (f'{XML_HEAD}<a:addressbook-query xmlns:d="DAV:" xmlns:a="{NS["a"]}">'
            '<d:prop><d:getetag/><a:address-data/></d:prop></a:addressbook-query>')
    return [(href, prop.findtext("a:address-data", "", NS))
            for href, prop in _multistatus(_send(c, "REPORT", book, body, "1"))
            if prop.findtext("a:address-data", "", NS).strip()]


def _all(conn: Conn, fresh: bool = False) -> list[dict]:
    """The user's contacts, parsed, from the cache while it is young."""
    hit = _cache.get(conn.uid)
    if not fresh and hit and hit[0] > time.monotonic() and hit[1] == conn.address:
        return hit[2]
    with _lock, _client(conn) as c:
        found = []
        for book in _books(c):
            for href, text in _cards(c, book):
                card = parse(text, href)
                if card is not None:
                    found.append(card)
    found.sort(key=lambda k: k["name"].casefold())
    _cache[conn.uid] = (time.monotonic() + CACHE_S, conn.address, found)
    return found


# ---------- vCard ----------

_ESCAPES = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}
_TYPES = {"cell": "mobile", "iphone": "iPhone", "main": "main", "home": "home", "work": "work",
          "other": "other", "pager": "pager", "fax": "fax", "school": "school"}
_IGNORED_TYPES = {"pref", "internet", "voice", "x400"}


def _split(value: str, sep: str) -> list[str]:
    """Split at unescaped `sep`, then unescape every part."""
    parts, buf, i = [], [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            buf.append(_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        if ch == sep:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _text(value: str) -> str:
    return _split(value, "\x00")[0].strip()


def _lines(text: str):
    """(group, NAME, {param: [values]}, raw value) per unfolded line."""
    for line in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        head, quoted, cut = line, False, -1
        for i, ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif ch == ":" and not quoted:
                cut = i
                break
        if cut < 0:
            continue
        head, value = line[:cut], line[cut + 1:]
        name, *raw_params = head.split(";")
        group, _, name = name.rpartition(".")
        params: dict[str, list[str]] = {}
        for p in raw_params:
            key, eq, val = p.partition("=")
            key, val = (key, val) if eq else ("type", key)
            params.setdefault(key.lower(), []).extend(
                v.strip('"') for v in val.split(",") if v)
        yield group.lower(), name.upper(), params, value


def _label(raw: str) -> str:
    """`_$!<Mobile>!$_` (Apple's own labels) -> "mobile"; a label of the person's own as it is."""
    m = re.fullmatch(r"_\$!<(.*)>!\$_", raw.strip())
    return m.group(1).lower() if m else raw.strip()


def _type_label(params: dict) -> str:
    types = [t.lower() for t in params.get("type", []) if t.lower() not in _IGNORED_TYPES]
    words = [_TYPES.get(t, t) for t in types]
    if "fax" in words and len(words) > 1:           # HOME + FAX -> "home fax"
        words = [w for w in words if w != "fax"] + ["fax"]
    return " ".join(dict.fromkeys(words))


def _birthday(value: str, params: dict) -> str:
    """ISO date, "--MM-DD" without a year; "" when it cannot be read."""
    v = value.strip()[:10] if "T" not in value else value.split("T")[0]
    m = (re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", v)
         or re.fullmatch(r"--()(\d{2})-?(\d{2})", v))
    if not m:
        return ""
    year, month, day = m.groups()
    omit = params.get("x-apple-omit-year")
    if not year or (omit and omit[0] == year) or int(year) == NO_YEAR:
        return f"--{month}-{day}"
    return f"{year}-{month}-{day}"


def _compact(data: dict) -> dict:
    return {k: v for k, v in data.items() if v not in ("", None, [], {})}


def parse(text: str, href: str = "") -> dict | None:
    """One vCard as a contact (empty fields left out); None for a group card."""
    lines = list(_lines(text))
    labels = {g: _label(v) for g, n, _, v in lines if g and n == "X-ABLABEL"}
    fields: dict[str, str] = {}
    lists: dict[str, list] = {"emails": [], "phones": [], "addresses": [], "urls": [],
                              "related": []}
    n_parts: list[str] = []
    for group, name, params, value in lines:
        label = labels.get(group) or _type_label(params)
        if name == "X-ADDRESSBOOKSERVER-KIND" and value.strip().lower() == "group":
            return None
        if name in ("FN", "NICKNAME", "TITLE", "UID", "NOTE"):
            fields.setdefault(name, _text(value))
        elif name == "N":
            n_parts = [p.strip() for p in _split(value, ";")]
        elif name == "ORG":
            org = [p.strip() for p in _split(value, ";")]
            fields.setdefault("ORG", org[0] if org else "")
            fields.setdefault("DEPT", org[1] if len(org) > 1 else "")
        elif name == "BDAY":
            fields.setdefault("BDAY", _birthday(value, params))
        elif name == "X-ABSHOWAS":
            fields["SHOWAS"] = value.strip().upper()
        elif name in ("EMAIL", "TEL", "URL", "X-ABRELATEDNAMES", "RELATED"):
            key = {"EMAIL": "emails", "TEL": "phones", "URL": "urls"}.get(name, "related")
            if _text(value):
                lists[key].append(_compact({"label": label, "value": _text(value)}))
        elif name == "ADR":
            p = (_split(value, ";") + [""] * 7)[:7]
            street = "\n".join(x.strip() for x in (p[1], p[2]) if x.strip())
            adr = _compact({"label": label, "street": street, "city": p[3].strip(),
                            "region": p[4].strip(), "postal_code": p[5].strip(),
                            "country": p[6].strip()})
            if set(adr) - {"label"}:
                lists["addresses"].append(adr)
    if n_parts and len(n_parts) < 5:
        n_parts += [""] * (5 - len(n_parts))
    from_n = " ".join(x for x in (n_parts[3:4] + n_parts[1:3] + n_parts[0:1] + n_parts[4:5])
                      if x) if n_parts else ""
    org = fields.get("ORG", "")
    emails = [e["value"] for e in lists["emails"]]
    name = ((org if fields.get("SHOWAS") == "COMPANY" and org else "")
            or fields.get("FN", "") or from_n or org or (emails[0] if emails else ""))
    file_name = unquote(href.rstrip("/").rsplit("/", 1)[-1]).removesuffix(".vcf")
    note = fields.get("NOTE", "")
    if len(note) > MAX_NOTE:
        note = note[:MAX_NOTE] + " [...]"
    return _compact({
        "id": fields.get("UID") or file_name, "name": name or "(no name)",
        "first_name": n_parts[1] if n_parts else "", "last_name": n_parts[0] if n_parts else "",
        "nickname": fields.get("NICKNAME", ""), "organization": org,
        "department": fields.get("DEPT", ""), "title": fields.get("TITLE", ""),
        **lists, "birthday": fields.get("BDAY", ""), "notes": note})


# ---------- the tools' functions ----------

def _digits(phone: str) -> str:
    """A phone number as digits, +49 / 0049 as the German 0."""
    d = re.sub(r"\D", "", phone)
    if phone.strip().startswith("+"):
        d = "00" + d
    return "0" + d[4:] if d.startswith("0049") else d


def _matches(card: dict, word: str) -> bool:
    text = " ".join([card.get("name", ""), card.get("nickname", ""), card.get("organization", ""),
                     card.get("first_name", ""), card.get("last_name", "")]
                    + [e["value"] for e in card.get("emails", [])]).casefold()
    if word in text:
        return True
    if re.fullmatch(r"[+\d][\d\s/()-]{2,}", word):
        wanted = _digits(word)
        return bool(wanted) and any(wanted in _digits(p["value"]) for p in card.get("phones", []))
    return False


def search(conn: Conn, query: str, limit: int = 20) -> dict:
    """Contacts where every word of `query` is in the name, nickname,
    organization, an e-mail address or a phone number."""
    words = (query or "").casefold().split()
    if not words:
        raise ValueError("Give a name, e-mail address or phone number to search for.")
    limit = max(1, min(int(20 if limit is None else limit), MAX_RESULTS))
    hits = [k for k in _all(conn) if all(_matches(k, w) for w in words)]
    return {"contacts": [_compact({"id": k["id"], "name": k["name"],
                                   "organization": k.get("organization", ""),
                                   "emails": [e["value"] for e in k.get("emails", [])],
                                   "phones": [p["value"] for p in k.get("phones", [])]})
                         for k in hits[:limit]],
            "total": len(hits)}


def get(conn: Conn, contact_id: str) -> dict:
    """One contact with everything that is useful (no photo)."""
    contact_id = (contact_id or "").strip()
    for fresh in (False, True):                  # a contact added since the last fetch
        card = next((k for k in _all(conn, fresh) if k["id"] == contact_id), None)
        if card is not None:
            return card
    raise ValueError("No contact with this id - take the id from contacts_search.")


def _zone(conn: Conn) -> ZoneInfo:
    try:
        return ZoneInfo(conn.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Berlin")


def _today(zone: ZoneInfo) -> date:
    return datetime.now(zone).date()


def _next(month: int, day: int, today: date) -> date:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:                       # 29 February outside a leap year
            d = date(year, 2, 28)
        if d >= today:
            return d
    raise AssertionError("unreachable")


def upcoming_birthdays(conn: Conn, days: int = 30) -> dict:
    """Birthdays from today up to `days` days ahead, in the person's time zone."""
    days = max(1, min(int(30 if days is None else days), 366))
    today = _today(_zone(conn))
    out = []
    for k in _all(conn):
        b = k.get("birthday", "")
        m = re.fullmatch(r"(\d{4}|-)-(\d{2})-(\d{2})", b)
        if not m:
            continue
        year, month, day = m.groups()
        when = _next(int(month), int(day), today)
        if (when - today).days > days:
            continue
        out.append(_compact({"id": k["id"], "name": k["name"], "date": when.isoformat(),
                             "in_days": (when - today).days,
                             "age": when.year - int(year) if year != "-" else None}))
    out.sort(key=lambda e: (e["date"], e["name"].casefold()))
    return {"from": today.isoformat(), "days": days, "birthdays": out}
