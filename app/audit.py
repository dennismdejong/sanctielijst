import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ip TEXT NOT NULL,
  user TEXT,
  user_agent TEXT,
  method TEXT,
  path TEXT,
  query TEXT,
  result_count INTEGER,
  sources TEXT,
  threshold INTEGER
)
"""

COLUMNS = "id, ts, ip, user, user_agent, method, path, query, result_count, sources, threshold"


def default_audit_db() -> Path:
    return Path(os.environ.get("AUDIT_DB", Path("data") / "audit.sqlite"))


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_audit_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _open(db_path) as conn:
        conn.execute(SCHEMA)
        conn.commit()


def log_event(
    db_path: Path,
    *,
    ip: str,
    user: str | None = None,
    user_agent: str,
    method: str,
    path: str,
    query: dict,
    result_count: int,
    sources: list[str],
    threshold: int,
    ts: str | None = None,
) -> None:
    init_audit_db(db_path)
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    with _open(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, ip, user, user_agent, method, path, query, result_count, sources, threshold) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                ip,
                user,
                user_agent,
                method,
                path,
                json.dumps(query, ensure_ascii=False),
                result_count,
                json.dumps(sources, ensure_ascii=False),
                threshold,
            ),
        )
        conn.commit()


def list_events(db_path: Path, limit: int = 100, offset: int = 0) -> list[dict]:
    init_audit_db(db_path)
    with _open(db_path) as conn:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM audit_log ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["query"] = json.loads(event["query"]) if event["query"] is not None else None
        event["sources"] = json.loads(event["sources"]) if event["sources"] is not None else None
        events.append(event)
    return events
