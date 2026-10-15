"""
Regression tests for auth and agent-proxy guards.

Blueprint-only Flask apps with a temp user store: no create_app(), so these never
touch storage/ or config/.env and are safe to run while the app is up.
"""
import contextlib
import io
import types
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def proxy_client():
    from backend.api.agents.proxy import agents_bp

    class FakeAuth:
        def require_auth(self, admin_only=False):
            return None

        def get_current_user(self):
            return types.SimpleNamespace(role="customer")  # demoted since login

    app = Flask(__name__)
    app.secret_key = "test"
    app.config["AUTH_SERVICE"] = FakeAuth()
    app.register_blueprint(agents_bp, url_prefix="/api/agents")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(user_id="u1", username="bob", role="admin")  # stale copy from login
    with patch("backend.api.agents.proxy.requests.request") as upstream:
        upstream.return_value = MagicMock(content=b"{}", status_code=200, headers={})
        yield client, upstream


# PATH_INFO as a WSGI server delivers it: "../" and "%2e%2e/" both arrive as "../";
# a double-encoded "%252e%252e" arrives as "%2e%2e", which requests turns into "..".
@pytest.mark.parametrize("path", [
    "/api/agents/test-healing-agent/../../settings",
    "/api/agents/test-healing-agent/%2e%2e/%2e%2e/settings",
    "/api/agents/test-healing-agent/.%2E/.%2E/settings",
])
@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_agent_proxy_refuses_dot_segments(proxy_client, path, method):
    client, upstream = proxy_client
    resp = client.open("/", method=method, json={}, environ_overrides={"PATH_INFO": path})
    assert resp.status_code == 404
    assert not upstream.called


def test_agent_proxy_forwards_normal_paths_with_the_stored_role(proxy_client):
    client, upstream = proxy_client
    assert client.get("/api/agents/test-healing-agent/queue").status_code == 200
    kwargs = upstream.call_args.kwargs
    assert kwargs["url"].endswith("/agents/test-healing-agent/queue")
    assert kwargs["headers"]["X-User-Role"] == "customer"


@pytest.fixture
def auth_client(tmp_path):
    from backend.api.auth import routes
    from backend.models.user import UserStorage
    from backend.services.auth_service import AuthService

    with contextlib.redirect_stdout(io.StringIO()):  # default-admin password for a throwaway store
        auth = AuthService(UserStorage(tmp_path / "users.json"))
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["AUTH_SERVICE"] = auth
    app.register_blueprint(routes.auth_bp, url_prefix="/api/auth")
    routes._signup_attempts.clear()
    yield app.test_client()
    routes._signup_attempts.clear()


@pytest.mark.parametrize("username", ["x');alert(1);//", "a b", "ab", "x" * 65, "evil\n", 123])
def test_signup_rejects_usernames_that_could_break_out_of_markup(auth_client, username):
    resp = auth_client.post("/api/auth/signup", json={"username": username, "password": "pw12345"})
    assert resp.status_code == 400


def test_signup_accepts_ordinary_names_and_is_rate_limited(auth_client):
    codes = [auth_client.post("/api/auth/signup", json={"username": f"user{i}@example.com", "password": "pw"})
             .status_code for i in range(11)]
    assert codes[:10] == [201] * 10
    assert codes[10] == 429


def test_a_colliding_user_id_cannot_overwrite_another_account(tmp_path):
    from backend.models.user import User, UserStorage

    store = UserStorage(tmp_path / "users.json")
    with patch.object(User, "_generate_user_id", staticmethod(lambda username: "21232f297a57")):
        assert store.create_user("admin", "pw", role="admin")
        assert store.create_user("collider", "pw") is None
    kept = store.get_user("21232f297a57")
    assert (kept.username, kept.role) == ("admin", "admin")


def test_a_user_an_admin_creates_can_sign_in_straight_away(auth_client):
    """Admin-created accounts used to start pending, so they could not log in
    until an admin approved the account they had just made."""
    with auth_client.session_transaction() as sess:
        sess.update(user_id="21232f297a57", username="admin", role="admin", status="active")
    created = auth_client.post("/api/auth/users",
                               json={"username": "carol", "password": "pw12345", "role": "customer"})
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["user"]["status"] == "active"

    with auth_client.session_transaction() as sess:
        sess.clear()
    login = auth_client.post("/api/auth/login", json={"username": "carol", "password": "pw12345"})
    assert login.status_code == 200, login.get_json()
