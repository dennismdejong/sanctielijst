# Internationale Sanctie-lijsten (VN/OFAC/VK/NL) + FATF-risicolanden — Implementatieplan

> **Voor agentic workers:** VERPLICHTE SUB-SKILL: gebruik superpowers:subagent-driven-development (aanbevolen) of superpowers:executing-plans om dit plan taak-voor-taak te implementeren. Stappen gebruiken checkbox (`- [ ]`)-syntax voor tracking.

**Goal:** Voeg lokale screening toe op de OpenSanctions `sanctions`-collectie (VN, OFAC, VK, NL-terroristenlijst) en een FATF/risicolanden-check, overal meedraaiend (UI, batch, watchlist).

**Architecture:** De `pep_ingest`-downloadlogica wordt gegeneraliseerd naar een collectie-parameter; een nieuwe `sanctions_ingest` downloadt de `sanctions`-collectie (excl. `eu_fsf`) naar `data/sanctions`. De zoekindex krijgt een derde bron `'sanctie'` (schema v4). Een aparte `risk_countries`-module flagt matches waarvan de nationaliteit op de FATF/EU-risicolijst staat. De optionele OpenSanctions-API blijft onveranderd.

**Tech Stack:** Python 3.11+, FastAPI, SQLite+FTS5, rapidfuzz, requests, reportlab/openpyxl, vanilla JS.

## Global Constraints

- Python 3.11+; geen nieuwe dependencies.
- Bestaande signatures van `list_pep_datasets`, `refresh_pep`, `build_index`, `rebuild_index`, `index_fresh`, `ensure_index` blijven compatibel (nieuwe parameters hebben defaults `=None`), zodat bestaande tests groen blijven.
- Bronwaarde voor sanctie-data in de index is exact de string `'sanctie'`.
- `eu_fsf` wordt NIET uit de `sanctions`-collectie gedownload/geïndexeerd.
- Nieuwe env-vars: `SANCTIONS_DATA_DIR` (default `data/sanctions`), `SANCTIONS_INDEX_ENABLED` (default: aan als `data/sanctions` bestaat), `RISK_COUNTRIES` (default `data/risk_countries.json`).
- SCHEMA_VERSION in `app/search_index.py` gaat van 3 naar 4.
- Risicolijst-data (FATF zwart/grijs, EU high-risk) zit in een handmatig versiebeheerd JSON-bestand; géén scraping.
- Alle stappen: TDD — eerst falende test, dan implementatie, dan groene test, dan commit.

---

### Task 1: `pep_ingest.py` generaliseren naar collecties

**Files:**
- Modify: `app/pep_ingest.py`
- Test: `tests/test_pep_ingest.py`

**Interfaces:**
- Produces:
  - `list_collection_datasets(index: dict, collection: str, exclude: tuple[str, ...] = ()) -> list[dict]`
  - `refresh_collection(root_dir, collection, *, index=None, force=False, dry_run=False, limit=None, logger=None, exclude=()) -> dict`
  - `write_datasets_meta(index, root_dir, *, collection: str = PEP_COLLECTION) -> None`
  - `list_pep_datasets(index)` en `refresh_pep(...)` behouden exact hun huidige signatures (wrappers).

- [ ] **Step 1: Schrijf de falende tests**

Voeg toe aan `tests/test_pep_ingest.py`:

```python
from app.pep_ingest import list_collection_datasets, refresh_collection


def test_list_collection_datasets_exclude():
    datasets = [
        make_source("us_ofac_sdn", collections=("sanctions",)),
        make_source("nl_terrorism_list", collections=("sanctions",)),
        make_source("eu_fsf", collections=("sanctions",)),
        make_source("al_kuvendi", collections=("peps",)),
    ]
    names = [d["name"] for d in list_collection_datasets(make_index(datasets), "sanctions", exclude=("eu_fsf",))]
    assert names == ["nl_terrorism_list", "us_ofac_sdn"]


def test_refresh_collection_writes_collection_key(tmp_path, monkeypatch):
    data = b"a"
    index = make_index([make_source("us_ofac_sdn", collections=("sanctions",), version="v1",
                                   resources=[make_resource(url="https://a", checksum=sha1_bytes(data))])])
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeStreamResp([data]))
    manifest = refresh_collection(tmp_path, "sanctions", index=index, logger=print)
    assert manifest["collection"] == "sanctions"
    assert manifest["stats"] == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0, "bytes": 100}
    assert (tmp_path / "us_ofac_sdn" / "entities.ftm.json").read_bytes() == data
    meta = json.loads((tmp_path / "datasets.json").read_text())
    assert "us_ofac_sdn" in meta


def test_refresh_pep_wrapper_delegates(tmp_path, monkeypatch):
    data = b"a"
    index = make_index([make_source("al_kuvendi", version="v1",
                                   resources=[make_resource(url="https://a", checksum=sha1_bytes(data))])])
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeStreamResp([data]))
    manifest = refresh_pep(tmp_path, index=index)
    assert manifest["collection"] == "peps"
```

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_pep_ingest.py::test_list_collection_datasets_exclude -v`
Expected: FAIL met `ImportError: cannot import name 'list_collection_datasets'`.

- [ ] **Step 3: Implementeer de generieke functies**

Vervang in `app/pep_ingest.py` de functie `list_pep_datasets` (regels 27-54) door:

```python
def list_collection_datasets(index: dict, collection: str, exclude: tuple[str, ...] = ()) -> list[dict]:
    raw = index.get("datasets") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    result = []
    for ds in raw:
        if not isinstance(ds, dict):
            continue
        if collection not in (ds.get("collections") or []):
            continue
        if ds.get("type") != "source":
            continue
        name = ds.get("name")
        if not name or name in exclude:
            continue
        resource = next(
            (r for r in (ds.get("resources") or []) if r.get("name") == RESOURCE_NAME),
            None,
        )
        if resource is None:
            continue
        result.append({
            "name": name,
            "version": ds.get("version", ""),
            "resource": resource,
        })
    result.sort(key=lambda d: d["name"])
    return result


def list_pep_datasets(index: dict) -> list[dict]:
    return list_collection_datasets(index, PEP_COLLECTION)
```

Vervang `refresh_pep` (regels 119-184) door een generieke `refresh_collection` plus wrapper:

```python
def refresh_collection(
    root_dir: Path,
    collection: str,
    *,
    index: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    logger: Callable[[str], None] | None = None,
    exclude: tuple[str, ...] = (),
) -> dict:
    if index is None:
        index = fetch_index()
    datasets = list_collection_datasets(index, collection, exclude=exclude)
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
    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "collection": collection,
        "sources": sources,
        "stats": stats,
    }
    if not dry_run:
        root_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = root_dir / MANIFEST_FILENAME
        tmp = manifest_path.with_suffix(manifest_path.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(result, indent=2))
        os.replace(tmp, manifest_path)
        write_datasets_meta(index, root_dir, collection=collection)
    return result


def refresh_pep(
    root_dir: Path,
    index: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    logger: Callable[[str], None] | None = None,
) -> dict:
    return refresh_collection(
        root_dir,
        PEP_COLLECTION,
        index=index,
        force=force,
        dry_run=dry_run,
        limit=limit,
        logger=logger,
    )
```

Wijzig `write_datasets_meta` (regel 187) naar een collectie-parameter:

```python
def write_datasets_meta(index: dict, root_dir: Path, *, collection: str = PEP_COLLECTION) -> None:
    raw = index.get("datasets") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    meta = {}
    for ds in raw:
        if not isinstance(ds, dict):
            continue
        if collection not in (ds.get("collections") or []):
            continue
        name = ds.get("name")
        if not name:
            continue
        pub = ds.get("publisher") or {}
        meta[name] = {
            "title": ds.get("title", ""),
            "publisher": pub.get("name", ""),
            "country": pub.get("country", ""),
            "official": bool(pub.get("official")),
            "url": ds.get("url", ""),
        }
    root_dir.mkdir(parents=True, exist_ok=True)
    path = root_dir / "datasets.json"
    if path.exists():
        try:
            if json.loads(path.read_text()) == meta:
                return
        except Exception:
            pass
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_pep_ingest.py -v`
Expected: alle tests PASS (bestaande + nieuwe).

- [ ] **Step 5: Commit**

```bash
git add app/pep_ingest.py tests/test_pep_ingest.py
git commit -m "refactor: generalize OpenSanctions collection download (pep + sanctions)"
```

---

### Task 2: `app/sanctions_ingest.py` (nieuw)

**Files:**
- Create: `app/sanctions_ingest.py`
- Test: `tests/test_sanctions_ingest.py`

**Interfaces:**
- Produces:
  - `SANCTIONS_COLLECTION = "sanctions"`, `EXCLUDE_DATASETS = ("eu_fsf",)`
  - `default_root() -> Path` (env `SANCTIONS_DATA_DIR`, default `data/sanctions`)
  - `list_sanctions_datasets(index: dict) -> list[dict]`
  - `refresh_sanctions(root_dir, *, index=None, force=False, dry_run=False, limit=None, logger=None) -> dict`
- Consumes: `list_collection_datasets`, `refresh_collection` uit Task 1.

- [ ] **Step 1: Schrijf de falende test**

`tests/test_sanctions_ingest.py`:

```python
import hashlib
import json
from pathlib import Path

import pytest
import requests

from app import sanctions_ingest
from app.pep_ingest import make_resource  # noqa: F401  (alleen type-hint-gebruik elders)
```

Nee — `make_resource`/`make_source` leven in `tests/test_pep_ingest.py`, niet in de app. Definieer ze lokaal:

```python
def make_resource(url="https://data.opensanctions.org/artifacts/x/1/entities.ftm.json", checksum="abc", size=100):
    return {"name": "entities.ftm.json", "url": url, "checksum": checksum, "size": size, "mime_type": "application/json+ftm"}


def make_source(name, collections=("sanctions",), type_="source", version="v1", resources=None):
    return {
        "name": name, "type": type_, "collections": list(collections), "version": version,
        "resources": resources if resources is not None else [make_resource()],
    }


def make_index(datasets):
    return {"datasets": datasets}


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeStreamResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield from self._chunks


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("app.pep_ingest.DOWNLOAD_PAUSE", 0)


def test_default_root_env(monkeypatch):
    monkeypatch.delenv("SANCTIONS_DATA_DIR", raising=False)
    assert sanctions_ingest.default_root() == Path("data/sanctions")
    monkeypatch.setenv("SANCTIONS_DATA_DIR", "/data/sanctions")
    assert sanctions_ingest.default_root() == Path("/data/sanctions")


def test_list_sanctions_datasets_excludes_eu_fsf():
    datasets = [
        make_source("us_ofac_sdn"),
        make_source("nl_terrorism_list"),
        make_source("eu_fsf"),
        make_source("un_sc_sanctions"),
        make_source("gb_fcdo_sanctions"),
        make_source("only_pep", collections=("peps",)),
    ]
    names = [d["name"] for d in sanctions_ingest.list_sanctions_datasets(make_index(datasets))]
    assert names == ["gb_fcdo_sanctions", "nl_terrorism_list", "un_sc_sanctions", "us_ofac_sdn"]


def test_refresh_sanctions_full_run(tmp_path, monkeypatch):
    data = b"x"
    index = make_index([
        make_source("nl_terrorism_list", version="v1", resources=[make_resource(url="https://nl", checksum=sha1_bytes(data))]),
        make_source("eu_fsf", version="v1", resources=[make_resource(url="https://eu", checksum=sha1_bytes(data))]),
    ])
    logs = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeStreamResp([data]))
    manifest = sanctions_ingest.refresh_sanctions(tmp_path, index=index, logger=logs.append)
    assert manifest["collection"] == "sanctions"
    assert manifest["stats"] == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0, "bytes": 100}
    assert (tmp_path / "nl_terrorism_list" / "entities.ftm.json").read_bytes() == data
    assert not (tmp_path / "eu_fsf").exists()
    assert any("nl_terrorism_list" in line for line in logs)
```

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_sanctions_ingest.py -v`
Expected: FAIL met `ModuleNotFoundError: No module named 'app.sanctions_ingest'`.

- [ ] **Step 3: Implementeer de module**

`app/sanctions_ingest.py`:

```python
import os
from pathlib import Path

from .pep_ingest import list_collection_datasets, refresh_collection

SANCTIONS_COLLECTION = "sanctions"
EXCLUDE_DATASETS = ("eu_fsf",)


def default_root() -> Path:
    return Path(os.environ.get("SANCTIONS_DATA_DIR", "data/sanctions"))


def list_sanctions_datasets(index: dict) -> list[dict]:
    return list_collection_datasets(index, SANCTIONS_COLLECTION, exclude=EXCLUDE_DATASETS)


def refresh_sanctions(
    root_dir: Path,
    *,
    index: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    logger=None,
) -> dict:
    return refresh_collection(
        root_dir,
        SANCTIONS_COLLECTION,
        index=index,
        force=force,
        dry_run=dry_run,
        limit=limit,
        logger=logger,
        exclude=EXCLUDE_DATASETS,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_sanctions_ingest.py -v`
Expected: alle tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sanctions_ingest.py tests/test_sanctions_ingest.py
git commit -m "feat: OpenSanctions sanctions-collectie downloader (VN/OFAC/VK/NL, excl. eu_fsf)"
```

---

### Task 3: `scripts/update_sanctions.py` (nieuw)

**Files:**
- Create: `scripts/update_sanctions.py`
- Test: `tests/test_update_sanctions.py`

**Interfaces:**
- Produces: CLI met `--root`, `--force`, `--dry-run`, `--limit`, `--interval`, `--once`, `--log`; module-API `parse_args`, `run_once`, `run_loop`, `main`.
- Consumes: `sanctions_ingest.fetch_index` en `sanctions_ingest.refresh_sanctions` (Task 2).

- [ ] **Step 1: Schrijf de falende tests**

`tests/test_update_sanctions.py`:

```python
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
```

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_update_sanctions.py -v`
Expected: FAIL met `ModuleNotFoundError: No module named 'scripts.update_sanctions'`.

- [ ] **Step 3: Implementeer het script**

`scripts/update_sanctions.py` (spiegelt `scripts/update_pep.py`):

```python
import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sanctions_ingest

_STOP = {"flag": False}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OpenSanctions sanctie-lijsten downloaden (VN/OFAC/VK/NL)")
    parser.add_argument("--root", default=sanctions_ingest.default_root(), help=f"data-map (default: %(default)s)")
    parser.add_argument("--force", action="store_true", help="alles opnieuw downloaden, ook ongewijzigde")
    parser.add_argument("--dry-run", action="store_true", help="plan alleen tonen, niets downloaden")
    parser.add_argument("--limit", type=int, default=None, help="maximaal aantal bronnen (testen)")
    parser.add_argument("--interval", type=float, default=0, help="blijf draaien, update elke N uren (Docker)")
    parser.add_argument("--once", action="store_true", help="eenmalig draaien (default)")
    parser.add_argument("--log", default=None, help="schrijf logs ook naar dit bestand")
    return parser.parse_args(argv)


def _emit(args, text: str) -> None:
    print(text, flush=True)
    if args.log:
        with Path(args.log).open("a") as fh:
            fh.write(text + "\n")


def run_once(args) -> int:
    try:
        index = sanctions_ingest.fetch_index()
    except Exception as exc:
        print(f"FATAAL: index download mislukt: {exc}", file=sys.stderr, flush=True)
        return 1

    def log(msg: str) -> None:
        _emit(args, msg)

    manifest = sanctions_ingest.refresh_sanctions(
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

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_update_sanctions.py -v`
Expected: alle tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_sanctions.py tests/test_update_sanctions.py
git commit -m "feat: weekly OpenSanctions sanctions-collectie downloader script"
```

---

### Task 4: Zoekindex — bron `sanctie` + schema v4

**Files:**
- Modify: `app/search_index.py`
- Test: `tests/test_search_index.py`

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION = 4`
  - `_stream_ftm(db, root: Path, source: str) -> tuple[int, int]`
  - `_stream_pep(db, pep_root)` en `_stream_sanctions(db, sanctions_root)` (wrappers)
  - `build_index(db_path, eu_entities, pep_root, sanctions_root=None, *, newest_input_mtime=None) -> dict` (stats: `eu_count`, `pep_count`, `sanctions_count`, `total`, `source_count`)
  - `rebuild_index(db_path, eu_xml, pep_root, sanctions_root=None) -> dict`
  - `index_fresh(db_path, eu_xml, pep_root, sanctions_root=None) -> bool`
  - `ensure_index(db_path, eu_xml, pep_root, sanctions_root=None) -> dict`
  - `_newest_input_mtime(eu_xml, pep_root, sanctions_root=None) -> float`

- [ ] **Step 1: Pas de bestaande test aan (verwachte stats)**

In `tests/test_search_index.py`, wijzig `test_load_stats_from_meta` (regel 361-369) zodat de exacte dict-vergelijking de nieuwe key bevat:

```python
def test_load_stats_from_meta(tmp_path):
    write_ftm(tmp_path, "ds1", [pep_person()])
    write_ftm(tmp_path, "ds2", [
        {"id": "X1", "caption": "X", "schema": "Person", "target": True, "datasets": ["ds2"], "properties": {"name": ["X"]}},
    ])
    stats = build_index(tmp_path / "search.sqlite", [eu_entity()], tmp_path)
    assert stats == {"eu_count": 1, "pep_count": 2, "sanctions_count": 0, "total": 3, "source_count": 2}
    loaded = load_stats(_open(tmp_path / "search.sqlite"))
    assert loaded == stats
```

- [ ] **Step 2: Schrijf de falende sanctie-tests**

Voeg toe aan `tests/test_search_index.py`:

```python
def test_stream_sanctions_source(tmp_path):
    pep_root = tmp_path / "pep"
    sanc_root = tmp_path / "sanc"
    write_ftm(pep_root, "ds_pep", [
        {"id": "P1", "caption": "PEPPER", "schema": "Person", "target": True, "datasets": ["ds_pep"], "properties": {"name": ["PEPPER"]}},
    ])
    write_ftm(sanc_root, "us_ofac_sdn", [
        {"id": "OFAC-1", "caption": "JOHN DOE", "schema": "Person", "target": True, "datasets": ["us_ofac_sdn"],
         "properties": {"name": ["JOHN DOE"], "citizenship": ["IR"]}},
        {"id": "OFAC-2", "caption": "ACME", "schema": "Company", "target": True, "datasets": ["us_ofac_sdn"],
         "properties": {"name": ["ACME"]}},
    ])
    stats = build_index(tmp_path / "search.sqlite", [], pep_root, sanc_root)
    assert stats == {"eu_count": 0, "pep_count": 1, "sanctions_count": 2, "total": 3, "source_count": 2}
    db = _open(tmp_path / "search.sqlite")
    rows = db.execute("SELECT id, source FROM entities ORDER BY source, id").fetchall()
    assert [tuple(r) for r in rows] == [("P1", "pep"), ("OFAC-1", "sanctie"), ("OFAC-2", "sanctie")]


def test_search_returns_sanctions_result(tmp_path):
    pep_root = tmp_path / "pep"
    sanc_root = tmp_path / "sanc"
    write_ftm(sanc_root, "us_ofac_sdn", [
        {"id": "OFAC-1", "caption": "JOHN DOE", "schema": "Person", "target": True, "datasets": ["us_ofac_sdn"],
         "properties": {"name": ["JOHN DOE"], "citizenship": ["IR"]}},
    ])
    build_index(tmp_path / "search.sqlite", [], pep_root, sanc_root)
    db = _open(tmp_path / "search.sqlite")
    results = search(db, "JOHN DOE")
    assert results and results[0]["entity"]["source"] == "sanctie"
    assert results[0]["entity"]["datasets"] == ["us_ofac_sdn"]
```

- [ ] **Step 3: Run test om het falen te zien**

Run: `python -m pytest tests/test_search_index.py -k "sanctions or load_stats" -v`
Expected: FAIL (`build_index` accepteert `sanctions_root` nog niet / verwachte stats kloppen niet).

- [ ] **Step 4: Implementeer in `app/search_index.py`**

Wijzig `SCHEMA_VERSION` (regel 16): `SCHEMA_VERSION = 4`.

Vervang `_stream_pep` (regels 130-203) door de generieke `_stream_ftm` + wrappers:

```python
def _stream_ftm(db, root: Path, source: str) -> tuple[int, int]:
    count = 0
    sources: set[str] = set()
    pos_buf: list[tuple] = []
    occ_buf: list[tuple] = []
    ent_buf: list[tuple] = []

    def flush() -> None:
        nonlocal pos_buf, occ_buf, ent_buf
        if pos_buf:
            db.executemany("INSERT OR REPLACE INTO _positions (id, caption) VALUES (?,?)", pos_buf)
            pos_buf = []
        if occ_buf:
            db.executemany("INSERT INTO _occupancies (holder, post, status, start, end) VALUES (?,?,?,?,?)", occ_buf)
            occ_buf = []
        if ent_buf:
            db.executemany(INSERT_SQL, ent_buf)
            ent_buf = []

    for ftm in sorted(root.glob(f"*/{FTM_FILENAME}")):
        dataset = ftm.parent.name
        with ftm.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                schema = data.get("schema")
                if schema == "Position":
                    pos_buf.append((data.get("id", ""), data.get("caption") or ""))
                elif schema == "Occupancy":
                    props = data.get("properties") or {}
                    occ_buf.append((
                        (props.get("holder") or [""])[0],
                        (props.get("post") or [""])[0],
                        (props.get("status") or [""])[0],
                        (props.get("startDate") or [""])[0],
                        (props.get("endDate") or [""])[0],
                    ))
                elif schema in ("Person", "Company") and data.get("target"):
                    props = data.get("properties") or {}
                    names = list((props.get("name") or []) + (props.get("alias") or []))
                    caption = data.get("caption") or ""
                    if caption and caption not in names:
                        names.insert(0, caption)
                    folded = " ".join(tokens(" ".join(names)))
                    ent_buf.append((
                        source,
                        data.get("id", ""),
                        caption,
                        schema,
                        json.dumps(names, ensure_ascii=False),
                        folded,
                        json.dumps(props.get("birthDate") or [], ensure_ascii=False),
                        json.dumps(props.get("birthPlace") or [], ensure_ascii=False),
                        json.dumps(props.get("citizenship") or [], ensure_ascii=False),
                        json.dumps(props.get("political") or [], ensure_ascii=False),
                        json.dumps(props.get("topics") or [], ensure_ascii=False),
                        "[]",
                        json.dumps(data.get("datasets") or [], ensure_ascii=False),
                        "",
                        None,
                    ))
                    count += 1
                    sources.add(dataset)
                if len(pos_buf) + len(occ_buf) + len(ent_buf) >= 20000:
                    flush()
    flush()
    return count, len(sources)


def _stream_pep(db, pep_root: Path) -> tuple[int, int]:
    return _stream_ftm(db, pep_root, "pep")


def _stream_sanctions(db, sanctions_root: Path) -> tuple[int, int]:
    return _stream_ftm(db, sanctions_root, "sanctie")
```

Vervang `_write_meta` (regels 229-238):

```python
def _write_meta(db, eu_count: int, pep_count: int, sanctions_count: int, source_count: int, newest_input_mtime: float) -> None:
    db.executemany(
        "INSERT INTO meta (key, value) VALUES (?,?)",
        [
            ("eu_count", str(eu_count)),
            ("pep_count", str(pep_count)),
            ("sanctions_count", str(sanctions_count)),
            ("source_count", str(source_count)),
            ("newest_input_mtime", str(newest_input_mtime)),
        ],
    )
```

Vervang `build_index` (regels 241-271):

```python
def build_index(
    db_path: Path,
    eu_entities: list[dict] | None,
    pep_root: Path,
    sanctions_root: Path | None = None,
    *,
    newest_input_mtime: float | None = None,
) -> dict:
    eu_entities = eu_entities or []
    if newest_input_mtime is None:
        newest_input_mtime = _newest_ftm_mtime(pep_root, sanctions_root)
    new_path = db_path.with_suffix(db_path.suffix + ".new")
    new_path.unlink(missing_ok=True)
    db = None
    try:
        db = _open(new_path)
        db.execute("PRAGMA page_size = 4096")
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.execute("PRAGMA cache_size = -64000")
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        eu_count = _insert_eu(db, eu_entities)
        pep_count, pep_sources = _stream_pep(db, pep_root)
        sanctions_count = 0
        sanc_sources = 0
        if sanctions_root is not None and sanctions_root.exists():
            sanctions_count, sanc_sources = _stream_sanctions(db, sanctions_root)
        _fill_positions(db)
        _fill_fts(db)
        _write_meta(db, eu_count, pep_count, sanctions_count, pep_sources + sanc_sources, newest_input_mtime)
        db.execute("DROP TABLE _occupancies")
        db.execute("DROP TABLE _positions")
        db.commit()
        db.close()
        db = None
        new_path.replace(db_path)
    finally:
        if db is not None:
            db.close()
        new_path.unlink(missing_ok=True)
    return {
        "eu_count": eu_count,
        "pep_count": pep_count,
        "sanctions_count": sanctions_count,
        "total": eu_count + pep_count + sanctions_count,
        "source_count": pep_sources + sanc_sources,
    }
```

Vervang de mtime-helpers (regels 379-393):

```python
def _newest_root_mtime(root: Path) -> float:
    newest = 0.0
    datasets = root / "datasets.json"
    if datasets.exists():
        newest = max(newest, datasets.stat().st_mtime)
    for ftm in root.glob(f"*/{FTM_FILENAME}"):
        newest = max(newest, ftm.stat().st_mtime)
    return newest


def _newest_ftm_mtime(pep_root: Path, sanctions_root: Path | None = None) -> float:
    newest = _newest_root_mtime(pep_root)
    if sanctions_root is not None:
        newest = max(newest, _newest_root_mtime(sanctions_root))
    return newest


def _newest_input_mtime(eu_xml: Path, pep_root: Path, sanctions_root: Path | None = None) -> float:
    newest = _newest_ftm_mtime(pep_root, sanctions_root)
    if eu_xml.exists():
        newest = max(newest, eu_xml.stat().st_mtime)
    return newest
```

Wijzig `index_fresh` (regels 396-412):

```python
def index_fresh(db_path: Path, eu_xml: Path, pep_root: Path, sanctions_root: Path | None = None) -> bool:
    if not db_path.exists():
        return False
    db = None
    try:
        db = _open(db_path)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute("SELECT value FROM meta WHERE key = 'newest_input_mtime'").fetchone()
        acknowledged = float(row[0]) if row is not None else db_path.stat().st_mtime
    except Exception:
        return False
    finally:
        if db is not None:
            db.close()
    if acknowledged < _newest_input_mtime(eu_xml, pep_root, sanctions_root):
        return False
    return version >= SCHEMA_VERSION
```

Wijzig `load_stats` (regels 415-424):

```python
def load_stats(db) -> dict:
    row = dict(db.execute("SELECT key, value FROM meta").fetchall())
    eu = int(row.get("eu_count", 0))
    pep = int(row.get("pep_count", 0))
    sanc = int(row.get("sanctions_count", 0))
    return {
        "eu_count": eu,
        "pep_count": pep,
        "sanctions_count": sanc,
        "total": eu + pep + sanc,
        "source_count": int(row.get("source_count", 0)),
    }
```

Wijzig `ensure_index` (regels 427-438):

```python
def ensure_index(db_path: Path, eu_xml: Path, pep_root: Path, sanctions_root: Path | None = None) -> dict:
    if not index_fresh(db_path, eu_xml, pep_root, sanctions_root):
        return {"db": None, "ready": False, "stats": None}
    db = None
    try:
        db = _open(db_path)
        stats = load_stats(db)
    except Exception:
        if db is not None:
            db.close()
        return {"db": None, "ready": False, "stats": None}
    return {"db": db, "ready": True, "stats": stats}
```

Wijzig `rebuild_index` (regels 441-443):

```python
def rebuild_index(db_path: Path, eu_xml: Path, pep_root: Path, sanctions_root: Path | None = None) -> dict:
    entities = ingest.parse_export(eu_xml.read_bytes()) if eu_xml.exists() else []
    return build_index(
        db_path,
        entities,
        pep_root,
        sanctions_root,
        newest_input_mtime=_newest_input_mtime(eu_xml, pep_root, sanctions_root),
    )
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_search_index.py -v`
Expected: alle tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/search_index.py tests/test_search_index.py
git commit -m "feat: zoekindex bron 'sanctie' (schema v4), sanctions_root door alle index-functies"
```

---

### Task 5: `rebuild.py` + main.py-rebuild-plumbing

**Files:**
- Modify: `app/rebuild.py`
- Modify: `app/main.py` (helpers `_run_rebuild_subprocess`, `_run_rebuild`, `_build_index`)
- Test: `tests/test_rebuild.py`

**Interfaces:**
- Consumes: `rebuild_index(db_path, eu_xml, pep_root, sanctions_root)` uit Task 4.
- Produces: `_run_rebuild(db_path, eu_xml, pep_root, sanctions_root)`, `_run_rebuild_subprocess(db_path, eu_xml, pep_root, sanctions_root)`, `_build_index(state, db_path, eu_xml, pep_root, sanctions_root)`.

- [ ] **Step 1: Schrijf de falende test**

Voeg toe aan `tests/test_rebuild.py`:

```python
def test_rebuild_with_sanctions_root(tmp_path):
    db_path = tmp_path / "search.sqlite"
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    (tmp_path / "sanc" / "us_ofac_sdn").mkdir(parents=True)
    (tmp_path / "sanc" / "us_ofac_sdn" / "entities.ftm.json").write_text(
        json.dumps({"id": "OFAC-1", "caption": "JOHN DOE", "schema": "Person", "target": True,
                    "datasets": ["us_ofac_sdn"], "properties": {"name": ["JOHN DOE"]}}) + "\n"
    )
    rc = rebuild_main(["--db", str(db_path), "--eu-xml", str(eu_xml), "--pep-root", str(tmp_path / "pep"),
                       "--sanctions-root", str(tmp_path / "sanc")])
    assert rc == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["sanctions_count"] == 1
    assert db_path.exists()
```

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_rebuild.py::test_rebuild_with_sanctions_root -v`
Expected: FAIL (`rebuild_main` kent `--sanctions-root` niet → `argparse` error).

- [ ] **Step 3: Implementeer**

`app/rebuild.py` — wijzig parser en aanroep:

```python
    parser.add_argument("--pep-root", required=True, type=Path)
    parser.add_argument("--sanctions-root", type=Path, default=None)
    args = parser.parse_args(argv)
    stats = rebuild_index(args.db, args.eu_xml, args.pep_root, args.sanctions_root)
```

`app/main.py` — wijzig de drie helpers:

```python
def _run_rebuild_subprocess(db_path: Path, eu_xml: Path, pep_root: Path, sanctions_root: Path) -> dict:
    cmd = [sys.executable, "-m", "app.rebuild",
           "--db", str(db_path), "--eu-xml", str(eu_xml), "--pep-root", str(pep_root),
           "--sanctions-root", str(sanctions_root)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=REBUILD_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = (exc.stderr or "").strip() or "geen stderr"
        raise RuntimeError(
            f"index rebuild subproces timeout na {REBUILD_SUBPROCESS_TIMEOUT}s: {stderr[-500:]}"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"index rebuild subproces exit {proc.returncode}: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def _run_rebuild(db_path: Path, eu_xml: Path, pep_root: Path, sanctions_root: Path) -> dict:
    if os.environ.get("PEP_INDEX_SUBPROCESS", "").strip().lower() in ("1", "true", "yes"):
        return _run_rebuild_subprocess(db_path, eu_xml, pep_root, sanctions_root)
    return search_index.rebuild_index(db_path, eu_xml, pep_root, sanctions_root)


def _build_index(state: dict, db_path: Path, eu_xml: Path, pep_root: Path, sanctions_root: Path) -> None:
    try:
        state["index_stats"] = _run_rebuild(db_path, eu_xml, pep_root, sanctions_root)
        state["index_status"] = "ready"
        state["index_error"] = None
    except Exception as exc:
        logger.exception("Index-rebuild mislukt")
        state["index_status"] = "error"
        state["index_error"] = f"Index-rebuild mislukt: {exc}"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_rebuild.py -v`
Expected: alle tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/rebuild.py app/main.py tests/test_rebuild.py
git commit -m "feat: sanctions_root door rebuild-subproces en app-rebuild-plumbing"
```

---

### Task 6: Risicolanden-module + data + script

**Files:**
- Create: `app/risk_countries.py`
- Create: `data/risk_countries.json`
- Create: `scripts/update_risk_countries.py`
- Test: `tests/test_risk_countries.py`

**Interfaces:**
- Produces:
  - `default_path() -> Path` (env `RISK_COUNTRIES`, default `data/risk_countries.json`)
  - `load_risk_countries(path: Path | None = None) -> dict` met keys `version`, `updated_at`, `fatf_blacklist`, `fatf_greylist`, `eu_high_risk` (alle codes upper)
  - `risk_flags(country_codes: list[str], data: dict | None = None) -> list[dict]` → `[{"code": "IR", "lists": ["fatf_blacklist", ...]}]`
  - `validate(data: dict) -> list[str]` (foutmeldingen; leeg = ok)

- [ ] **Step 1: Schrijf de falende tests**

`tests/test_risk_countries.py`:

```python
import json

import pytest

from app import risk_countries


@pytest.fixture(autouse=True)
def clear_cache():
    risk_countries.load_risk_countries.cache_clear()
    yield
    risk_countries.load_risk_countries.cache_clear()


def write(tmp_path, name, **over):
    data = {
        "version": "v1",
        "updated_at": "t",
        "fatf_blacklist": ["IR"],
        "fatf_greylist": ["MM"],
        "eu_high_risk": [],
    }
    data.update(over)
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_default_path_env(monkeypatch):
    monkeypatch.delenv("RISK_COUNTRIES", raising=False)
    assert risk_countries.default_path() == __import__("pathlib").Path("data/risk_countries.json")
    monkeypatch.setenv("RISK_COUNTRIES", "/tmp/risk.json")
    assert risk_countries.default_path() == __import__("pathlib").Path("/tmp/risk.json")


def test_load_missing_returns_empty(tmp_path):
    data = risk_countries.load_risk_countries(tmp_path / "none.json")
    assert data["fatf_blacklist"] == []
    assert data["version"] == ""


def test_load_normalises_upper(tmp_path):
    path = write(tmp_path, "risk.json", fatf_blacklist=["ir", "KP"])
    data = risk_countries.load_risk_countries(path)
    assert data["fatf_blacklist"] == ["IR", "KP"]


def test_risk_flags(tmp_path):
    path = write(tmp_path, "risk.json", fatf_blacklist=["IR", "KP"], eu_high_risk=["IR"])
    data = risk_countries.load_risk_countries(path)
    flags = risk_countries.risk_flags(["IR", "NL", "kp"], data=data)
    assert flags == [
        {"code": "IR", "lists": ["fatf_blacklist", "eu_high_risk"]},
        {"code": "KP", "lists": ["fatf_blacklist"]},
    ]


def test_risk_flags_empty_codes():
    assert risk_countries.risk_flags([]) == []


def test_validate_ok():
    assert risk_countries.validate({"fatf_blacklist": ["IR"], "fatf_greylist": [], "eu_high_risk": []}) == []


def test_validate_errors():
    errs = risk_countries.validate({
        "fatf_blacklist": ["I", "IR", "IR", 5],
        "fatf_greylist": "niet-lijst",
        "eu_high_risk": [],
    })
    assert any("ISO2" in e for e in errs)
    assert any("duplicaat" in e for e in errs)
    assert any("moet een lijst" in e for e in errs)
```

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_risk_countries.py -v`
Expected: FAIL met `ModuleNotFoundError: No module named 'app.risk_countries'`.

- [ ] **Step 3: Implementeer de module**

`app/risk_countries.py`:

```python
import json
import os
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path("data/risk_countries.json")
_ISO2 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

_EMPTY = {"version": "", "updated_at": "", "fatf_blacklist": [], "fatf_greylist": [], "eu_high_risk": []}


def default_path() -> Path:
    env = os.environ.get("RISK_COUNTRIES")
    return Path(env) if env else DEFAULT_PATH


@lru_cache(maxsize=1)
def load_risk_countries(path: Path | None = None) -> dict:
    path = path or default_path()
    try:
        data = json.loads(path.read_text())
    except Exception:
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    return {
        "version": str(data.get("version", "")),
        "updated_at": str(data.get("updated_at", "")),
        "fatf_blacklist": [str(c).upper() for c in (data.get("fatf_blacklist") or [])],
        "fatf_greylist": [str(c).upper() for c in (data.get("fatf_greylist") or [])],
        "eu_high_risk": [str(c).upper() for c in (data.get("eu_high_risk") or [])],
    }


def risk_flags(country_codes: list[str], data: dict | None = None) -> list[dict]:
    data = data or load_risk_countries()
    lookup = {
        "fatf_blacklist": set(data["fatf_blacklist"]),
        "fatf_greylist": set(data["fatf_greylist"]),
        "eu_high_risk": set(data["eu_high_risk"]),
    }
    flags = []
    for code in country_codes:
        if not code:
            continue
        c = code.strip().upper()
        lists = [name for name, codes in lookup.items() if c in codes]
        if lists:
            flags.append({"code": c, "lists": lists})
    return flags


def validate(data: dict) -> list[str]:
    errors = []
    for key in ("fatf_blacklist", "fatf_greylist", "eu_high_risk"):
        values = data.get(key) or []
        if not isinstance(values, list):
            errors.append(f"{key}: moet een lijst zijn")
            continue
        seen = set()
        for code in values:
            if not isinstance(code, str) or len(code) != 2 or any(ch not in _ISO2 for ch in code.upper()):
                errors.append(f"{key}: ongeldige ISO2-code {code!r}")
            elif code.upper() in seen:
                errors.append(f"{key}: duplicaat {code.upper()}")
            else:
                seen.add(code.upper())
    return errors
```

- [ ] **Step 4: Maak de starter-data aan**

`data/risk_countries.json` (handmatig bij te houden; de zwarte lijst is de langlopende FATF high-risk set):

```json
{
  "version": "2026-08",
  "updated_at": "2026-08-03T00:00:00+00:00",
  "fatf_blacklist": ["KP", "IR", "MM"],
  "fatf_greylist": [],
  "eu_high_risk": []
}
```

- [ ] **Step 5: Implementeer het validatie-script**

`scripts/update_risk_countries.py`:

```python
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import risk_countries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Risicolandenlijst (FATF/EU) valideren en bijwerken")
    parser.add_argument("--path", default=risk_countries.default_path(), help="pad naar de JSON (default: %(default)s)")
    args = parser.parse_args(argv)
    path = Path(args.path)
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        print(f"FATAAL: kan {path} niet lezen: {exc}", file=sys.stderr)
        return 1
    errors = risk_countries.validate(data)
    if errors:
        for error in errors:
            print(f"FOUT: {error}", file=sys.stderr)
        return 1
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    print(f"OK: {path} gevalideerd en bijgewerkt (updated_at={data['updated_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_risk_countries.py -v`
Expected: alle tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/risk_countries.py data/risk_countries.json scripts/update_risk_countries.py tests/test_risk_countries.py
git commit -m "feat: FATF/EU-risicolanden-lijst, loader, validatie-script"
```

---

### Task 7: `main.py` — wiring, serialisatie, status, export-payload

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `sanctions_ingest` (Task 2), `search_index` (Task 4), `risk_countries` (Task 6).
- Produces:
  - `default_sanctions_root() -> Path`, `SANCTIONS_ROOT`
  - `_sanctions_enabled(sanctions_root: Path) -> bool`
  - `_serialize_sanctions_result(result: dict, datasets_meta: dict) -> dict` (source `'sanctie'`, keys `sanctie`, `eu`, `pep`, `opensanctions`, `risk_countries`)
  - Resultaat-shape: elk resultaat (eu/pep/sanctie) krijgt `risk_countries: [...]`

- [ ] **Step 1: Schrijf de falende test**

Voeg toe aan `tests/test_main.py`:

```python
def _write_ftm(root, dataset, entities):
    path = root / dataset / "entities.ftm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in entities:
            fh.write(json.dumps(e) + "\n")


def test_search_returns_sanctions_result(tmp_path, monkeypatch):
    import app.risk_countries as rc

    pep_root = tmp_path / "pep"
    sanc_root = tmp_path / "sanc"
    pep_root.mkdir()
    _write_ftm(sanc_root, "us_ofac_sdn", [
        {"id": "OFAC-1", "caption": "JOHN DOE", "schema": "Person", "target": True, "datasets": ["us_ofac_sdn"],
         "properties": {"name": ["JOHN DOE"], "citizenship": ["IR"]}},
    ])
    (sanc_root / "datasets.json").write_text(json.dumps({
        "us_ofac_sdn": {"title": "OFAC SDN List", "publisher": "US Treasury", "country": "us", "official": True, "url": "https://example.org/ofac"},
    }))
    monkeypatch.setenv("RISK_COUNTRIES", str(tmp_path / "risk.json"))
    (tmp_path / "risk.json").write_text(json.dumps({
        "version": "2026-08", "updated_at": "t", "fatf_blacklist": ["IR"], "fatf_greylist": [], "eu_high_risk": [],
    }))
    rc.load_risk_countries.cache_clear()
    db_path = tmp_path / "search.sqlite"
    search_index.rebuild_index(db_path, tmp_path / "eu.xml", pep_root, sanc_root)
    client = TestClient(create_app(entities=[], pep_root=pep_root, sanctions_root=sanc_root, search_db=db_path))
    data = client.get("/api/search", params={"name": "JOHN DOE"}).json()
    sources = {r["source"] for r in data["results"]}
    assert "sanctie" in sources
    first = next(r for r in data["results"] if r["source"] == "sanctie")
    assert first["sanctie"]["datasets"][0]["title"] == "OFAC SDN List"
    assert first["risk_countries"] == [{"code": "IR", "lists": ["fatf_blacklist"]}]


def test_status_shows_sanctions_and_risk(tmp_path, monkeypatch):
    import app.risk_countries as rc

    monkeypatch.setenv("RISK_COUNTRIES", str(tmp_path / "risk.json"))
    (tmp_path / "risk.json").write_text(json.dumps({
        "version": "2026-08", "updated_at": "t", "fatf_blacklist": ["IR"], "fatf_greylist": [], "eu_high_risk": [],
    }))
    rc.load_risk_countries.cache_clear()
    client = TestClient(create_app(entities=ENTITIES, sanctions_root=tmp_path / "sanc"))
    data = client.get("/api/status").json()
    assert data["risk"]["version"] == "2026-08"
    assert data["risk"]["counts"]["fatf_blacklist"] == 1
    assert "sanctions_count" in data["index"]
```

Note: `test_search_returns_sanctions_result` heeft `import json` nodig bovenaan `test_main.py`; voeg `import json` toe als die er niet staat (controleer de import-regels bovenaan het bestand; momenteel staan er geen `json`/`search_index`-imports — voeg ze toe).

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_main.py -k "sanctions" -v`
Expected: FAIL (`_serialize_sanctions_result` ontbreekt, `sanctions_root`-param onbekend).

- [ ] **Step 3: Implementeer in `app/main.py`**

Wijzig de imports (regel 21-24):

```python
from . import audit
from . import auth
from . import batch
from . import eu_ingest, ingest, matcher, opensanctions
from . import pep_ingest
from . import risk_countries
from . import search_index
from . import watchlist
```

Voeg na `PEP_ROOT` (regel 38) toe:

```python
def default_sanctions_root() -> Path:
    return Path(os.environ.get("SANCTIONS_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "sanctions")))


SANCTIONS_ROOT = default_sanctions_root()
```

Voeg na `_pep_enabled` (regel 151) toe:

```python
SANCTIONS_INDEX_ENV = "SANCTIONS_INDEX_ENABLED"


def _sanctions_enabled(sanctions_root: Path) -> bool:
    env = os.environ.get(SANCTIONS_INDEX_ENV)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no")
    return sanctions_root.exists()
```

Wijzig `_serialize_eu_result` (regels 66-94): voeg vlak voor de `"eu": {`-key een `risk_countries`-regel toe:

```python
        "risk_countries": risk_countries.risk_flags(
            [c["iso2"] for c in raw.get("citizenships", []) if c.get("iso2")]
        ),
```

Wijzig `_serialize_eu_result_from_dict` (regels 97-123): zelfde toevoeging voor de dict-variant:

```python
        "risk_countries": risk_countries.risk_flags(
            [c["iso2"] for c in entity["citizenships"] if c.get("iso2")]
        ),
```

Wijzig `_serialize_pep_result` (regels 154-187): voeg `risk_countries` toe aan de return:

```python
        "risk_countries": risk_countries.risk_flags(
            [c for c in entity.get("citizenships", []) if isinstance(c, str)]
        ),
```

Voeg na `_serialize_pep_result` een nieuwe functie toe:

```python
def _serialize_sanctions_result(result: dict, datasets_meta: dict) -> dict:
    entity = result["entity"]
    datasets = []
    for ds_id in entity.get("datasets", []):
        meta = datasets_meta.get(ds_id, {})
        datasets.append({
            "id": ds_id,
            "title": meta.get("title") or ds_id,
            "country": meta.get("country", ""),
            "url": f"https://www.opensanctions.org/datasets/{ds_id}/",
        })
    return {
        "source": "sanctie",
        "score": result["score"],
        "entity": {
            "name": entity.get("caption", ""),
            "schema": entity.get("schema", ""),
            "birth_dates": entity.get("birth_dates", []),
            "birth_places": entity.get("birth_places", []),
            "citizenships": entity.get("citizenships", []),
            "political": entity.get("political", []),
            "topics": entity.get("topics", []),
            "positions": entity.get("positions") or [],
        },
        "sanctie": {
            "id": entity.get("id", ""),
            "url": f"https://opensanctions.org/entities/{entity.get('id', '')}",
            "datasets": datasets,
            "matched_name": result["matched_name"],
            "details": result["details"],
        },
        "risk_countries": risk_countries.risk_flags(
            [c for c in entity.get("citizenships", []) if isinstance(c, str)]
        ),
        "eu": None,
        "pep": None,
        "opensanctions": None,
    }
```

Wijzig `_to_watchlist_match` (regels 190-219): voeg na de `elif source == "pep":`-branch een sanctie-branch toe:

```python
    elif source == "sanctie":
        match_id = (result.get("sanctie") or {}).get("id") or ""
        datasets = [d.get("id") for d in ((result.get("sanctie") or {}).get("datasets") or []) if d.get("id")]
        naam = entity.get("name") or ""
```

Wijzig `create_app` (regels 270-322): voeg de parameter en wiring toe:

```python
def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    eu_root: Path | None = None,
    static_dir: Path | None = None,
    pep_root: Path | None = None,
    sanctions_root: Path | None = None,
    pep_sync: bool | None = None,
    search_db: Path | None = None,
) -> FastAPI:
    eu_root = eu_root or default_eu_root()
    static_dir = static_dir or STATIC_DIR
    pep_root = pep_root or default_pep_root()
    sanctions_root = sanctions_root or default_sanctions_root()
```

en direct daarna (regel 304):

```python
    enabled = _pep_enabled(pep_root) or _sanctions_enabled(sanctions_root) or eu_xml.exists()
```

en (regel 306):

```python
    datasets_meta = _load_datasets_meta(pep_root)
    datasets_meta.update(_load_datasets_meta(sanctions_root))
```

en (regel 309):

```python
            result = search_index.ensure_index(db_path, eu_xml, pep_root, sanctions_root)
```

en (regels 319, 322, 379, 424) — alle vier de `_build_index(...)`-aanroepen krijgen `sanctions_root` als 5e argument, bv.:

```python
            _build_index(state, db_path, eu_xml, pep_root, sanctions_root)
            threading.Thread(target=_build_index, args=(state, db_path, eu_xml, pep_root, sanctions_root), daemon=True).start()
```

Wijzig `_status` (regels 374-405):

```python
        risk_data = risk_countries.load_risk_countries()
```

en de `data_version`-regel (393):

```python
            "data_version": round(search_index._newest_input_mtime(eu_xml, pep_root, sanctions_root), 3),
```

en het `index`-dict (regels 397-404):

```python
            "index": {
                "enabled": state["index_status"] != "disabled",
                "status": state["index_status"],
                "eu_count": stats.get("eu_count", 0),
                "pep_count": stats.get("pep_count", 0),
                "sanctions_count": stats.get("sanctions_count", 0),
                "source_count": stats.get("source_count", 0),
                "error": state["index_error"],
            },
            "risk": {
                "version": risk_data["version"],
                "counts": {
                    "fatf_blacklist": len(risk_data["fatf_blacklist"]),
                    "fatf_greylist": len(risk_data["fatf_greylist"]),
                    "eu_high_risk": len(risk_data["eu_high_risk"]),
                },
            },
```

Wijzig de statuscheck in `_status` (regel 375):

```python
        if state["index_status"] == "ready" and not search_index.index_fresh(state["db_path"], eu_xml, pep_root, sanctions_root):
```

Wijzig `run_search` (regels 436-440):

```python
                for r in search_index.search(db, query.name, query.birth_year, query.nationality, query.birth_place, query.entity_type):
                    if r["entity"]["source"] == "eu":
                        results.append(_serialize_eu_result(r, query.name))
                    elif r["entity"]["source"] == "pep":
                        results.append(_serialize_pep_result(r, datasets_meta))
                    else:
                        results.append(_serialize_sanctions_result(r, datasets_meta))
```

Wijzig `search_export`-payload (regel 713):

```python
        risk_data = risk_countries.load_risk_countries()
        payload = {
            "query": {"name": query.name, "birth_year": query.birth_year, "nationality": query.nationality, "birth_place": query.birth_place, "entity_type": query.entity_type},
            "results": results, "warnings": warnings,
            "meta": state["meta"], "pep_meta": pep_ingest.load_pep_manifest(pep_root),
            "sanctions_meta": pep_ingest.load_pep_manifest(sanctions_root),
            "risk_meta": {"version": risk_data["version"], "updated_at": risk_data["updated_at"]},
            "version": os.environ.get("APP_VERSION", "dev"),
            "author": author, "generated_at": generated,
            "threshold": matcher.THRESHOLD, "max_results": matcher.MAX_RESULTS,
        }
```

Wijzig `_batch_report` (regels 789-804) zodat het PDF-rapport de versies meekrijgt:

```python
        results = batch.get_results(batch_db, batch_id)
        if format == "csv":
            content = render_batch_csv(job, results).encode("utf-8-sig")
            media_type = "text/csv; charset=utf-8"
            extension = "csv"
        else:
            risk_data = risk_countries.load_risk_countries()
            report_meta = dict(state["meta"])
            report_meta["risk_version"] = risk_data["version"]
            report_meta["sanctions_updated_at"] = (pep_ingest.load_pep_manifest(sanctions_root) or {}).get("updated_at", "")
            content = render_batch_pdf(job, results, report_meta)
            media_type = "application/pdf"
            extension = "pdf"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_main.py -v`
Expected: alle tests PASS. Mocht een bestaande test een exacte resultaat-shape asserten, pas die dan aan op de nieuwe `risk_countries`-key.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: sanctie-resultaten en risicoland-flags in API, status en export-payload"
```

---

### Task 8: Export — sanctie-bron + risicoland in PDF/CSV/XLSX

**Files:**
- Modify: `app/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: resultaat-shape uit Task 7 (`source: 'sanctie'`, `sanctie`-key, `risk_countries`), payload-keys `sanctions_meta`, `risk_meta`.

- [ ] **Step 1: Schrijf de falende test**

Voeg toe aan `tests/test_export.py`:

```python
def _sanctions_payload(**over):
    payload = _payload()
    payload["results"] = [{
        "source": "sanctie",
        "score": 95,
        "entity": {"name": "JOHN DOE", "schema": "Person", "birth_dates": [], "birth_places": [], "citizenships": ["IR"], "topics": ["sanction"]},
        "sanctie": {"id": "OFAC-1", "url": "https://opensanctions.org/entities/OFAC-1",
                    "datasets": [{"id": "us_ofac_sdn", "title": "OFAC SDN List", "country": "us", "url": "https://www.opensanctions.org/datasets/us_ofac_sdn/"}],
                    "matched_name": "JOHN DOE",
                    "details": [{"feature": "naam", "score": 95, "label": "Naam 95% (via \"JOHN DOE\")"}]},
        "risk_countries": [{"code": "IR", "lists": ["fatf_blacklist"]}],
        "eu": None, "pep": None, "opensanctions": None,
    }]
    payload.update(over)
    return payload


def test_export_csv_includes_sanctions_source():
    rows = _export_rows(_sanctions_payload()["results"])
    row = rows[0]
    assert row[2] == "Sancties"
    assert "us_ofac_sdn" in row[3]
    assert "Risicoland IR" in row[4]


def test_export_pdf_includes_sanctions_and_risk():
    data = render_search_pdf(_sanctions_payload())
    text = _decoded_text(data).decode("latin-1", "replace")
    assert "Sancties (int.)" in text
    assert "OFAC SDN List" in text
    assert "Risicoland: IR" in text


def test_export_pdf_dataversies_meta():
    payload = _payload()
    payload["sanctions_meta"] = {"updated_at": "2026-08-03T09:00:00+00:00"}
    payload["risk_meta"] = {"version": "2026-08", "updated_at": "t"}
    data = render_search_pdf(payload)
    text = _decoded_text(data).decode("latin-1", "replace")
    assert "Sancties-update" in text
    assert "Risicolanden-versie" in text
```

- [ ] **Step 2: Run test om het falen te zien**

Run: `python -m pytest tests/test_export.py -k "sanctions or risk or dataversies" -v`
Expected: FAIL (`Sancties` label/bronnen ontbreken).

- [ ] **Step 3: Implementeer in `app/export.py`**

Wijzig `_result_paragraphs` (regel 35):

```python
    source_label = {"eu": "EU sanctielijst", "pep": "PEP", "sanctie": "Sancties (int.)", "opensanctions": "OpenSanctions"}.get(source, source)
```

Voeg na de `opensanctions`-branch (regel 113) toe, vóór `parts.append(Spacer(1, 4))`:

```python
    if result.get("sanctie") is not None:
        if entity.get("schema"):
            parts.append(Paragraph(f"Schema: {_escape(entity['schema'])}", styles["body"]))
        for when in entity.get("birth_dates") or []:
            parts.append(Paragraph(f"Geboortedata/-plaats: {_escape(when)}", styles["body"]))
        for where in entity.get("birth_places") or []:
            parts.append(Paragraph(f"Geboortedata/-plaats: {_escape(where)}", styles["body"]))
        for country in entity.get("citizenships") or []:
            parts.append(Paragraph(f"Nationaliteit: {_escape(country)}", styles["body"]))
        for tag in entity.get("topics") or []:
            parts.append(Paragraph(f"Risico-tags: {_escape(tag)}", styles["body"]))
        for ds in result["sanctie"].get("datasets", []):
            parts.append(Paragraph(f"Bron: {_escape(ds.get('title', ''))} ({_escape((ds.get('country') or '').upper())}) — {_escape(ds.get('url', ''))}", styles["body"]))
        parts.append(Paragraph(f"Details: {_escape(result['sanctie'].get('url', ''))}", styles["body"]))
    for flag in result.get("risk_countries") or []:
        parts.append(Paragraph(f"Risicoland: {_escape(flag.get('code', ''))} ({_escape(', '.join(flag.get('lists') or []))})", styles["body"]))
```

Wijzig `render_search_pdf` dataversie-sectie (na regel 144):

```python
    sanctions_meta = payload.get("sanctions_meta", {}) or {}
    story.append(Paragraph(f"Sancties-update: {_escape(sanctions_meta.get('updated_at', 'onbekend'))}", body))
    risk_meta = payload.get("risk_meta", {}) or {}
    story.append(Paragraph(f"Risicolanden-versie: {_escape(risk_meta.get('version', 'onbekend'))}", body))
```

Wijzig `_EXPORT_BRONLABELS` (regel 164):

```python
_EXPORT_BRONLABELS = {"eu": "EU", "pep": "PEP", "sanctie": "Sancties", "opensanctions": "OpenSanctions"}
```

Wijzig `_export_rows` (regels 168-207): voeg een sanctie-branch toe en risicoland aan de details:

```python
        if pep:
            details = [d.get("label", "") for d in (pep.get("details") or []) if d.get("label")]
            datasets = [d.get("title") or d.get("id") for d in (pep.get("datasets") or []) if d]
            birth_dates = entity.get("birth_dates") or []
            citizenships = entity.get("citizenships") or []
            link = pep.get("url", "")
        elif eu:
            details = [d.get("label", "") for d in (eu.get("details") or []) if d.get("label")]
            birth_dates = [b.get("date") or b.get("year") for b in (entity.get("birthdates") or []) if isinstance(b, dict)]
            citizenships = [c.get("description") or c.get("iso2") for c in (entity.get("citizenships") or []) if isinstance(c, dict)]
        elif result.get("sanctie"):
            details = [d.get("label", "") for d in (result["sanctie"].get("details") or []) if d.get("label")]
            datasets = [d.get("title") or d.get("id") for d in (result["sanctie"].get("datasets") or []) if d]
            birth_dates = entity.get("birth_dates") or []
            citizenships = entity.get("citizenships") or []
            link = result["sanctie"].get("url", "")
        elif os_result:
            datasets = os_result.get("datasets") or []
            birth_dates = [b.get("date") or b.get("year") for b in (entity.get("birthdates") or []) if isinstance(b, dict)]
            citizenships = [c.get("description") or c.get("iso2") for c in (entity.get("citizenships") or []) if isinstance(c, dict)]
            link = os_result.get("url", "")
        for flag in result.get("risk_countries") or []:
            details.append("Risicoland {} ({})".format(flag.get("code", ""), ", ".join(flag.get("lists") or [])))
```

Wijzig `render_batch_pdf` dataversie-sectie (na regel 258):

```python
    if meta.get("sanctions_updated_at"):
        story.append(Paragraph(f"Sancties-update: {_escape(meta['sanctions_updated_at'])}", body))
    if meta.get("risk_version"):
        story.append(Paragraph(f"Risicolanden-versie: {_escape(meta['risk_version'])}", body))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_export.py -v`
Expected: alle tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/export.py tests/test_export.py
git commit -m "feat: sanctie-bron en risicoland-flags in PDF/CSV/XLSX-rapporten"
```

---

### Task 9: UI — sanctie-kaart, badges, statusregel

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`
- Test: geen aparte test (handmatige controle + `test_smoke.py` draait al de pagina-oproep).

- [ ] **Step 1: Voeg de risicoland-helper en badge toe**

In `static/app.js`, voeg na `sourceBadge` (regel 44) toe:

```javascript
function riskFlagsHtml(item) {
  const riskFlags = (item.risk_countries || []).map((f) =>
    chip(`Risicoland ${f.code} · ${f.lists.map((l) => l.replaceAll("_", " ")).join(", ")}`, "bad")
  ).join("");
  return riskFlags ? `<p class="muted">${riskFlags}</p>` : "";
}
```

Wijzig `sourceBadge` (regel 39-44):

```javascript
function sourceBadge(sources) {
  const parts = [];
  if (sources.includes("eu")) parts.push('<span class="badge badge-eu">EU sanctielijst</span>');
  if (sources.includes("sanctie")) parts.push('<span class="badge badge-sanctie">Sancties (int.)</span>');
  if (sources.includes("opensanctions")) parts.push('<span class="badge badge-os">OpenSanctions</span>');
  return parts.join(" ");
}
```

- [ ] **Step 2: Voeg `riskFlagsHtml` toe aan eu- en pep-kaarten**

In `euCard`, voeg direct na de `natLine`-regel (regel 65) toe:

```javascript
  const riskLine = riskFlagsHtml(item);
```

en voeg `${riskLine}` toe aan de template, direct na `${natLine}` (regel 75).

In `pepCard`, voeg `const riskLine = riskFlagsHtml(item);` na `natLine` (regel 126) toe en `${riskLine}` in de template na `${natLine}` (regel 136).

- [ ] **Step 3: Voeg `sanctCard` toe**

Voeg na `pepCard` (na regel 143) toe:

```javascript
function sanctCard(item) {
  const sanc = item.sanctie;
  const entity = item.entity;
  const chips = (sanc.details || []).map((d) => {
    const tone = d.score >= 85 ? "ok" : d.score >= 50 ? "warn" : "bad";
    return chip(d.label, tone);
  }).join("");
  const dsChips = (sanc.datasets || []).slice(0, 5).map((d) =>
    `<a class="chip chip-sanctie" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.title)}${d.country ? " · " + escapeHtml(d.country.toUpperCase()) : ""}</a>`
  ).join("");
  const topics = (entity.topics || []).slice(0, 4).map((t) => chip(t, "warn")).join("");
  const riskLine = riskFlagsHtml(item);
  const births = (entity.birth_dates || []).slice(0, 2).map(escapeHtml).join(", ");
  const birthLine = births ? `<p class="muted">Geboren: ${births}</p>` : "";
  const natLine = (entity.citizenships || []).length
    ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.toUpperCase())).join(", ")}</p>` : "";
  return `
    <article class="card card-sanctie">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        <span class="badge badge-sanctie">Sancties (int.)</span>
      </div>
      <p class="ref">Schema: ${escapeHtml(entity.schema || "-")}</p>
      <p class="score-line">Totaalscore: <strong>${item.score}</strong>/100 ${chips}</p>
      ${birthLine}
      ${natLine}
      ${riskLine}
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${dsChips ? `<p class="muted">Bronnen: ${dsChips}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(sanc.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
    </article>`;
}
```

- [ ] **Step 4: Render de sanctie-kaart**

Wijzig `renderResults` (regel 160-162):

```javascript
    if (item.source === "opensanctions") html = osCard(item);
    else if (item.source === "pep") html = pepCard(item);
    else if (item.source === "sanctie") html = sanctCard(item);
    else html = euCard(item);
```

- [ ] **Step 5: Statusregel uitbreiden**

Wijzig de statusregel (regel 195-203):

```javascript
    if (s.index) {
      if (s.index.status === "building") {
        parts.push("Index wordt opgebouwd…");
      } else if (s.index.status === "error") {
        parts.push("Index-fout");
      } else if (s.index.enabled) {
        parts.push(`${s.index.pep_count.toLocaleString("nl-NL")} PEP-records`);
        if (s.index.sanctions_count) parts.push(`${s.index.sanctions_count.toLocaleString("nl-NL")} sanctie-records`);
      }
    }
    if (s.risk && s.risk.version) {
      parts.push(`Risicolanden v${s.risk.version}`);
    }
```

- [ ] **Step 6: CSS-badges**

In `static/style.css`, na `.badge-pep` (regel 227) toevoegen:

```css
.badge-sanctie { background: #0f766e; }
.chip-sanctie { background: #ccfbf1; color: #115e59; border-color: #0f766e; }
```

- [ ] **Step 7: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat: UI-kaart en badges voor internationale sancties + risicoland-flags"
```

---

### Task 10: README, `.env.example`, `docker-compose.yml`

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: `.env.example`**

Voeg toe aan `tests/../.env.example` (na regel 8):

```
# Internationale sanctie-lijsten (OFAC, VN, VK, NL-terroristenlijst) via de OpenSanctions 'sanctions'-collectie.
# Zet op 0 om uit te schakelen (default: aan als data/sanctions bestaat)
SANCTIONS_INDEX_ENABLED=
# Pad naar de OpenSanctions sanctions-data (default: data/sanctions)
SANCTIONS_DATA_DIR=
# Pad naar de risicolandenlijst (FATF zwart/grijs + EU high-risk) (default: data/risk_countries.json)
RISK_COUNTRIES=
```

- [ ] **Step 2: `docker-compose.yml`**

Voeg de `sanctions-downloader`-service toe en mount het volume in `app`:

```yaml
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - PEP_DATA_DIR=/data/pep
      - EU_DATA_DIR=/data/eu
      - SANCTIONS_DATA_DIR=/data/sanctions
      - OPENSANCTIONS_API_KEY=${OPENSANCTIONS_API_KEY:-}
    volumes:
      - pep-data:/data/pep
      - eu-data:/data/eu
      - sanctions-data:/data/sanctions
      - search-data:/app/data
    restart: unless-stopped

  sanctions-downloader:
    build:
      context: .
      dockerfile: Dockerfile.downloader
    command: ["scripts/update_sanctions.py", "--interval", "168"]
    environment:
      - SANCTIONS_DATA_DIR=/data/sanctions
    volumes:
      - sanctions-data:/data/sanctions
    restart: unless-stopped

volumes:
  pep-data:
  eu-data:
  sanctions-data:
  search-data:
```

- [ ] **Step 3: `README.md`**

Voeg twee secties toe (na de sectie "OpenSanctions (optioneel)", rond regel 64):

```markdown
## Internationale sancties (VN, OFAC, VK, NL-terroristenlijst)

Naast de EU-lijst en de PEP-data download de app de volledige OpenSanctions
**`sanctions`-collectie** (OFAC, VN, VK, Nederlandse nationale terroristenlijst en
alle overige sanctieregimes; de EU-lijst `eu_fsf` wordt overgeslagen omdat we die
al via de officiële XML hebben) naar `data/sanctions/`:

```bash
.venv/bin/python scripts/update_sanctions.py --once
```

Deze data draait mee in de UI-zoekopdracht, batch-screening en watchlists. Zet
`SANCTIONS_INDEX_ENABLED=0` om uit te schakelen. In de container verzorgt de service
`sanctions-downloader` (volume `sanctions-data`) de wekelijkse update.

## Risicolanden (FATF / EU high-risk)

`data/risk_countries.json` (overschrijfbaar met `RISK_COUNTRIES`) bevat de FATF
zwarte en grijze lijst en de EU high-risk derde landen (ISO2-codes). De lijst is
handmatig te onderhouden; valideer en voorzie van een timestamp met:

```bash
.venv/bin/python scripts/update_risk_countries.py
```

Een match waarvan de nationaliteit op de lijst staat, krijgt in de UI en de
rapporten een 'Risicoland'-markering. De versie staat in `/api/status`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md .env.example docker-compose.yml
git commit -m "docs: internationale sancties + risicolanden in README, env, docker-compose"
```

---

### Task 11: Volledige verificatie

**Files:** geen wijzigingen.

- [ ] **Step 1: Draai de volledige testsuite**

Run: `python -m pytest -v`
Expected: alle tests PASS (bestaande + nieuwe).

- [ ] **Step 2: Sanity-check import + status**

Run: `python -c "from app.main import create_app; app = create_app(); print('ok')"`
Expected: print `ok`.

- [ ] **Step 3: Controleer op achtergebleven 3-argument-calls**

Run: `rg -n "build_index\(|rebuild_index\(|index_fresh\(|ensure_index\(" app/ | rg -v "sanctions_root"`
Expected: alleen regels die `sanctions_root` (of een expliciete default) bevatten; geen call die de nieuwe param mist waar hij nodig is.

- [ ] **Step 4: Commit (alleen als er iets resteert)**

```bash
git status --short
```
Als er nog niet-gecommitte wijzigingen zijn, commit die met een passende boodschap.

---

## Self-Review

**Spec-dekking:**
- Sectie 1 (download sanctions-collectie) → Tasks 1-3.
- Sectie 2 (index bron `sanctie`, schema v4, alle functies, serialisatie, watchlist, status) → Tasks 4, 5, 7.
- Sectie 3 (risk_countries JSON, loader, flags, script) → Task 6 (+ integratie in Tasks 7-8).
- Sectie 4 (UI + export) → Tasks 8, 9.
- Sectie 5 (tests, README, env, docker) → Tasks 1-10, 11.
- "Niet-scope" (Vessel, transactiescreening, adverse media, API-wijziging) → niet aangeraakt.

**Type-consistentie:** `sanctions_root` is overal `Path | None = None` (behalve in main.py-helpers waar het `Path` is, omdat create_app het altijd resolvet). Bronwaarde `'sanctie'` is consistent in `search_index._stream_sanctions` en `_serialize_sanctions_result`. Payload-keys `sanctions_meta`, `risk_meta` en resultaat-key `risk_countries` zijn in Task 7 geproduceerd en in Task 8 geconsumeerd. `_serialize_sanctions_result` retourneert `sanctie`, `eu`, `pep`, `opensanctions` — `_to_watchlist_match` leest `sanctie.id`/`sanctie.datasets`.

**Placeholder-check:** elke code-stap bevat volledige code; geen TBD/TODO.
