"""Test fixtures. No test talks to MyAuth or iCloud, and none touches a real
database: MyAuth is faked at `myauth._fetch_session`, `_fetch_access` and
`_request` (the iCloud connection it hands out); iCloud at the mock points of
mail.py (`_imap`), calendar.py and contacts.py (`_client`) - the fakes are in
fake_icloud.py and fake_carddav.py. All names, addresses and mails are made up
(the fictitious family "Muster")."""
import pytest

PUBLIC = "https://icloud.example"
SAME_ORIGIN = {"Origin": PUBLIC}
EMAIL, PASSWORD = "anja@icloud.example", "abcdefghijklmnop"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_DB", str(tmp_path / "app.db"))
    # never the real ~/.config/icloud-mcp of the standalone mode
    monkeypatch.setenv("ICLOUD_MCP_ACCOUNT", str(tmp_path / "config" / "account.json"))
    monkeypatch.setenv("MYAUTH_APP_KEY", "test-app-key")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    for name in ("PUBLIC_URL", "MCP_URL", "UI_URL"):
        monkeypatch.delenv(name, raising=False)
    from icloud_mcp import account, db
    account.use_source(account.MyAuthSource())     # a standalone test must not leak
    db.init()
    yield tmp_path
    account.use_source(account.MyAuthSource())


# --- MyAuth ------------------------------------------------------------------


class FakeMyAuth:
    """Which browser cookie belongs to whom, who may use the app, and whose
    iCloud is connected."""

    def __init__(self) -> None:
        self.users: dict[int, dict] = {}
        self.cookies: dict[str, int] = {}
        self.allowed: set[int] = set()
        self.down = False
        self.icloud: dict[int, dict] = {}
        self.handed_out = 0

    def connect_icloud(self, uid: int, email: str = EMAIL, password: str = PASSWORD) -> None:
        self.icloud[uid] = {"email": email, "password": password}

    def add(self, uid: int, username: str, display_name: str = "",
            cookie: str | None = None, allowed: bool = True) -> int:
        self.users[uid] = {"uid": uid, "username": username,
                           "display_name": display_name or username, "is_admin": False,
                           "timezone": "Europe/Berlin"}
        if cookie:
            self.cookies[cookie] = uid
        if allowed:
            self.allowed.add(uid)
        return uid

    def _down(self):
        from icloud_mcp.access import myauth
        if self.down:
            raise myauth.Unavailable("down")

    async def session(self, cookie):
        self._down()
        uid = self.cookies.get(cookie)
        if uid is None:
            return 401, {"detail": "not_authenticated"}
        if uid not in self.allowed:
            return 403, {"detail": "app_not_allowed"}
        return 200, {**self.users[uid], "apps": ["mcp-icloud"], "language": None}

    async def access(self, uid):
        self._down()
        if uid in self.users and uid in self.allowed:
            return 200, dict(self.users[uid])
        return 403, {"detail": "not_allowed"}

    async def request(self, method, url, params, data=None, files=None, timeout=10):
        """The internal endpoints the server calls through myauth.call."""
        self._down()
        uid = int(params.get("uid", 0))
        assert params.get("app") == "mcp-icloud"
        if uid not in self.allowed:
            return 403, {"detail": "not_allowed"}
        entry = self.icloud.get(uid)
        if url.endswith("/api/internal/icloud/token"):
            if entry is None:
                return 404, {"detail": "not_connected"}
            self.handed_out += 1
            return 200, {**entry, "account": entry["email"]}
        if url.endswith("/api/internal/connections"):
            return 200, {"icloud": {"connected": entry is not None,
                                    "account": entry["email"] if entry else None,
                                    "last_error": None, "last_error_at": None}}
        return 404, {"detail": "unknown"}


@pytest.fixture(autouse=True)
def fake_myauth(monkeypatch):
    from icloud_mcp.access import myauth
    fake = FakeMyAuth()
    monkeypatch.setenv("MYAUTH_URL", "http://myauth.test")
    monkeypatch.setattr(myauth, "_fetch_session", fake.session)
    monkeypatch.setattr(myauth, "_fetch_access", fake.access)
    monkeypatch.setattr(myauth, "_request", fake.request)
    myauth.clear_cache()
    yield fake
    myauth.clear_cache()


@pytest.fixture
def client(monkeypatch):
    """The HTTP app as the browser sees it."""
    from starlette.testclient import TestClient

    from icloud_mcp.server import build_http_app
    monkeypatch.setenv("PUBLIC_URL", PUBLIC)
    with TestClient(build_http_app(), base_url=PUBLIC) as c:
        yield c


@pytest.fixture
def user_id(fake_myauth):
    """Anja: a MyAuth user who may use the app, known to this server."""
    from icloud_mcp import users
    fake_myauth.add(7, "anja", "Anja", cookie="anja-cookie")
    users.ensure_user(7, "anja", "Anja")
    return 7


@pytest.fixture
def anja(client, user_id):
    """A browser signed in to MyAuth as Anja."""
    client.cookies.set("sso_session", "anja-cookie")
    return client


@pytest.fixture
def connected(user_id, fake_myauth):
    """Anja with iCloud connected in MyAuth."""
    fake_myauth.connect_icloud(user_id)
    return user_id


@pytest.fixture
def conn(user_id):
    """Anja's connection as the mail, calendar and contact functions get it."""
    from icloud_mcp.account import Conn
    return Conn(uid=user_id, address=EMAIL, password=PASSWORD, timezone="Europe/Berlin")
