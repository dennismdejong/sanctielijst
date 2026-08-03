from pathlib import Path

import pytest

from scripts import update_sanctions as cli


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
    assert Path(args.root) == Path("data/sanctions")


def test_parse_args_once_flag():
    assert cli.parse_args(["--once"]).once is True


def test_run_once_success(monkeypatch, capsys):
    manifest = make_manifest(stats={"total": 83, "downloaded": 2, "skipped": 80, "failed": 1, "bytes": 10})
    monkeypatch.setattr(cli.sanctions_ingest, "fetch_index", lambda: {"datasets": []})
    monkeypatch.setattr(cli.sanctions_ingest, "refresh_sanctions", lambda *a, **k: manifest)
    args = cli.parse_args(["--dry-run", "--limit", "5"])
    assert cli.run_once(args) == 0
    assert "2 gedownload" in capsys.readouterr().out


def test_run_once_index_failure(monkeypatch, capsys):
    def boom():
        raise RuntimeError("kapot")

    monkeypatch.setattr(cli.sanctions_ingest, "fetch_index", boom)
    args = cli.parse_args([])
    assert cli.run_once(args) == 1
    assert "kapot" in capsys.readouterr().err


def test_main_once(monkeypatch):
    monkeypatch.setattr(cli.sanctions_ingest, "fetch_index", lambda: {"datasets": []})
    monkeypatch.setattr(cli.sanctions_ingest, "refresh_sanctions", lambda *a, **k: make_manifest())
    assert cli.main(["--dry-run", "--limit", "1"]) == 0
