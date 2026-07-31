from pathlib import Path

import pytest

from scripts import update_pep as cli


def make_manifest(**over):
    manifest = {
        "updated_at": "t",
        "sources": {},
        "stats": {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0},
    }
    manifest.update(over)
    return manifest


def test_parse_args_defaults():
    args = cli.parse_args([])
    assert args.force is False
    assert args.dry_run is False
    assert args.limit is None
    assert args.interval == 0
    assert Path(args.root) == Path("data/pep")


def test_run_once_success(monkeypatch, capsys):
    manifest = make_manifest(stats={"total": 189, "downloaded": 3, "skipped": 185, "failed": 1, "bytes": 10})
    monkeypatch.setattr(cli.pep_ingest, "fetch_index", lambda: {"datasets": []})
    monkeypatch.setattr(cli.pep_ingest, "refresh_pep", lambda *a, **k: manifest)
    args = cli.parse_args(["--dry-run", "--limit", "5"])
    assert cli.run_once(args) == 0
    out = capsys.readouterr().out
    assert "3 gedownload" in out


def test_run_once_index_failure(monkeypatch, capsys):
    def boom():
        raise RuntimeError("kapot")

    monkeypatch.setattr(cli.pep_ingest, "fetch_index", boom)
    args = cli.parse_args([])
    assert cli.run_once(args) == 1
    err = capsys.readouterr().err
    assert "kapot" in err


def test_main_once(monkeypatch):
    monkeypatch.setattr(cli.pep_ingest, "fetch_index", lambda: {"datasets": []})
    monkeypatch.setattr(cli.pep_ingest, "refresh_pep", lambda *a, **k: make_manifest())
    assert cli.main(["--dry-run", "--limit", "1"]) == 0


def test_run_loop_stops_gracefully(monkeypatch):
    calls = {"n": 0}
    sleeps = {"n": 0}

    def fake_run_once(args):
        calls["n"] += 1
        return 0

    def fake_sleep(seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            cli._STOP["flag"] = True

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(cli.signal, "signal", lambda *a, **k: None)
    args = cli.parse_args(["--interval", "168"])
    assert cli.run_loop(args) == 0
    assert calls["n"] == 2
    assert sleeps["n"] == 2
