from pathlib import Path

import pytest

from scripts import update_eu as cli


def make_manifest(**over):
    manifest = {"updated_at": "t", "stats": {"downloaded": 0, "skipped": 0, "failed": 0}}
    manifest.update(over)
    return manifest


def test_parse_args_defaults():
    args = cli.parse_args([])
    assert args.force is False
    assert args.dry_run is False
    assert args.interval == 0
    assert Path(args.root) == Path("data/eu")


def test_parse_args_once_flag():
    assert cli.parse_args(["--once"]).once is True
    assert cli.parse_args([]).once is False


def test_main_once_flag_overrides_interval(monkeypatch):
    calls = {"once": 0, "loop": 0}

    def fake_run_once(args):
        calls["once"] += 1
        return 0

    def fake_run_loop(args):
        calls["loop"] += 1
        return 1

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    assert cli.main(["--once", "--interval", "168"]) == 0
    assert calls == {"once": 1, "loop": 0}


def test_run_once_success(monkeypatch, capsys):
    manifest = make_manifest(stats={"downloaded": 1, "skipped": 0, "failed": 0})
    monkeypatch.setattr(cli.eu_ingest, "fetch_headers", lambda *a, **k: {"last_modified": "x"})
    monkeypatch.setattr(cli.eu_ingest, "refresh_eu", lambda *a, **k: manifest)
    args = cli.parse_args(["--dry-run"])
    assert cli.run_once(args) == 0
    out = capsys.readouterr().out
    assert "1 gedownload" in out


def test_run_once_head_failure(monkeypatch, capsys):
    def boom():
        raise RuntimeError("kapot")

    monkeypatch.setattr(cli.eu_ingest, "fetch_headers", boom)
    args = cli.parse_args([])
    assert cli.run_once(args) == 1
    err = capsys.readouterr().err
    assert "kapot" in err


def test_main_once(monkeypatch):
    monkeypatch.setattr(cli.eu_ingest, "fetch_headers", lambda *a, **k: {"last_modified": "x"})
    monkeypatch.setattr(cli.eu_ingest, "refresh_eu", lambda *a, **k: make_manifest())
    assert cli.main(["--dry-run"]) == 0


def test_run_loop_stops_gracefully(monkeypatch):
    calls = {"n": 0}
    sleeps = {"n": 0}

    def fake_run_once(args):
        calls["n"] += 1
        return 0

    def fake_sleep(seconds):
        sleeps["n"] += 1
        cli._STOP["flag"] = True

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(cli.signal, "signal", lambda *a, **k: None)
    args = cli.parse_args(["--interval", "168"])
    assert cli.run_loop(args) == 0
    assert calls["n"] == 1
    assert sleeps["n"] >= 1


def test_run_loop_sleep_is_sliced(monkeypatch):
    seen = []

    def fake_sleep(seconds):
        seen.append(seconds)
        cli._STOP["flag"] = True

    monkeypatch.setattr(cli, "run_once", lambda args: 0)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(cli.signal, "signal", lambda *a, **k: None)
    args = cli.parse_args(["--interval", "168"])
    assert cli.run_loop(args) == 0
    assert seen and max(seen) <= 60


def test_main_interval_routes_to_loop(monkeypatch):
    calls = {"once": 0, "loop": 0}

    def fake_run_once(args):
        calls["once"] += 1
        return 0

    def fake_run_loop(args):
        calls["loop"] += 1
        return 0

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    assert cli.main(["--interval", "168"]) == 0
    assert calls == {"once": 0, "loop": 1}
