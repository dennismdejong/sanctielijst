import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, TimedSerializer

ROLES = ("admin", "analist", "viewer")
SESSION_MAX_AGE = 43200

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
    db_path: Path, idp: str, idp_subject: str, default_role: str = "viewer"
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
        username=idp_subject,
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
