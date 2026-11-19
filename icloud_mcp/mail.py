"""iCloud mail over IMAP (imap.mail.me.com), for the tools: list folders,
search, read, and put a draft into the Drafts folder. Nothing here sends,
deletes, moves or marks a mail as read (BODY.PEEK, folders opened read-only) -
the person sends a draft themselves, from their own devices.

Answers are cut (MAX_BODY characters of text, no attachment contents) so one
mail does not eat the conversation's context. Taken over from Ash
(app/mail.py). `_imap` is the mock point of the tests."""

from __future__ import annotations

import base64
import imaplib
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, make_msgid, parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .account import Conn, IcloudError, refused

HOST, PORT = "imap.mail.me.com", 993
MAX_BODY = 20000
MAX_RESULTS = 50
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ROLES = {"\\drafts": "drafts", "\\sent": "sent", "\\trash": "trash", "\\junk": "junk",
         "\\archive": "archive"}
LIST_LINE = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)')
FETCH_HEAD = re.compile(rb"UID (\d+)|FLAGS \(([^)]*)\)|RFC822\.SIZE (\d+)")


def _imap() -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(HOST, PORT, timeout=30)


@contextmanager
def session(conn: Conn):
    try:
        con = _imap()
    except OSError as e:
        raise IcloudError(f"iCloud Mail is not answering ({e}).") from e
    try:
        try:
            con.login(conn.address, conn.password)
        except imaplib.IMAP4.error as e:
            raise refused("mail") from e
        yield con
    finally:
        try:
            con.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def check(conn: Conn) -> None:
    """Raises IcloudError when the login fails."""
    with session(conn):
        pass


# ---------- folder names (IMAP's modified UTF-7, RFC 3501 5.1.3) ----------

def decode_name(name: str) -> str:
    def part(m: re.Match) -> str:
        if m.group(1) == "":
            return "&"
        b64 = m.group(1).replace(",", "/")
        return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", part, name)


def encode_name(name: str) -> str:
    out, buf = [], []

    def flush():
        if buf:
            b64 = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=")
            out.append("&" + b64.replace("/", ",") + "-")
            buf.clear()
    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def _quote(name: str) -> str:
    raw = encode_name(name)
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ok(answer: tuple, what: str) -> list:
    status, data = answer
    if status != "OK":
        raise IcloudError(f"iCloud Mail: {what} failed ({_text(data)}).")
    return data


def _text(data) -> str:
    return " ".join(d.decode(errors="replace") if isinstance(d, bytes) else str(d)
                    for d in data or [] if d)[:200]


def _folders(con) -> list[dict]:
    out = []
    for line in _ok(con.list(), "listing the folders"):
        m = LIST_LINE.match(line.decode(errors="replace") if isinstance(line, bytes) else line)
        if not m:
            continue
        raw = m.group("name").strip()
        if raw.startswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        flags = m.group("flags").lower().split()
        if "\\noselect" in flags:
            continue
        role = next((ROLES[f] for f in flags if f in ROLES), "inbox" if raw.upper() == "INBOX" else "")
        out.append({"name": decode_name(raw), "role": role})
    return out


def _select(con, folder: str) -> None:
    status, data = con.select(_quote(folder or "INBOX"), readonly=True)
    if status != "OK":
        raise IcloudError(f'There is no folder "{folder}" - mail_folders lists them.')


def folders(conn: Conn) -> dict:
    with session(conn) as con:
        return {"folders": _folders(con)}


# ---------- search ----------

def _day(value: str, field: str) -> str:
    try:
        d = date.fromisoformat(value.strip())
    except ValueError as e:
        raise IcloudError(f"{field}: a date like 2026-11-06.") from e
    return f"{d.day}-{MONTHS[d.month - 1]}-{d.year}"


def _criteria(sender: str, to: str, subject: str, text: str, since: str, before: str,
              unread: bool) -> tuple[list[str], bytes | None]:
    """IMAP SEARCH keys; a value with non-ASCII characters goes last, as a
    literal (imaplib sends one, at the end) - so only one of them per search."""
    keys, literal = [], None
    if since:
        keys += ["SINCE", _day(since, "since")]
    if before:
        keys += ["BEFORE", _day(before, "before")]
    if unread:
        keys.append("UNSEEN")
    plain = []
    for key, value in (("FROM", sender), ("TO", to), ("SUBJECT", subject), ("TEXT", text)):
        value = (value or "").strip()
        if not value:
            continue
        if value.isascii():
            plain += [key, '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"']
        elif literal is None:
            literal = (key, value.encode())
        else:
            raise IcloudError("Only one search term with umlauts or other non-ASCII characters "
                              "per search.")
    keys += plain
    if literal:
        keys.append(literal[0])
        return keys, literal[1]
    return keys or ["ALL"], None


def _envelope(head: bytes, meta: bytes, zone: str) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(head, headersonly=True)
    uid = flags = size = None
    for m in FETCH_HEAD.finditer(meta):
        uid = uid or m.group(1)
        flags = flags if m.group(2) is None else m.group(2)
        size = size or m.group(3)
    return {"uid": int(uid) if uid else None, "date": _date(msg.get("Date"), zone),
            "from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
            "subject": str(msg.get("Subject", "")),
            "unread": b"\\seen" not in (flags or b"").lower(), "size": int(size or 0)}


def _date(value, zone: str) -> str:
    try:
        return parsedate_to_datetime(str(value)).astimezone(ZoneInfo(zone)).isoformat(
            timespec="minutes")
    except (TypeError, ValueError, IndexError):
        return str(value or "")


def search(conn: Conn, folder: str = "INBOX", sender: str = "", to: str = "",
           subject: str = "", text: str = "", since: str = "", before: str = "",
           unread: bool = False, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), MAX_RESULTS))
    keys, literal = _criteria(sender, to, subject, text, since, before, unread)
    with session(conn) as con:
        _select(con, folder)
        if literal is not None:
            con.literal = literal
            data = _ok(con.uid("SEARCH", "CHARSET", "UTF-8", *keys), "searching")
        else:
            data = _ok(con.uid("SEARCH", *keys), "searching")
        uids = [u for u in b" ".join(d for d in data if d).split()]
        found = len(uids)
        uids = uids[-limit:]
        mails = []
        if uids:
            data = _ok(con.uid("FETCH", b",".join(uids).decode(),
                               "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS "
                               "(FROM TO SUBJECT DATE)])"), "fetching")
            mails = [_envelope(part[1], part[0], conn.timezone)
                     for part in data if isinstance(part, tuple)]
            mails.sort(key=lambda m: m["uid"] or 0, reverse=True)
    return {"folder": folder or "INBOX", "found": found, "shown": len(mails), "mails": mails}


# ---------- read ----------

class _Text(HTMLParser):
    """HTML mail to plain text: block tags become line breaks, scripts and styles go."""

    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t\xa0]+", " ", "".join(p.parts))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _fetch(con, uid: int) -> tuple[EmailMessage, bytes]:
    data = _ok(con.uid("FETCH", str(int(uid)), "(UID FLAGS BODY.PEEK[])"), "fetching")
    part = next((p for p in data if isinstance(p, tuple)), None)
    if part is None:
        raise IcloudError(f"There is no mail with uid {uid} in this folder.")
    return BytesParser(policy=policy.default).parsebytes(part[1]), part[0]


def read(conn: Conn, uid: int, folder: str = "INBOX") -> dict:
    with session(conn) as con:
        _select(con, folder)
        msg, meta = _fetch(con, uid)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            text = body.get_content()
        except (LookupError, ValueError):
            text = body.get_payload(decode=True).decode("utf-8", errors="replace")
        if body.get_content_type() == "text/html":
            text = html_text(text)
    attachments = []
    for a in msg.iter_attachments():
        payload = a.get_payload(decode=True) or b""
        attachments.append({"name": a.get_filename() or "", "type": a.get_content_type(),
                            "size": len(payload)})
    head = _envelope(b"", meta, conn.timezone)
    return {"folder": folder or "INBOX", "uid": int(uid), "date": _date(msg.get("Date"), conn.timezone),
            "from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
            "cc": str(msg.get("Cc", "")), "subject": str(msg.get("Subject", "")),
            "unread": head["unread"], "text": text[:MAX_BODY],
            "cut": len(text) > MAX_BODY, "attachments": attachments}


# ---------- draft ----------

def _addresses(value: str, field: str) -> str:
    pairs = [(n, a) for n, a in getaddresses([value or ""]) if a]
    if any("@" not in a for _, a in pairs):
        raise IcloudError(f"{field}: not a valid e-mail address.")
    return ", ".join(f"{n} <{a}>" if n else a for n, a in pairs)


def draft(conn: Conn, to: str = "", subject: str = "", body: str = "", cc: str = "",
          reply_to_uid: int | None = None, reply_folder: str = "INBOX") -> dict:
    msg = EmailMessage()
    msg["From"] = conn.address
    with session(conn) as con:
        if reply_to_uid:
            _select(con, reply_folder)
            original, _ = _fetch(con, reply_to_uid)
            ref = str(original.get("Message-ID", "")).strip()
            if ref:
                msg["In-Reply-To"] = ref
                msg["References"] = " ".join(
                    x for x in (str(original.get("References", "")).strip(), ref) if x)
            to = to or str(original.get("Reply-To") or original.get("From") or "")
            old = str(original.get("Subject", ""))
            subject = subject or (old if old.lower().startswith(("re:", "aw:")) else f"Re: {old}")
        to = _addresses(to, "to")
        if not to:
            raise IcloudError("to: who is the draft for?")
        msg["To"] = to
        if cc:
            msg["Cc"] = _addresses(cc, "cc")
        msg["Subject"] = subject
        msg["Date"] = format_datetime(datetime.now(timezone.utc))
        msg["Message-ID"] = make_msgid(domain=conn.address.split("@", 1)[1])
        msg.set_content(body or "")
        box = next((f["name"] for f in _folders(con) if f["role"] == "drafts"), "Drafts")
        _ok(con.append(_quote(box), "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()),
                       msg.as_bytes()), "saving the draft")
    return {"saved_in": box, "to": to, "cc": msg.get("Cc", ""), "subject": subject,
            "note": "The draft is in iCloud - only the person can send it, from their own devices."}
