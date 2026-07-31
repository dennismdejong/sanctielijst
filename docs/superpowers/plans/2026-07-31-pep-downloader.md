# OpenSanctions PEP-downloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download alle individuele PEP-bronnen van OpenSanctions (189 datasets, `entities.ftm.json` per bron) naar `data/pep/`, wekelijks bijgewerkt via een testbaar Python-script dat zowel op de host (cron/launchd) als later in Docker draait.

**Architecture:** Een herbruikbare module `app/pep_ingest.py` (zelfde stijl als `app/ingest.py`) haalt de OpenSanctions-hoofdindex op, filtert PEP-bronnen op het `collections`-veld, en downloadt per bron het `entities.ftm.json`-artefact met SHA-1-checksum-verificatie (temp+rename, retry). Een dunne CLI-wrapper `scripts/update_pep.py` biedt eenmalig (`--once`) en loop-modus (`--interval`), met `PEP_DATA_DIR`-env-ondersteuning voor Docker. Resultaten worden bijgehouden in `data/pep/manifest.json`. Docker-artefacten (`Dockerfile`, `docker-compose.yml`) worden meegeleverd maar pas later gebruikt.

**Tech Stack:** Python 3.11, stdlib (`json`, `hashlib`, `datetime`, `argparse`, `signal`), `requests` (bestaande dependency), pytest voor tests (gemockte HTTP, geen live downloads).

## Global Constraints

- Python 3.11+; geen nieuwe dependencies buiten bestaande `requirements.txt`.
- Alle code in het Nederlands waar UI/meldingen betreft; code-identifiers Engels.
- Data-opslag: `data/pep/<dataset>/entities.ftm.json` + `data/pep/manifest.json`, pad configureerbaar via env `PEP_DATA_DIR` of CLI `--root`.
- Databron (constante): `https://data.opensanctions.org/datasets/latest/index.json`.
- Enige te downloaden resource per bron: `entities.ftm.json`; checksum is SHA-1 uit de index.
- Timeout 120s per download; 1 retry bij fouten; korte pauze (0.5s) tussen bronnen.
- Geen code-commentaar tenzij niet-voor-de-hand liggend (retry/atomic-logica).
- **Parallelle agent:** een andere agent werkt tegelijk aan `app/main.py`, `static/`, `tests/test_*.py` en `README.md`. Stage nooit via `git add .`; stage alleen de bestanden van deze taken. Bij conflicten in `.gitignore`/`README.md`: handmatig mergen, alleen toevoegen wat deze taken betreft.
- Testsuite draaien met `.venv/bin/python -m pytest` (repo-stijl).

---

### Task 1: PEP ingest module — index + filter

**Files:**
- Create: `app/pep_ingest.py`
- Test: `tests/test_pep_ingest.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Constants: `INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"`, `RESOURCE_NAME = "entities.ftm.json"`, `TIMEOUT = 120`, `DOWNLOAD_PAUSE = 0.5`, `MANIFEST_FILENAME = "manifest.json"`, `PEP_COLLECTION = "peps"`.
  - `default_root() -> Path` — `Path(os.environ.get("PEP_DATA_DIR", "data/pep"))`.
  - `fetch_index(url: str = INDEX_URL, timeout: int = TIMEOUT) -> dict` — `requests.get` + `raise_for_status` + `.json()`.
  - `list_pep_datasets(index: dict) -> list[dict]` — retourneert `[{"name", "version", "resource"}]` voor elke dataset waar `"peps"` in `collections`, `type == "source"` en een resource met `name == RESOURCE_NAME` bestaat; gesorteerd op `name`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pep_ingest.py`:
```python
from pathlib import Path

import pytest

from app.pep_ingest import (
    INDEX_URL,
    RESOURCE_NAME,
    TIMEOUT,
    default_root,
    fetch_index,
    list_pep_datasets,
)


def make_resource(name=RESOURCE_NAME, url="https://data.opensanctions.org/artifacts/x/1/entities.ftm.json", checksum="abc", size=100):
    return {"name": name, "url": url, "checksum": checksum, "size": size, "mime_type": "application/json+ftm"}


def make_source(name, collections=("peps",), type_="source", version="v1", resources=None):
    return {
        "name": name,
        "type": type_,
        "collections": list(collections),
        "version": version,
        "resources": resources if resources is not None else [make_resource()],
    }


def make_index(datasets):
    return {"datasets": datasets}


def test_constants():
    assert INDEX_URL == "https://data.opensanctions.org/datasets/latest/index.json"
    assert RESOURCE_NAME == "entities.ftm.json"
    assert TIMEOUT == 120


def test_default_root_env(monkeypatch):
    monkeypatch.delenv("PEP_DATA_DIR", raising=False)
    assert default_root() == Path("data/pep")
    monkeypatch.setenv("PEP_DATA_DIR", "/data/pep")
    assert default_root() == Path("/data/pep")


def test_fetch_index(monkeypatch):
    import requests

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"datasets": []}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "get", fake_get)
    assert fetch_index() == {"datasets": []}
    assert captured["url"] == INDEX_URL
    assert captured["timeout"] == 120


def test_list_pep_datasets_filters():
    datasets = [
        make_source("al_kuvendi"),
        make_source("eu_meps", collections=("default", "peps")),
        make_source("br_pep", collections=("peps",)),
        make_source("eu_fsf", collections=("default",)),
        make_source("wd_peps", type_="external", collections=("peps",)),
        make_source("no_ftm", collections=("peps",), resources=[make_resource(name="senzing.json")]),
        make_source("empty", collections=("peps",), resources=[]),
    ]
    pep = list_pep_datasets(make_index(datasets))
    names = [d["name"] for d in pep]
    assert names == ["al_kuvendi", "br_pep", "eu_meps"]
    al = pep[0]
    assert al["resource"]["name"] == RESOURCE_NAME
    assert al["version"] == "v1"


def test_list_pep_datasets_dict_index():
    index = {"datasets": {
        "al_kuvendi": make_source("al_kuvendi"),
        "eu_fsf": make_source("eu_fsf", collections=("default",)),
    }}
    assert [d["name"] for d in list_pep_datasets(index)] == ["al_kuvendi"]


def test_list_pep_datasets_missing_key():
    assert list_pep_datasets({}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pep_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pep_ingest'`.

- [ ] **Step 3: Write minimal implementation**

`app/pep_ingest.py`:
```python
import os
from pathlib import Path

import requests

INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"
RESOURCE_NAME = "entities.ftm.json"
TIMEOUT = 120
DOWNLOAD_PAUSE = 0.5
MANIFEST_FILENAME = "manifest.json"
PEP_COLLECTION = "peps"


def default_root() -> Path:
    return Path(os.environ.get("PEP_DATA_DIR", "data/pep"))


def fetch_index(url: str = INDEX_URL, timeout: int = TIMEOUT) -> dict:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_pep_datasets(index: dict) -> list[dict]:
    raw = index.get("datasets") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    result = []
    for ds in raw:
        if not isinstance(ds, dict):
            continue
        if PEP_COLLECTION not in (ds.get("collections") or []):
            continue
        if ds.get("type") != "source":
            continue
        resource = next(
            (r for r in (ds.get("resources") or []) if r.get("name") == RESOURCE_NAME),
            None,
        )
        if resource is None:
            continue
        result.append({
            "name": ds["name"],
            "version": ds.get("version", ""),
            "resource": resource,
        })
    result.sort(key=lambda d: d["name"])
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pep_ingest.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pep_ingest.py tests/test_pep_ingest.py
git commit -m "feat: PEP index fetch and dataset filtering"
```

---

### Task 2: download_artifact met checksum + retry

**Files:**
- Modify: `app/pep_ingest.py`
- Test: `tests/test_pep_ingest.py`

**Interfaces:**
- Consumes: constants from Task 1 (`TIMEOUT`, `DOWNLOAD_PAUSE`).
- Produces:
  - `_sha1(path: Path) -> str` — SHA-1 hex van een bestand (chunked, 64 KiB).
  - `download_artifact(url: str, dest: Path, checksum: str, timeout: int = TIMEOUT, retries: int = 1) -> None` — streamt naar `<dest>.part`, verifieert SHA-1 tegen `checksum`, `part.replace(dest)` (atomic), ruimt `.part` op; bij fout opnieuw proberen tot `retries` keer en dan de laatste fout opnieuw gooien. Checksum-mismatch is een `ValueError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pep_ingest.py`:
```python
import hashlib

import requests

from app.pep_ingest import download_artifact


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("app.pep_ingest.DOWNLOAD_PAUSE", 0)


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeStreamResp:
    def __init__(self, chunks, ok=True):
        self._chunks = chunks
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("500")

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_artifact_writes_and_verifies(tmp_path, monkeypatch):
    data = b'{"entities": []}'
    dest = tmp_path / "al_kuvendi" / "entities.ftm.json"
    captured = {}

    def fake_get(url, timeout, stream=False):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_artifact("https://x/entities.ftm.json", dest, sha1_bytes(data))
    assert dest.read_bytes() == data
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    assert captured["stream"] is True


def test_download_artifact_checksum_mismatch_raises(tmp_path, monkeypatch):
    dest = tmp_path / "x" / "entities.ftm.json"
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeStreamResp([b"data"]))
    with pytest.raises(ValueError, match="checksum"):
        download_artifact("https://x", dest, "ffff")
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_artifact_retries_then_raises(tmp_path, monkeypatch):
    dest = tmp_path / "x" / "entities.ftm.json"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(requests.ConnectionError):
        download_artifact("https://x", dest, "abc", retries=1)
    assert calls["n"] == 2


def test_download_artifact_retry_succeeds(tmp_path, monkeypatch):
    data = b"data"
    dest = tmp_path / "x" / "entities.ftm.json"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("down")
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_artifact("https://x", dest, sha1_bytes(data), retries=1)
    assert calls["n"] == 2
    assert dest.read_bytes() == data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pep_ingest.py -k download_artifact -v`
Expected: FAIL with `ImportError: cannot import name 'download_artifact'`.

- [ ] **Step 3: Write implementation**

Append to `app/pep_ingest.py`:
```python
import hashlib
import time

import requests


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_artifact(url: str, dest: Path, checksum: str, timeout: int = TIMEOUT, retries: int = 1) -> None:
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
            if _sha1(part) != checksum:
                raise ValueError(f"checksum mismatch voor {dest.name}")
            part.replace(dest)
            return
        except Exception as exc:
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(DOWNLOAD_PAUSE)
    raise last_error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pep_ingest.py -k download_artifact -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pep_ingest.py tests/test_pep_ingest.py
git commit -m "feat: artifact download with SHA-1 verification and retry"
```

---

### Task 3: refresh_pep + manifest

**Files:**
- Modify: `app/pep_ingest.py`
- Test: `tests/test_pep_ingest.py`

**Interfaces:**
- Consumes: `list_pep_datasets`, `download_artifact`, `default_root`, `MANIFEST_FILENAME`, `DOWNLOAD_PAUSE`, `TIMEOUT` from Tasks 1–2.
- Produces:
  - `load_pep_manifest(root_dir: Path) -> dict` — leest `root_dir / MANIFEST_FILENAME`; `{}` als afwezig of corrupt.
  - `refresh_pep(root_dir: Path, index: dict | None = None, force: bool = False, dry_run: bool = False, limit: int | None = None, logger: Callable[[str], None] | None = None) -> dict` — haalt de index op als `index is None`, filtert, en per bron: skip (zelfde `version` + checksum + bestand bestaat + status `ok`), anders download. Bij `dry_run` wordt niets gedownload/gemanifesteerd (status `"pending"`); tellers tellen wat er zou gebeuren. Retourneert het manifest `{"updated_at", "sources", "stats"}` met `stats = {"total", "downloaded", "skipped", "failed", "bytes"}`; schrijft het manifest weg tenzij `dry_run`. Bij per-bron-fout: `status: "error"` + `error`-veld, ga door.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pep_ingest.py`:
```python
import json

from app.pep_ingest import load_pep_manifest, refresh_pep


def test_load_pep_manifest_missing(tmp_path):
    assert load_pep_manifest(tmp_path) == {}


def test_load_pep_manifest_reads(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"updated_at": "t"}))
    assert load_pep_manifest(tmp_path) == {"updated_at": "t"}


def test_load_pep_manifest_corrupt(tmp_path):
    (tmp_path / "manifest.json").write_text("{niet-json")
    assert load_pep_manifest(tmp_path) == {}


def test_refresh_pep_full_run(tmp_path, monkeypatch):
    index = make_index([
        make_source("al_kuvendi", version="v1", resources=[make_resource(url="https://a", checksum=sha1_bytes(b"a"))]),
        make_source("br_pep", version="v1", resources=[make_resource(url="https://b", checksum=sha1_bytes(b"b"))]),
    ])
    logs = []

    def fake_get(url, timeout, stream=False):
        data = b"a" if url == "https://a" else b"b"
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    manifest = refresh_pep(tmp_path, index=index, logger=logs.append)
    assert manifest["stats"] == {"total": 2, "downloaded": 2, "skipped": 0, "failed": 0, "bytes": 200}
    assert (tmp_path / "al_kuvendi" / "entities.ftm.json").read_bytes() == b"a"
    assert manifest["sources"]["al_kuvendi"]["status"] == "ok"
    assert manifest["sources"]["al_kuvendi"]["version"] == "v1"
    assert (tmp_path / "manifest.json").exists()
    assert any("al_kuvendi" in line for line in logs)


def test_refresh_pep_skips_unchanged(tmp_path, monkeypatch):
    data = b"a"
    index = make_index([make_source("al_kuvendi", version="v1", resources=[make_resource(url="https://a", checksum=sha1_bytes(data))])])
    (tmp_path / "al_kuvendi").mkdir(parents=True)
    (tmp_path / "al_kuvendi" / "entities.ftm.json").write_bytes(data)
    previous = {
        "updated_at": "x",
        "sources": {"al_kuvendi": {"version": "v1", "checksum": sha1_bytes(data), "size": 100, "downloaded_at": "t", "status": "ok"}},
        "stats": {},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(previous))
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("should not download"))
    manifest = refresh_pep(tmp_path, index=index)
    assert manifest["stats"] == {"total": 1, "downloaded": 0, "skipped": 1, "failed": 0, "bytes": 0}


def test_refresh_pep_force_redownloads(tmp_path, monkeypatch):
    data = b"a"
    index = make_index([make_source("al_kuvendi", version="v1", resources=[make_resource(url="https://a", checksum=sha1_bytes(data))])])
    (tmp_path / "al_kuvendi").mkdir(parents=True)
    (tmp_path / "al_kuvendi" / "entities.ftm.json").write_bytes(data)
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    manifest = refresh_pep(tmp_path, index=index, force=True)
    assert calls["n"] == 1
    assert manifest["stats"]["downloaded"] == 1


def test_refresh_pep_dry_run_writes_nothing(tmp_path, monkeypatch):
    index = make_index([make_source("al_kuvendi", version="v1")])
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("should not download"))
    manifest = refresh_pep(tmp_path, index=index, dry_run=True)
    assert manifest["stats"]["downloaded"] == 1
    assert manifest["sources"]["al_kuvendi"]["status"] == "pending"
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "al_kuvendi").exists()


def test_refresh_pep_source_error_recorded(tmp_path, monkeypatch):
    index = make_index([
        make_source("al_kuvendi", version="v1", resources=[make_resource(url="https://ok", checksum=sha1_bytes(b"d"))]),
        make_source("br_pep", version="v1", resources=[make_resource(url="https://bad", checksum="ffff")]),
    ])

    def fake_get(url, timeout, stream=False):
        return FakeStreamResp([b"d" if url == "https://ok" else b"x"])

    monkeypatch.setattr(requests, "get", fake_get)
    manifest = refresh_pep(tmp_path, index=index)
    assert manifest["stats"] == {"total": 2, "downloaded": 1, "skipped": 0, "failed": 1, "bytes": 100}
    assert manifest["sources"]["br_pep"]["status"] == "error"
    assert "checksum" in manifest["sources"]["br_pep"]["error"]
    assert (tmp_path / "al_kuvendi" / "entities.ftm.json").exists()
    assert not (tmp_path / "br_pep" / "entities.ftm.json").exists()


def test_refresh_pep_limit(tmp_path, monkeypatch):
    index = make_index([
        make_source("al_kuvendi", version="v1", resources=[make_resource(url="https://a", checksum=sha1_bytes(b"a"))]),
        make_source("br_pep", version="v1", resources=[make_resource(url="https://b", checksum=sha1_bytes(b"b"))]),
    ])
    monkeypatch.setattr(requests, "get", lambda url, timeout, stream=False: FakeStreamResp([url.encode()]))
    manifest = refresh_pep(tmp_path, index=index, limit=1)
    assert manifest["stats"]["total"] == 1
    assert manifest["stats"]["downloaded"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pep_ingest.py -k "refresh or load_pep" -v`
Expected: FAIL with `ImportError: cannot import name 'load_pep_manifest'`.

- [ ] **Step 3: Write implementation**

Append to `app/pep_ingest.py`:
```python
import json
from datetime import datetime, timezone
from typing import Callable


def load_pep_manifest(root_dir: Path) -> dict:
    manifest_path = root_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        return {}


def _source_entry(version: str, resource: dict, status: str, error: str = "") -> dict:
    entry = {
        "version": version,
        "checksum": resource.get("checksum", ""),
        "size": resource.get("size", 0),
        "downloaded_at": None,
        "status": status,
    }
    if error:
        entry["error"] = error
    return entry


def refresh_pep(
    root_dir: Path,
    index: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    logger: Callable[[str], None] | None = None,
) -> dict:
    if index is None:
        index = fetch_index()
    datasets = list_pep_datasets(index)
    if limit is not None:
        datasets = datasets[:limit]
    manifest = load_pep_manifest(root_dir)
    sources = dict(manifest.get("sources", {}))
    stats = {"total": len(datasets), "downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0}
    for ds in datasets:
        name = ds["name"]
        resource = ds["resource"]
        version = ds.get("version", "")
        entry = sources.get(name, {})
        dest = root_dir / name / RESOURCE_NAME
        skip = (
            not force
            and entry.get("version") == version
            and entry.get("status") == "ok"
            and entry.get("checksum") == resource.get("checksum")
            and dest.exists()
        )
        if skip:
            sources[name] = dict(entry)
            stats["skipped"] += 1
            if logger:
                logger(f"{name}: overgeslagen (ongewijzigd)")
            continue
        if dry_run:
            sources[name] = _source_entry(version, resource, "pending")
            stats["downloaded"] += 1
            if logger:
                logger(f"{name}: zou downloaden")
            continue
        try:
            download_artifact(resource["url"], dest, resource.get("checksum", ""))
            entry = _source_entry(version, resource, "ok")
            entry["downloaded_at"] = datetime.now(timezone.utc).isoformat()
            sources[name] = entry
            stats["downloaded"] += 1
            stats["bytes"] += resource.get("size", 0)
            if logger:
                logger(f"{name}: gedownload")
        except Exception as exc:
            sources[name] = _source_entry(version, resource, "error", str(exc))
            stats["failed"] += 1
            if logger:
                logger(f"{name}: fout ({exc})")
        if not dry_run:
            time.sleep(DOWNLOAD_PAUSE)
    result = {"updated_at": datetime.now(timezone.utc).isoformat(), "sources": sources, "stats": stats}
    if not dry_run:
        root_dir.mkdir(parents=True, exist_ok=True)
        (root_dir / MANIFEST_FILENAME).write_text(json.dumps(result, indent=2))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pep_ingest.py -v`
Expected: 16 passed (6 Task 1 + 4 Task 2 + 6 Task 3).

- [ ] **Step 5: Commit**

```bash
git add app/pep_ingest.py tests/test_pep_ingest.py
git commit -m "feat: PEP refresh pipeline with manifest and skip logic"
```

---

### Task 4: CLI-wrapper met eenmalige en loop-modus

**Files:**
- Create: `scripts/update_pep.py`
- Create: `scripts/__init__.py`
- Test: `tests/test_update_pep.py`

**Interfaces:**
- Consumes: `app.pep_ingest` (`fetch_index`, `refresh_pep`, `default_root`).
- Produces:
  - `parse_args(argv: list[str] | None = None) -> argparse.Namespace` — flags: `--root` (default `default_root()`), `--force`, `--dry-run`, `--limit N`, `--interval HOURS` (float, default 0), `--log FILE`.
  - `run_once(args) -> int` — `0` bij succes (ook met per-bron-fouten), `1` bij fatale fout (index onbereikbaar).
  - `run_loop(args) -> int` — draait `run_once` elke `args.interval * 3600` seconden; stopt graceful op SIGTERM/SIGINT.
  - `main(argv: list[str] | None = None) -> int` — kiest loop vs once.
  - Logs naar stdout; optioneel `--log FILE` schrijft hetzelfde naar een bestand.

- [ ] **Step 1: Write the failing tests**

`tests/test_update_pep.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_update_pep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts'`.

- [ ] **Step 3: Write the CLI**

`scripts/__init__.py` (leeg bestand).

`scripts/update_pep.py`:
```python
import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pep_ingest

_STOP = {"flag": False}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OpenSanctions PEP-lijsten downloaden")
    parser.add_argument("--root", default=pep_ingest.default_root(), help=f"data-map (default: %(default)s)")
    parser.add_argument("--force", action="store_true", help="alles opnieuw downloaden, ook ongewijzigde")
    parser.add_argument("--dry-run", action="store_true", help="plan alleen tonen, niets downloaden")
    parser.add_argument("--limit", type=int, default=None, help="maximaal aantal bronnen (testen)")
    parser.add_argument("--interval", type=float, default=0, help="blijf draaien, update elke N uren (Docker)")
    parser.add_argument("--log", default=None, help="schrijf logs ook naar dit bestand")
    return parser.parse_args(argv)


def _emit(args, text: str) -> None:
    print(text)
    if args.log:
        Path(args.log).open("a").write(text + "\n")


def run_once(args) -> int:
    try:
        index = pep_ingest.fetch_index()
    except Exception as exc:
        print(f"FATAAL: index download mislukt: {exc}", file=sys.stderr)
        return 1

    def log(msg: str) -> None:
        _emit(args, msg)

    manifest = pep_ingest.refresh_pep(
        Path(args.root),
        index=index,
        force=args.force,
        dry_run=args.dry_run,
        limit=args.limit,
        logger=log,
    )
    stats = manifest.get("stats", {})
    _emit(
        args,
        "Klaar: "
        f"{stats.get('downloaded', 0)} gedownload, "
        f"{stats.get('skipped', 0)} overgeslagen, "
        f"{stats.get('failed', 0)} mislukt "
        f"(totaal {stats.get('total', 0)})",
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
        time.sleep(args.interval * 3600)
    return last_code


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.interval and args.interval > 0:
        return run_loop(args)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_update_pep.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_pep.py scripts/__init__.py tests/test_update_pep.py
git commit -m "feat: CLI wrapper with one-shot and interval modes"
```

---

### Task 5: Docker-artefacten + scheduling-docs

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Modify: `.gitignore` (voeg `data/pep/` toe)
- Modify: `README.md` (append sectie "Wekelijkse PEP-download")

**Interfaces:**
- Consumes: `scripts/update_pep.py` (Task 4), `app/pep_ingest.py` (Tasks 1–3).
- Produces: containerizable downloader + host-scheduling-documentatie.

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

ENV PEP_DATA_DIR=/data/pep

ENTRYPOINT ["python", "scripts/update_pep.py"]
CMD ["--interval", "168"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  pep-downloader:
    build: .
    command: ["--interval", "168"]
    environment:
      - PEP_DATA_DIR=/data/pep
    volumes:
      - pep-data:/data/pep
    restart: unless-stopped

volumes:
  pep-data:
```

- [ ] **Step 3: Create `.dockerignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.git/
.env
data/
docs/
static/
tests/
```

- [ ] **Step 4: Update `.gitignore`**

Voeg de regel `data/pep/` toe aan het einde van `.gitignore` (naast de bestaande `data/*.xml`-regels). Als de parallelle agent dit bestand tegelijk wijzigt: handmatig samenvoegen, alleen de PEP-regel toevoegen.

- [ ] **Step 5: Append README-sectie**

Voeg aan het einde van `README.md` toe:

```markdown
## Wekelijks bijwerken PEP-data (OpenSanctions)

Download alle individuele PEP-bronnen (~0.8 GB, `entities.ftm.json` per bron) naar `data/pep/`:

```bash
.venv/bin/python scripts/update_pep.py --once
```

- Manifest: `data/pep/manifest.json` (downloaddatum, versie, checksums, status per bron).
- Ongewijzigde bronnen worden overgeslagen; alleen gewijzigde worden herdownload.
- Kies een pad met `--root` of env `PEP_DATA_DIR`.

**Cron (macOS/Linux), wekelijks maandag 04:00:**

```cron
0 4 * * 1 cd /pad/naar/sanctielijst && .venv/bin/python scripts/update_pep.py --once >> data/pep/update.log 2>&1
```

**Docker:** de service `pep-downloader` in `docker-compose.yml` draait hetzelfde script in loop-modus (`--interval 168`) met data op een volume.
```

- [ ] **Step 6: Verify — imports + full test suite**

Run:
```bash
.venv/bin/python -c "from app import pep_ingest; from scripts import update_pep"
.venv/bin/python -m pytest -v
```
Expected: imports slagen; alle tests groen (bestaande app-tests van de andere agent + de nieuwe 21 tests).

- [ ] **Step 7: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore .gitignore README.md
git commit -m "feat: Docker packaging and weekly PEP update docs"
```

---

## Self-Review

**Spec coverage:**
- Alle PEP-bronnen via bulk-download → Task 1 (filter), Task 3 (refresh) ✔
- Opslag `data/pep/` + manifest → Tasks 3, 5 (`.gitignore`) ✔
- Alleen `entities.ftm.json` → Task 1 (`RESOURCE_NAME`) ✔
- Wekelijks bijwerken → Task 4 (`--interval`/`--once`) + Task 5 (cron/docker) ✔
- Docker-ready (env `PEP_DATA_DIR`, volume, entrypoint) → Tasks 1 (`default_root`), 5 ✔
- Error-handling (retry, checksum, per-bron-error) → Task 2, 3, 4 ✔
- Teststrategie (gemockte HTTP) → Tasks 1–4 ✔

**Placeholders:** geen TBD/TODO; elke stap bevat volledige code.

**Type-consistentie:** `fetch_index`, `list_pep_datasets`, `download_artifact`, `refresh_pep`, `load_pep_manifest`, `default_root`, `parse_args`, `run_once`, `run_loop`, `main` worden identiek gebruikt in later tasks als gedefinieerd in eerdere tasks.
