import sqlite3
import uuid
from pathlib import Path

from app import watchlist


class FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})


class FakeResponse:
    def __init__(self):
        self.set_cookies = []

    def set_cookie(self, key, value, **kwargs):
        self.set_cookies.append((key, value, kwargs))


def _match(entity_id, score, naam="Entity X", bron="eu", datasets=None):
    return {"id": entity_id, "naam": naam, "score": score, "bron": bron, "datasets": datasets or []}


def _search_fn(matches):
    def search(name, **fields):
        return [dict(m) for m in matches]

    return search


def test_default_watchlist_db_default_path(monkeypatch):
    monkeypatch.delenv("WATCHLIST_DB", raising=False)
    assert watchlist.default_watchlist_db() == Path("data") / "watchlists.sqlite"


def test_default_watchlist_db_env_override(monkeypatch):
    monkeypatch.setenv("WATCHLIST_DB", "/tmp/custom/watchlists.sqlite")
    assert watchlist.default_watchlist_db() == Path("/tmp/custom/watchlists.sqlite")


def test_init_watchlist_db_creates_tables(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    watchlist.init_watchlist_db(db_path)
    conn = sqlite3.connect(db_path)
    wl_columns = [r[1] for r in conn.execute("PRAGMA table_info(watchlists)")]
    hit_columns = [r[1] for r in conn.execute("PRAGMA table_info(watchlist_hits)")]
    conn.close()
    assert wl_columns == ["id", "owner", "label", "created_at"]
    assert hit_columns == ["id", "watchlist_id", "owner", "ts", "match_json"]


def test_init_watchlist_db_idempotent(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    watchlist.init_watchlist_db(db_path)
    watchlist.init_watchlist_db(db_path)


def test_get_or_create_key_sets_new_key_on_response():
    request = FakeRequest()
    response = FakeResponse()
    key = watchlist.get_or_create_key(request, response)
    assert uuid.UUID(key).version == 4
    assert len(response.set_cookies) == 1
    cookie_key, cookie_value, kwargs = response.set_cookies[0]
    assert cookie_key == "watch_key"
    assert cookie_value == key
    assert kwargs.get("httponly") is True


def test_get_or_create_key_returns_existing_key_unchanged():
    existing = str(uuid.uuid4())
    request = FakeRequest({"watch_key": existing})
    response = FakeResponse()
    key = watchlist.get_or_create_key(request, response)
    assert key == existing
    assert response.set_cookies == []


def test_add_watchlist_returns_opaque_record(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    record = watchlist.add_watchlist(db_path, "owner-a", label="Mijn lijst")
    assert set(record) == {"id", "owner", "label", "created_at"}
    assert record["owner"] == "owner-a"
    assert record["label"] == "Mijn lijst"
    assert record["id"] and record["created_at"]


def test_list_watchlists_only_returns_owners(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    watchlist.add_watchlist(db_path, "owner-a", label="lijst a")
    watchlist.add_watchlist(db_path, "owner-b", label="lijst b")
    listed = watchlist.list_watchlists(db_path, "owner-a")
    assert [w["label"] for w in listed] == ["lijst a"]
    assert all(w["owner"] == "owner-a" for w in listed)


def test_delete_watchlist_owner_only(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    record = watchlist.add_watchlist(db_path, "owner-a")
    assert watchlist.delete_watchlist(db_path, "owner-a", record["id"]) is True
    assert watchlist.list_watchlists(db_path, "owner-a") == []

    other = watchlist.add_watchlist(db_path, "owner-b")
    assert watchlist.delete_watchlist(db_path, "owner-a", other["id"]) is False
    assert len(watchlist.list_watchlists(db_path, "owner-b")) == 1


def test_delete_watchlist_removes_hits(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    watchlist.rescan_watch(db_path, "owner-a", wl["id"], "Naam", {}, _search_fn([_match("eu-1", 95)]))
    assert len(watchlist.list_hits(db_path, "owner-a")) == 1
    watchlist.delete_watchlist(db_path, "owner-a", wl["id"])
    assert watchlist.list_hits(db_path, "owner-a") == []


def test_rescan_watch_produces_hits(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    result = watchlist.rescan_watch(
        db_path,
        "owner-a",
        wl["id"],
        "Persoon Onder Bewaking",
        {"nationality": "NL"},
        _search_fn([_match("eu-1", 95)]),
    )
    assert result["watchlist_id"] == wl["id"]
    assert result["new"] == 1
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["watchlist_id"] == wl["id"]
    assert hit["match"] == _match("eu-1", 95)
    assert len(watchlist.list_hits(db_path, "owner-a")) == 1


def test_rescan_watch_dedup_second_rescan_no_new(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    first = watchlist.rescan_watch(db_path, "owner-a", wl["id"], "Naam", {}, _search_fn([_match("eu-1", 95)]))
    assert first["new"] == 1
    second = watchlist.rescan_watch(db_path, "owner-a", wl["id"], "Naam", {}, _search_fn([_match("eu-1", 95)]))
    assert second["new"] == 0
    assert second["hits"] == []
    assert len(watchlist.list_hits(db_path, "owner-a")) == 1


def test_rescan_watch_dedup_key_includes_score(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    watchlist.rescan_watch(db_path, "owner-a", wl["id"], "Naam", {}, _search_fn([_match("eu-1", 95)]))
    watchlist.rescan_watch(db_path, "owner-a", wl["id"], "Naam", {}, _search_fn([_match("eu-1", 92)]))
    assert len(watchlist.list_hits(db_path, "owner-a")) == 2


def test_rescan_watch_threshold_filtering(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    result = watchlist.rescan_watch(
        db_path,
        "owner-a",
        wl["id"],
        "Naam",
        {},
        _search_fn([_match("high", 95), _match("low", 50)]),
    )
    assert result["new"] == 1
    assert result["hits"][0]["match"]["id"] == "high"

    custom = watchlist.add_watchlist(db_path, "owner-a")
    result = watchlist.rescan_watch(
        db_path,
        "owner-a",
        custom["id"],
        "Naam",
        {},
        _search_fn([_match("mid", 88)]),
        threshold=85,
    )
    assert result["new"] == 1


def test_rescan_watch_owner_mismatch_is_noop(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    called = []

    def search(name, **fields):
        called.append(name)
        return [_match("eu-1", 95)]

    result = watchlist.rescan_watch(db_path, "owner-b", wl["id"], "Naam", {}, search)
    assert result == {"watchlist_id": wl["id"], "hits": [], "new": 0}
    assert called == []
    assert watchlist.list_hits(db_path, "owner-a") == []


def test_list_hits_filters_by_owner_and_watchlist(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a")
    watchlist.rescan_watch(db_path, "owner-a", wl["id"], "Naam", {}, _search_fn([_match("eu-1", 95)]))
    watchlist.add_watchlist(db_path, "owner-b")
    assert watchlist.list_hits(db_path, "owner-b") == []
    hits = watchlist.list_hits(db_path, "owner-a")
    assert len(hits) == 1
    assert hits[0]["watchlist_id"] == wl["id"]
    assert hits[0]["match"] == _match("eu-1", 95)
    assert len(watchlist.list_hits(db_path, "owner-a", watchlist_id=wl["id"])) == 1
    assert watchlist.list_hits(db_path, "owner-a", watchlist_id="nope") == []


def test_watched_name_is_never_stored(tmp_path):
    db_path = tmp_path / "watchlists.sqlite"
    wl = watchlist.add_watchlist(db_path, "owner-a", label="Mijn lijst")
    secret = "GEHEIME_NAAM_UIT_DE_BROWSER"
    watchlist.rescan_watch(
        db_path,
        "owner-a",
        wl["id"],
        secret,
        {"nationality": "NL"},
        _search_fn([_match("eu-1", 95)]),
    )
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith("sqlite_")]
    columns = []
    for table in tables:
        columns += [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    assert not any(
        "name" in col.lower() or "naam" in col.lower() or "crit" in col.lower() for col in columns
    )
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table}"):
            for value in row:
                if isinstance(value, str):
                    assert secret not in value
    conn.close()
