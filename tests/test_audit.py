import sqlite3
from pathlib import Path

from app import audit


def test_default_audit_db_default_path(monkeypatch):
    monkeypatch.delenv("AUDIT_DB", raising=False)
    assert audit.default_audit_db() == Path("data") / "audit.sqlite"


def test_default_audit_db_env_override(monkeypatch):
    monkeypatch.setenv("AUDIT_DB", "/tmp/custom/audit.sqlite")
    assert audit.default_audit_db() == Path("/tmp/custom/audit.sqlite")


def test_init_audit_db_creates_table(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    audit.init_audit_db(db_path)
    conn = sqlite3.connect(db_path)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)")]
    conn.close()
    assert columns == ["id", "ts", "ip", "user", "user_agent", "method", "path", "query", "result_count", "sources", "threshold"]


def test_init_audit_db_idempotent(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    audit.init_audit_db(db_path)
    audit.init_audit_db(db_path)


def test_log_event_writes_and_reads_back(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    audit.log_event(
        db_path,
        ip="127.0.0.1",
        user="alice",
        user_agent="pytest",
        method="GET",
        path="/api/search",
        query={"q": "poetin"},
        result_count=3,
        sources=["eu", "pep"],
        threshold=90,
    )
    events = audit.list_events(db_path)
    assert len(events) == 1
    event = events[0]
    assert event["ip"] == "127.0.0.1"
    assert event["user"] == "alice"
    assert event["user_agent"] == "pytest"
    assert event["method"] == "GET"
    assert event["path"] == "/api/search"
    assert event["query"] == {"q": "poetin"}
    assert event["result_count"] == 3
    assert event["sources"] == ["eu", "pep"]
    assert event["threshold"] == 90
    assert event["ts"]


def test_log_event_user_none_stored_as_null(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    audit.log_event(db_path, ip="127.0.0.1", user_agent="pytest", method="GET", path="/api/search", query={}, result_count=0, sources=[], threshold=90)
    event = audit.list_events(db_path)[0]
    assert event["user"] is None


def test_log_event_creates_missing_db(tmp_path):
    db_path = tmp_path / "nested" / "audit.sqlite"
    audit.log_event(db_path, ip="127.0.0.1", user_agent="pytest", method="GET", path="/api/search", query={}, result_count=0, sources=[], threshold=90)
    assert db_path.exists()
    assert len(audit.list_events(db_path)) == 1


def test_list_events_sorted_desc_with_limit_and_offset(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    for i in range(3):
        audit.log_event(
            db_path,
            ip="127.0.0.1",
            user_agent="pytest",
            method="GET",
            path=f"/api/search?q={i}",
            query={"q": str(i)},
            result_count=i,
            sources=[],
            threshold=90,
            ts=f"2026-07-31T0{i}:00:00+00:00",
        )
    all_events = audit.list_events(db_path)
    assert [e["query"] for e in all_events] == [{"q": "2"}, {"q": "1"}, {"q": "0"}]
    limited = audit.list_events(db_path, limit=2)
    assert [e["query"] for e in limited] == [{"q": "2"}, {"q": "1"}]
    paged = audit.list_events(db_path, limit=1, offset=2)
    assert [e["query"] for e in paged] == [{"q": "0"}]


def test_list_events_missing_db_returns_empty(tmp_path):
    assert audit.list_events(tmp_path / "nope" / "audit.sqlite") == []
