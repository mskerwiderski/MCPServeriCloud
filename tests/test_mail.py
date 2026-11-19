"""Mail (icloud_mcp/mail.py): folders, search, read without marking read, and
drafts - nothing is sent. IMAP faked (fake_icloud.FakeIMAP)."""

import pytest
from fake_icloud import ic  # noqa: F401 - the fixture

from icloud_mcp import mail
from icloud_mcp.account import Conn, IcloudError


def test_folders_search_and_read_without_marking_read(conn, ic):  # noqa: F811
    out = mail.folders(conn)
    assert {"name": "Geschäft", "role": ""} in out["folders"]
    assert {"name": "Drafts", "role": "drafts"} in out["folders"]
    assert all(f["name"] != "Archiv" for f in out["folders"])          # \Noselect

    out = mail.search(conn, sender="sw-muster.example", since="2026-11-01", unread=True)
    assert out["found"] == 2 and [m["uid"] for m in out["mails"]] == [102, 101]
    assert out["mails"][1]["subject"] == "Rechnung" and not out["mails"][1]["unread"]
    assert out["mails"][0]["unread"] and out["mails"][0]["date"] == "2026-11-05T10:15+01:00"
    command, args, literal = ic.imap.commands[0]
    assert args == ("SINCE", "1-Nov-2026", "UNSEEN", "FROM", '"sw-muster.example"')
    assert literal is None
    assert ic.imap.selected[1] is True                                  # read-only

    out = mail.read(conn, 101)
    assert out["text"].strip() == "Ihre Rechnung."
    assert out["attachments"] == [{"name": "rechnung.pdf", "type": "application/pdf", "size": 10}]
    assert "BODY.PEEK[]" in ic.imap.commands[-1][1][1]
    out = mail.read(conn, 102)
    assert out["text"].split() == ["Hallo", "Grüße"]
    with pytest.raises(IcloudError, match="999"):
        mail.read(conn, 999)


def test_search_with_umlauts_sends_one_literal(conn, ic):  # noqa: F811
    mail.search(conn, folder="Geschäft", subject="Grüße", sender="oma")
    command, args, literal = ic.imap.commands[0]
    assert args == ("CHARSET", "UTF-8", "FROM", '"oma"', "SUBJECT") and literal == "Grüße".encode()
    assert ic.imap.selected[0] == '"Gesch&AOQ-ft"'
    with pytest.raises(IcloudError, match="non-ASCII"):
        mail.search(conn, subject="Grüße", text="Bär")
    with pytest.raises(IcloudError, match="Gibtsnicht"):
        mail.search(conn, folder="Gibtsnicht")
    with pytest.raises(IcloudError, match="a date like"):
        mail.search(conn, since="gestern")


def test_a_reply_draft_lands_in_drafts_and_nothing_is_sent(conn, ic):  # noqa: F811
    out = mail.draft(conn, reply_to_uid=101, body="Danke, ist bezahlt.")
    assert out["saved_in"] == "Drafts" and out["subject"] == "Re: Rechnung"
    assert "only the person can send it" in out["note"]
    (box, flags, raw), = ic.imap.appended
    assert box == '"Drafts"' and flags == "(\\Draft \\Seen)"
    text = raw.decode()
    assert "To: Stadtwerke Musterstadt <info@sw-muster.example>" in text
    assert "In-Reply-To: <a@sw-muster.example>" in text
    assert f"From: {conn.address}" in text and "Danke, ist bezahlt." in text
    with pytest.raises(IcloudError, match="to:"):
        mail.draft(conn, subject="x", body="y")
    with pytest.raises(IcloudError, match="not a valid"):
        mail.draft(conn, to="max", subject="x")


def test_a_rejected_sign_in_says_so(conn, ic):  # noqa: F811
    wrong = Conn(uid=conn.uid, address=conn.address, password="x" * 16, timezone=conn.timezone)
    with pytest.raises(IcloudError, match="rejected the sign-in"):
        mail.folders(wrong)
    with pytest.raises(IcloudError, match="rejected the sign-in"):
        mail.check(wrong)


def test_modified_utf7_folder_names():
    for name in ("INBOX", "Geschäft", "Rechnungen & Belege", "日本語", "Ölß/Ä"):
        assert mail.decode_name(mail.encode_name(name)) == name
    assert mail.encode_name("Geschäft") == "Gesch&AOQ-ft"
    assert mail.encode_name("A&B") == "A&-B"
