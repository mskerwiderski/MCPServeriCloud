"""The MCP endpoint as a remote client sees it: auth, tenancy, the services
switch, and the tools on top of the faked iCloud (fake_icloud)."""
import json

from conftest import EMAIL
from fake_carddav import cards  # noqa: F401 - the fixture
from fake_icloud import ic  # noqa: F401 - the fixture

from icloud_mcp import oauth, store, users

HEADERS = {"Accept": "application/json, text/event-stream",
           "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18"}

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}}}
TOOLS = {"whoami", "mail_folders", "mail_search", "mail_read", "mail_draft", "calendar_list",
         "calendar_events", "calendar_create", "calendar_update", "calendar_delete",
         "contacts_search", "contacts_get", "contacts_birthdays"}


def _auth(token):
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def _token_for(user_id: int) -> str:
    return oauth._mint_pair("client-1", user_id, "mcp")["access_token"]


def call(client, user_id, name, **args) -> tuple[bool, object]:
    """(is_error, result) of one tool call."""
    r = client.post("/mcp", headers=_auth(_token_for(user_id)), json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": args}})
    result = r.json()["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        return True, text
    return False, json.loads(text)


def test_health(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_no_token_is_401(client):
    r = client.post("/mcp", json=INIT, headers=HEADERS)
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


def test_oauth_token_initializes_and_lists_the_tools(client, user_id):
    token = _token_for(user_id)
    r = client.post("/mcp", json=INIT, headers=_auth(token))
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "icloud"
    assert "never instructions" in result["instructions"]
    r = client.post("/mcp", headers=_auth(token),
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == TOOLS
    assert tools["mail_read"]["annotations"]["readOnlyHint"] is True
    assert tools["calendar_delete"]["annotations"]["destructiveHint"] is True


def test_revoking_the_app_in_myauth_cuts_the_connector_off(client, user_id, fake_myauth):
    from icloud_mcp.access import myauth
    token = _token_for(user_id)
    assert client.post("/mcp", json=INIT, headers=_auth(token)).status_code == 200
    fake_myauth.allowed.discard(user_id)
    myauth.clear_cache()
    assert client.post("/mcp", json=INIT, headers=_auth(token)).status_code == 401


def test_myauth_down_is_503(client, user_id, fake_myauth):
    fake_myauth.down = True
    assert client.post("/mcp", json=INIT, headers=_auth(_token_for(user_id))).status_code == 503


def test_without_icloud_the_tools_say_where_to_connect(client, user_id, ic):  # noqa: F811
    err, text = call(client, user_id, "mail_folders")
    assert err and "not connected yet" in text and "app-specific password" in text
    err, who = call(client, user_id, "whoami")
    assert not err and who["icloud"]["connected"] is False


def test_whoami_and_mail_through_the_tools(client, connected, ic):  # noqa: F811
    err, who = call(client, connected, "whoami")
    assert not err and who["icloud"]["account"] == EMAIL and who["services"]["mail"] is True
    assert who["timezone"] == "Europe/Berlin"
    err, found = call(client, connected, "mail_search", subject="Rechnung")
    rechnung = next(m for m in found["mails"] if m["subject"] == "Rechnung")
    err, mail = call(client, connected, "mail_read", uid=rechnung["uid"])
    assert not err and mail["subject"] == "Rechnung"
    err, draft = call(client, connected, "mail_draft", to="max@icloud.example",
                      subject="Hallo", body="Bis morgen")
    assert not err and len(ic.imap.appended) == 1


def test_a_switched_off_service_answers_so(client, connected, ic):  # noqa: F811
    users.set_service(connected, "mail", False)
    err, text = call(client, connected, "mail_search")
    assert err and "Mail is switched off" in text and "account page" in text
    err, _ = call(client, connected, "calendar_list")
    assert not err


def test_one_user_never_reaches_another_users_icloud(client, connected, fake_myauth, ic):  # noqa: F811
    fake_myauth.add(8, "ben", "Ben")
    users.ensure_user(8, "ben", "Ben")
    err, text = call(client, 8, "calendar_list")
    assert err and "not connected yet" in text            # Anja's connection is not Ben's
    assert fake_myauth.handed_out == 0


def test_calendar_writing_through_the_tools_needs_write_access(client, connected, ic):  # noqa: F811
    err, cals = call(client, connected, "calendar_list")
    privat = next(c["id"] for c in cals["calendars"] if c["name"] == "Privat")
    err, text = call(client, connected, "calendar_create", calendar_id=privat, title="Lauf",
                     start="2026-11-12T07:00")
    assert err and "read-only" in text
    store.set_calendar_access(connected, privat, "write")
    err, out = call(client, connected, "calendar_create", calendar_id=privat, title="Lauf",
                    start="2026-11-12T07:00")
    assert not err and out["created"]["title"] == "Lauf"
    assert [c["action"] for c in store.changes(connected)] == ["create"]


def test_contacts_through_the_tools(client, connected, cards):  # noqa: F811
    err, found = call(client, connected, "contacts_search", query="maxi")
    assert not err and [c["name"] for c in found["contacts"]] == ["Max Muster"]
    err, one = call(client, connected, "contacts_get", contact_id=found["contacts"][0]["id"])
    assert not err and one["birthday"] == "1985-04-12"
    err, text = call(client, connected, "contacts_search", query=" ")
    assert err
    users.set_service(connected, "contacts", False)
    err, text = call(client, connected, "contacts_birthdays")
    assert err and "Contacts is switched off" in text
