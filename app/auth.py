import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from authlib.integrations.httpx_client import AsyncOAuth2Client
from itsdangerous import BadSignature, SignatureExpired, TimedSerializer, URLSafeTimedSerializer

ROLES = ("admin", "analist", "viewer")
SESSION_MAX_AGE = 43200
ENTRA_STATE_MAX_AGE = 600
ENTRA_DISCOVERY_PATH = "/.well-known/openid-configuration"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE,
  password_hash TEXT,
  role TEXT NOT NULL CHECK(role IN ('admin','analist','viewer')),
  idp TEXT,
  idp_subject TEXT UNIQUE,
  created_at TEXT
)
"""


def default_auth_db() -> Path:
    return Path(os.environ.get("AUTH_DB") or str(Path("data") / "auth.sqlite"))


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _open(db_path) as conn:
        conn.execute(SCHEMA)
        conn.commit()


def _row_to_user(row: sqlite3.Row) -> dict:
    user = dict(row)
    user.pop("password_hash", None)
    return user


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_user(
    db_path: Path,
    username: str,
    password: str | None = None,
    role: str = "viewer",
    idp: str | None = None,
    idp_subject: str | None = None,
) -> dict:
    if role not in ROLES:
        raise ValueError(f"ongeldige rol: {role}")
    if password is None and idp_subject is None:
        raise ValueError("password of idp_subject is verplicht")
    init_auth_db(db_path)
    password_hash = hash_password(password) if password is not None else None
    user_id = secrets.token_hex(16)
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with _open(db_path) as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, idp, idp_subject, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, password_hash, role, idp, idp_subject, created_at),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"gebruiker bestaat al: {username or idp_subject}") from exc
    return {
        "id": user_id,
        "username": username,
        "password_hash": password_hash,
        "role": role,
        "idp": idp,
        "idp_subject": idp_subject,
        "created_at": created_at,
    }


def find_by_credentials(db_path: Path, username: str, password: str) -> dict | None:
    init_auth_db(db_path)
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row is None or row["password_hash"] is None:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return _row_to_user(row)


def find_or_create_idp_user(
    db_path: Path,
    idp: str,
    idp_subject: str,
    default_role: str = "viewer",
    username: str | None = None,
) -> dict:
    init_auth_db(db_path)
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE idp = ? AND idp_subject = ?",
            (idp, idp_subject),
        ).fetchone()
    if row is not None:
        return _row_to_user(row)
    return create_user(
        db_path,
        username=username or idp_subject,
        role=default_role,
        idp=idp,
        idp_subject=idp_subject,
    )


def create_session(user: dict, secret: str, max_age: int = SESSION_MAX_AGE) -> str:
    serializer = TimedSerializer(secret)
    return serializer.dumps({"user_id": user["id"], "role": user["role"]})


def verify_session(token: str, secret: str, max_age: int = SESSION_MAX_AGE) -> dict | None:
    serializer = TimedSerializer(secret)
    try:
        return serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def current_user(request, db_path: Path, secret: str) -> dict | None:
    token = request.cookies.get("session")
    if not token:
        return None
    payload = verify_session(token, secret)
    if payload is None:
        return None
    init_auth_db(db_path)
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE id = ?",
            (payload["user_id"],),
        ).fetchone()
    if row is None:
        return None
    return {"user_id": row["id"], "username": row["username"], "role": row["role"]}


def entra_config() -> dict | None:
    if os.environ.get("AUTH_ENTRA_ENABLED", "0") not in ("1", "true", "True", "yes"):
        return None
    tenant = os.environ.get("AUTH_ENTRA_TENANT")
    client_id = os.environ.get("AUTH_ENTRA_CLIENT_ID")
    client_secret = os.environ.get("AUTH_ENTRA_CLIENT_SECRET")
    redirect_uri = os.environ.get("AUTH_ENTRA_REDIRECT_URI")
    if not (tenant and client_id and client_secret and redirect_uri):
        raise ValueError(
            "AUTH_ENTRA_ENABLED=1 vereist AUTH_ENTRA_TENANT, AUTH_ENTRA_CLIENT_ID, "
            "AUTH_ENTRA_CLIENT_SECRET en AUTH_ENTRA_REDIRECT_URI"
        )
    issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
    return {
        "issuer": issuer,
        "discovery_url": f"{issuer}{ENTRA_DISCOVERY_PATH}",
        "tenant": tenant,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": os.environ.get("AUTH_ENTRA_SCOPE") or "openid profile email",
        "default_role": os.environ.get("AUTH_ENTRA_DEFAULT_ROLE") or "viewer",
    }


def entra_client(config: dict, **kwargs) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        redirect_uri=config["redirect_uri"],
        scope=config["scope"],
        code_challenge_method="S256",
        discovery_url=config["discovery_url"],
        **kwargs,
    )


async def _discover(client: AsyncOAuth2Client) -> dict:
    discovery_url = client.metadata.get("discovery_url")
    if not discovery_url:
        raise ValueError("geen discovery_url in client-config")
    resp = await client.request("GET", discovery_url, withhold_token=True)
    resp.raise_for_status()
    data = resp.json()
    client.metadata["authorization_endpoint"] = data["authorization_endpoint"]
    client.metadata["token_endpoint"] = data["token_endpoint"]
    client.metadata["userinfo_endpoint"] = data["userinfo_endpoint"]
    return client.metadata


def _sign_state(secret: str, value: str) -> str:
    return URLSafeTimedSerializer(secret).dumps(value)


def _verify_state(state: str, secret: str, max_age: int = ENTRA_STATE_MAX_AGE) -> str:
    serializer = URLSafeTimedSerializer(secret)
    try:
        return serializer.loads(state, max_age=max_age)
    except (BadSignature, SignatureExpired):
        raise ValueError("ongeldige of verlopen state")


async def entra_authorize_url(
    client: AsyncOAuth2Client, state_secret: str | None = None
) -> tuple[str, str, str]:
    state_secret = state_secret or os.environ.get("AUTH_SECRET")
    if not state_secret:
        raise ValueError("AUTH_SECRET is vereist voor de state")
    metadata = await _discover(client)
    code_verifier = secrets.token_urlsafe(32)
    state = _sign_state(state_secret, secrets.token_urlsafe(16))
    url, _ = client.create_authorization_url(
        metadata["authorization_endpoint"],
        state=state,
        code_verifier=code_verifier,
    )
    return url, code_verifier, state


async def entra_exchange(
    client: AsyncOAuth2Client,
    code: str,
    code_verifier: str,
    state: str,
    state_secret: str,
) -> dict:
    _verify_state(state, state_secret)
    metadata = await _discover(client)
    token = await client.fetch_token(
        metadata["token_endpoint"],
        grant_type="authorization_code",
        code=code,
        code_verifier=code_verifier,
    )
    resp = await client.request(
        "GET",
        metadata["userinfo_endpoint"],
        headers={"Authorization": f"Bearer {token['access_token']}"},
        withhold_token=True,
    )
    resp.raise_for_status()
    userinfo = resp.json()
    preferred_username = userinfo.get("preferred_username")
    email = userinfo.get("email")
    return {
        "sub": userinfo["sub"],
        "preferred_username": preferred_username,
        "email": email,
        "username": preferred_username or email or userinfo["sub"],
    }
