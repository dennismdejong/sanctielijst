# PEP-zoekintegratie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De sanctielijst-app (hernoemd naar "Compliance Zoeker") zoekt naast de EU-sanctielijst óók in de lokaal gedownloade OpenSanctions PEP-data (~730K target-entiteiten), met per hit zichtbaar uit welke PEP-bron(nen) die komt plus een link naar opensanctions.org.

**Architecture:** Een nieuwe module `app/pep_index.py` bouwt uit `data/pep/*/entities.ftm.json` (JSON Lines) een in-memory index van `target: true` Person/Company-entiteiten met een token-inverted-map voor snelle kandidatenreductie; `search_pep` hergebruikt dezelfde scoring-filosofie als de EU-matcher. De downloader schrijft dataset-metadata (`data/pep/datasets.json`) zodat elke hit toonbare broninfo heeft. `app/main.py` laadt de index (cache via pickle, env `PEP_INDEX_ENABLED`) en voegt PEP-resultaten met `source: "pep"` toe aan `/api/search`. Frontend rendert PEP-kaarten met bron-badges en opensanctions-links.

**Tech Stack:** Python 3.11, stdlib (`json`, `pickle`, `re`, `os`, `datetime`), bestaande `rapidfuzz` en `requests`; pytest.

## Global Constraints

- Python 3.11+; geen nieuwe dependencies.
- Titel app: **"Compliance Zoeker"** (overal: `static/index.html` `<title>`+`<h1>`, FastAPI-title, README). Subtekst vermeldt EU-sancties én PEP.
- Scoring: naam 60 / geboortejaar 20 / nationaliteit 10 / geboorteplaats 10; drempel 90; max 20 resultaten; alleen ingevulde kenmerken meetellen.
- PEP-index: alleen `target: true` + schema `Person`/`Company`; `entity_type` filter `person`→`Person`, `enterprise`→`Company`.
- Cache: `data/pep/index.pkl`; geldig als nieuwer dan alle `entities.ftm.json`-bestanden én `datasets.json`; corrupt → herbouwen.
- `PEP_INDEX_ENABLED` env: `"0"`/`"false"`/`"no"` schakelt PEP uit; default aan als `data/pep/` bestaat.
- Per PEP-hit: `source: "pep"`, `pep.url = https://opensanctions.org/entities/<id>`, per dataset `{id, title, country, url}`.
- UI-taal Nederlands; identifiers Engels.
- Geen code-commentaar tenzij niet-voor-de-hand liggend.
- **Parallelle agent:** stage nooit via `git add .`; alleen eigen bestanden. Deze taken wijzigen bestaande bestanden (`app/main.py`, `static/*`, `README.md`, `.env.example`, `tests/test_ingest.py`, `tests/test_main.py`) — lees voor elke wijziging de actuele inhoud en behoud bestaande functionaliteit.
- Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: PEP-index bouwer

**Files:**
- Create: `app/pep_index.py`
- Test: `tests/test_pep_index.py`

**Interfaces:**
- Consumes: nothing (leest `data/pep/`-bestanden direct).
- Produces:
  - Constants: `ENTITIES_FILENAME = "entities.ftm.json"`, `PEP_INDEX_FILENAME = "index.pkl"`, `DATASETS_FILENAME = "datasets.json"`, `INDEX_ENV = "PEP_INDEX_ENABLED"`.
  - `_tokens(text: str) -> list[str]` — lowercase tokens ≥2 alfanumerieke tekens.
  - `_extract_entity(line: str) -> dict | None` — JSONL-regel → record `{id, caption, schema, datasets, names, birth_dates, birth_places, citizenships, political, topics}`; `None` bij corrupte regel of niet `target: true`/`Person`/`Company`.
  - `build_index(root_dir: Path) -> dict` — `{"entities": [...], "token_map": {token: [idx]}, "datasets": {ds: count}, "built_at": iso, "skipped_lines": int}`.
  - `save_index(root_dir, index)`, `load_index_cache(root_dir) -> dict | None` (mtime-check t.o.v. `entities.ftm.json` én `datasets.json`; corrupt → `None`; zet `index["source"] = "cached"`).
  - `load_or_build_index(root_dir: Path, force: bool = False) -> dict | None` — `None` als er geen `*/entities.ftm.json`-bestanden zijn; anders cache-of-bouw (`source: "cached"|"built"`), schrijft cache, en voegt `datasets_meta` toe uit `datasets.json`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pep_index.py`:
```python
import json
import os
import time
from pathlib import Path

import pytest

from app.pep_index import (
    build_index,
    load_index_cache,
    load_or_build_index,
    save_index,
    _tokens,
)


def write_ftm(root, dataset, entities):
    path = root / dataset / "entities.ftm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in entities:
            fh.write(json.dumps(e) + "\n")


def person(id_, caption, target=True, datasets=("ds1",), **props):
    return {"id": id_, "caption": caption, "schema": "Person", "target": target, "datasets": list(datasets), "properties": props}


def company(id_, caption, target=True, datasets=("ds1",), **props):
    return {"id": id_, "caption": caption, "schema": "Company", "target": target, "datasets": list(datasets), "properties": props}


FIXTURE = [
    person("NK-1", "JORGE FERNANDEZ", birthDate=["1965-03-01"], citizenship=["ar"], political=["PRIMERO SAN LUIS"], topics=["role.pep"]),
    person("NK-2", "Maria Lopez", target=False),
    person("NK-3", "GUILLERMO CESAR AGUERO", birthDate=["1970"]),
    company("NK-4", "Yacimientos Petroliferos"),
    {"id": "NK-5", "caption": "Occupancy", "schema": "Occupancy", "target": True, "datasets": ["ds1"], "properties": {"holder": ["Q1"]}},
    person("NK-6", "Jorge Luis"),
]


def test_tokens():
    assert _tokens("JORGE FERNANDEZ") == ["jorge", "fernandez"]
    assert _tokens("a b !! c-de") == ["c", "de"]
    assert _tokens("") == []


def test_build_index_filters(tmp_path):
    path = tmp_path / "ds1" / "entities.ftm.json"
    path.parent.mkdir(parents=True)
    with path.open("w") as fh:
        for e in FIXTURE:
            fh.write(json.dumps(e) + "\n")
        fh.write("dit is geen geldige json\n")
    index = build_index(tmp_path)
    ids = [e["id"] for e in index["entities"]]
    assert ids == ["NK-1", "NK-3", "NK-4", "NK-6"]
    assert index["skipped_lines"] == 3
    jorge = index["entities"][0]
    assert jorge["names"] == ["JORGE FERNANDEZ"]
    assert jorge["birth_dates"] == ["1965-03-01"]
    assert jorge["citizenships"] == ["ar"]
    assert "jorge" in index["token_map"]
    assert "fernandez" in index["token_map"]


def test_build_index_no_ftm(tmp_path):
    assert build_index(tmp_path)["entities"] == []


def test_load_or_build_index_none_when_empty(tmp_path):
    assert load_or_build_index(tmp_path) is None
    assert load_or_build_index(tmp_path / "niet-bestaand") is None


def test_load_or_build_index_caches(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ")])
    first = load_or_build_index(tmp_path)
    assert first["source"] == "built"
    assert (tmp_path / "index.pkl").exists()
    second = load_or_build_index(tmp_path)
    assert second["source"] == "cached"
    assert [e["id"] for e in second["entities"]] == ["NK-1"]


def test_cache_stale_when_ftm_newer(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ")])
    load_or_build_index(tmp_path)
    future = time.time() + 1000
    os.utime(tmp_path / "ds1" / "entities.ftm.json", (future, future))
    assert load_index_cache(tmp_path) is None


def test_cache_corrupt_pickle(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ")])
    (tmp_path / "index.pkl").write_bytes(b"kapot")
    assert load_index_cache(tmp_path) is None


def test_datasets_meta_attached(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ", datasets=("ar_parliament",))])
    (tmp_path / "datasets.json").write_text(json.dumps({"ar_parliament": {"title": "Argentina Parliament", "country": "ar"}}))
    index = load_or_build_index(tmp_path)
    assert index["datasets_meta"]["ar_parliament"]["title"] == "Argentina Parliament"
    assert index["datasets"]["ar_parliament"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pep_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pep_index'`.

- [ ] **Step 3: Write minimal implementation**

`app/pep_index.py`:
```python
import json
import os
import pickle
import re
from datetime import datetime, timezone
from pathlib import Path

ENTITIES_FILENAME = "entities.ftm.json"
PEP_INDEX_FILENAME = "index.pkl"
DATASETS_FILENAME = "datasets.json"
INDEX_ENV = "PEP_INDEX_ENABLED"


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2]


def _extract_entity(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not data.get("target"):
        return None
    schema = data.get("schema")
    if schema not in ("Person", "Company"):
        return None
    props = data.get("properties") or {}
    names = list((props.get("name") or []) + (props.get("alias") or []))
    caption = data.get("caption") or ""
    if caption and caption not in names:
        names.insert(0, caption)
    return {
        "id": data.get("id", ""),
        "caption": caption,
        "schema": schema,
        "datasets": data.get("datasets") or [],
        "names": names,
        "birth_dates": props.get("birthDate") or [],
        "birth_places": props.get("birthPlace") or [],
        "citizenships": props.get("citizenship") or [],
        "political": props.get("political") or [],
        "topics": props.get("topics") or [],
    }


def build_index(root_dir: Path) -> dict:
    entities = []
    token_map = {}
    datasets = {}
    skipped_lines = 0
    for ftm in sorted(root_dir.glob(f"*/{ENTITIES_FILENAME}")):
        with ftm.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entity = _extract_entity(line)
                if entity is None:
                    skipped_lines += 1
                    continue
                idx = len(entities)
                entities.append(entity)
                seen = set()
                for name in entity["names"]:
                    for token in _tokens(name):
                        if token not in seen:
                            seen.add(token)
                            token_map.setdefault(token, []).append(idx)
                for ds in entity["datasets"]:
                    datasets[ds] = datasets.get(ds, 0) + 1
    return {
        "entities": entities,
        "token_map": token_map,
        "datasets": datasets,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "skipped_lines": skipped_lines,
    }


def _newest_input_mtime(root_dir: Path) -> float:
    newest = 0.0
    for pattern in (f"*/{ENTITIES_FILENAME}", DATASETS_FILENAME):
        for path in root_dir.glob(pattern):
            newest = max(newest, path.stat().st_mtime)
    return newest


def save_index(root_dir: Path, index: dict) -> None:
    (root_dir / PEP_INDEX_FILENAME).write_bytes(pickle.dumps(index))


def load_index_cache(root_dir: Path) -> dict | None:
    pkl = root_dir / PEP_INDEX_FILENAME
    if not pkl.exists():
        return None
    if pkl.stat().st_mtime < _newest_input_mtime(root_dir):
        return None
    try:
        with pkl.open("rb") as fh:
            index = pickle.load(fh)
    except Exception:
        return None
    index["source"] = "cached"
    return index


def _load_datasets_meta(root_dir: Path) -> dict:
    path = root_dir / DATASETS_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_or_build_index(root_dir: Path, force: bool = False) -> dict | None:
    if not root_dir.exists():
        return None
    if not any(root_dir.glob(f"*/{ENTITIES_FILENAME}")):
        return None
    if not force:
        cached = load_index_cache(root_dir)
        if cached is not None:
            cached["datasets_meta"] = _load_datasets_meta(root_dir)
            return cached
    index = build_index(root_dir)
    index["source"] = "built"
    index["datasets_meta"] = _load_datasets_meta(root_dir)
    save_index(root_dir, index)
    return index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pep_index.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pep_index.py tests/test_pep_index.py
git commit -m "feat: PEP index builder with token map and pickle cache"
```

---

### Task 2: PEP-zoeken

**Files:**
- Modify: `app/pep_index.py`
- Test: `tests/test_pep_index.py`

**Interfaces:**
- Consumes: `_tokens`, index-shape from Task 1.
- Produces:
  - Constants: `THRESHOLD = 60`, `MAX_RESULTS = 20`.
  - `search_pep(index: dict, name: str, birth_year: int | None = None, nationality: str | None = None, birth_place: str | None = None, entity_type: str | None = None, threshold: int = THRESHOLD, max_results: int = MAX_RESULTS) -> list[dict]`
  - Retourneert gesorteerd (aflopend op `score`) maximaal `max_results` records `{"entity": <record>, "score": int, "matched_name": str | None, "details": [{"feature", "score", "label"}]}`.
  - Kandidaten = unie van `token_map`-indices van de query-tokens; daarna fuzzy `token_set_ratio` over `names` (beste match). Gewogen score zoals EU; `entity_type`-filter (`person`→`Person`, `enterprise`→`Company`); geboortejaar: eerste 4 cijfers van `birth_dates` (exact 100, ±1 75, ±2 50); nationaliteit: exact ISO (case-insensitive); geboorteplaats: beste `token_set_ratio`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pep_index.py`:
```python
from app.pep_index import MAX_RESULTS, THRESHOLD, search_pep


@pytest.fixture
def pep_index_data(tmp_path):
    write_ftm(tmp_path, "ds1", FIXTURE)
    return build_index(tmp_path), tmp_path


def test_search_exact_top(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE FERNANDEZ")
    assert results[0]["entity"]["id"] == "NK-1"
    assert results[0]["score"] == 100
    assert results[0]["matched_name"] == "JORGE FERNANDEZ"
    assert results[0]["details"][0]["feature"] == "naam"


def test_search_fuzzy(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE FERNÁNDEZ")
    assert results and results[0]["score"] >= 80


def test_search_birth_year_boosts(pep_index_data):
    index, _ = pep_index_data
    exact = search_pep(index, "JORGE", birth_year=1965)
    wrong = search_pep(index, "JORGE", birth_year=1999)
    assert exact and wrong
    assert exact[0]["score"] >= wrong[0]["score"]


def test_search_nationality_match(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE", nationality="ar")
    assert any(d["feature"] == "nationaliteit" and d["score"] == 100 for r in results for d in r["details"])


def test_search_entity_type_filter(pep_index_data):
    index, _ = pep_index_data
    people = search_pep(index, "JORGE", entity_type="person")
    enterprises = search_pep(index, "JORGE", entity_type="enterprise")
    assert people and not enterprises
    comps = search_pep(index, "Yacimientos", entity_type="enterprise")
    assert comps and comps[0]["entity"]["schema"] == "Company"


def test_search_threshold_and_max(pep_index_data):
    index, _ = pep_index_data
    low = search_pep(index, "JORGE", threshold=0)
    assert len(low) >= 2
    capped = search_pep(index, "JORGE", threshold=0, max_results=1)
    assert len(capped) == 1
    assert THRESHOLD == 60
    assert MAX_RESULTS == 20


def test_search_no_candidates(pep_index_data):
    index, _ = pep_index_data
    assert search_pep(index, "Zzqqq Xxww") == []
    assert search_pep(index, "!!") == []


def test_search_sorts_desc(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE", threshold=0)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pep_index.py -k search_pep -v`
Expected: FAIL with `ImportError: cannot import name 'search_pep'`.

- [ ] **Step 3: Write implementation**

Append to `app/pep_index.py`:
```python
from rapidfuzz import fuzz

THRESHOLD = 60
MAX_RESULTS = 20


def _birth_year(value: str) -> int | None:
    match = re.match(r"(\d{4})", value)
    return int(match.group(1)) if match else None


def _name_score(names: list[str], query: str) -> tuple[int, str | None]:
    best = 0
    best_name = None
    q = query.strip()
    for name in names:
        if not name:
            continue
        score = fuzz.token_set_ratio(q, name)
        if score > best:
            best = score
            best_name = name
    return best, best_name


def search_pep(
    index: dict,
    name: str,
    birth_year: int | None = None,
    nationality: str | None = None,
    birth_place: str | None = None,
    entity_type: str | None = None,
    threshold: int = THRESHOLD,
    max_results: int = MAX_RESULTS,
) -> list[dict]:
    token_map = index.get("token_map", {})
    entities = index.get("entities", [])
    candidates = set()
    for token in _tokens(name):
        candidates.update(token_map.get(token, []))
    results = []
    for idx in candidates:
        entity = entities[idx]
        if entity_type == "person" and entity["schema"] != "Person":
            continue
        if entity_type == "enterprise" and entity["schema"] != "Company":
            continue
        n_score, matched = _name_score(entity["names"], name)
        weights = [60]
        details = [{
            "feature": "naam",
            "score": n_score,
            "label": f'Naam {n_score}% (via "{matched}")' if matched else "Naam 0%",
        }]
        if birth_year is not None:
            best = 0
            for date in entity["birth_dates"]:
                year = _birth_year(date)
                if year is None:
                    continue
                diff = abs(birth_year - year)
                score = 100 if diff == 0 else 75 if diff == 1 else 50 if diff == 2 else 0
                best = max(best, score)
            weights.append(20)
            details.append({
                "feature": "geboortejaar",
                "score": best,
                "label": "Geboortejaar exact" if best == 100 else f"Geboortejaar ({best}%)",
            })
        if nationality:
            q = nationality.strip().upper()
            best = max((100 for c in entity["citizenships"] if c.strip().upper() == q), default=0)
            weights.append(10)
            details.append({
                "feature": "nationaliteit",
                "score": best,
                "label": "Nationaliteit match" if best >= 85 else f"Nationaliteit ({best}%)",
            })
        if birth_place:
            best = max((fuzz.token_set_ratio(birth_place.strip(), p) for p in entity["birth_places"]), default=0)
            weights.append(10)
            details.append({"feature": "geboorteplaats", "score": best, "label": f"Geboorteplaats {best}%"})
        total = round(sum(w * d["score"] for w, d in zip(weights, details)) / sum(weights))
        if total < threshold:
            continue
        results.append({"entity": entity, "score": total, "matched_name": matched, "details": details})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pep_index.py -v`
Expected: 16 passed (8 + 8).

- [ ] **Step 5: Commit**

```bash
git add app/pep_index.py tests/test_pep_index.py
git commit -m "feat: PEP fuzzy search with weighted scoring"
```

---

### Task 3: Dataset-metadata in de downloader

**Files:**
- Modify: `app/pep_ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `PEP_COLLECTION`, constants in `app/pep_ingest.py`.
- Produces:
  - `write_datasets_meta(index: dict, root_dir: Path) -> None` — schrijft `root_dir / "datasets.json"` (atomic via tmp+`os.replace`) met per PEP-dataset `{name: {"title", "publisher", "country", "official", "url"}}`.
  - `refresh_pep(...)` roept dit aan aan het einde, alleen als `not dry_run`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -k write_datasets_meta -v`
Expected: FAIL with `ImportError: cannot import name 'write_datasets_meta'`.

- [ ] **Step 3: Write implementation**

Append to `app/pep_ingest.py`:
```python
def write_datasets_meta(index: dict, root_dir: Path) -> None:
    raw = index.get("datasets") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    meta = {}
    for ds in raw:
        if not isinstance(ds, dict):
            continue
        if PEP_COLLECTION not in (ds.get("collections") or []):
            continue
        pub = ds.get("publisher") or {}
        meta[ds["name"]] = {
            "title": ds.get("title", ""),
            "publisher": pub.get("name", ""),
            "country": pub.get("country", ""),
            "official": bool(pub.get("official")),
            "url": ds.get("url", ""),
        }
    root_dir.mkdir(parents=True, exist_ok=True)
    path = root_dir / "datasets.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
```

Voeg aan het einde van `refresh_pep` toe (na het schrijven van het manifest, binnen dezelfde `if not dry_run:`-blok):
```python
        write_datasets_meta(index, root_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: alle bestaande tests + 2 nieuwe slagen.

- [ ] **Step 5: Commit**

```bash
git add app/pep_ingest.py tests/test_ingest.py
git commit -m "feat: persist PEP dataset metadata on refresh"
```

---

### Task 4: App-integratie (API + titel)

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `pep_index.load_or_build_index`, `pep_index.search_pep`, `pep_index.INDEX_ENV`, `pep_index.DATASETS_FILENAME`, `matcher.SearchQuery`, `matcher.MAX_RESULTS`.
- Produces:
  - `PEP_ROOT = Path(__file__).resolve().parent.parent / "data" / "pep"`.
  - `_pep_enabled(pep_root: Path) -> bool` — env `PEP_INDEX_ENABLED`: `"0"|"false"|"no"` → uit; ingesteld anders → aan; oningesteld → `pep_root.exists()`.
  - `_serialize_pep_result(result: dict, index: dict) -> dict` — `{"source": "pep", "score", "entity": {...}, "pep": {"id", "url", "datasets": [{id,title,country,url}], "matched_name", "details"}, "eu": None, "opensanctions": None}`.
  - `create_app(..., pep_root: Path = PEP_ROOT)` — laadt index in `state["pep"]` (of `None`); FastAPI title `"Compliance Zoeker"`.
  - `GET /api/status` → extra `pep_index: {enabled, entity_count, datasets_count, source}`.
  - `GET /api/search` → PEP-resultaten toegevoegd (na EU, voor OS), gemerged gesorteerd.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main.py` (aan het einde; de bestaande tests blijven ongewijzigd, een autouse-fixture zet PEP uit zodat die snel blijven):
```python
import pytest

from app import pep_index


@pytest.fixture(autouse=True)
def pep_disabled(monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "0")


def _write_pep_fixture(root):
    import json
    for ds, entities in [
        ("ar_parliament", [
            {"id": "NK-x", "caption": "JORGE FERNANDEZ", "schema": "Person", "target": True, "datasets": ["ar_parliament"],
             "properties": {"birthDate": ["1965-03-01"], "citizenship": ["ar"], "political": ["PRIMERO SAN LUIS"], "topics": ["role.pep"]}},
        ]),
    ]:
        p = root / ds / "entities.ftm.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as fh:
            for e in entities:
                fh.write(json.dumps(e) + "\n")
    (root / "datasets.json").write_text(json.dumps({"ar_parliament": {"title": "Argentina Members of Parliament", "publisher": "HCDN", "country": "ar", "official": True, "url": "https://parlament.ar"}}))


def test_status_pep_disabled():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/status").json()
    assert data["pep_index"]["enabled"] is False


def test_status_pep_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path))
    data = client.get("/api/status").json()
    assert data["pep_index"]["enabled"] is True
    assert data["pep_index"]["entity_count"] == 1
    assert data["pep_index"]["datasets_count"] == 1


def test_search_pep_hit_with_sources(tmp_path, monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()
    pep_results = [r for r in data["results"] if r["source"] == "pep"]
    assert pep_results
    first = pep_results[0]
    assert first["score"] == 100
    assert first["pep"]["id"] == "NK-x"
    assert first["pep"]["url"] == "https://opensanctions.org/entities/NK-x"
    assert first["pep"]["datasets"][0]["id"] == "ar_parliament"
    assert first["pep"]["datasets"][0]["title"] == "Argentina Members of Parliament"
    assert first["pep"]["datasets"][0]["country"] == "ar"


def test_search_pep_entity_type_filter(tmp_path, monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ", "entity_type": "enterprise"}).json()
    assert not [r for r in data["results"] if r["source"] == "pep"]
```

Let op: `ENTITIES` en `TestClient` bestaan al bovenaan `tests/test_main.py`; importeer `create_app` daar al. Plaats de nieuwe tests ná de bestaande.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_main.py -k "pep" -v`
Expected: FAIL (PEP-endpoints ontbreken / import-errors).

- [ ] **Step 3: Write implementation**

In `app/main.py`:

Voeg bovenaan toe (na `from . import ingest, matcher, opensanctions`):
```python
from . import pep_index
```
En na `CACHE_DIR = ...`:
```python
PEP_ROOT = Path(__file__).resolve().parent.parent / "data" / "pep"
```

Voeg toe (na `_serialize_os_result`):
```python
def _pep_enabled(pep_root: Path) -> bool:
    env = os.environ.get(pep_index.INDEX_ENV)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no")
    return pep_root.exists()


def _serialize_pep_result(result: dict, index: dict) -> dict:
    entity = result["entity"]
    ds_meta = index.get("datasets_meta", {})
    datasets = []
    for ds_id in entity["datasets"]:
        meta = ds_meta.get(ds_id, {})
        datasets.append({
            "id": ds_id,
            "title": meta.get("title") or ds_id,
            "country": meta.get("country", ""),
            "url": f"https://www.opensanctions.org/datasets/{ds_id}/",
        })
    return {
        "source": "pep",
        "score": result["score"],
        "entity": {
            "name": entity["caption"],
            "schema": entity["schema"],
            "birth_dates": entity["birth_dates"],
            "birth_places": entity["birth_places"],
            "citizenships": entity["citizenships"],
            "political": entity["political"],
            "topics": entity["topics"],
        },
        "pep": {
            "id": entity["id"],
            "url": f"https://opensanctions.org/entities/{entity['id']}",
            "datasets": datasets,
            "matched_name": result["matched_name"],
            "details": result["details"],
        },
        "eu": None,
        "opensanctions": None,
    }
```

Wijzig `create_app`:
```python
def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    cache_dir: Path = CACHE_DIR,
    static_dir: Path = STATIC_DIR,
    pep_root: Path = PEP_ROOT,
) -> FastAPI:
    if entities is None:
        entities, meta = ingest.load_index(cache_dir)
    else:
        meta = {}
    if os_api_key is None:
        os_api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    pep = pep_index.load_or_build_index(pep_root) if _pep_enabled(pep_root) else None
    state = {"entities": entities, "meta": meta, "pep": pep}
    opensanctions_active = bool(os_api_key)

    app = FastAPI(title="Compliance Zoeker")
```

Wijzig `_status()`:
```python
        pep = state["pep"]
        return {
            "cached_at": cached_at,
            "generated_at": state["meta"].get("generated_at"),
            "entity_count": len(state["entities"]),
            "data_age_hours": age_hours,
            "opensanctions_active": opensanctions_active,
            "source": state["meta"].get("source", "unknown"),
            "pep_index": {
                "enabled": pep is not None,
                "entity_count": len(pep.get("entities", [])) if pep else 0,
                "datasets_count": len(pep.get("datasets", {})) if pep else 0,
                "source": pep.get("source") if pep else None,
            },
        }
```

Wijzig in `search()` (na de EU-loop, vóór de OpenSanctions-blok):
```python
        if state["pep"] is not None:
            for r in pep_index.search_pep(
                state["pep"],
                query.name,
                query.birth_year,
                query.nationality,
                query.birth_place,
                query.entity_type,
            ):
                results.append(_serialize_pep_result(r, state["pep"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: alle bestaande tests + 4 nieuwe slagen.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: integrate PEP search into API with source badges"
```

---

### Task 5: Frontend, titel en docs

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/style.css`, `README.md`, `.env.example`

**Interfaces:**
- Consumes: `/api/search`-responses (`source: "pep"` met `entity`, `pep`), `/api/status` (`pep_index`).
- Produces: titel "Compliance Zoeker" + PEP-resultaatkaarten met bron-dataset-chips en opensanctions-link.

- [ ] **Step 1: Update `static/index.html`**

- `<title>Sanctielijst Zoeker</title>` → `<title>Compliance Zoeker</title>`
- `<h1>Sanctielijst Zoeker</h1>` → `<h1>Compliance Zoeker</h1>`
- Subtekst `<p>Zoek personen en bedrijven in de EU sanctielijsten.</p>` → `<p>Zoek personen en bedrijven in de EU-sanctielijsten en OpenSanctions PEP-data.</p>`

- [ ] **Step 2: Update `static/app.js`**

Voeg `sourceBadge`-ondersteuning voor pep toe in `renderResults` en een nieuwe `pepCard`-functie (na `osCard`):

```js
function pepCard(item) {
  const pep = item.pep;
  const entity = item.entity;
  const chips = (pep.details || []).map((d) => {
    const tone = d.score >= 85 ? "ok" : d.score >= 50 ? "warn" : "bad";
    return chip(d.label, tone);
  }).join("");
  const dsChips = (pep.datasets || []).slice(0, 5).map((d) =>
    `<a class="chip chip-pep" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.title)}${d.country ? " · " + escapeHtml(d.country.toUpperCase()) : ""}</a>`
  ).join("");
  const topics = (entity.topics || []).slice(0, 4).map((t) => chip(t, "warn")).join("");
  const political = (entity.political || []).length
    ? `<p class="muted">Partij/fractie: ${entity.political.map(escapeHtml).join(", ")}</p>` : "";
  const births = (entity.birth_dates || []).slice(0, 2).map(escapeHtml).join(", ");
  const birthLine = births ? `<p class="muted">Geboren: ${births}</p>` : "";
  const natLine = (entity.citizenships || []).length
    ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.toUpperCase())).join(", ")}</p>` : "";
  return `
    <article class="card card-pep">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        <span class="badge badge-pep">PEP</span>
      </div>
      <p class="ref">Schema: ${escapeHtml(entity.schema || "-")}</p>
      <p class="score-line">Totaalscore: <strong>${item.score}</strong>/100 ${chips}</p>
      ${birthLine}
      ${natLine}
      ${political}
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${dsChips ? `<p class="muted">Bronnen: ${dsChips}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(pep.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
    </article>`;
}
```

Vervang in `renderResults` de `forEach`:
```js
  data.results.forEach((item) => {
    let html;
    if (item.source === "opensanctions") html = osCard(item);
    else if (item.source === "pep") html = pepCard(item);
    else html = euCard(item);
    resultsEl.insertAdjacentHTML("beforeend", html);
  });
```

Vervang in `loadStatus` de `parts`-aanmaak:
```js
    const parts = [
      `${s.entity_count.toLocaleString("nl-NL")} records`,
      s.source === "fresh" ? "data vers" : "data gecachet",
      s.opensanctions_active ? "OpenSanctions actief" : "OpenSanctions niet actief",
    ];
    if (s.pep_index && s.pep_index.enabled) {
      parts.push(`${s.pep_index.entity_count.toLocaleString("nl-NL")} PEP-records`);
    }
```

- [ ] **Step 3: Update `static/style.css`**

Voeg aan het einde toe (badge- en chip-kleur voor PEP; pas aan naar de bestaande kleurenstijl van `.badge-eu`/`.chip`):
```css
.badge-pep {
  background: #7c3aed;
  color: #fff;
}
.chip-pep {
  background: #ede9fe;
  border: 1px solid #7c3aed;
  color: #5b21b6;
}
```
Check eerst in het bestand hoe `.badge-eu` en `.chip` zijn gedefinieerd en stem de kleuren daarop af.

- [ ] **Step 4: Update `.env.example`**

Voeg toe:
```
# PEP-zoeken: zet op 0 om uit te schakelen (default: aan als data/pep bestaat)
PEP_INDEX_ENABLED=
```

- [ ] **Step 5: Update `README.md`**

- Eerste alinea: "Compliance Zoeker" + beschrijf dat er ook in OpenSanctions PEP-data wordt gezocht.
- "Data"-sectie: vermeld de PEP-index (`data/pep/`, `PEP_INDEX_ENABLED`) en dat de index automatisch wordt gebouwd/gecacht.

- [ ] **Step 6: Verify — full suite + live check**

Run:
```bash
.venv/bin/python -m pytest -v
```
Expected: alle tests groen (bestaande app-tests + 23 nieuwe: 17 pep-index, 2 ingest, 4 main).

Optioneel (alleen als de server draait): start `uvicorn app.main:create_app --factory --port 8000` en check `http://localhost:8000` toont "Compliance Zoeker" en dat `GET /api/status` `pep_index` toont.

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/app.js static/style.css .env.example README.md
git commit -m "feat: Compliance Zoeker branding and PEP result cards"
```

---

## Self-Review

**Spec coverage:**
- PEP-index (target+Person/Company, token-map) → Task 1 ✔
- Zoeklogica met zelfde scoring + entity_type-filter → Task 2 ✔
- Bronherleiding (dataset-metadata + opensanctions-link) → Task 3 (metadata) + Task 4 (serialisatie `pep.datasets`) ✔
- App-integratie (`/api/status`, `/api/search`, `PEP_INDEX_ENABLED`) → Task 4 ✔
- Frontend-kaarten met bronbadge/chips → Task 5 ✔
- Titel "Compliance Zoeker" → Task 4 (FastAPI) + Task 5 (frontend/README) ✔
- Foutafhandeling (corrupt FTM/cache, geen data/pep) → Task 1 (`_extract_entity`/`load_index_cache`) + Task 4 (`_pep_enabled`) ✔
- Teststrategie → elke task ✔

**Placeholders:** geen TBD/TODO; complete code in elke stap.

**Type-consistentie:** `build_index`, `load_or_build_index`, `search_pep`, `write_datasets_meta`, `_pep_enabled`, `_serialize_pep_result`, `pep_index.INDEX_ENV`, `pep_index.DATASETS_FILENAME` worden in latere tasks identiek gebruikt als gedefinieerd in eerdere.
