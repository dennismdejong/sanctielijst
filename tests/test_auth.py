import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from starlette.requests import Request

from app import auth


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "auth.sqlite"
    auth.init_auth_db(path)
    return path


def make_request(cookies=None):
    headers = []
    if cookies:
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", cookie_header.encode()))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    return Request(scope)


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_default_auth_db_default_path(monkeypatch):
    monkeypatch.delenv("AUTH_DB", raising=False)
    assert auth.default_auth_db() == Path("data") / "auth.sqlite"


def test_default_auth_db_env_override(monkeypatch):
    monkeypatch.setenv("AUTH_DB", "/tmp/custom/auth.sqlite")
    assert auth.default_auth_db() == Path("/tmp/custom/auth.sqlite")


def test_init_auth_db_creates_schema(tmp_path):
    db_path = tmp_path / "auth.sqlite"
    auth.init_auth_db(db_path)
    conn = sqlite3.connect(db_path)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    conn.close()
    assert columns == ["id", "username", "password_hash", "role", "idp", "idp_subject", "created_at"]


def test_init_auth_db_idempotent(tmp_path):
    db_path = tmp_path / "auth.sqlite"
    auth.init_auth_db(db_path)
    auth.init_auth_db(db_path)


def test_hash_and_verify_password():
    hashed = auth.hash_password("geheim")
    assert hashed.startswith("$2")
    assert hashed != "geheim"
    assert auth.verify_password("geheim", hashed)
    assert not auth.verify_password("fout", hashed)


def test_verify_password_rejects_invalid_hash():
    assert not auth.verify_password("geheim", "niet-een-bcrypt-hash")
    assert not auth.verify_password("geheim", None)


def test_create_user_local(db_path):
    user = auth.create_user(db_path, username="alice", password="geheim", role="admin")
    assert user["username"] == "alice"
    assert user["role"] == "admin"
    assert user["idp"] is None
    assert user["idp_subject"] is None
    assert user["password_hash"].startswith("$2")
    assert user["created_at"]
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT * FROM users WHERE username = ?", ("alice",)).fetchone()
    conn.close()
    assert row is not None


def test_create_user_entra(db_path):
    user = auth.create_user(
        db_path, username="bob@example.com", idp="entra", idp_subject="sub-123", role="analist"
    )
    assert user["idp"] == "entra"
    assert user["idp_subject"] == "sub-123"
    assert user["password_hash"] is None


def test_create_user_duplicate_username_raises(db_path):
    auth.create_user(db_path, username="alice", password="geheim")
    with pytest.raises(ValueError):
        auth.create_user(db_path, username="alice", password="anders")


def test_create_user_duplicate_idp_subject_raises(db_path):
    auth.create_user(db_path, username="bob", idp="entra", idp_subject="sub-1")
    with pytest.raises(ValueError):
        auth.create_user(db_path, username="carol", idp="entra", idp_subject="sub-1")


def test_create_user_invalid_role_raises(db_path):
    with pytest.raises(ValueError):
        auth.create_user(db_path, username="alice", password="geheim", role="superuser")


def test_create_user_requires_password_or_idp_subject(db_path):
    with pytest.raises(ValueError):
        auth.create_user(db_path, username="ghost")


def test_find_by_credentials_success(db_path):
    auth.create_user(db_path, username="alice", password="geheim", role="admin")
    user = auth.find_by_credentials(db_path, "alice", "geheim")
    assert user is not None
    assert user["username"] == "alice"
    assert user["role"] == "admin"
    assert "password_hash" not in user


def test_find_by_credentials_wrong_password(db_path):
    auth.create_user(db_path, username="alice", password="geheim")
    assert auth.find_by_credentials(db_path, "alice", "fout") is None


def test_find_by_credentials_unknown_user(db_path):
    assert auth.find_by_credentials(db_path, "niemand", "geheim") is None


def test_find_by_credentials_entra_user_without_password(db_path):
    auth.create_user(db_path, username="bob", idp="entra", idp_subject="sub-1")
    assert auth.find_by_credentials(db_path, "bob", "geheim") is None


def test_find_or_create_idp_user_creates(db_path):
    user = auth.find_or_create_idp_user(db_path, "entra", "sub-new", default_role="viewer")
    assert user["idp"] == "entra"
    assert user["idp_subject"] == "sub-new"
    assert user["role"] == "viewer"
    assert user["password_hash"] is None


def test_find_or_create_idp_user_returns_existing(db_path):
    created = auth.find_or_create_idp_user(db_path, "entra", "sub-1", default_role="admin")
    found = auth.find_or_create_idp_user(db_path, "entra", "sub-1", default_role="viewer")
    assert found["id"] == created["id"]
    assert found["role"] == "admin"


def test_create_and_verify_session_roundtrip():
    user = {"id": "u-1", "role": "admin"}
    token = auth.create_session(user, "geheim", max_age=43200)
    payload = auth.verify_session(token, "geheim")
    assert payload == {"user_id": "u-1", "role": "admin"}


def test_verify_session_wrong_secret():
    token = auth.create_session({"id": "u-1", "role": "viewer"}, "geheim")
    assert auth.verify_session(token, "ander-geheim") is None


def test_verify_session_tampered():
    token = auth.create_session({"id": "u-1", "role": "admin"}, "geheim")
    tampered = token[:-4] + ("x" * 4)
    assert auth.verify_session(tampered, "geheim") is None


def test_verify_session_empty_or_garbage():
    assert auth.verify_session("", "geheim") is None
    assert auth.verify_session("geen-token", "geheim") is None


def test_verify_session_expired(monkeypatch):
    clock = Clock(1_000_000.0)
    monkeypatch.setattr("time.time", clock)
    token = auth.create_session({"id": "u-1", "role": "viewer"}, "geheim", max_age=30)
    clock.now += 31
    assert auth.verify_session(token, "geheim", max_age=30) is None


def test_verify_session_within_max_age(monkeypatch):
    clock = Clock(1_000_000.0)
    monkeypatch.setattr("time.time", clock)
    token = auth.create_session({"id": "u-1", "role": "viewer"}, "geheim", max_age=30)
    clock.now += 29
    assert auth.verify_session(token, "geheim") == {"user_id": "u-1", "role": "viewer"}


def test_current_user_from_request(db_path):
    created = auth.create_user(db_path, username="alice", password="geheim", role="admin")
    token = auth.create_session(created, "geheim")
    request = make_request({"session": token})
    user = auth.current_user(request, db_path, "geheim")
    assert user == {"user_id": created["id"], "username": "alice", "role": "admin"}


def test_current_user_without_cookie(db_path):
    auth.create_user(db_path, username="alice", password="geheim")
    assert auth.current_user(make_request(), db_path, "geheim") is None


def test_current_user_invalid_token(db_path):
    auth.create_user(db_path, username="alice", password="geheim")
    request = make_request({"session": "onzin-token"})
    assert auth.current_user(request, db_path, "geheim") is None


def test_current_user_expired_token(db_path, monkeypatch):
    created = auth.create_user(db_path, username="alice", password="geheim")
    token = auth.create_session(created, "geheim", max_age=30)
    clock = Clock()
    monkeypatch.setattr("time.time", clock)
    clock.now += 31
    assert auth.current_user(make_request({"session": token}), db_path, "geheim") is None


def test_current_user_unknown_user_id(db_path):
    auth.create_user(db_path, username="alice", password="geheim")
    token = auth.create_session({"id": "bestaat-niet", "role": "admin"}, "geheim")
    request = make_request({"session": token})
    assert auth.current_user(request, db_path, "geheim") is None


ENTRA_DISCOVERY = {
    "issuer": "https://login.microsoftonline.com/tenant-test/v2.0",
    "authorization_endpoint": "https://login.microsoftonline.com/tenant-test/oauth2/v2.0/authorize",
    "token_endpoint": "https://login.microsoftonline.com/tenant-test/oauth2/v2.0/token",
    "userinfo_endpoint": "https://login.microsoftonline.com/tenant-test/oauth2/v2.0/userinfo",
}


def set_entra_env(monkeypatch, **overrides):
    env = {
        "AUTH_ENTRA_ENABLED": "1",
        "AUTH_ENTRA_TENANT": "tenant-test",
        "AUTH_ENTRA_CLIENT_ID": "client-1",
        "AUTH_ENTRA_CLIENT_SECRET": "secret-1",
        "AUTH_ENTRA_REDIRECT_URI": "https://app.example/callback",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def make_entra_transport(userinfo=None):
    def handler(request):
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=ENTRA_DISCOVERY)
        if path.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": "at-mock", "token_type": "Bearer", "expires_in": 3600},
            )
        if path.endswith("/userinfo"):
            return httpx.Response(
                200,
                json=userinfo
                or {
                    "sub": "sub-123",
                    "preferred_username": "bob@example.com",
                    "email": "bob@example.com",
                },
            )
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


async def run_exchange(monkeypatch, userinfo):
    set_entra_env(monkeypatch)
    config = auth.entra_config()
    client = auth.entra_client(config, transport=make_entra_transport(userinfo))
    _, code_verifier, state = await auth.entra_authorize_url(client, state_secret="geheim")
    return await auth.entra_exchange(client, "code-123", code_verifier, state, "geheim")


def make_entra_client(monkeypatch):
    set_entra_env(monkeypatch)
    config = auth.entra_config()
    client = auth.entra_client(config, transport=make_entra_transport())
    return client, config


def test_entra_config_disabled(monkeypatch):
    monkeypatch.delenv("AUTH_ENTRA_ENABLED", raising=False)
    assert auth.entra_config() is None


def test_entra_config_enabled(monkeypatch):
    set_entra_env(monkeypatch)
    config = auth.entra_config()
    assert config["issuer"] == "https://login.microsoftonline.com/tenant-test/v2.0"
    assert config["discovery_url"].endswith("/.well-known/openid-configuration")
    assert config["client_id"] == "client-1"
    assert config["scope"] == "openid profile email"
    assert config["default_role"] == "viewer"


def test_entra_config_custom_scope_and_role(monkeypatch):
    set_entra_env(
        monkeypatch, AUTH_ENTRA_SCOPE="openid", AUTH_ENTRA_DEFAULT_ROLE="analist"
    )
    config = auth.entra_config()
    assert config["scope"] == "openid"
    assert config["default_role"] == "analist"


@pytest.mark.parametrize("missing", ["AUTH_ENTRA_TENANT", "AUTH_ENTRA_CLIENT_ID", "AUTH_ENTRA_CLIENT_SECRET", "AUTH_ENTRA_REDIRECT_URI"])
def test_entra_config_missing_required_raises(monkeypatch, missing):
    set_entra_env(monkeypatch, **{missing: ""})
    with pytest.raises(ValueError):
        auth.entra_config()


@pytest.mark.anyio
async def test_entra_authorize_url_pkce(monkeypatch):
    client, _ = make_entra_client(monkeypatch)
    url, code_verifier, state = await auth.entra_authorize_url(client, state_secret="geheim")
    params = parse_qs(urlparse(url).query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-1"]
    assert params["redirect_uri"] == ["https://app.example/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [create_s256_code_challenge(code_verifier)]
    assert params["state"] == [state]
    assert code_verifier


@pytest.mark.anyio
async def test_entra_exchange_returns_mapped_user(monkeypatch, db_path):
    client, _ = make_entra_client(monkeypatch)
    _, code_verifier, state = await auth.entra_authorize_url(client, state_secret="geheim")
    userinfo = await auth.entra_exchange(client, "code-123", code_verifier, state, "geheim")
    assert userinfo == {
        "sub": "sub-123",
        "preferred_username": "bob@example.com",
        "email": "bob@example.com",
        "username": "bob@example.com",
    }
    user = auth.find_or_create_idp_user(
        db_path, "entra", userinfo["sub"], username=userinfo["username"]
    )
    assert user["username"] == "bob@example.com"
    assert user["idp_subject"] == "sub-123"


@pytest.mark.anyio
async def test_entra_exchange_username_fallbacks(monkeypatch):
    result = await run_exchange(monkeypatch, {"sub": "sub-x", "email": "x@example.com"})
    assert result["username"] == "x@example.com"
    assert result["preferred_username"] is None
    result = await run_exchange(monkeypatch, {"sub": "sub-y"})
    assert result["username"] == "sub-y"


@pytest.mark.anyio
async def test_entra_exchange_invalid_state(monkeypatch):
    client, _ = make_entra_client(monkeypatch)
    with pytest.raises(ValueError):
        await auth.entra_exchange(client, "code-123", "verifier", "onzin-state", "geheim")


@pytest.mark.anyio
async def test_entra_exchange_expired_state(monkeypatch):
    client, _ = make_entra_client(monkeypatch)
    clock = Clock(1_000_000.0)
    monkeypatch.setattr("time.time", clock)
    _, code_verifier, state = await auth.entra_authorize_url(client, state_secret="geheim")
    clock.now += auth.ENTRA_STATE_MAX_AGE + 1
    with pytest.raises(ValueError):
        await auth.entra_exchange(client, "code-123", code_verifier, state, "geheim")
