# SQLite+FTS5 Zoekindex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vervang de in-memory zoekindex (EU ~6.000 + PEP ~730K entiteiten, ~1.1GB RAM) door één SQLite+FTS5-index op disk (`data/search.sqlite`), zodat de app ~30-50MB RAM gebruikt, instant start en rebuilds zero-downtime uitvoert. Zoekresultaten blijven identiek.

**Architecture:** Een nieuwe module `app/search_index.py` bouwt één SQLite-database met een `entities`-tabel (EU- en PEP-records genormaliseerd, `raw`-JSON voor EU-display) plus een content-backed FTS5-tabel over accent-gevouwen namen. Zoeken: FTS5-MATCH → kandidaten → rapidfuzz-scoring (containment→100, gewichten, drempel 90, max 20). Rebuild schrijft naar `search.sqlite.new` en swapped atomic. `app/main.py` houdt geen entities meer in het geheugen; elke zoekopdracht opent een verbinding. `app/pep_index.py` wordt verwijderd (logica verhuist naar `search_index.py`).

**Tech Stack:** Python 3.11, stdlib (`sqlite3` met FTS5, `json`, `re`, `unicodedata`, `os`, `threading`), bestaande `rapidfuzz`; pytest. Géén nieuwe dependencies.

## Global Constraints

- Python 3.11+; geen nieuwe dependencies buiten `requirements.txt`.
- Eén zoekindex voor EU + PEP; per entiteit `source` = `eu`|`pep`.
- Score: naam 60 / geboortejaar 20 / nationaliteit 10 / geboorteplaats 10; drempel **90**; max **20**; alleen ingevulde kenmerken; token-containment → 100 (accent-gevouwen).
- `entity_type`-filter: `person`→`Person`, `enterprise`→`Company` (ook EU `subject_type`).
- Atomic rebuild: altijd bouwen naar `<db>.new`, daarna `os.replace`. Geen gedeelde SQLite-verbinding tussen threads; per zoekopdracht een nieuwe verbinding.
- DB-pad: `SEARCH_DB` env, anders `data/search.sqlite` (in de container: `/app/data/search.sqlite`, op de bestaande volume).
- `PEP_INDEX_ENABLED=0` schakelt PEP uit (alleen EU geïndexeerd); EU altijd aan.
- UI-taal Nederlands; identifiers Engels. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- **Parallelle agent:** de andere agent werkt aan `app/main.py`/`app/ingest.py`/`app/eu_ingest.py`. Implementeer deze taken pas nadat die agent klaar is en zijn werk gecommit heeft. Lees bestaande bestanden vóór elke wijziging; stage nooit via `git add .`.
- Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: search_index — helpers en record-extractie

**Files:**
- Create: `app/search_index.py`
- Test: `tests/test_search_index.py`

**Interfaces:**
- Consumes: `app.ingest.parse_export` (EU-XML → lijst van EU-entiteiten), FTM-bestanden onder `pep_root` (JSON Lines).
- Produces:
  - Constants: `THRESHOLD = 90`, `MAX_RESULTS = 20`, `INDEX_ENV = "PEP_INDEX_ENABLED"`, `DB_FILENAME = "search.sqlite"`.
  - `default_db_path() -> Path` — `os.environ.get("SEARCH_DB")` of `Path("data") / DB_FILENAME`.
  - `fold(text: str) -> str` — NFKD + combining-strip + lowercase.
  - `tokens(text: str) -> list[str]` — `fold` + split op non-alphanumeriek, lengte ≥ 2.
  - `_eu_records(entities: list[dict]) -> list[dict]` — EU-entiteit → `{"source": "eu", "id", "caption", "schema", "names", "birth_dates", "birth_places", "citizenships", "political": [], "topics": [], "datasets": [], "eu_ref", "raw"}`.
  - `_pep_records(pep_root: Path) -> list[dict]` — FTM-lijnen → zelfde recordvorm met `source: "pep"`, `schema` `Person`/`Company`, `datasets` uit de FTM-entiteit, `raw: None`; skip niet-`target`/niet-Person/Company en corrupte regels.

- [ ] **Step 1: Write the failing tests**

`tests/test_search_index.py`:
```python
import json
from pathlib import Path

import pytest

from app.search_index import (
    THRESHOLD,
    MAX_RESULTS,
    fold,
    tokens,
    _eu_records,
    _pep_records,
)


def test_constants():
    assert THRESHOLD == 90
    assert MAX_RESULTS == 20


def test_fold_accents():
    assert fold("JORGE FERNÁNDEZ") == "jorge fernandez"
    assert fold("MÜLLER") == "muller"


def test_tokens():
    assert tokens("JORGE FERNÁNDEZ") == ["jorge", "fernandez"]
    assert tokens("a b !! c-de") == ["de"]


def eu_entity(eu_ref="EU.1", name="John Smith", subject_type="person", birthdates=None, citizenships=None):
    return {
        "logical_id": eu_ref,
        "eu_reference_number": eu_ref,
        "united_nations_id": "",
        "designation_date": "2022-01-01",
        "subject_type": subject_type,
        "aliases": [{"whole_name": name, "first_name": "", "last_name": "", "strong": True, "function": "Diplomat", "title": ""}],
        "citizenships": citizenships or [],
        "birthdates": birthdates or [],
        "addresses": [],
        "identifications": [],
        "regulations": [{"number_title": "2022/123", "publication_date": "2022-02-01", "programme": "XX", "publication_url": "https://eur-lex.europa.eu/x"}],
        "remarks": ["let op"],
    }


def test_eu_records_normalise():
    records = _eu_records([eu_entity(birthdates=[{"date": "1971-02-15", "year": 1971, "year_from": None, "year_to": None, "city": "", "place": "Kabul", "iso2": "AF", "country": "AFGHANISTAN"}], citizenships=[{"iso2": "AF", "description": "AFGHANISTAN"}])])
    r = records[0]
    assert r["source"] == "eu"
    assert r["schema"] == "Person"
    assert r["names"] == ["John Smith"]
    assert r["birth_dates"] == ["1971-02-15"]
    assert r["birth_places"] == ["Kabul"]
    assert r["citizenships"] == ["AF"]
    assert r["eu_ref"] == "EU.1"
    assert "regulations" in r["raw"]


def test_eu_records_enterprise():
    r = _eu_records([eu_entity(name="Rosneft", subject_type="enterprise")])[0]
    assert r["schema"] == "Company"
    assert r["names"] == ["Rosneft"]


def write_ftm(root, dataset, entities):
    path = root / dataset / "entities.ftm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in entities:
            fh.write(json.dumps(e) + "\n")


def test_pep_records_filters():
    import tempfile
    root = Path(tempfile.mkdtemp())
    write_ftm(root, "ds1", [
        {"id": "NK-1", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ds1"],
         "properties": {"name": ["JORGE FERNÁNDEZ"], "birthDate": ["1965-03-01"], "citizenship": ["ar"], "topics": ["role.pep"]}},
        {"id": "NK-2", "caption": "Maria", "schema": "Person", "target": False, "datasets": ["ds1"], "properties": {}},
        {"id": "NK-3", "caption": "X", "schema": "Occupancy", "target": True, "datasets": ["ds1"], "properties": {}},
        {"id": "NK-4", "caption": "ACME", "schema": "Company", "target": True, "datasets": ["ds2"], "properties": {"name": ["ACME"]}},
    ])
    write_ftm(root, "ds1", [])  # second file in same dataset must not collide
    records = _pep_records(root)
    ids = [r["id"] for r in records]
    assert ids == ["NK-1", "NK-4"]
    jorge = records[0]
    assert jorge["source"] == "pep"
    assert jorge["names"] == ["JORGE FERNÁNDEZ", "JORGE FERNÁNDEZ"]
    assert jorge["birth_dates"] == ["1965-03-01"]
    assert jorge["citizenships"] == ["ar"]
    assert jorge["datasets"] == ["ds1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_search_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.search_index'`.

- [ ] **Step 3: Write minimal implementation**

`app/search_index.py`:
```python
import json
import os
import re
import unicodedata
from pathlib import Path

THRESHOLD = 90
MAX_RESULTS = 20
INDEX_ENV = "PEP_INDEX_ENABLED"
DB_FILENAME = "search.sqlite"
FTM_FILENAME = "entities.ftm.json"


def default_db_path() -> Path:
    env = os.environ.get("SEARCH_DB")
    if env:
        return Path(env)
    return Path(os.environ.get("SEARCH_DATA_DIR", "data")) / DB_FILENAME


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", fold(text)) if len(t) >= 2]


def _eu_records(entities: list[dict]) -> list[dict]:
    records = []
    for e in entities:
        names = [a["whole_name"] for a in e["aliases"] if a["whole_name"]]
        caption = names[0] if names else e.get("logical_id", "")
        records.append({
            "source": "eu",
            "id": e.get("logical_id", ""),
            "caption": caption,
            "schema": "Company" if e.get("subject_type") == "enterprise" else "Person",
            "names": names,
            "birth_dates": [b["date"] for b in e.get("birthdates", []) if b.get("date")],
            "birth_places": [b.get("place") or b.get("city") for b in e.get("birthdates", []) if b.get("place") or b.get("city")],
            "citizenships": [c["iso2"] for c in e.get("citizenships", []) if c.get("iso2")],
            "political": [],
            "topics": [],
            "datasets": [],
            "eu_ref": e.get("eu_reference_number", ""),
            "raw": e,
        })
    return records


def _extract_entity(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("target"):
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


def _pep_records(pep_root: Path) -> list[dict]:
    records = []
    for ftm in sorted(pep_root.glob(f"*/{FTM_FILENAME}")):
        with ftm.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entity = _extract_entity(line)
                if entity is None:
                    continue
                records.append({
                    "source": "pep",
                    "id": entity["id"],
                    "caption": entity["caption"],
                    "schema": entity["schema"],
                    "names": entity["names"],
                    "birth_dates": entity["birth_dates"],
                    "birth_places": entity["birth_places"],
                    "citizenships": entity["citizenships"],
                    "political": entity["political"],
                    "topics": entity["topics"],
                    "datasets": entity["datasets"],
                    "eu_ref": "",
                    "raw": None,
                })
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_search_index.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/search_index.py tests/test_search_index.py
git commit -m "feat: search index helpers and EU/PEP record extraction"
```

---

### Task 2: search_index — SQLite-schema, build_index, search

**Files:**
- Modify: `app/search_index.py`
- Test: `tests/test_search_index.py`

**Interfaces:**
- Consumes: `_eu_records`, `_pep_records`, `tokens`, `fold`, constants from Task 1.
- Produces:
  - `build_index(db_path: Path, eu_entities: list[dict] | None, pep_root: Path) -> dict` — maakt schema, bulk-insert records, vult `names_fts` (content-backed), schrijft atomic naar `<db_path>.new` + `os.replace`; retourneert `{"eu_count", "pep_count", "total"}`.
  - `_open(db_path: Path) -> sqlite3.Connection` — connectie met `check_same_thread=False`, row_factory `sqlite3.Row`.
  - `_schema(db) -> int` — `SELECT count(*) FROM entities`.
  - `search(db, name, birth_year=None, nationality=None, birth_place=None, entity_type=None, threshold=THRESHOLD, max_results=MAX_RESULTS) -> list[dict]` — FTS5-kandidaten, rijen ophalen, per kandidaat scoren (containment→100, gewichten, drempel, sorteer, cap); retourneert records `{"entity": dict, "score": int, "matched_name": str|None, "details": [{"feature","score","label"}]}` met `entity` inclusief `source`, `id`, `caption`, `schema`, `names`, `birth_dates`, `birth_places`, `citizenships`, `political`, `topics`, `datasets`, `eu_ref`, `raw`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search_index.py`:
```python
import sqlite3

from app.search_index import _open, build_index, search


def build_fixture(root, db_path, include_eu=True, include_pep=True):
    eu = [eu_entity()] if include_eu else []
    if include_pep:
        write_ftm(root, "ds1", [
            {"id": "NK-1", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ds1"],
             "properties": {"name": ["JORGE FERNÁNDEZ"], "birthDate": ["1965-03-01"], "citizenship": ["ar"]}},
        ])
    return build_index(db_path, eu, root)


def test_build_index_counts(tmp_path):
    stats = build_fixture(tmp_path, tmp_path / "search.sqlite")
    assert stats["eu_count"] == 1
    assert stats["pep_count"] == 1
    assert stats["total"] == 2
    assert (tmp_path / "search.sqlite").exists()
    assert not (tmp_path / "search.sqlite.new").exists()


def test_build_index_fresh_overwrites(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    build_fixture(tmp_path, db_path)
    assert _schema(_open(db_path)) == 2


def test_search_exact_and_fuzzy(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "JORGE FERNANDEZ")
    assert results and results[0]["entity"]["source"] == "pep"
    assert results[0]["score"] == 100
    assert results[0]["matched_name"] == "JORGE FERNÁNDEZ"
    results = search(db, "JORGE FERNÁNDEZ")
    assert results and results[0]["entity"]["id"] == "NK-1"


def test_search_eu_source_and_raw(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "John Smith")
    assert results and results[0]["entity"]["source"] == "eu"
    assert results[0]["entity"]["raw"]["eu_reference_number"] == "EU.1"


def test_search_entity_type_filter(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    assert [r["entity"]["source"] for r in search(db, "JORGE", entity_type="enterprise")] == []
    assert search(db, "JORGE", entity_type="person")[0]["entity"]["source"] == "pep"


def test_search_threshold_and_max(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    assert search(db, "JORGE", threshold=0, max_results=1)
    assert len(search(db, "JORGE", threshold=0, max_results=1)) == 1
    assert search(db, "Zzqqq Xxww") == []
    assert search(db, "!!") == []


def test_search_birth_year_and_nationality(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "JORGE", birth_year=1965, nationality="ar", threshold=60)
    assert results and results[0]["score"] >= 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_search_index.py -k "build_index or search" -v`
Expected: FAIL with `ImportError: cannot import name 'build_index'`.

- [ ] **Step 3: Write implementation**

Append to `app/search_index.py`:
```python
import sqlite3
from rapidfuzz import fuzz

SCHEMA = """
CREATE TABLE entities (
  rowid INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  id TEXT NOT NULL,
  caption TEXT NOT NULL,
  schema TEXT NOT NULL,
  names TEXT NOT NULL,
  names_folded TEXT NOT NULL,
  birth_dates TEXT NOT NULL,
  birth_places TEXT NOT NULL,
  citizenships TEXT NOT NULL,
  political TEXT NOT NULL,
  topics TEXT NOT NULL,
  datasets TEXT NOT NULL,
  eu_ref TEXT,
  raw TEXT
);
CREATE VIRTUAL TABLE names_fts USING fts5(names_folded, content='entities', content_rowid='rowid');
"""


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _schema(db) -> int:
    return db.execute("SELECT count(*) FROM entities").fetchone()[0]


def build_index(db_path: Path, eu_entities: list[dict] | None, pep_root: Path) -> dict:
    eu_entities = eu_entities or []
    records = _eu_records(eu_entities) + _pep_records(pep_root)
    new_path = db_path.with_suffix(db_path.suffix + ".new")
    db = _open(new_path)
    db.executescript(SCHEMA)
    db.executemany(
        "INSERT INTO entities (source, id, caption, schema, names, names_folded, birth_dates, birth_places, citizenships, political, topics, datasets, eu_ref, raw) "
        "VALUES (:source, :id, :caption, :schema, :names, :names_folded, :birth_dates, :birth_places, :citizenships, :political, :topics, :datasets, :eu_ref, :raw)",
        [{
            "source": r["source"],
            "id": r["id"],
            "caption": r["caption"],
            "schema": r["schema"],
            "names": json.dumps(r["names"], ensure_ascii=False),
            "names_folded": " ".join(tokens(" ".join(r["names"]))),
            "birth_dates": json.dumps(r["birth_dates"], ensure_ascii=False),
            "birth_places": json.dumps(r["birth_places"], ensure_ascii=False),
            "citizenships": json.dumps(r["citizenships"], ensure_ascii=False),
            "political": json.dumps(r["political"], ensure_ascii=False),
            "topics": json.dumps(r["topics"], ensure_ascii=False),
            "datasets": json.dumps(r["datasets"], ensure_ascii=False),
            "eu_ref": r["eu_ref"],
            "raw": json.dumps(r["raw"], ensure_ascii=False) if r["raw"] is not None else None,
        } for r in records],
    )
    for idx, r in enumerate(records):
        names_folded = " ".join(tokens(" ".join(r["names"])))
        db.execute("INSERT INTO names_fts (rowid, names_folded) VALUES (?, ?)", (idx + 1, names_folded))
    db.commit()
    db.close()
    new_path.replace(db_path)
    counts = {"eu_count": sum(1 for r in records if r["source"] == "eu"), "pep_count": sum(1 for r in records if r["source"] == "pep")}
    counts["total"] = counts["eu_count"] + counts["pep_count"]
    return counts
```

Let op: `_schema` is `SELECT count(*) FROM entities` maar retourneert een int via `[0]`. Pas de helper aan zodat `_schema(db)` het aantal teruggeeft (zie bovenstaande `_schema`).

Append scoring + search:
```python
def _birth_year(value: str) -> int | None:
    match = re.match(r"(\d{4})", value)
    return int(match.group(1)) if match else None


def _name_score(names: list[str], query: str) -> tuple[int, str | None]:
    best = 0
    best_name = None
    q = fold(query).strip()
    q_tokens = set(tokens(q))
    for name in names:
        if not name:
            continue
        c_tokens = set(tokens(name))
        if q_tokens and c_tokens and q_tokens <= c_tokens:
            score = 100
        else:
            score = fuzz.token_set_ratio(q, fold(name))
        if score > best:
            best = score
            best_name = name
    return best, best_name


def search(db, name, birth_year=None, nationality=None, birth_place=None, entity_type=None, threshold=THRESHOLD, max_results=MAX_RESULTS):
    query_tokens = tokens(name)
    if not query_tokens:
        return []
    match_expr = " AND ".join(f'"{t}"' for t in query_tokens)
    rows = db.execute(
        "SELECT e.rowid, e.source, e.id, e.caption, e.schema, e.names, e.birth_dates, e.birth_places, e.citizenships, e.political, e.topics, e.datasets, e.eu_ref, e.raw "
        "FROM names_fts JOIN entities e ON e.rowid = names_fts.rowid WHERE names_fts MATCH ?",
        (match_expr,),
    ).fetchall()
    results = []
    for row in rows:
        entity = {
            "source": row["source"],
            "id": row["id"],
            "caption": row["caption"],
            "schema": row["schema"],
            "names": json.loads(row["names"]),
            "birth_dates": json.loads(row["birth_dates"]),
            "birth_places": json.loads(row["birth_places"]),
            "citizenships": json.loads(row["citizenships"]),
            "political": json.loads(row["political"]),
            "topics": json.loads(row["topics"]),
            "datasets": json.loads(row["datasets"]),
            "eu_ref": row["eu_ref"],
            "raw": json.loads(row["raw"]) if row["raw"] else None,
        }
        if entity_type == "person" and entity["schema"] != "Person":
            continue
        if entity_type == "enterprise" and entity["schema"] != "Company":
            continue
        n_score, matched = _name_score(entity["names"], name)
        weights = [60]
        details = [{"feature": "naam", "score": n_score, "label": f'Naam {n_score}% (via "{matched}")' if matched else "Naam 0%"}]
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
            details.append({"feature": "geboortejaar", "score": best, "label": "Geboortejaar exact" if best == 100 else f"Geboortejaar ({best}%)"})
        if nationality:
            q = nationality.strip().upper()
            best = max((100 for c in entity["citizenships"] if c.strip().upper() == q), default=0)
            weights.append(10)
            details.append({"feature": "nationaliteit", "score": best, "label": "Nationaliteit match" if best >= 85 else f"Nationaliteit ({best}%)"})
        if birth_place:
            best = max((fuzz.token_set_ratio(birth_place.strip(), fold(p)) for p in entity["birth_places"]), default=0)
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

Run: `.venv/bin/python -m pytest tests/test_search_index.py -v`
Expected: 14 passed (6 + 8).

- [ ] **Step 5: Commit**

```bash
git add app/search_index.py tests/test_search_index.py
git commit -m "feat: SQLite+FTS5 index build and search"
```

---

### Task 3: search_index — ensure_index met mtime + atomic rebuild

**Files:**
- Modify: `app/search_index.py`
- Test: `tests/test_search_index.py`

**Interfaces:**
- Consumes: `build_index`, `_open`, `_schema`, `fold`/`tokens` from Tasks 1-2.
- Produces:
  - `_newest_input_mtime(eu_xml: Path, pep_root: Path, datasets_json: Path | None = None) -> float`.
  - `index_fresh(db_path: Path, eu_xml: Path, pep_root: Path) -> bool` — DB bestaat en `db_path.stat().st_mtime >=` nieuwste input-mtime.
  - `load_stats(db) -> dict` — `{"eu_count", "pep_count", "total", "source_count"}` via SQL.
  - `ensure_index(db_path: Path, eu_xml: Path, pep_root: Path) -> dict` — retourneert `{"db": _open(db_path) | None, "ready": bool, "stats": {...} | None}`; bouwt NIET zelf (de app bouwt in de achtergrond), alleen openen als fresh.
  - `rebuild_index(db_path: Path, eu_xml: Path, pep_root: Path) -> dict` — parset EU-XML (`ingest.parse_export`) + `build_index`; retourneert stats; bij fout gooit het door (caller logt).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search_index.py`:
```python
import os
import time

from app.search_index import ensure_index, index_fresh, load_stats, rebuild_index


def test_index_fresh_logic(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    assert index_fresh(db_path, eu_xml, tmp_path) is True
    future = time.time() + 1000
    os.utime(eu_xml, (future, future))
    assert index_fresh(db_path, eu_xml, tmp_path) is False


def test_ensure_index_opens_fresh(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    result = ensure_index(db_path, eu_xml, tmp_path)
    assert result["ready"] is True
    assert result["stats"]["total"] == 2


def test_ensure_index_not_ready_when_missing(tmp_path):
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    result = ensure_index(tmp_path / "search.sqlite", eu_xml, tmp_path)
    assert result["ready"] is False
    assert result["db"] is None


def test_load_stats(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    stats = load_stats(_open(db_path))
    assert stats["eu_count"] == 1
    assert stats["pep_count"] == 1


def test_rebuild_index(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    stats = rebuild_index(db_path, eu_xml, tmp_path)
    assert stats["total"] == 2
    assert index_fresh(db_path, eu_xml, tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_search_index.py -k "fresh or ensure or stats or rebuild" -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_index'`.

- [ ] **Step 3: Write implementation**

Append to `app/search_index.py`:
```python
from . import ingest


def _newest_input_mtime(eu_xml: Path, pep_root: Path) -> float:
    newest = 0.0
    for path in [eu_xml, pep_root / "datasets.json"]:
        if path.exists():
            newest = max(newest, path.stat().st_mtime)
    for ftm in pep_root.glob(f"*/{FTM_FILENAME}"):
        newest = max(newest, ftm.stat().st_mtime)
    return newest


def index_fresh(db_path: Path, eu_xml: Path, pep_root: Path) -> bool:
    if not db_path.exists():
        return False
    return db_path.stat().st_mtime >= _newest_input_mtime(eu_xml, pep_root)


def load_stats(db) -> dict:
    eu = db.execute("SELECT count(*) FROM entities WHERE source = 'eu'").fetchone()[0]
    pep = db.execute("SELECT count(*) FROM entities WHERE source = 'pep'").fetchone()[0]
    sources = db.execute("SELECT count(DISTINCT json_each.value) FROM entities, json_each(datasets) WHERE source = 'pep'").fetchone()[0]
    return {"eu_count": eu, "pep_count": pep, "total": eu + pep, "source_count": sources}


def ensure_index(db_path: Path, eu_xml: Path, pep_root: Path) -> dict:
    if index_fresh(db_path, eu_xml, pep_root):
        db = _open(db_path)
        return {"db": db, "ready": True, "stats": load_stats(db)}
    return {"db": None, "ready": False, "stats": None}


def rebuild_index(db_path: Path, eu_xml: Path, pep_root: Path) -> dict:
    entities = ingest.parse_export(eu_xml.read_bytes()) if eu_xml.exists() else []
    return build_index(db_path, entities, pep_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_search_index.py -v`
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add app/search_index.py tests/test_search_index.py
git commit -m "feat: index freshness check, stats and atomic rebuild"
```

---

### Task 4: main.py-integratie (DB-state, status, search) + verwijder pep_index.py

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_main.py`
- Delete: `app/pep_index.py`, `tests/test_pep_index.py`

**Interfaces:**
- Consumes: `search_index.ensure_index`, `search_index.rebuild_index`, `search_index.search`, `search_index.load_stats`, `search_index.default_db_path`, `search_index.INDEX_ENV`; `eu_ingest` (manifest), `ingest.parse_export`; `matcher.MAX_RESULTS`.
- Produces:
  - `default_search_db() -> Path` — `SEARCH_DB` env of `SEARCH_DATA_DIR`/`search.sqlite`.
  - `create_app(..., search_db: Path | None = None, db_sync: bool | None = None)`:
    - `state = {"db_path", "index_status": "disabled"|"ready"|"building", "index_stats", "index_error", "meta"}`.
    - Bij startup: EU-XML-pad = `eu_root / eu_ingest.XML_FILENAME`; PEP-root = `pep_root`. Als `_pep_enabled(pep_root)` of `PEP_INDEX_ENABLED`/EU-data aanwezig → `ensure_index`; als ready → `state["index_status"]="ready"`, `index_stats`=load_stats. Anders: `state["index_status"]="building"` en achtergrond-thread `_build_index(state, ...)`.
    - `_build_index(state, db_path, eu_xml, pep_root)` — `rebuild_index(...)`, daarna `state["index_stats"]=load_stats`, `state["index_status"]="ready"`; bij fout: `index_status="error"`, `index_error`.
    - `_status()`: `entity_count` = `index_stats["total"]` (of 0), `index: {enabled, status, eu_count, pep_count, source_count}` i.p.v. `pep_index`.
    - `search()`: één `search_index.search(db, ...)` (per request nieuwe verbinding via `_open(state["db_path"])`), resultaten met `source` uit de entity; serialiseer EU (`raw`) en PEP (datasets-metadata uit `pep_root/datasets.json`). `state["entities"]` verdwijnt.
  - Verwijder `pep_index` import en `_serialize_pep_result`/`_serialize_eu_result` worden aangepast aan de nieuwe recordvorm.

- [ ] **Step 1: Write/update the failing tests**

Vervang de PEP-fixture-tests in `tests/test_main.py` door op SQLite gebaseerde tests. Bestaande EU-tests die `create_app(entities=ENTITIES)` gebruiken blijven werken: `create_app` accepteert nog steeds `entities` voor testdoeleinden, maar de search gebruikt de DB als die ready is; als er géén `search_db` wordt meegegeven en er geen data is → `index_status="disabled"` en EU-zoek valt terug op `state["entities"]`.

Pas `tests/test_main.py` aan:
- Voeg bovenin toe: `from app import search_index` en helpers om een tmp SQLite te bouwen.
- Voeg een autouse-fixture toe die `SEARCH_DB` en `PEP_INDEX_ENABLED` neutraliseert zodat bestaande tests niet per ongeluk de echte `data/search.sqlite` gebruiken.
- Nieuwe tests:
```python
def _write_search_db(root):
    from app.search_index import build_index
    write_pep_fixture(root)  # hergebruik bestaande _write_pep_fixture naar tmp
    return build_index(root / "search.sqlite", [make_eu_entity()], root)


def make_eu_entity():
    return {
        "logical_id": "EU.1", "eu_reference_number": "EU.1", "united_nations_id": "",
        "designation_date": "2022-01-01", "subject_type": "person",
        "aliases": [{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}],
        "citizenships": [], "birthdates": [], "addresses": [], "identifications": [],
        "regulations": [{"number_title": "2022/123", "publication_date": "2022-02-01", "programme": "XX", "publication_url": "https://eur-lex.europa.eu/x"}],
        "remarks": [],
    }


def test_status_index_ready(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/status").json()
    assert data["index"]["status"] == "ready"
    assert data["index"]["pep_count"] == 1
    assert data["index"]["eu_count"] == 1


def test_search_db_merges_eu_and_pep(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()
    assert [r["source"] for r in data["results"]] == ["pep"]
    first = [r for r in data["results"] if r["source"] == "pep"][0]
    assert first["pep"]["datasets"][0]["id"] == "ar_parliament"
    data = client.get("/api/search", params={"name": "John Smith"}).json()
    eu = [r for r in data["results"] if r["source"] == "eu"][0]
    assert eu["eu"]["matched_alias"] == "John Smith"
```
En verwijder `tests/test_pep_index.py` (logica zit nu in `test_search_index.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_main.py -k "index or db" -v`
Expected: FAIL (nieuwe velden/zoekpad ontbreken).

- [ ] **Step 3: Implement `app/main.py`**

Vervang de `pep_index`-afhankelijkheid:
```python
from . import eu_ingest, ingest, matcher, opensanctions
from . import search_index
```

Voeg toe (na `EU_ROOT = default_eu_root()`):
```python
def default_search_db() -> Path:
    return search_index.default_db_path()


SEARCH_DB = default_search_db()
```

Vervang `_serialize_eu_result` door een versie die vanuit het DB-record (met `raw`) serialiseert:
```python
def _serialize_eu_result(result: dict, query_name: str) -> dict:
    entity = result["entity"]
    raw = entity.get("raw") or {}
    aliases = [a["whole_name"] for a in raw.get("aliases", []) if a.get("whole_name")]
    return {
        "source": "eu",
        "score": result["score"],
        "entity": {
            "name": result["matched_name"] or query_name,
            "eu_reference_number": raw.get("eu_reference_number", entity.get("eu_ref", "")),
            "united_nations_id": raw.get("united_nations_id", ""),
            "subject_type": raw.get("subject_type", ""),
            "designation_date": raw.get("designation_date", ""),
            "aliases": aliases,
            "citizenships": raw.get("citizenships", []),
            "birthdates": raw.get("birthdates", []),
            "addresses": raw.get("addresses", []),
            "identifications": raw.get("identifications", []),
            "regulations": raw.get("regulations", []),
            "function": next((a["function"] for a in raw.get("aliases", []) if a.get("function")), ""),
            "remarks": raw.get("remarks", []),
        },
        "eu": {
            "total_score": result["score"],
            "matched_alias": result["matched_name"],
            "details": result["details"],
        },
        "opensanctions": None,
    }
```

Vervang `_serialize_pep_result` door een versie die dataset-metadata uit `pep_root/datasets.json` laadt:
```python
def _serialize_pep_result(result: dict, datasets_meta: dict) -> dict:
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
        "source": "pep",
        "score": result["score"],
        "entity": {
            "name": entity.get("caption", ""),
            "schema": entity.get("schema", ""),
            "birth_dates": entity.get("birth_dates", []),
            "birth_places": entity.get("birth_places", []),
            "citizenships": entity.get("citizenships", []),
            "political": entity.get("political", []),
            "topics": entity.get("topics", []),
        },
        "pep": {
            "id": entity.get("id", ""),
            "url": f"https://opensanctions.org/entities/{entity.get('id', '')}",
            "datasets": datasets,
            "matched_name": result["matched_name"],
            "details": result["details"],
        },
        "eu": None,
        "opensanctions": None,
    }
```

Voeg `_build_index` en herschrijf `create_app` (de EU-entities blijven alleen als fallback wanneer de DB disabled is):
```python
def _build_index(state: dict, db_path: Path, eu_xml: Path, pep_root: Path) -> None:
    try:
        search_index.rebuild_index(db_path, eu_xml, pep_root)
        state["index_stats"] = search_index.load_stats(search_index._open(db_path))
        state["index_status"] = "ready"
        state["index_error"] = None
    except Exception:
        logger.exception("Index-rebuild mislukt")
        state["index_status"] = "error"
        state["index_error"] = "Index-rebuild mislukt"


def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    eu_root: Path = EU_ROOT,
    static_dir: Path = STATIC_DIR,
    pep_root: Path = PEP_ROOT,
    pep_sync: bool | None = None,
    search_db: Path | None = None,
) -> FastAPI:
    meta = eu_ingest.load_eu_manifest(eu_root)
    eu_xml = eu_root / eu_ingest.XML_FILENAME
    if entities is None:
        entities = ingest.parse_export(eu_xml.read_bytes()) if eu_xml.exists() else []
        meta.setdefault("status", "ok" if entities else "missing")
    if os_api_key is None:
        os_api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    if pep_sync is None:
        pep_sync = os.environ.get("PEP_INDEX_SYNC", "").strip().lower() in ("1", "true", "yes")
    db_path = search_db if search_db is not None else default_search_db()
    enabled = _pep_enabled(pep_root) or eu_xml.exists()
    state = {"db_path": db_path, "index_status": "disabled", "index_stats": None, "index_error": None, "entities": entities, "meta": meta}
    datasets_meta = _load_datasets_meta(pep_root)
    if enabled:
        result = search_index.ensure_index(db_path, eu_xml, pep_root)
        if result["ready"]:
            state["index_status"] = "ready"
            state["index_stats"] = result["stats"]
        elif pep_sync:
            _build_index(state, db_path, eu_xml, pep_root)
        else:
            state["index_status"] = "building"
            threading.Thread(target=_build_index, args=(state, db_path, eu_xml, pep_root), daemon=True).start()
    opensanctions_active = bool(os_api_key)
    ...
```

Voeg helper toe:
```python
def _load_datasets_meta(pep_root: Path) -> dict:
    path = pep_root / "datasets.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
```

Herschrijf `_status()` (vervang `pep_index`-blok):
```python
        stats = state["index_stats"] or {}
        return {
            "version": os.environ.get("APP_VERSION", "dev"),
            "cached_at": meta.get("downloaded_at"),
            "generated_at": meta.get("generation_date"),
            "entity_count": stats.get("total", len(state["entities"])),
            "data_age_hours": _data_age_hours(meta.get("downloaded_at")),
            "opensanctions_active": opensanctions_active,
            "source": meta.get("status", "unknown"),
            "index": {
                "enabled": state["index_status"] != "disabled",
                "status": state["index_status"],
                "eu_count": stats.get("eu_count", 0),
                "pep_count": stats.get("pep_count", 0),
                "source_count": stats.get("source_count", 0),
                "error": state["index_error"],
            },
        }
```

Herschrijf `search()`:
```python
        results = []
        warnings = []
        if state["index_status"] == "ready":
            db = search_index._open(state["db_path"])
            try:
                for r in search_index.search(db, query.name, query.birth_year, query.nationality, query.birth_place, query.entity_type):
                    if r["entity"]["source"] == "eu":
                        results.append(_serialize_eu_result(r, query.name))
                    else:
                        results.append(_serialize_pep_result(r, datasets_meta))
            finally:
                db.close()
        elif state["index_status"] == "building":
            warnings.append("Zoekindex wordt opgebouwd; probeer het zo nog eens")
        else:
            for r in matcher.search_eu(state["entities"], query):
                results.append(_serialize_eu_result_from_dict(r, query.name))
```
(`_serialize_eu_result_from_dict` = oude `_serialize_eu_result` voor `matcher.EuMatchResult`; hernoem zodat de DB-versie de nieuwe naam krijgt. Zie stap 3 voor de exacte naamgeving.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: alle tests (aangepast + nieuw) groen. Verwijder `tests/test_pep_index.py` en `app/pep_index.py`.

- [ ] **Step 5: Commit**

```bash
git rm app/pep_index.py tests/test_pep_index.py
git add app/main.py tests/test_main.py
git commit -m "feat: wire SQLite search index into app, remove in-memory pep index"
```

---

### Task 5: Frontend-status + docs + Docker

**Files:**
- Modify: `static/app.js`
- Modify: `README.md`
- Modify: `Dockerfile`, `docker-compose.yml`, `server/docker-compose.yml` (niet in deze repo) indien nodig
- Test: n.v.t. (frontend), volledige suite ter verificatie

**Interfaces:**
- Consumes: `/api/status` → `index.status` (`disabled|ready|building|error`), `index.eu_count`, `index.pep_count`.
- Produces: statusregel toont index-status; docs verwijzen naar SQLite.

- [ ] **Step 1: Update `static/app.js`**

Vervang in `loadStatus` het `pep_index`-blok:
```js
    if (s.index) {
      if (s.index.status === "building") {
        parts.push("Index wordt opgebouwd…");
      } else if (s.index.status === "error") {
        parts.push("Index-fout");
      } else if (s.index.enabled) {
        parts.push(`${s.index.pep_count.toLocaleString("nl-NL")} PEP-records`);
      }
    }
```

- [ ] **Step 2: Update `README.md`**

- In de "Data"-sectie: beschrijf `data/search.sqlite` (SQLite+FTS5-zoekindex over EU+PEP) i.p.v. de in-memory/pickle-index; `SEARCH_DB`-env; `PEP_INDEX_ENABLED=0` schakelt PEP uit.
- In de PEP-sectie: de app herbouwt de index automatisch in de achtergrond als de data verandert (geen restart nodig).

- [ ] **Step 3: Verify — full suite + Docker**

Run:
```bash
.venv/bin/python -m pytest -v
```
Expected: alle tests groen. Bouw daarna `podman build -f Dockerfile -t sanctielijst-app:test .` en draai een smoke-test (health + search) zoals eerder.

- [ ] **Step 4: Commit**

```bash
git add static/app.js README.md
git commit -m "feat: index status in UI and SQLite docs"
```

---

## Self-Review

**Spec coverage:**
- Eén SQLite+FTS5-index voor EU+PEP → Task 1-2 (schema, records, build) ✔
- Zoekresultaten identiek (scorelogica, drempel 90, containment) → Task 2 ✔
- Atomic rebuild + mtime-trigger + achtergrond → Task 3 ✔
- App zonder in-memory index; status `index.*`; één zoekpad → Task 4 ✔
- `pep_index.py` verwijderd → Task 4 ✔
- Frontend-status + docs + Docker → Task 5 ✔
- RAM ~30-50MB, instant startup → Task 3-4 (per-request verbinding, geen persistent connection) ✔

**Placeholders:** geen TBD/TODO; complete code per stap.

**Type-consistentie:** `fold`, `tokens`, `_eu_records`, `_pep_records`, `build_index`, `_open`, `_schema`, `load_stats`, `search`, `ensure_index`, `index_fresh`, `rebuild_index`, `default_db_path` worden identiek gebruikt in latere tasks als gedefinieerd in eerdere.

**Notitie:** de exacte naamgeving van `_serialize_eu_result`-varianten in Task 4 kan licht afwijken van de huidige `main.py`-staat (de parallelle agent wijzigt dit bestand). Lees vóór implementatie de actuele `main.py` en behoud de bestaande serializers waar mogelijk; voeg de DB-varianten toe zonder bestaande tests te breken.
