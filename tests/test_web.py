"""The account page: the connection's state from MyAuth, the services, the
calendar access, undo, deleting one's data, and that a form from another site
is refused."""
from conftest import SAME_ORIGIN
from fake_icloud import ic  # noqa: F401 - the fixture

from icloud_mcp import events, store, users
from icloud_mcp.account import Conn


def test_signed_out_goes_to_myauth(client):
    r = client.get("/account", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_not_allowed_in_myauth(client, fake_myauth):
    fake_myauth.add(9, "gast", cookie="gast-cookie", allowed=False)
    client.cookies.set("sso_session", "gast-cookie")
    r = client.get("/account")
    assert r.status_code == 403 and "not enabled for iCloud MCP" in r.text


def test_without_icloud_the_page_says_how_to_connect(anja):
    page = anja.get("/account").text
    assert "not connected" in page and "Connect iCloud in MyAuth" in page
    assert "/connections#pf-icloud" in page and "app-specific password" in page
    assert "https://icloud.example/mcp" in page or "icloud.example" in page


def test_connected_page_lists_the_calendars_read_only(anja, connected, ic):  # noqa: F811
    page = anja.get("/account").text
    assert "connected" in page and "anja@icloud.example" in page
    assert "Privat" in page and "Familie" in page and "Erinnerungen" not in page
    assert all(c["access"] == "read" for c in store.calendars(connected))


def test_services_and_calendar_access_are_saved(anja, connected, ic):  # noqa: F811
    anja.get("/account")
    r = anja.post("/account/services", data={"mail": "1", "contacts": "1"}, headers=SAME_ORIGIN)
    assert r.status_code == 200 and "Saved." in r.text
    assert users.services(connected) == {"mail": True, "calendar": False, "contacts": True}
    users.set_service(connected, "calendar", True)
    privat = next(c for c in store.calendars(connected) if c["name"] == "Privat")
    anja.post("/account/calendars", data={"do": "save", f"access_{privat['id']}": "write"},
              headers=SAME_ORIGIN)
    assert next(c for c in store.calendars(connected) if c["id"] == privat["id"])["access"] == "write"


def test_a_form_from_another_site_is_refused(anja, connected):
    r = anja.post("/account/services", data={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert users.services(connected)["mail"] is True


def test_undo_puts_the_event_back(anja, connected, ic):  # noqa: F811
    conn = Conn(uid=connected, address="anja@icloud.example", password="abcdefghijklmnop",
                timezone="Europe/Berlin")
    events.calendar_list(conn)
    privat = next(c["id"] for c in store.calendars(connected) if c["name"] == "Privat")
    store.set_calendar_access(connected, privat, "write")
    out = events.list_events(conn, "2026-11-10", calendar_id=privat)
    zahnarzt = next(e for e in out["events"] if e["title"] == "Zahnarzt")
    events.delete(conn, privat, zahnarzt["event_id"])
    assert "/123/calendars/home/zahnarzt.ics" not in ic.dav.objects
    page = anja.get("/account").text
    assert "Zahnarzt" in page and "deleted" in page and "Undo" in page
    change = store.changes(connected)[0]
    r = anja.post("/account/undo", data={"id": str(change["id"])}, headers=SAME_ORIGIN)
    assert r.status_code == 200 and "The change was undone." in r.text
    assert "/123/calendars/home/zahnarzt.ics" in ic.dav.objects
    r = anja.post("/account/undo", data={"id": str(change["id"])}, headers=SAME_ORIGIN)
    assert "can no longer be undone" in r.text


def test_delete_my_data(anja, connected, ic):  # noqa: F811
    anja.get("/account")
    assert store.calendars(connected)
    r = anja.post("/account/delete", headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and store.calendars(connected) == []
    assert users.get_user(connected) is None


def test_consent_page_warns_without_icloud(anja):
    r = anja.get("/oauth/authorize", params={
        "response_type": "code", "client_id": "nope", "redirect_uri": "https://claude.ai/cb",
        "code_challenge": "x" * 43, "code_challenge_method": "S256"})
    # an unknown client is refused before the page - the warning itself:
    from icloud_mcp.oauth import _AuthorizeRequest, _consent_html
    req = _AuthorizeRequest(client_id="c", client_name="Claude", redirect_uri="https://claude.ai/cb",
                            code_challenge="x" * 43, state=None, scope="mcp")
    html = _consent_html(req, {"display_name": "Anja"}, connected=False)
    assert "not connected yet" in html and "cannot send, delete or move mail" in html
    assert r.status_code in (400, 200)
