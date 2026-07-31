# EU Sanctielijst Downloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process and update the EU sanctions list the same way as the PEP list: a manifest-based downloader (`app/eu_ingest.py` + `scripts/update_eu.py`) with skip-if-unchanged via HTTP `Last-Modified`, weekly scheduling, and a read-only app that consumes the downloaded data + manifest instead of downloading at startup.

**Architecture:** Mirror the PEP downloader pattern (`app/pep_ingest.py` / `scripts/update_pep.py`). `eu_ingest.py` HEAD-checks the EU XML endpoint for `Last-Modified`, skips when unchanged, else streams the XML to `.part` and atomically renames it, computing SHA-1/size/generation_date/entity_count into a flat `manifest.json`. The CLI `update_eu.py` runs once or in a SIGTERM-aware loop (`--interval`). The app (`app/main.py`) reads `data/eu/eu_sanctions.xml` + manifest and no longer downloads; `POST /api/refresh` calls `refresh_eu` inline. Docker downloader image is generalized to serve both scripts.

**Tech Stack:** Python 3.11+ (stdlib `hashlib/json/os/time/datetime/pathlib`, `requests`, FastAPI). No new dependencies.

## Global Constraints

- Python 3.11+ only; no new dependencies beyond existing `requests` (used by `app/ingest.py`, `app/pep_ingest.py`).
- UI/CLI copy in **Nederlands** (log messages, argparse help, README).
- EU XML URL (constant): `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw`
- Data dir: env `EU_DATA_DIR`, default `data/eu`. Manifest: `data/eu/manifest.json`. XML: `data/eu/eu_sanctions.xml`.
- Skip-if-unchanged key: HTTP `Last-Modified` header (the endpoint's `generationDate`/filename date are NOT used as the skip key). Timeout 120s, retries 1, pause 0.5s.
- Weekly cadence: cron/launchd `0 4 * * 1`; Docker `--interval 168`.
- App is read-only for EU data: no download at startup. `POST /api/refresh` triggers `eu_ingest.refresh_eu` inline.
- Existing patterns to mirror: `app/pep_ingest.py`, `scripts/update_pep.py`, `tests/test_pep_ingest.py`, `tests/test_update_pep.py`. No code comments unless the code is non-obvious.

---
### Task 1: `app/eu_ingest.py` module

**Files:**
- Create: `app/eu_ingest.py`
- Test: `tests/test_eu_ingest.py`

**Interfaces:**
- Consumes: `app.ingest.parse_export(xml_bytes) -> list[dict]`, `app.ingest._read_generation_date(xml_bytes) -> str` (both exist in Task 3's unchanged code).
- Produces:
  - Constants: `EU_XML_URL: str`, `XML_FILENAME = "eu_sanctions.xml"`, `MANIFEST_FILENAME = "manifest.json"`, `TIMEOUT = 120`, `DOWNLOAD_PAUSE = 0.5`
  - `default_root() -> Path` — `Path(os.environ.get("EU_DATA_DIR", "data/eu"))`
  - `fetch_headers(url: str = EU_XML_URL, timeout: int = TIMEOUT) -> dict` — `{last_modified, content_length, content_disposition}` (empty string when absent); raises on HTTP error
  - `download_xml(url: str, dest: Path, timeout: int = TIMEOUT, retries: int = 1) -> None` — streams to `<dest>.part`, atomic rename, cleans `.part`, retries once after `DOWNLOAD_PAUSE`
  - `load_eu_manifest(root_dir: Path) -> dict` — empty dict when absent or corrupt
  - `refresh_eu(root_dir: Path, force: bool = False, dry_run: bool = False, logger: Callable[[str], None] | None = None) -> dict` — returns the manifest dict

- [ ] **Step 1: Write the failing tests**

`tests/test_eu_ingest.py`:
```python
import hashlib
import json
from pathlib import Path

import pytest
import requests

from app.eu_ingest import (
    EU_XML_URL,
    MANIFEST_FILENAME,
    TIMEOUT,
    XML_FILENAME,
    default_root,
    download_xml,
    fetch_headers,
    load_eu_manifest,
    refresh_eu,
)

FIXTURE_XML = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
LAST_MODIFIED = "Tue, 28 Jul 2026 09:50:13 GMT"


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("app.eu_ingest.DOWNLOAD_PAUSE", 0)


def test_constants():
    assert EU_XML_URL == "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
    assert XML_FILENAME == "eu_sanctions.xml"
    assert MANIFEST_FILENAME == "manifest.json"
    assert TIMEOUT == 120


def test_default_root_env(monkeypatch):
    monkeypatch.delenv("EU_DATA_DIR", raising=False)
    assert default_root() == Path("data/eu")
    monkeypatch.setenv("EU_DATA_DIR", "/data/eu")
    assert default_root() == Path("/data/eu")


def test_fetch_headers(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def headers(self):
            return {
                "Last-Modified": LAST_MODIFIED,
                "Content-Length": "24816725",
                "Content-Disposition": 'attachment; filename="20260728-FULL-1_1(xsd).xml"',
            }

    def fake_head(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "head", fake_head)
    result = fetch_headers()
    assert captured["url"] == EU_XML_URL
    assert captured["timeout"] == 120
    assert result == {
        "last_modified": LAST_MODIFIED,
        "content_length": "24816725",
        "content_disposition": 'attachment; filename="20260728-FULL-1_1(xsd).xml"',
    }


def test_fetch_headers_missing_values(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def headers(self):
            return {}

    monkeypatch.setattr(requests, "head", lambda *a, **k: FakeResp())
    assert fetch_headers() == {"last_modified": "", "content_length": "", "content_disposition": ""}


def make_headers(last_modified=LAST_MODIFIED):
    return {"last_modified": last_modified, "content_length": "123", "content_disposition": "attachment"}


class FakeStreamResp:
    def __init__(self, chunks, ok=True):
        self._chunks = chunks
        self._ok = ok

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("500")

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_xml_writes_atomic(tmp_path, monkeypatch):
    data = b"<export/>"
    dest = tmp_path / "eu_sanctions.xml"
    captured = {}

    def fake_get(url, timeout, stream=False):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_xml("https://x/eu.xml", dest)
    assert dest.read_bytes() == data
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    assert captured["stream"] is True


def test_download_xml_retries_then_raises(tmp_path, monkeypatch):
    dest = tmp_path / "eu_sanctions.xml"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(requests.ConnectionError):
        download_xml("https://x", dest, retries=1)
    assert calls["n"] == 2
    assert not dest.exists()


def test_download_xml_retry_succeeds(tmp_path, monkeypatch):
    data = b"<export/>"
    dest = tmp_path / "eu_sanctions.xml"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("down")
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_xml("https://x", dest, retries=1)
    assert calls["n"] == 2
    assert dest.read_bytes() == data


def test_load_eu_manifest_missing(tmp_path):
    assert load_eu_manifest(tmp_path) == {}


def test_load_eu_manifest_reads(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"status": "ok"}))
    assert load_eu_manifest(tmp_path) == {"status": "ok"}


def test_load_eu_manifest_corrupt(tmp_path):
    (tmp_path / "manifest.json").write_text("{niet-json")
    assert load_eu_manifest(tmp_path) == {}


def test_refresh_eu_full_run(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())

    def fake_download(url, dest, **kw):
        dest.write_bytes(FIXTURE_XML)

    monkeypatch.setattr("app.eu_ingest.download_xml", fake_download)
    manifest = refresh_eu(tmp_path, logger=logs.append)
    assert manifest["stats"] == {"downloaded": 1, "skipped": 0, "failed": 0}
    assert manifest["status"] == "ok"
    assert manifest["last_modified"] == LAST_MODIFIED
    assert manifest["generation_date"] == "2026-07-28T11:43:32+02:00"
    assert manifest["entity_count"] == 2
    assert manifest["checksum"]
    assert manifest["size"] == len(FIXTURE_XML)
    assert manifest["downloaded_at"]
    assert (tmp_path / XML_FILENAME).read_bytes() == FIXTURE_XML
    assert (tmp_path / "manifest.json").exists()
    assert any("EU-lijst" in line for line in logs)


def test_refresh_eu_skips_unchanged(tmp_path, monkeypatch):
    (tmp_path / XML_FILENAME).write_bytes(FIXTURE_XML)
    previous = {
        "updated_at": "x",
        "last_modified": LAST_MODIFIED,
        "checksum": "abc",
        "size": len(FIXTURE_XML),
        "generation_date": "2026-07-28T11:43:32+02:00",
        "entity_count": 2,
        "downloaded_at": "t",
        "status": "ok",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(previous))
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr("app.eu_ingest.download_xml", lambda *a, **k: pytest.fail("should not download"))
    manifest = refresh_eu(tmp_path)
    assert manifest["stats"] == {"downloaded": 0, "skipped": 1, "failed": 0}
    assert manifest["status"] == "ok"


def test_refresh_eu_force_redownloads(tmp_path, monkeypatch):
    (tmp_path / XML_FILENAME).write_bytes(FIXTURE_XML)
    previous = {"updated_at": "x", "last_modified": LAST_MODIFIED, "status": "ok"}
    (tmp_path / "manifest.json").write_text(json.dumps(previous))
    calls = {"n": 0}

    def fake_download(url, dest, **kw):
        calls["n"] += 1
        dest.write_bytes(FIXTURE_XML)

    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr("app.eu_ingest.download_xml", fake_download)
    manifest = refresh_eu(tmp_path, force=True)
    assert calls["n"] == 1
    assert manifest["stats"]["downloaded"] == 1


def test_refresh_eu_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr("app.eu_ingest.download_xml", lambda *a, **k: pytest.fail("should not download"))
    manifest = refresh_eu(tmp_path, dry_run=True)
    assert manifest["stats"]["downloaded"] == 1
    assert manifest["status"] == "pending"
    assert not (tmp_path / XML_FILENAME).exists()
    assert not (tmp_path / "manifest.json").exists()


def test_refresh_eu_download_error_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr(
        "app.eu_ingest.download_xml",
        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")),
    )
    manifest = refresh_eu(tmp_path)
    assert manifest["stats"] == {"downloaded": 0, "skipped": 0, "failed": 1}
    assert manifest["status"] == "error"
    assert "down" in manifest["error"]
    assert (tmp_path / "manifest.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eu_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.eu_ingest'`.

- [ ] **Step 3: Write the module**

`app/eu_ingest.py`:
```python
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from .ingest import _read_generation_date, parse_export

EU_XML_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
XML_FILENAME = "eu_sanctions.xml"
MANIFEST_FILENAME = "manifest.json"
TIMEOUT = 120
DOWNLOAD_PAUSE = 0.5


def default_root() -> Path:
    return Path(os.environ.get("EU_DATA_DIR", "data/eu"))


def fetch_headers(url: str = EU_XML_URL, timeout: int = TIMEOUT) -> dict:
    resp = requests.head(url, timeout=timeout)
    resp.raise_for_status()
    headers = resp.headers
    return {
        "last_modified": headers.get("Last-Modified", ""),
        "content_length": headers.get("Content-Length", ""),
        "content_disposition": headers.get("Content-Disposition", ""),
    }


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_xml(url: str, dest: Path, timeout: int = TIMEOUT, retries: int = 1) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    last_error = None
    for attempt in range(retries + 1):
        try:
            part.unlink(missing_ok=True)
            with requests.get(url, timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
            part.replace(dest)
            return
        except Exception as exc:
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(DOWNLOAD_PAUSE)
    raise last_error


def load_eu_manifest(root_dir: Path) -> dict:
    manifest_path = root_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(root_dir: Path, manifest: dict) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root_dir / MANIFEST_FILENAME
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, manifest_path)


def refresh_eu(
    root_dir: Path,
    force: bool = False,
    dry_run: bool = False,
    logger: Callable[[str], None] | None = None,
) -> dict:
    headers = fetch_headers()
    last_modified = headers["last_modified"]
    dest = root_dir / XML_FILENAME
    manifest = load_eu_manifest(root_dir)
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    skip = (
        not force
        and manifest.get("last_modified") == last_modified
        and manifest.get("status") == "ok"
        and dest.exists()
    )
    if skip:
        result = dict(manifest)
        result["updated_at"] = datetime.now(timezone.utc).isoformat()
        result["stats"] = {"downloaded": 0, "skipped": 1, "failed": 0}
        if logger:
            logger("EU-lijst: overgeslagen (ongewijzigd)")
        return result
    if dry_run:
        result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_modified": last_modified,
            "status": "pending",
            "stats": {"downloaded": 1, "skipped": 0, "failed": 0},
        }
        if logger:
            logger("EU-lijst: zou downloaden")
        return result
    try:
        download_xml(EU_XML_URL, dest)
        content = dest.read_bytes()
        result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_modified": last_modified,
            "checksum": _sha1(dest),
            "size": len(content),
            "generation_date": _read_generation_date(content),
            "entity_count": len(parse_export(content)),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "stats": {"downloaded": 1, "skipped": 0, "failed": 0},
        }
        if logger:
            logger("EU-lijst: gedownload")
    except Exception as exc:
        result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_modified": last_modified,
            "status": "error",
            "error": str(exc),
            "stats": {"downloaded": 0, "skipped": 0, "failed": 1},
        }
        if logger:
            logger(f"EU-lijst: fout ({exc})")
    _write_manifest(root_dir, result)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_eu_ingest.py -v`
Expected: 15 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures (existing suite unchanged by this task).

- [ ] **Step 6: Commit**

```bash
git add app/eu_ingest.py tests/test_eu_ingest.py
git commit -m "feat: EU manifest downloader with Last-Modified skip"
```

---
### Task 2: `scripts/update_eu.py` CLI

**Files:**
- Create: `scripts/update_eu.py`
- Test: `tests/test_update_eu.py`

**Interfaces:**
- Consumes: `app.eu_ingest.fetch_headers() -> dict`, `app.eu_ingest.refresh_eu(root_dir, force, dry_run, logger) -> dict` from Task 1.
- Produces: CLI with exit code `0` on success (also per-source download errors), `1` on fatal HEAD failure. `--root`, `--force`, `--dry-run`, `--interval HOURS`, `--once`, `--log FILE`.

- [ ] **Step 1: Write the failing tests**

`tests/test_update_eu.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_update_eu.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.update_eu'`.

- [ ] **Step 3: Write the CLI**

`scripts/update_eu.py`:
```python
import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import eu_ingest

_STOP = {"flag": False}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="EU sanctielijst downloaden")
    parser.add_argument("--root", default=eu_ingest.default_root(), help=f"data-map (default: %(default)s)")
    parser.add_argument("--force", action="store_true", help="opnieuw downloaden, ook ongewijzigd")
    parser.add_argument("--dry-run", action="store_true", help="plan alleen tonen, niets downloaden")
    parser.add_argument("--interval", type=float, default=0, help="blijf draaien, update elke N uren (Docker)")
    parser.add_argument("--once", action="store_true", help="eenmalig draaien (default)")
    parser.add_argument("--log", default=None, help="schrijf logs ook naar dit bestand")
    return parser.parse_args(argv)


def _emit(args, text: str) -> None:
    print(text, flush=True)
    if args.log:
        Path(args.log).open("a").write(text + "\n")


def run_once(args) -> int:
    try:
        eu_ingest.fetch_headers()
    except Exception as exc:
        print(f"FATAAL: HEAD download mislukt: {exc}", file=sys.stderr, flush=True)
        return 1

    def log(msg: str) -> None:
        _emit(args, msg)

    manifest = eu_ingest.refresh_eu(
        Path(args.root),
        force=args.force,
        dry_run=args.dry_run,
        logger=log,
    )
    stats = manifest.get("stats", {})
    _emit(
        args,
        "Klaar: "
        f"{stats.get('downloaded', 0)} gedownload, "
        f"{stats.get('skipped', 0)} overgeslagen, "
        f"{stats.get('failed', 0)} mislukt",
    )
    return 0


def _handle_stop(signum, frame):
    _STOP["flag"] = True


def run_loop(args) -> int:
    _STOP["flag"] = False
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    last_code = 0
    while not _STOP["flag"]:
        last_code = run_once(args)
        if _STOP["flag"]:
            break
        deadline = time.monotonic() + args.interval * 3600
        while not _STOP["flag"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(60, remaining))
    return last_code


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.interval and args.interval > 0 and not args.once:
        return run_loop(args)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_update_eu.py -v`
Expected: 9 passed.

- [ ] **Step 5: Smoke-test the CLI dry-run**

Run: `.venv/bin/python scripts/update_eu.py --dry-run`
Expected: prints a line containing `zou downloaden` or `overgeslagen` and `Klaar:`, exit code 0.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no failures.

- [ ] **Step 7: Commit**

```bash
git add scripts/update_eu.py tests/test_update_eu.py
git commit -m "feat: EU sanctielijst downloader CLI"
```

---
### Task 3: Read-only app + refactor `app/ingest.py`

**Files:**
- Modify: `app/ingest.py` (remove download/cache; keep parsing)
- Modify: `app/main.py`
- Modify: `tests/test_ingest.py` (remove download/cache tests)
- Modify: `tests/test_main.py` (update refresh mocks + status from manifest)

**Interfaces:**
- Consumes: `app.eu_ingest` (`refresh_eu`, `load_eu_manifest`, `XML_FILENAME`, `default_root`) from Task 1.
- Produces:
  - `app.ingest`: unchanged `parse_export`, `_read_generation_date`; deleted `download_xml`, `refresh`, `load_index`, `DATASET_URL`, `CACHE_TTL`, `XML_FILENAME`, `META_FILENAME`.
  - `app.main.default_eu_root() -> Path`, `app.main.EU_ROOT: Path`
  - `create_app(entities=None, os_api_key=None, eu_root: Path = EU_ROOT, static_dir=STATIC_DIR, pep_root=PEP_ROOT)` — `cache_dir` param removed.
  - `_status()` top-level fields now sourced from manifest: `cached_at` = `downloaded_at`, `generated_at` = `generation_date`, `data_age_hours` computed from `downloaded_at`, `source` = manifest `status` (`ok`/`missing`/`error`/`unknown`).

- [ ] **Step 1: Write the failing tests first (new main tests + updated ingest tests)**

Append to `tests/test_main.py`:
```python
def test_default_eu_root_uses_env(monkeypatch):
    from pathlib import Path

    from app import main as main_module
    monkeypatch.setenv("EU_DATA_DIR", "/data/eu")
    assert main_module.default_eu_root() == Path("/data/eu")
    monkeypatch.delenv("EU_DATA_DIR", raising=False)
    assert main_module.default_eu_root() == main_module.EU_ROOT


def test_refresh_success(tmp_path, monkeypatch):
    import app.main as main

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    manifest = {"status": "ok", "downloaded_at": "2026-07-31T12:00:00+00:00", "generation_date": "2026-07-28T11:43:32+02:00", "entity_count": 2}
    monkeypatch.setattr(main.eu_ingest, "refresh_eu", lambda *a, **k: manifest)
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    resp = client.post("/api/refresh")
    assert resp.status_code == 200
    assert resp.json()["source"] == "ok"


def test_refresh_head_failure_returns_503(tmp_path, monkeypatch):
    import app.main as main

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    monkeypatch.setattr(main.eu_ingest, "refresh_eu", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    resp = client.post("/api/refresh")
    assert resp.status_code == 503


def test_status_source_from_manifest(tmp_path):
    import json

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    (eu_root / "manifest.json").write_text(json.dumps({
        "status": "ok",
        "downloaded_at": "2026-07-31T12:00:00+00:00",
        "generation_date": "2026-07-28T11:43:32+02:00",
    }))
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    data = client.get("/api/status").json()
    assert data["source"] == "ok"
    assert data["generated_at"] == "2026-07-28T11:43:32+02:00"
    assert data["entity_count"] == 2
```

Replace the existing `test_refresh_reports_fresh_source` in `tests/test_main.py` (lines 90-101 in the current file) with nothing — the two new refresh tests above cover it.

Rewrite the download/cache section of `tests/test_ingest.py`. The current file has, after `test_parse_export_enterprise`, a block starting at `import json` with `write_cache` and 8 download/cache tests, then `from app.pep_ingest import write_datasets_meta` and 4 `write_datasets_meta` tests. Replace the download/cache block (the `import json` line plus `write_cache` plus all 8 `test_load_index_*` / `test_download_xml_*` / `test_refresh_downloads_and_writes` tests) so that `import json` remains (the `write_datasets_meta` tests use it) but the download/cache tests and helper are gone. Final `tests/test_ingest.py` should be exactly:
```python
import json

from app.ingest import parse_export

FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export" generationDate="2026-07-28T11:43:32+02:00" globalFileId="1">
  <sanctionEntity logicalId="L1" euReferenceNumber="EU.471.56" designationDate="2001-02-01" unitedNationId="TAL123">
    <subjectType code="person" classificationCode="P"/>
    <nameAlias firstName="Abdul" lastName="Hazem" wholeName="Abdul Hai Hazem Abdul Qader" strong="true" function="Diplomat"/>
    <nameAlias wholeName="Abdul Hai Hazem" strong="false"/>
    <citizenship countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <birthdate year="1971" birthdate="1971-02-15" place="Kabul" countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <address city="Kabul" street="Main St" region="Kabul Province" countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <identification number="D123" identificationTypeCode="passport" identificationTypeDescription="National passport" countryIso2Code="AF"/>
    <regulation numberTitle="2001/154/CFSP" publicationDate="2001-02-27" programme="AFG">
      <publicationUrl>https://eur-lex.europa.eu/example</publicationUrl>
    </regulation>
    <remark>Some remarks text</remark>
  </sanctionEntity>
  <sanctionEntity logicalId="L2" euReferenceNumber="EU.2" designationDate="2022-03-09">
    <subjectType code="enterprise" classificationCode="E"/>
    <nameAlias wholeName="Rosneft" strong="true"/>
    <citizenship countryIso2Code="RU" countryDescription="RUSSIAN FEDERATION"/>
  </sanctionEntity>
</export>
"""


def test_parse_export_person():
    entities = parse_export(FIXTURE)
    assert len(entities) == 2
    person = entities[0]
    assert person["logical_id"] == "L1"
    assert person["eu_reference_number"] == "EU.471.56"
    assert person["united_nations_id"] == "TAL123"
    assert person["designation_date"] == "2001-02-01"
    assert person["subject_type"] == "person"
    assert person["aliases"] == [
        {"whole_name": "Abdul Hai Hazem Abdul Qader", "first_name": "Abdul", "last_name": "Hazem", "strong": True, "function": "Diplomat", "title": ""},
        {"whole_name": "Abdul Hai Hazem", "first_name": "", "last_name": "", "strong": False, "function": "", "title": ""},
    ]
    assert person["citizenships"] == [{"iso2": "AF", "description": "AFGHANISTAN"}]
    assert person["birthdates"] == [{"date": "1971-02-15", "year": 1971, "year_from": None, "year_to": None, "city": "", "place": "Kabul", "iso2": "AF", "country": "AFGHANISTAN"}]
    assert person["addresses"] == [{"city": "Kabul", "street": "Main St", "region": "Kabul Province", "iso2": "AF", "country": "AFGHANISTAN"}]
    assert person["identifications"] == [{"number": "D123", "type_code": "passport", "type_description": "National passport", "iso2": "AF"}]
    assert person["regulations"] == [{"number_title": "2001/154/CFSP", "publication_date": "2001-02-27", "programme": "AFG", "publication_url": "https://eur-lex.europa.eu/example"}]
    assert person["remarks"] == ["Some remarks text"]


def test_parse_export_enterprise():
    entities = parse_export(FIXTURE)
    ent = entities[1]
    assert ent["subject_type"] == "enterprise"
    assert ent["aliases"][0]["whole_name"] == "Rosneft"
    assert ent["aliases"][0]["strong"] is True
    assert ent["citizenships"] == [{"iso2": "RU", "description": "RUSSIAN FEDERATION"}]
    assert ent["birthdates"] == []


from app.pep_ingest import write_datasets_meta


def test_write_datasets_meta(tmp_path):
    index = {"datasets": [
        {"name": "ar_parliament", "collections": ["peps"], "title": "Argentina Members of Parliament", "publisher": {"name": "HCDN", "country": "ar", "official": True}, "url": "https://parlament.ar"},
        {"name": "eu_fsf", "collections": ["default"], "title": "EU Sanctions", "publisher": {"name": "EU"}},
    ]}
    write_datasets_meta(index, tmp_path)
    meta = json.loads((tmp_path / "datasets.json").read_text())
    assert meta == {
        "ar_parliament": {"title": "Argentina Members of Parliament", "publisher": "HCDN", "country": "ar", "official": True, "url": "https://parlament.ar"},
    }


def test_write_datasets_meta_atomic_no_tmp_left(tmp_path):
    write_datasets_meta({"datasets": []}, tmp_path)
    assert (tmp_path / "datasets.json").exists()
    assert not (tmp_path / "datasets.json.tmp").exists()


def test_write_datasets_meta_skips_when_unchanged(tmp_path):
    index = {"datasets": [{"name": "ar_parliament", "collections": ["peps"], "title": "Argentina Parliament", "publisher": {"name": "HCDN", "country": "ar", "official": True}, "url": "x"}]}
    write_datasets_meta(index, tmp_path)
    before = (tmp_path / "datasets.json").stat().st_mtime_ns
    import time
    time.sleep(0.01)
    write_datasets_meta(index, tmp_path)
    after = (tmp_path / "datasets.json").stat().st_mtime_ns
    assert before == after
    assert not (tmp_path / "datasets.json.tmp").exists()


def test_write_datasets_meta_updates_when_changed(tmp_path):
    index = {"datasets": [{"name": "ar_parliament", "collections": ["peps"], "title": "Old", "publisher": {"country": "ar"}}]}
    write_datasets_meta(index, tmp_path)
    index["datasets"][0]["title"] = "New"
    write_datasets_meta(index, tmp_path)
    meta = json.loads((tmp_path / "datasets.json").read_text())
    assert meta["ar_parliament"]["title"] == "New"
```

- [ ] **Step 2: Run the new/updated tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_main.py tests/test_ingest.py -v`
Expected: FAIL — `AttributeError: module 'app.main' has no attribute 'eu_ingest'` (import `main.eu_ingest` fails) and `ImportError: cannot import name 'download_xml'` removed so the ingest tests run; the new main tests fail on missing `default_eu_root`.

- [ ] **Step 3: Slim `app/ingest.py`**

Replace the whole of `app/ingest.py` with:
```python
import xml.etree.ElementTree as ET

NS = {"fsd": "http://eu.europa.ec/fpi/fsd/export"}


def _to_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_export(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entities = []
    for se in root.findall("fsd:sanctionEntity", NS):
        st = se.find("fsd:subjectType", NS)
        subject_code = st.get("code", "") if st is not None else ""
        aliases = []
        for na in se.findall("fsd:nameAlias", NS):
            aliases.append({
                "whole_name": na.get("wholeName", "").strip(),
                "first_name": na.get("firstName", "").strip(),
                "last_name": na.get("lastName", "").strip(),
                "strong": na.get("strong", "false") == "true",
                "function": na.get("function", "").strip(),
                "title": na.get("title", "").strip(),
            })
        citizenships = []
        for c in se.findall("fsd:citizenship", NS):
            citizenships.append({
                "iso2": c.get("countryIso2Code", "").strip().upper(),
                "description": c.get("countryDescription", "").strip().upper(),
            })
        birthdates = []
        for b in se.findall("fsd:birthdate", NS):
            birthdates.append({
                "date": b.get("birthdate", "").strip(),
                "year": _to_int(b.get("year", "")),
                "year_from": _to_int(b.get("yearRangeFrom", "")),
                "year_to": _to_int(b.get("yearRangeTo", "")),
                "city": b.get("city", "").strip(),
                "place": b.get("place", "").strip(),
                "iso2": b.get("countryIso2Code", "").strip().upper(),
                "country": b.get("countryDescription", "").strip().upper(),
            })
        addresses = []
        for a in se.findall("fsd:address", NS):
            addresses.append({
                "city": a.get("city", "").strip(),
                "street": a.get("street", "").strip(),
                "region": a.get("region", "").strip(),
                "iso2": a.get("countryIso2Code", "").strip().upper(),
                "country": a.get("countryDescription", "").strip().upper(),
            })
        identifications = []
        for i in se.findall("fsd:identification", NS):
            identifications.append({
                "number": i.get("number", "").strip(),
                "type_code": i.get("identificationTypeCode", "").strip(),
                "type_description": i.get("identificationTypeDescription", "").strip(),
                "iso2": i.get("countryIso2Code", "").strip().upper(),
            })
        regulations = []
        for r in se.findall("fsd:regulation", NS):
            pu = r.find("fsd:publicationUrl", NS)
            regulations.append({
                "number_title": r.get("numberTitle", "").strip(),
                "publication_date": r.get("publicationDate", "").strip(),
                "programme": r.get("programme", "").strip(),
                "publication_url": pu.text.strip() if pu is not None and pu.text else "",
            })
        remarks = []
        for rm in se.findall("fsd:remark", NS):
            if rm.text and rm.text.strip():
                remarks.append(rm.text.strip())
        entities.append({
            "logical_id": se.get("logicalId", ""),
            "eu_reference_number": se.get("euReferenceNumber", ""),
            "united_nations_id": se.get("unitedNationId", ""),
            "designation_date": se.get("designationDate", ""),
            "subject_type": "enterprise" if subject_code == "enterprise" else "person",
            "aliases": aliases,
            "citizenships": citizenships,
            "birthdates": birthdates,
            "addresses": addresses,
            "identifications": identifications,
            "regulations": regulations,
            "remarks": remarks,
        })
    return entities


def _read_generation_date(xml_bytes: bytes) -> str:
    return ET.fromstring(xml_bytes).get("generationDate", "")
```

- [ ] **Step 4: Update `app/main.py`**

Apply these edits to `app/main.py`:

Edit 1 — imports (replace `import time` and the two `from . import ...` lines so the top of the module reads):
```python
import dataclasses
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import eu_ingest, ingest, matcher, opensanctions
from . import pep_index
```

Edit 2 — remove `CACHE_DIR` and add EU root helpers. Replace this block:
```python
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"


def default_pep_root() -> Path:
    return Path(os.environ.get("PEP_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "pep")))


PEP_ROOT = default_pep_root()
```
with:
```python
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def default_pep_root() -> Path:
    return Path(os.environ.get("PEP_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "pep")))


PEP_ROOT = default_pep_root()


def default_eu_root() -> Path:
    return Path(os.environ.get("EU_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "eu")))


EU_ROOT = default_eu_root()


def _data_age_hours(downloaded_at: str | None) -> float | None:
    if not downloaded_at:
        return None
    try:
        parsed = datetime.fromisoformat(downloaded_at)
    except ValueError:
        return None
    return round((datetime.now(timezone.utc) - parsed).total_seconds() / 3600, 1)
```

Edit 3 — `create_app` signature and EU loading (replace the current `def create_app(...)` header through the `state = {...}` line):
```python
def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    eu_root: Path = EU_ROOT,
    static_dir: Path = STATIC_DIR,
    pep_root: Path = PEP_ROOT,
) -> FastAPI:
    meta = eu_ingest.load_eu_manifest(eu_root)
    if entities is None:
        xml_path = eu_root / eu_ingest.XML_FILENAME
        if xml_path.exists():
            entities = ingest.parse_export(xml_path.read_bytes())
            meta.setdefault("status", "ok")
        else:
            entities = []
            meta.setdefault("status", "missing")
    if os_api_key is None:
        os_api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    pep = pep_index.load_or_build_index(pep_root) if _pep_enabled(pep_root) else None
    state = {"entities": entities, "meta": meta, "pep": pep}
```

Edit 4 — `_status()` body (replace the return dict of `_status`, keeping the `pep_index` block):
```python
    def _status() -> dict:
        meta = state["meta"]
        pep = state["pep"]
        return {
            "cached_at": meta.get("downloaded_at"),
            "generated_at": meta.get("generation_date"),
            "entity_count": len(state["entities"]),
            "data_age_hours": _data_age_hours(meta.get("downloaded_at")),
            "opensanctions_active": opensanctions_active,
            "source": meta.get("status", "unknown"),
            "pep_index": {
                "enabled": pep is not None,
                "entity_count": len(pep.get("entities", [])) if pep else 0,
                "datasets_count": len(pep.get("datasets", {})) if pep else 0,
                "source": pep.get("source") if pep else None,
            },
        }
```

Edit 5 — `/api/refresh` route (replace the whole route):
```python
    @app.post("/api/refresh")
    def refresh():
        try:
            manifest = eu_ingest.refresh_eu(eu_root)
            state["meta"] = manifest
            xml_path = eu_root / eu_ingest.XML_FILENAME
            if xml_path.exists():
                state["entities"] = ingest.parse_export(xml_path.read_bytes())
            return _status()
        except Exception:
            logger.exception("Verversen mislukt")
            raise HTTPException(status_code=503, detail="Verversen mislukt")
```

- [ ] **Step 5: Run the affected tests**

Run: `.venv/bin/python -m pytest tests/test_main.py tests/test_ingest.py -v`
Expected: PASS. (`test_ingest.py` now has 6 tests; `test_main.py` has the new EU tests passing.)

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. Check the final count in the output.

- [ ] **Step 7: Commit**

```bash
git add app/ingest.py app/main.py tests/test_ingest.py tests/test_main.py
git commit -m "feat: read-only EU data consumption via manifest"
```

---
### Task 4: Docker, gitignore, README

**Files:**
- Modify: `Dockerfile.downloader`
- Modify: `docker-compose.yml`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/update_eu.py` from Task 2 (the generalized downloader image runs it).
- Produces: `eu-downloader` compose service; `eu-data` volume; README documents the EU weekly update + the generalized downloader image.

- [ ] **Step 1: Generalize `Dockerfile.downloader`**

Replace `Dockerfile.downloader` with:
```dockerfile
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python"]
CMD ["scripts/update_pep.py", "--interval", "168"]
```

- [ ] **Step 2: Add the `eu-downloader` service to `docker-compose.yml`**

Replace `docker-compose.yml` with:
```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - PEP_DATA_DIR=/data/pep
      - EU_DATA_DIR=/data/eu
      - OPENSANCTIONS_API_KEY=${OPENSANCTIONS_API_KEY:-}
    volumes:
      - pep-data:/data/pep
      - eu-data:/data/eu
    restart: unless-stopped

  pep-downloader:
    build:
      context: .
      dockerfile: Dockerfile.downloader
    command: ["scripts/update_pep.py", "--interval", "168"]
    environment:
      - PEP_DATA_DIR=/data/pep
    volumes:
      - pep-data:/data/pep
    restart: unless-stopped

  eu-downloader:
    build:
      context: .
      dockerfile: Dockerfile.downloader
    command: ["scripts/update_eu.py", "--interval", "168"]
    environment:
      - EU_DATA_DIR=/data/eu
    volumes:
      - eu-data:/data/eu
    restart: unless-stopped

volumes:
  pep-data:
  eu-data:
```

- [ ] **Step 3: Add `data/eu/` to `.gitignore`**

`.gitignore` currently ends with `data/pep/`. Append `data/eu/` so the file reads:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
data/*.xml
data/cache_meta.json
data/pep/
data/eu/
```

- [ ] **Step 4: Update `README.md`**

Edit the `## Data` section — replace:
```
Bij de eerste start downloadt de app de EU sanctielijst (XML 1.1, ~25 MB) van `data.europa.eu` en cacht deze in `data/`. De cache wordt automatisch ververst als deze ouder is dan 24 uur. Forceer verversen via `POST /api/refresh`.
```
with:
```
De app leest de EU sanctielijst (XML 1.1, ~25 MB) uit `data/eu/` (env `EU_DATA_DIR`). De download gebeurt door de downloader (`scripts/update_eu.py`, zie "Wekelijks bijwerken EU-data"); de app downloadt niet meer zelf. `POST /api/refresh` voert dezelfde manifest-refresh direct uit.
```

Add a new section `## Wekelijks bijwerken EU-data (data.europa.eu)` after the `## OpenSanctions (optioneel)` section (before `## Tests`):
```markdown
## Wekelijks bijwerken EU-data (data.europa.eu)

Download de EU sanctielijst (XML 1.1, ~25 MB) naar `data/eu/`:

```bash
.venv/bin/python scripts/update_eu.py --once
```

- Manifest: `data/eu/manifest.json` (`Last-Modified`, checksum, grootte, generatiedatum, aantal records, status).
- Via een HEAD-verzoek wordt gecontroleerd of de lijst is gewijzigd; ongewijzigd = overgeslagen, alleen bij wijziging wordt de 25 MB gedownload.
- Kies een pad met `--root` of env `EU_DATA_DIR`.

**Cron (macOS/Linux), wekelijks maandag 04:00:**

```cron
0 4 * * 1 cd /pad/naar/sanctielijst && .venv/bin/python scripts/update_eu.py --once >> data/eu/update.log 2>&1
```

**Container:** de service `eu-downloader` in `docker-compose.yml` draait hetzelfde script in loop-modus (`--interval 168`) met data op het `eu-data`-volume.
```

Update the `## Container images (GHCR)` section — replace:
```
- `ghcr.io/dennismdejong/sanctielijst-downloader:<tag>` en `:latest` — de PEP-downloader
```
with:
```
- `ghcr.io/dennismdejong/sanctielijst-downloader:<tag>` en `:latest` — de downloader (zowel `scripts/update_pep.py` als `scripts/update_eu.py`, kies via het commando-argument)
```
and replace:
```
podman run --rm ghcr.io/dennismdejong/sanctielijst-downloader:latest --once
```
with:
```
podman run --rm ghcr.io/dennismdejong/sanctielijst-downloader:latest scripts/update_pep.py --once
podman run --rm ghcr.io/dennismdejong/sanctielijst-downloader:latest scripts/update_eu.py --once
```
and replace the closing line:
```
`docker-compose.yml` bevat beide services (`app` + `pep-downloader`) met een gedeeld `pep-data`-volume.
```
with:
```
`docker-compose.yml` bevat de services `app`, `pep-downloader` en `eu-downloader` met de volumes `pep-data` en `eu-data`.
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (unchanged by this task — verify no accidental edit broke anything).

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.downloader docker-compose.yml .gitignore README.md
git commit -m "chore: EU downloader in Docker, gitignore and docs"
```

---
## Self-Review Notes

- Spec coverage: `app/eu_ingest.py` (Task 1), CLI `update_eu.py` (Task 2), read-only app + ingest refactor (Task 3), Docker/gitignore/README (Task 4). Weekly cadence documented in README (cron `0 4 * * 1`, Docker `--interval 168`).
- Manifest is flat (spec decision), skip key is HTTP `Last-Modified`, HEAD failure is fatal in the CLI (exit 1) but download failure records `status: "error"` and exits 0 — mirroring PEP's index-vs-source error split.
- `app/ingest.py` keeps only parsing (`parse_export`, `_read_generation_date`); all download/cache logic moved to `eu_ingest.py`.
- `create_app`'s `cache_dir` param is renamed to `eu_root`; all callers updated (only tests).
- Final manual smoke test (outside automated tests): after a real run, `python scripts/update_eu.py --once` downloads the list; run it a second time and confirm "overgeslagen (ongewijzigd)".
