"""The standalone mode: the account file (0600), the check with iCloud, the
local source in place of MyAuth, and the command line."""
import asyncio
import os
import stat

import httpx
import pytest
from conftest import EMAIL, PASSWORD
from fake_icloud import ic  # noqa: F401 - the fixture

from icloud_mcp import account, cli, local, store, users
from icloud_mcp.account import IcloudError


def test_normalize_takes_apples_dashes_and_refuses_an_apple_id_password():
    assert local.normalize(" Anja@iCloud.example ", "ABCD-efgh-ijkl-mnop") == (EMAIL, PASSWORD)
    with pytest.raises(ValueError, match="16 letters"):
        local.normalize(EMAIL, "MyAppleID-Passw0rd!")
    with pytest.raises(ValueError, match="e-mail"):
        local.normalize("anja", PASSWORD)


def test_check_asks_caldav_once(monkeypatch):
    def handle(request):
        ok = request.headers["authorization"] == httpx.BasicAuth(EMAIL, PASSWORD)._auth_header
        return httpx.Response(207 if ok else 401)
    monkeypatch.setattr(local, "_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handle)))
    local.check(EMAIL, PASSWORD)
    with pytest.raises(IcloudError, match="rejected"):
        local.check(EMAIL, "zzzzzzzzzzzzzzzz")


def test_the_account_file_is_only_for_this_user():
    path = local.save(EMAIL, PASSWORD)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert local.load() == {"email": EMAIL, "password": PASSWORD}
    assert local.forget() and local.load() is None


def test_the_local_source_replaces_myauth(ic):  # noqa: F811
    local.prepare()
    with pytest.raises(IcloudError, match="icloud-mcp login"):
        asyncio.run(account.conn(local.USER_ID, "calendar"))
    local.save(EMAIL, PASSWORD)
    conn = asyncio.run(account.conn(local.USER_ID, "calendar"))
    assert conn.address == EMAIL and conn.uid == 0
    users.set_service(local.USER_ID, "mail", False)
    with pytest.raises(IcloudError, match="icloud-mcp services"):
        asyncio.run(account.conn(local.USER_ID, "mail"))


def test_cli_calendars_access_and_changes(ic, capsys):  # noqa: F811
    local.save(EMAIL, PASSWORD)
    assert cli.main(["calendars"]) == 0
    out = capsys.readouterr().out
    assert "Privat" in out and "read" in out
    assert cli.main(["access", "privat", "write"]) == 0
    assert next(c for c in store.calendars(0) if c["name"] == "Privat")["access"] == "write"
    assert cli.main(["access", "privat", "admin"]) == 2
    assert cli.main(["services", "contacts", "off"]) == 0
    assert "contacts  off" in capsys.readouterr().out
    assert cli.main(["changes"]) == 0
    assert "No calendar changes" in capsys.readouterr().out


def test_status_without_an_account(capsys):
    assert cli.main(["status"]) == 1
    assert "icloud-mcp login" in capsys.readouterr().out
