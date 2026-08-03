"""Watchlist module: opaque watch-IDs per owner, without storing watched names.

Need-to-know (variant 2): the server never stores the watched name or the search
criteria. Each owner is identified by an opaque ``owner`` key (today the
``watch_key`` cookie UUID, later an account user id). Watched names live in the
browser and are sent per rescan; this module screens them through an injected
``search_fn`` and persists only the resulting public match data as hits.

``search_fn`` contract
    Callers inject ``search_fn(name, **fields)`` returning a JSON-serialisable
    list of match dicts. Each match dict must provide:
      id        stable entity identifier (used for dedup across rescans)
      naam      public name of the matched entity
      score     match score 0-100 (higher is better)
      bron      source label, e.g. "eu", "pep", "sanctie" or "opensanctions"
      datasets  JSON-serialisable list of dataset descriptors
    Matches with ``score`` at or above ``threshold`` are stored; a hit is only
    stored if (watchlist_id, id, score) does not already exist for that
    watchlist. The ``name`` argument is never written to the database.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

WATCH_KEY_COOKIE = "watch_key"

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlists (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  label TEXT DEFAULT '',
  created_at TEXT
)
"""

HITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist_hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  watchlist_id TEXT NOT NULL,
  owner TEXT NOT NULL,
  ts TEXT,
  match_json TEXT
)
"""


def default_watchlist_db() -> Path:
    return Path(os.environ.get("WATCHLIST_DB") or str(Path("data") / "watchlists.sqlite"))


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_watchlist_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _open(db_path) as conn:
        conn.execute(SCHEMA)
        conn.execute(HITS_SCHEMA)
        conn.commit()


def get_or_create_key(request, response) -> str:
    """Return the caller's ``watch_key`` UUID, generating and setting it when absent."""
    key = request.cookies.get(WATCH_KEY_COOKIE)
    if key:
        return key
    key = str(uuid.uuid4())
    secure = getattr(getattr(request, "url", None), "scheme", "") == "https"
    response.set_cookie(WATCH_KEY_COOKIE, key, httponly=True, samesite="lax", secure=secure, path="/")
    return key


def add_watchlist(db_path: Path, owner: str, label: str = "") -> dict:
    init_watchlist_db(db_path)
    watchlist_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _open(db_path) as conn:
        conn.execute(
            "INSERT INTO watchlists (id, owner, label, created_at) VALUES (?, ?, ?, ?)",
            (watchlist_id, owner, label, now),
        )
        conn.commit()
    return {"id": watchlist_id, "owner": owner, "label": label, "created_at": now}


def list_watchlists(db_path: Path, owner: str) -> list[dict]:
    init_watchlist_db(db_path)
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT id, owner, label, created_at FROM watchlists WHERE owner = ? ORDER BY created_at, id",
            (owner,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_watchlist(db_path: Path, owner: str, watchlist_id: str) -> bool:
    init_watchlist_db(db_path)
    with _open(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM watchlists WHERE id = ? AND owner = ?",
            (watchlist_id, owner),
        )
        conn.commit()
    if cursor.rowcount == 0:
        return False
    with _open(db_path) as conn:
        conn.execute(
            "DELETE FROM watchlist_hits WHERE watchlist_id = ? AND owner = ?",
            (watchlist_id, owner),
        )
        conn.commit()
    return True


def _owner_has_watchlist(db_path: Path, owner: str, watchlist_id: str) -> bool:
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlists WHERE id = ? AND owner = ?",
            (watchlist_id, owner),
        ).fetchone()
    return row is not None


def _existing_match_keys(db_path: Path, watchlist_id: str) -> set[tuple]:
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT match_json FROM watchlist_hits WHERE watchlist_id = ?",
            (watchlist_id,),
        ).fetchall()
    keys = set()
    for row in rows:
        if not row["match_json"]:
            continue
        match = json.loads(row["match_json"])
        keys.add((match.get("id"), match.get("score")))
    return keys


def rescan_watch(
    db_path: Path,
    owner: str,
    watchlist_id: str,
    name: str,
    fields: dict,
    search_fn,
    threshold: int = 90,
) -> dict:
    """Screen ``name`` via ``search_fn`` and store new public matches as hits.

    The watchlist must belong to ``owner``; otherwise this is a no-op and
    ``search_fn`` is not called. ``name`` and ``fields`` are used only for the
    screening call and are never persisted or logged.
    """
    init_watchlist_db(db_path)
    if not _owner_has_watchlist(db_path, owner, watchlist_id):
        return {"watchlist_id": watchlist_id, "hits": [], "new": 0}
    matches = search_fn(name, **fields) or []
    seen = _existing_match_keys(db_path, watchlist_id)
    now = datetime.now(timezone.utc).isoformat()
    new_hits = []
    with _open(db_path) as conn:
        for match in matches:
            if match["score"] < threshold:
                continue
            key = (match["id"], match["score"])
            if key in seen:
                continue
            seen.add(key)
            cursor = conn.execute(
                "INSERT INTO watchlist_hits (watchlist_id, owner, ts, match_json) VALUES (?, ?, ?, ?)",
                (watchlist_id, owner, now, json.dumps(match, ensure_ascii=False)),
            )
            new_hits.append({"id": cursor.lastrowid, "watchlist_id": watchlist_id, "ts": now, "match": match})
        conn.commit()
    return {"watchlist_id": watchlist_id, "hits": new_hits, "new": len(new_hits)}


def list_hits(db_path: Path, owner: str, watchlist_id: str | None = None) -> list[dict]:
    init_watchlist_db(db_path)
    query = "SELECT id, watchlist_id, ts, match_json FROM watchlist_hits WHERE owner = ?"
    params: list = [owner]
    if watchlist_id is not None:
        query += " AND watchlist_id = ?"
        params.append(watchlist_id)
    query += " ORDER BY id"
    with _open(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    hits = []
    for row in rows:
        hit = dict(row)
        hit["match"] = json.loads(hit.pop("match_json"))
        hits.append(hit)
    return hits
