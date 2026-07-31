# Sanctielijst Zoeker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Dutch-language web app to search persons and companies in the EU sanctions list (data.europa.eu XML), with fuzzy name matching, per-feature match explanations, and optional OpenSanctions `/match` screening.

**Architecture:** FastAPI server parses the EU XML 1.1 into an in-memory index with a 24h TTL cache in `data/`. A matcher module scores entities per feature (naam 60%, geboortejaar 20%, nationaliteit 10%, geboorteplaats 10%) using rapidfuzz; results ≥ threshold 60 are returned with explainable detail chips. An optional OpenSanctions client runs in parallel when `OPENSANCTIONS_API_KEY` is set. A vanilla HTML/JS frontend renders combined, source-badged result cards.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, rapidfuzz, requests, python-dotenv, pytest, httpx (for TestClient).

## Global Constraints

- Python 3.11+ only; no other runtime.
- Dependencies limited to: `fastapi`, `uvicorn[standard]`, `rapidfuzz`, `requests`, `python-dotenv`, and dev deps `pytest`, `httpx`.
- UI language: **Nederlands** (all copy in interface, labels, chips, warnings).
- EU data source (constant): `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw`
- OpenSanctions endpoint (constant): `https://api.opensanctions.org/match/default`, auth header `Authorization: ApiKey <key>`, key from env `OPENSANCTIONS_API_KEY`.
- Cache TTL: 24 hours. EU download timeout 120s, OpenSanctions timeout 30s.
- Scoring weights: naam 60, geboortejaar 20, nationaliteit 10, geboorteplaats 10; threshold 60; max results 20.
- No code comments unless the code is non-obvious (e.g. cache fallback logic).

---
### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `pytest.ini`
- Create: `data/.gitkeep`
- Create: `app/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: installable dev environment; pytest runs from repo root with `app` and `tests` importable as packages.

- [ ] **Step 1: Create dependency manifest**

`requirements.txt`:
```
fastapi==0.115.0
uvicorn[standard]==0.32.0
rapidfuzz==3.9.7
requests==2.32.3
python-dotenv==1.0.1
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 2: Create env example and gitignore**

`.env.example`:
```
# Vul je gratis OpenSanctions API-key in om wereldwijde screening in te schakelen.
# Gratis key: https://www.opensanctions.org/account/ (vrij voor niet-commercieel gebruik)
OPENSANCTIONS_API_KEY=
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.env
data/*.xml
data/cache_meta.json
.pytest_cache/
```

- [ ] **Step 3: Create pytest config**

`pytest.ini`:
```
[pytest]
testpaths = tests
```

- [ ] **Step 4: Create package markers and data dir**

Create empty files `data/.gitkeep`, `app/__init__.py`, `tests/__init__.py`.

- [ ] **Step 5: Write the smoke test**

`tests/test_smoke.py`:
```python
def test_smoke():
    assert 1 + 1 == 2
```

- [ ] **Step 6: Run the test suite**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .env.example .gitignore pytest.ini data/.gitkeep app/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: scaffold sanctielijst project"
```

---
### Task 2: EU XML parser

**Files:**
- Create: `app/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: nothing (stdlib `xml.etree.ElementTree` only).
- Produces:
  - `parse_export(xml_bytes: bytes) -> list[dict]` — one dict per `<sanctionEntity>` with exact keys: `logical_id`, `eu_reference_number`, `united_nations_id`, `designation_date`, `subject_type` (`"person"|"enterprise"`), `aliases` (`[{whole_name, first_name, last_name, strong, function, title}]`), `citizenships` (`[{iso2, description}]`), `birthdates` (`[{date, year, year_from, year_to, city, place, iso2, country}]`), `addresses` (`[{city, street, region, iso2, country}]`), `identifications` (`[{number, type_code, type_description, iso2}]`), `regulations` (`[{number_title, publication_date, programme, publication_url}]`), `remarks` (`list[str]`).

- [ ] **Step 1: Write the failing parse test**

`tests/test_ingest.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest'`.

- [ ] **Step 3: Write minimal parser**

`app/ingest.py`:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest.py tests/test_ingest.py
git commit -m "feat: parse EU sanctions XML into entity dicts"
```

---
### Task 3: EU data download + cache

**Files:**
- Modify: `app/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `parse_export` from Task 2.
- Produces:
  - `DATASET_URL: str` (constant)
  - `CACHE_TTL: int` (constant, 86400)
  - `XML_FILENAME = "eu_sanctions.xml"`, `META_FILENAME = "cache_meta.json"`
  - `download_xml(url: str = DATASET_URL, timeout: int = 120) -> bytes`
  - `refresh(cache_dir: Path, url: str = DATASET_URL) -> dict` — downloads, writes XML + meta, returns meta with keys `cached_at` (unix), `generated_at` (str), `entity_count` (int).
  - `load_index(cache_dir: Path, url: str = DATASET_URL, ttl: int = CACHE_TTL) -> tuple[list[dict], dict]` — returns `(entities, meta)`; meta additionally has `source` (`"fresh"|"cached"`) and optionally `error`. Downloads when cache missing/stale; on download failure falls back to cached XML if present, else raises.

- [ ] **Step 1: Write the failing cache tests**

Append to `tests/test_ingest.py`:
```python
import json
from pathlib import Path
import pytest
from app.ingest import download_xml, load_index, refresh


def write_cache(tmp_path: Path, xml: bytes, cached_at: int):
    (tmp_path / "eu_sanctions.xml").write_bytes(xml)
    meta = {"cached_at": cached_at, "generated_at": "2026-07-28T11:43:32+02:00", "entity_count": 2}
    (tmp_path / "cache_meta.json").write_text(json.dumps(meta))


def test_download_xml_calls_requests(monkeypatch):
    import requests
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def content(self):
            return b"<xml/>"

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "get", fake_get)
    result = download_xml()
    assert result == b"<xml/>"
    assert captured["url"] == "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
    assert captured["timeout"] == 120


def test_refresh_downloads_and_writes(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: xml)
    meta = refresh(tmp_path)
    assert (tmp_path / "eu_sanctions.xml").read_bytes() == xml
    assert (tmp_path / "cache_meta.json").exists()
    assert meta["entity_count"] == 2
    assert meta["generated_at"] == "2026-07-28T11:43:32+02:00"


def test_load_index_downloads_when_missing(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: xml)
    entities, meta = load_index(tmp_path, ttl=86400)
    assert len(entities) == 2
    assert meta["source"] == "fresh"


def test_load_index_uses_cache_when_fresh(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    write_cache(tmp_path, xml, cached_at=9999999999)
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: pytest.fail("should not download"))
    entities, meta = load_index(tmp_path, ttl=86400)
    assert len(entities) == 2
    assert meta["source"] == "cached"


def test_load_index_falls_back_to_cache_on_error(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    write_cache(tmp_path, xml, cached_at=1)
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    entities, meta = load_index(tmp_path, ttl=0)
    assert len(entities) == 2
    assert meta["source"] == "cached"
    assert "boom" in meta["error"]


def test_load_index_raises_when_no_cache_and_download_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        load_index(tmp_path, ttl=0)
```

- [ ] **Step 2: Create the test fixture**

Create `tests/fixtures/eu_sample.xml` with the exact same XML content as `FIXTURE` in Task 2 (the two-entity export, `generationDate="2026-07-28T11:43:32+02:00"`).

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ImportError: cannot import name 'download_xml'`.

- [ ] **Step 4: Implement download + cache**

Append to `app/ingest.py`:
```python
import json
import time
from pathlib import Path

import requests

DATASET_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
CACHE_TTL = 24 * 60 * 60
XML_FILENAME = "eu_sanctions.xml"
META_FILENAME = "cache_meta.json"


def _read_generation_date(xml_bytes: bytes) -> str:
    return ET.fromstring(xml_bytes).get("generationDate", "")


def download_xml(url: str = DATASET_URL, timeout: int = 120) -> bytes:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def refresh(cache_dir: Path, url: str = DATASET_URL) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    content = download_xml(url)
    (cache_dir / XML_FILENAME).write_bytes(content)
    meta = {
        "cached_at": int(time.time()),
        "generated_at": _read_generation_date(content),
        "entity_count": len(parse_export(content)),
    }
    (cache_dir / META_FILENAME).write_text(json.dumps(meta))
    return meta


def load_index(cache_dir: Path, url: str = DATASET_URL, ttl: int = CACHE_TTL) -> tuple[list[dict], dict]:
    xml_path = cache_dir / XML_FILENAME
    meta_path = cache_dir / META_FILENAME
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    age = time.time() - meta.get("cached_at", 0) if meta.get("cached_at") else None
    stale = age is None or age > ttl
    if stale:
        try:
            meta = refresh(cache_dir, url)
            meta["source"] = "fresh"
        except Exception as exc:
            if xml_path.exists():
                meta = dict(meta)
                meta["source"] = "cached"
                meta["error"] = str(exc)
            else:
                raise
    else:
        meta = dict(meta)
        meta["source"] = "cached"
    return parse_export(xml_path.read_bytes()), meta
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add app/ingest.py tests/test_ingest.py tests/fixtures/eu_sample.xml
git commit -m "feat: EU XML download with 24h cache and fallback"
```

---
### Task 4: Matcher — name scoring

**Files:**
- Create: `app/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SearchQuery` dataclass: `name: str`, `birth_year: int | None = None`, `nationality: str | None = None`, `birth_place: str | None = None`, `entity_type: str | None = None`.
  - `name_score(query_name: str, aliases: list[dict]) -> tuple[int, str | None]` — best rapidfuzz `token_set_ratio` (0–100) across aliases; strong aliases get a 1.2x bonus capped at 100; returns `(score, matched_alias_whole_name)`.
  - Constants: `WEIGHT_NAME = 60`, `WEIGHT_BIRTH_YEAR = 20`, `WEIGHT_NATIONALITY = 10`, `WEIGHT_BIRTH_PLACE = 10`, `STRONG_BONUS = 1.2`.

- [ ] **Step 1: Write the failing name-score tests**

`tests/test_matcher.py`:
```python
from app.matcher import STRONG_BONUS, WEIGHT_NAME, name_score


def alias(whole, strong=False):
    return {"whole_name": whole, "first_name": "", "last_name": "", "strong": strong, "function": "", "title": ""}


def test_name_exact_100():
    aliases = [alias("John Smith", strong=True)]
    score, matched = name_score("John Smith", aliases)
    assert score == 100
    assert matched == "John Smith"


def test_name_fuzzy_high():
    aliases = [alias("John Smith", strong=True)]
    score, _ = name_score("Jhon Smit", aliases)
    assert 80 <= score <= 99


def test_name_strong_bonus_beats_weak():
    aliases = [alias("Jon Smit", strong=False), alias("John Smith", strong=True)]
    strong_score, strong_alias = name_score("John Smith", aliases)
    weak_score, weak_alias = name_score("Jon Smit", aliases)
    assert strong_alias == "John Smith"
    assert weak_alias == "Jon Smit"
    assert weak_score >= 80
    assert strong_score >= weak_score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_matcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.matcher'`.

- [ ] **Step 3: Write name scoring**

`app/matcher.py`:
```python
from dataclasses import dataclass

from rapidfuzz import fuzz

WEIGHT_NAME = 60
WEIGHT_BIRTH_YEAR = 20
WEIGHT_NATIONALITY = 10
WEIGHT_BIRTH_PLACE = 10
STRONG_BONUS = 1.2


@dataclass
class SearchQuery:
    name: str
    birth_year: int | None = None
    nationality: str | None = None
    birth_place: str | None = None
    entity_type: str | None = None


def name_score(query_name: str, aliases: list[dict]) -> tuple[int, str | None]:
    best_score = 0
    best_alias = None
    q = query_name.strip()
    for alias in aliases:
        candidate = alias["whole_name"] or f"{alias['first_name']} {alias['last_name']}".strip()
        if not candidate:
            continue
        score = fuzz.token_set_ratio(q, candidate)
        if alias["strong"]:
            score = min(100, int(score * STRONG_BONUS))
        if score > best_score:
            best_score = score
            best_alias = alias["whole_name"] or candidate
    return best_score, best_alias
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_matcher.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/matcher.py tests/test_matcher.py
git commit -m "feat: fuzzy name scoring with strong-alias bonus"
```

---
### Task 5: Matcher — full entity scoring + search

**Files:**
- Modify: `app/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `SearchQuery`, `name_score`, weight constants from Task 4.
- Produces:
  - `MatchDetail` dataclass: `feature: str`, `score: int`, `label: str`.
  - `EuMatchResult` dataclass: `entity: dict`, `total_score: int`, `details: list[MatchDetail]`, `matched_alias: str | None = None`.
  - `birth_year_score(query_year: int, birthdates: list[dict]) -> int`
  - `nationality_score(query: str, citizenships: list[dict]) -> int`
  - `birth_place_score(query: str, birthdates: list[dict]) -> int`
  - `score_entity(entity: dict, query: SearchQuery) -> EuMatchResult | None`
  - `search_eu(entities: list[dict], query: SearchQuery) -> list[EuMatchResult]`
  - Constants: `THRESHOLD = 60`, `MAX_RESULTS = 20`.

Scoring rules: birth year exact=100, ±1=75, ±2=50 else 0; year range containing year=75. Nationality: exact ISO (case-insensitive)=100, description `token_set_ratio` ≥85→100, ≥70→50 else 0. Birth place: best `token_set_ratio` against `place`/`city`. Total = weighted average of provided features only; entity dropped if `entity_type` mismatch or total < THRESHOLD.

- [ ] **Step 1: Write the failing scoring tests**

Append to `tests/test_matcher.py`:
```python
from app.matcher import (
    MAX_RESULTS,
    THRESHOLD,
    SearchQuery,
    birth_place_score,
    birth_year_score,
    nationality_score,
    score_entity,
    search_eu,
)


def make_entity(**overrides):
    entity = {
        "logical_id": "1",
        "eu_reference_number": "EU.1",
        "united_nations_id": "",
        "designation_date": "",
        "subject_type": "person",
        "aliases": [],
        "citizenships": [],
        "birthdates": [],
        "addresses": [],
        "identifications": [],
        "regulations": [],
        "remarks": [],
    }
    entity.update(overrides)
    return entity


def test_birth_year_scores():
    bd = [{"year": 1971}]
    assert birth_year_score(1971, bd) == 100
    assert birth_year_score(1972, bd) == 75
    assert birth_year_score(1973, bd) == 50
    assert birth_year_score(1975, bd) == 0
    assert birth_year_score(1971, []) == 0


def test_birth_year_range():
    bd = [{"year_from": 1950, "year_to": 1960}]
    assert birth_year_score(1955, bd) == 75
    assert birth_year_score(1940, bd) == 0


def test_nationality_scores():
    cit = [{"iso2": "AF", "description": "AFGHANISTAN"}]
    assert nationality_score("AF", cit) == 100
    assert nationality_score("af", cit) == 100
    assert nationality_score("Afghanistan", cit) == 100
    assert nationality_score("NL", cit) == 0


def test_birth_place_scores():
    bd = [{"place": "Kabul", "city": ""}]
    assert birth_place_score("Kabul", bd) == 100
    assert birth_place_score("Kabol", bd) >= 70
    assert birth_place_score("Amsterdam", bd) == 0


def test_score_entity_name_only():
    entity = make_entity(aliases=[{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}])
    result = score_entity(entity, SearchQuery(name="John Smith"))
    assert result is not None
    assert result.total_score == 100
    assert result.matched_alias == "John Smith"
    assert result.details[0].feature == "naam"


def test_score_entity_weighted_combination():
    entity = make_entity(
        aliases=[{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}],
        birthdates=[{"year": 1971}],
    )
    result = score_entity(entity, SearchQuery(name="John Smith", birth_year=1971))
    assert result is not None
    expected = round((60 * 100 + 20 * 100) / 80)
    assert result.total_score == expected
    assert len(result.details) == 2


def test_score_entity_below_threshold_returns_none():
    entity = make_entity(aliases=[{"whole_name": "Xavier Xyzzy", "first_name": "", "last_name": "", "strong": False, "function": "", "title": ""}])
    result = score_entity(entity, SearchQuery(name="John Smith"))
    assert result is None


def test_score_entity_entity_type_filter():
    entity = make_entity(subject_type="person", aliases=[{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}])
    assert score_entity(entity, SearchQuery(name="John Smith", entity_type="enterprise")) is None
    assert score_entity(entity, SearchQuery(name="John Smith", entity_type="person")) is not None


def test_search_eu_sorts_and_caps():
    entities = []
    for i in range(30):
        entities.append(make_entity(
            logical_id=str(i),
            aliases=[{"whole_name": "Name Number", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}],
        ))
    query = SearchQuery(name="Name Number")
    results = search_eu(entities, query)
    assert len(results) <= MAX_RESULTS
    scores = [r.total_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_eu_empty_when_no_match():
    entity = make_entity(aliases=[{"whole_name": "Rosneft", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}])
    assert search_eu([entity], SearchQuery(name="Completely Unrelated Name")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_matcher.py -v`
Expected: FAIL with `ImportError: cannot import name 'birth_year_score'`.

- [ ] **Step 3: Implement full scoring**

Append to `app/matcher.py`:
```python
from dataclasses import dataclass

THRESHOLD = 60
MAX_RESULTS = 20


@dataclass
class MatchDetail:
    feature: str
    score: int
    label: str


@dataclass
class EuMatchResult:
    entity: dict
    total_score: int
    details: list[MatchDetail]
    matched_alias: str | None = None


def birth_year_score(query_year: int, birthdates: list[dict]) -> int:
    best = 0
    for b in birthdates:
        if b.get("year") is not None:
            diff = abs(query_year - b["year"])
            if diff == 0:
                score = 100
            elif diff == 1:
                score = 75
            elif diff == 2:
                score = 50
            else:
                score = 0
        elif b["year_from"] is not None and b["year_to"] is not None:
            score = 75 if b["year_from"] <= query_year <= b["year_to"] else 0
        else:
            score = 0
        best = max(best, score)
    return best


def _fuzzy_threshold(ratio: int) -> int:
    if ratio >= 85:
        return 100
    if ratio >= 70:
        return 50
    return 0


def nationality_score(query: str, citizenships: list[dict]) -> int:
    q = query.strip().upper()
    best = 0
    for c in citizenships:
        if c["iso2"] == q:
            best = max(best, 100)
        if c["description"]:
            best = max(best, _fuzzy_threshold(fuzz.token_set_ratio(q, c["description"])))
    return best


def birth_place_score(query: str, birthdates: list[dict]) -> int:
    q = query.strip()
    best = 0
    for b in birthdates:
        for candidate in (b["place"], b["city"]):
            if candidate:
                score = fuzz.token_set_ratio(q, candidate)
                best = max(best, score if score >= 70 else 0)
    return best


def score_entity(entity: dict, query: SearchQuery) -> EuMatchResult | None:
    if query.entity_type and entity["subject_type"] != query.entity_type:
        return None
    weights = []
    details = []
    name_score_value, matched_alias = name_score(query.name, entity["aliases"])
    weights.append(WEIGHT_NAME)
    label = f'Naam {name_score_value}% (via "{matched_alias}")' if matched_alias else "Naam 0%"
    details.append(MatchDetail("naam", name_score_value, label))
    if query.birth_year is not None:
        s = birth_year_score(query.birth_year, entity["birthdates"])
        weights.append(WEIGHT_BIRTH_YEAR)
        label = "Geboortejaar exact" if s == 100 else f"Geboortejaar ({s}%)"
        details.append(MatchDetail("geboortejaar", s, label))
    if query.nationality:
        s = nationality_score(query.nationality, entity["citizenships"])
        weights.append(WEIGHT_NATIONALITY)
        label = "Nationaliteit match" if s >= 85 else f"Nationaliteit ({s}%)"
        details.append(MatchDetail("nationaliteit", s, label))
    if query.birth_place:
        s = birth_place_score(query.birth_place, entity["birthdates"])
        weights.append(WEIGHT_BIRTH_PLACE)
        details.append(MatchDetail("geboorteplaats", s, f"Geboorteplaats {s}%"))
    if not weights:
        return None
    total = round(sum(w * d.score for w, d in zip(weights, details)) / sum(weights))
    if total < THRESHOLD:
        return None
    return EuMatchResult(entity=entity, total_score=total, details=details, matched_alias=matched_alias)


def search_eu(entities: list[dict], query: SearchQuery) -> list[EuMatchResult]:
    results = []
    for entity in entities:
        result = score_entity(entity, query)
        if result is not None:
            results.append(result)
    results.sort(key=lambda r: r.total_score, reverse=True)
    return results[:MAX_RESULTS]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_matcher.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add app/matcher.py tests/test_matcher.py
git commit -m "feat: weighted entity scoring with match explanations"
```

---
### Task 6: OpenSanctions client (optional)

**Files:**
- Create: `app/opensanctions.py`
- Test: `tests/test_opensanctions.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `API_URL = "https://api.opensanctions.org/match/default"`, `TIMEOUT = 30`, `THRESHOLD = 0.7`, `LIMIT = 10`, `TOPICS = ["sanction", "sanction.linked", "debarment"]`.
  - `match_opensanctions(api_key: str, name: str, birth_year: int | None = None, nationality: str | None = None, birth_place: str | None = None) -> list[dict]` — POSTs to `/match/default`; returns parsed result dicts `{id, caption, schema, score, match, explanations, datasets, properties, url}`; raises on HTTP/JSON errors (caller catches).

- [ ] **Step 1: Write the failing client test**

`tests/test_opensanctions.py`:
```python
import json
from app.opensanctions import match_opensanctions


SAMPLE_RESPONSE = {
    "limit": 5,
    "responses": {
        "q": {
            "status": 200,
            "results": [
                {
                    "id": "NK-abc123",
                    "caption": "Aleksandr ZAKHAROV",
                    "schema": "Person",
                    "score": 0.85,
                    "match": True,
                    "explanations": {"name_match": {"score": 0.9}},
                    "datasets": ["eu_fsf", "us_ofac_sdn"],
                    "properties": {"birthDate": ["1965"], "citizenship": ["ru"]},
                }
            ],
            "total": {"value": 1, "relation": "eq"},
            "query": {},
        }
    },
}


def test_match_opensanctions_sends_expected_payload(monkeypatch):
    import requests

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE_RESPONSE

    def fake_post(url, headers, params, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post)
    results = match_opensanctions("KEY123", "Aleksandr Zakharov", birth_year=1965, nationality="RU")
    assert captured["url"] == "https://api.opensanctions.org/match/default"
    assert captured["headers"]["Authorization"] == "ApiKey KEY123"
    assert captured["params"]["threshold"] == 0.7
    assert captured["params"]["limit"] == 10
    assert captured["timeout"] == 30
    query = captured["json"]["queries"]["q"]
    assert query["schema"] == "Person"
    assert query["properties"]["firstName"] == ["Aleksandr"]
    assert query["properties"]["lastName"] == ["Zakharov"]
    assert query["properties"]["birthDate"] == ["1965"]
    assert query["properties"]["nationality"] == ["RU"]


def test_match_opensanctions_parses_results(monkeypatch):
    import requests

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE_RESPONSE

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    results = match_opensanctions("KEY123", "Aleksandr Zakharov")
    assert len(results) == 1
    r = results[0]
    assert r["id"] == "NK-abc123"
    assert r["caption"] == "Aleksandr ZAKHAROV"
    assert r["score"] == 0.85
    assert r["match"] is True
    assert r["url"] == "https://opensanctions.org/entities/NK-abc123"


def test_match_opensanctions_raises_on_http_error(monkeypatch):
    import requests

    class FakeResp:
        def raise_for_status(self):
            raise requests.HTTPError("401")

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    try:
        match_opensanctions("BAD", "x")
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_opensanctions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.opensanctions'`.

- [ ] **Step 3: Implement the client**

`app/opensanctions.py`:
```python
import requests

API_URL = "https://api.opensanctions.org/match/default"
TIMEOUT = 30
THRESHOLD = 0.7
LIMIT = 10
TOPICS = ["sanction", "sanction.linked", "debarment"]


def match_opensanctions(
    api_key: str,
    name: str,
    birth_year: int | None = None,
    nationality: str | None = None,
    birth_place: str | None = None,
) -> list[dict]:
    parts = name.split()
    properties = {"name": [name]}
    if parts:
        properties["firstName"] = [parts[0]]
        if len(parts) > 1:
            properties["lastName"] = [" ".join(parts[1:])]
    if birth_year is not None:
        properties["birthDate"] = [str(birth_year)]
    if nationality:
        properties["nationality"] = [nationality]
    if birth_place:
        properties["birthPlace"] = [birth_place]
    query = {"schema": "Person", "properties": properties}
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"ApiKey {api_key}"},
        params={"threshold": THRESHOLD, "limit": LIMIT, "topics": TOPICS},
        json={"queries": {"q": query}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    response = data.get("responses", {}).get("q", {})
    return [
        {
            "id": r.get("id", ""),
            "caption": r.get("caption", ""),
            "schema": r.get("schema", ""),
            "score": r.get("score", 0.0),
            "match": r.get("match", False),
            "explanations": r.get("explanations", {}),
            "datasets": r.get("datasets", []),
            "properties": r.get("properties", {}),
            "url": f"https://opensanctions.org/entities/{r.get('id', '')}",
        }
        for r in response.get("results", [])
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_opensanctions.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/opensanctions.py tests/test_opensanctions.py
git commit -m "feat: OpenSanctions match client with optional API key"
```

---
### Task 7: FastAPI routes

**Files:**
- Create: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `app.ingest` (`load_index`, `refresh`, `parse_export`, `XML_FILENAME`), `app.matcher` (`SearchQuery`, `search_eu`), `app.opensanctions` (`match_opensanctions`).
- Produces:
  - `create_app(entities: list[dict] | None = None, os_api_key: str | None = None, cache_dir: Path = CACHE_DIR) -> FastAPI`
  - Routes:
    - `GET /` → serves `static/index.html`
    - `GET /api/health` → `{"status": "ok"}`
    - `GET /api/status` → `{cached_at, generated_at, entity_count, data_age_hours, opensanctions_active, source}`
    - `GET /api/search?name=…&birth_year=…&nationality=…&birth_place=…&entity_type=person|enterprise` → `{query, results, warnings, opensanctions_active}`
    - `POST /api/refresh` → returns current status or `503 {"detail": …}`
  - Result item shape: `{"source": "eu"|"opensanctions", "score": int (0–100), "entity": {...}, "eu": {...}|null, "opensanctions": {...}|null}`.

- [ ] **Step 1: Write the failing API tests**

`tests/test_main.py`:
```python
from pathlib import Path
import json

from fastapi.testclient import TestClient

from app.main import create_app


def make_entity(eu_ref, whole_name, subject_type="person", year=None, country=None, place=None):
    return {
        "logical_id": eu_ref,
        "eu_reference_number": eu_ref,
        "united_nations_id": "",
        "designation_date": "2022-01-01",
        "subject_type": subject_type,
        "aliases": [{"whole_name": whole_name, "first_name": "", "last_name": "", "strong": True, "function": "Diplomat", "title": ""}],
        "citizenships": [{"iso2": country, "description": country}] if country else [],
        "birthdates": [{"date": "", "year": year, "year_from": None, "year_to": None, "city": "", "place": place, "iso2": country or "", "country": country or ""}] if year or place else [],
        "addresses": [],
        "identifications": [],
        "regulations": [{"number_title": "2022/123", "publication_date": "2022-02-01", "programme": "XX", "publication_url": "https://eur-lex.europa.eu/x"}],
        "remarks": [],
    }


ENTITIES = [
    make_entity("EU.471.56", "Abdul Hai Hazem Abdul Qader", year=1971, country="AF", place="Kabul"),
    make_entity("EU.2", "Rosneft", subject_type="enterprise", country="RU"),
]

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "eu_sample.xml"


def test_health():
    client = TestClient(create_app(entities=ENTITIES))
    assert client.get("/api/health").json() == {"status": "ok"}


def test_status_fields():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/status").json()
    assert data["entity_count"] == 2
    assert data["opensanctions_active"] is False
    assert "source" in data


def test_search_returns_eu_result():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Abdul Hai Hazem"}).json()
    assert data["results"]
    first = data["results"][0]
    assert first["source"] == "eu"
    assert first["eu"]["total_score"] == 100
    assert first["entity"]["eu_reference_number"] == "EU.471.56"
    assert any(d["feature"] == "naam" for d in first["eu"]["details"])


def test_search_birth_year_boosts():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Abdul", "birth_year": 1971}).json()
    scores = [r["score"] for r in data["results"] if r["source"] == "eu"]
    assert scores and scores[0] == 100


def test_search_entity_type_filter():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Rosneft", "entity_type": "enterprise"}).json()
    assert any(r["source"] == "eu" for r in data["results"])


def test_search_requires_name():
    client = TestClient(create_app(entities=ENTITIES))
    resp = client.get("/api/search")
    assert resp.status_code == 422


def test_search_with_opensanctions_merges(monkeypatch):
    import app.main as main

    fake_os = [
        {
            "id": "NK-x",
            "caption": "Abdul Qader",
            "schema": "Person",
            "score": 0.8,
            "match": True,
            "explanations": {"name_match": {"score": 0.8}},
            "datasets": ["eu_fsf"],
            "properties": {},
            "url": "https://opensanctions.org/entities/NK-x",
        }
    ]
    monkeypatch.setattr(main.opensanctions, "match_opensanctions", lambda *a, **k: fake_os)
    client = TestClient(create_app(entities=ENTITIES, os_api_key="KEY"))
    data = client.get("/api/search", params={"name": "Abdul Qader"}).json()
    sources = {r["source"] for r in data["results"]}
    assert sources == {"eu", "opensanctions"}
    os_result = [r for r in data["results"] if r["source"] == "opensanctions"][0]
    assert os_result["score"] == 80
    assert os_result["opensanctions"]["url"] == "https://opensanctions.org/entities/NK-x"


def test_search_opensanctions_failure_adds_warning(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.opensanctions, "match_opensanctions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    client = TestClient(create_app(entities=ENTITIES, os_api_key="KEY"))
    data = client.get("/api/search", params={"name": "Abdul Hai Hazem"}).json()
    assert data["results"]
    assert any("OpenSanctions" in w for w in data["warnings"])


def test_search_no_match_returns_empty():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Zzq Qqxx"}).json()
    assert data["results"] == []


def test_index_serves_html(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>hi</h1>")
    client = TestClient(create_app(entities=ENTITIES, static_dir=static))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text == "<h1>hi</h1>"
```

Note: the last test needs `create_app` to accept a `static_dir` parameter. That parameter is added in Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Implement the FastAPI app**

`app/main.py`:
```python
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import ingest, matcher, opensanctions

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"


def _serialize_eu_result(result: matcher.EuMatchResult, query_name: str) -> dict:
    entity = result.entity
    return {
        "source": "eu",
        "score": result.total_score,
        "entity": {
            "name": result.matched_alias or query_name,
            "eu_reference_number": entity["eu_reference_number"],
            "united_nations_id": entity["united_nations_id"],
            "subject_type": entity["subject_type"],
            "designation_date": entity["designation_date"],
            "aliases": [a["whole_name"] for a in entity["aliases"] if a["whole_name"]],
            "citizenships": [{"iso2": c["iso2"], "description": c["description"]} for c in entity["citizenships"]],
            "birthdates": entity["birthdates"],
            "addresses": entity["addresses"],
            "identifications": entity["identifications"],
            "regulations": entity["regulations"],
            "function": next((a["function"] for a in entity["aliases"] if a["function"]), ""),
            "remarks": entity["remarks"],
        },
        "eu": {
            "total_score": result.total_score,
            "matched_alias": result.matched_alias,
            "details": [d.__dict__ for d in result.details],
        },
        "opensanctions": None,
    }


def _serialize_os_result(result: dict) -> dict:
    props = result["properties"]
    return {
        "source": "opensanctions",
        "score": round(result["score"] * 100),
        "entity": {
            "name": result["caption"],
            "schema": result["schema"],
            "aliases": list(dict.fromkeys(props.get("alias", []) + props.get("name", [])))[:10],
            "birthdates": [{"date": d, "year": None, "year_from": None, "year_to": None, "city": "", "place": "", "iso2": "", "country": ""} for d in props.get("birthDate", [])],
            "citizenships": [{"iso2": c.upper(), "description": c.upper()} for c in props.get("citizenship", [])],
            "countries": props.get("country", []),
            "topics": props.get("topics", []),
            "program_ids": props.get("programId", []),
            "source_urls": props.get("sourceUrl", [])[:3],
        },
        "eu": None,
        "opensanctions": result,
    }


def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    cache_dir: Path = CACHE_DIR,
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    if entities is None:
        entities, meta = ingest.load_index(cache_dir)
    else:
        meta = {}
    state = {"entities": entities, "meta": meta}
    opensanctions_active = bool(os_api_key)

    app = FastAPI(title="Sanctielijst Zoeker")

    @app.get("/")
    def index():
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    def _status() -> dict:
        cached_at = state["meta"].get("cached_at")
        age_hours = round((time.time() - cached_at) / 3600, 1) if cached_at else None
        return {
            "cached_at": cached_at,
            "generated_at": state["meta"].get("generated_at"),
            "entity_count": len(state["entities"]),
            "data_age_hours": age_hours,
            "opensanctions_active": opensanctions_active,
            "source": state["meta"].get("source", "unknown"),
        }

    @app.get("/api/status")
    def status():
        return _status()

    @app.post("/api/refresh")
    def refresh():
        try:
            meta = ingest.refresh(cache_dir)
            state["entities"] = ingest.parse_export((cache_dir / ingest.XML_FILENAME).read_bytes())
            state["meta"] = meta
            return _status()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Verversen mislukt: {exc}")

    @app.get("/api/search")
    def search(
        name: str = Query(..., min_length=1),
        birth_year: int | None = Query(None, ge=1900, le=2100),
        nationality: str | None = None,
        birth_place: str | None = None,
        entity_type: str | None = Query(None, pattern="^(person|enterprise)$"),
    ):
        query = matcher.SearchQuery(
            name=name.strip(),
            birth_year=birth_year,
            nationality=(nationality or "").strip() or None,
            birth_place=(birth_place or "").strip() or None,
            entity_type=entity_type,
        )
        results = []
        warnings = []
        for r in matcher.search_eu(state["entities"], query):
            results.append(_serialize_eu_result(r, query.name))
        if opensanctions_active:
            try:
                for r in opensanctions.match_opensanctions(
                    os_api_key, query.name, query.birth_year, query.nationality, query.birth_place
                ):
                    results.append(_serialize_os_result(r))
            except Exception:
                warnings.append("OpenSanctions tijdelijk niet beschikbaar")
        results.sort(key=lambda r: r["score"], reverse=True)
        return {
            "query": {
                "name": query.name,
                "birth_year": query.birth_year,
                "nationality": query.nationality,
                "birth_place": query.birth_place,
                "entity_type": query.entity_type,
            },
            "results": results,
            "warnings": warnings,
            "opensanctions_active": opensanctions_active,
        }

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_main.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: FastAPI routes for search, status, refresh, health"
```

---
### Task 8: Frontend + README

**Files:**
- Create: `static/index.html`
- Create: `static/app.js`
- Create: `static/style.css`
- Create: `README.md`

**Interfaces:**
- Consumes: `GET /api/search`, `GET /api/status` from Task 7 (exact response shapes).
- Produces: runnable web UI + setup docs.

- [ ] **Step 1: Create `static/index.html`**

```html
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sanctielijst Zoeker</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="hero">
    <h1>Sanctielijst Zoeker</h1>
    <p>Zoek personen en bedrijven in de EU sanctielijsten.</p>
  </header>
  <main>
    <section class="search-panel">
      <form id="search-form">
        <div class="field">
          <label for="name">Naam <span class="required">*</span></label>
          <input type="text" id="name" name="name" required autocomplete="off" placeholder="bijv. Abdul Hai Hazem">
        </div>
        <div class="row">
          <div class="field">
            <label for="birth_year">Geboortejaar</label>
            <input type="number" id="birth_year" name="birth_year" min="1900" max="2100" placeholder="bijv. 1971">
          </div>
          <div class="field">
            <label for="entity_type">Type</label>
            <select id="entity_type" name="entity_type">
              <option value="">Alle</option>
              <option value="person">Persoon</option>
              <option value="enterprise">Bedrijf</option>
            </select>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label for="nationality">Nationaliteit</label>
            <input type="text" id="nationality" name="nationality" placeholder="bijv. Afghanistan of AF">
          </div>
          <div class="field">
            <label for="birth_place">Geboorteplaats</label>
            <input type="text" id="birth_place" name="birth_place" placeholder="bijv. Kabul">
          </div>
        </div>
        <button type="submit" id="search-btn">Zoeken</button>
      </form>
      <p id="status-line" class="status-line" role="status"></p>
    </section>
    <section id="warnings" class="warnings" hidden></section>
    <section id="results" class="results" aria-live="polite"></section>
    <section id="empty-state" class="empty" hidden>
      <p>Geen overeenkomsten gevonden. Probeer een andere schrijfwijze of vul meer kenmerken in.</p>
    </section>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `static/app.js`**

```js
const form = document.getElementById("search-form");
const resultsEl = document.getElementById("results");
const emptyEl = document.getElementById("empty-state");
const warningsEl = document.getElementById("warnings");
const statusLine = document.getElementById("status-line");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function chip(label, tone = "ok") {
  return `<span class="chip chip-${tone}">${escapeHtml(label)}</span>`;
}

function sourceBadge(sources) {
  const parts = [];
  if (sources.includes("eu")) parts.push('<span class="badge badge-eu">EU sanctielijst</span>');
  if (sources.includes("opensanctions")) parts.push('<span class="badge badge-os">OpenSanctions</span>');
  return parts.join(" ");
}

function euCard(item) {
  const eu = item.eu;
  const entity = item.entity;
  const chips = eu.details.map((d) => {
    const tone = d.score >= 85 ? "ok" : d.score >= 50 ? "warn" : "bad";
    return chip(d.label, tone);
  }).join("");
  const aliases = (entity.aliases || []).slice(0, 3).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
  const regs = (entity.regulations || []).map((r) => {
    const title = escapeHtml(r.number_title || r.programme || "Reglement");
    if (r.publication_url) return `<a href="${escapeHtml(r.publication_url)}" target="_blank" rel="noopener">${title}</a>`;
    return title;
  }).join(", ");
  const births = (entity.birthdates || []).filter((b) => b.date || b.year).slice(0, 2)
    .map((b) => {
      const bits = [b.date || b.year, b.place || b.city].filter(Boolean);
      return bits.join(", ");
    });
  const birthLine = births.length ? `<p class="muted">Geboren: ${births.map(escapeHtml).join(" · ")}</p>` : "";
  const natLine = entity.citizenships.length ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.description || c.iso2)).join(", ")}</p>` : "";
  return `
    <article class="card">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        ${sourceBadge(["eu"])}
      </div>
      <p class="ref">EU-ref: ${escapeHtml(entity.eu_reference_number || "-")}${entity.united_nations_id ? ` · VN-id: ${escapeHtml(entity.united_nations_id)}` : ""}</p>
      <p class="score-line">Totaalscore: <strong>${item.score}</strong>/100 ${chips}</p>
      ${birthLine}
      ${natLine}
      ${aliases ? `<ul class="aliases">${aliases}</ul>` : ""}
      ${regs ? `<p class="muted">Reglement(en): ${regs}</p>` : ""}
      ${entity.function ? `<p class="muted">Functie: ${escapeHtml(entity.function)}</p>` : ""}
    </article>`;
}

function osCard(item) {
  const os = item.opensanctions;
  const entity = item.entity;
  const exp = Object.keys(os.explanations || {})
    .filter((k) => (os.explanations[k] || {}).score > 0)
    .map((k) => chip(k, "warn")).join("");
  const datasets = (os.datasets || []).slice(0, 5).join(", ");
  const topics = (entity.topics || []).slice(0, 4).map((t) => chip(t, "warn")).join("");
  return `
    <article class="card card-os">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        ${sourceBadge(["opensanctions"])}
      </div>
      <p class="ref">Schema: ${escapeHtml(entity.schema || "-")}</p>
      <p class="score-line">Score: <strong>${os.score.toFixed(2)}</strong> (${os.match ? "match" : "geen match"}) ${exp}</p>
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${os.datasets ? `<p class="muted">Datasets: ${escapeHtml(datasets)}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(os.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
    </article>`;
}

function renderResults(data) {
  resultsEl.innerHTML = "";
  warningsEl.hidden = true;
  warningsEl.textContent = "";
  if (data.warnings.length) {
    warningsEl.hidden = false;
    warningsEl.innerHTML = data.warnings.map((w) => `<p>${escapeHtml(w)}</p>`).join("");
  }
  if (!data.results.length) {
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;
  data.results.forEach((item) => {
    const html = item.source === "opensanctions" ? osCard(item) : euCard(item);
    resultsEl.insertAdjacentHTML("beforeend", html);
  });
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const s = await res.json();
    const parts = [
      `${s.entity_count.toLocaleString("nl-NL")} records`,
      s.source === "fresh" ? "data vers" : "data gecachet",
      s.opensanctions_active ? "OpenSanctions actief" : "OpenSanctions niet actief",
    ];
    statusLine.textContent = parts.join(" · ");
  } catch {
    statusLine.textContent = "Status niet beschikbaar";
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("name").value.trim();
  if (!name) return;
  const params = new URLSearchParams();
  params.set("name", name);
  const birthYear = document.getElementById("birth_year").value;
  if (birthYear) params.set("birth_year", birthYear);
  const nationality = document.getElementById("nationality").value.trim();
  if (nationality) params.set("nationality", nationality);
  const birthPlace = document.getElementById("birth_place").value.trim();
  if (birthPlace) params.set("birth_place", birthPlace);
  const entityType = document.getElementById("entity_type").value;
  if (entityType) params.set("entity_type", entityType);
  resultsEl.innerHTML = '<p class="loading">Zoeken...</p>';
  emptyEl.hidden = true;
  warningsEl.hidden = true;
  try {
    const res = await fetch(`/api/search?${params}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Fout bij zoeken");
    }
    renderResults(await res.json());
  } catch (err) {
    resultsEl.innerHTML = "";
    warningsEl.hidden = false;
    warningsEl.innerHTML = `<p>${escapeHtml(err.message)}</p>`;
  }
});

loadStatus();
```

- [ ] **Step 3: Create `static/style.css`**

```css
:root {
  --eu: #003399;
  --os: #1a1a1a;
  --ok: #1a7f37;
  --warn: #9a6700;
  --bad: #cf222e;
  --muted: #57606a;
  --border: #d0d7de;
  --bg: #f6f8fa;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: #1f2328;
  line-height: 1.5;
}

.hero {
  background: var(--eu);
  color: #fff;
  padding: 2rem 1rem;
  text-align: center;
}

.hero h1 { margin: 0 0 0.25rem; }
.hero p { margin: 0; opacity: 0.9; }

main {
  max-width: 860px;
  margin: 0 auto;
  padding: 1rem;
}

.search-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1rem 0.75rem;
  margin-top: 1rem;
}

.row { display: flex; gap: 1rem; }
.row .field { flex: 1; }
.field { margin-bottom: 0.75rem; }

label { display: block; font-weight: 600; margin-bottom: 0.25rem; font-size: 0.9rem; }
.required { color: var(--bad); }

input, select {
  width: 100%;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 1rem;
}

button {
  background: var(--eu);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 0.6rem 1.4rem;
  font-size: 1rem;
  cursor: pointer;
}
button:hover { opacity: 0.9; }

.status-line { margin: 0.5rem 0 0; font-size: 0.85rem; color: var(--muted); }

.warnings {
  background: #fff8c5;
  border: 1px solid #d4a72c;
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
  margin-top: 1rem;
  font-size: 0.9rem;
}
.warnings p { margin: 0.25rem 0; }

.results { margin-top: 1rem; }

.card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 0.75rem;
}
.card-os { border-left: 4px solid var(--os); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.card h2 { margin: 0; font-size: 1.15rem; }

.ref { color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0; }
.score-line { margin: 0.4rem 0; }
.muted { color: var(--muted); font-size: 0.9rem; margin: 0.25rem 0; }

.aliases { margin: 0.25rem 0; padding-left: 1.2rem; color: var(--muted); font-size: 0.9rem; }

.badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
}
.badge-eu { background: var(--eu); }
.badge-os { background: var(--os); }

.chip {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  border: 1px solid transparent;
  margin-left: 0.3rem;
  white-space: nowrap;
}
.chip-ok { background: #dafbe1; color: var(--ok); border-color: var(--ok); }
.chip-warn { background: #fff8c5; color: var(--warn); border-color: var(--warn); }
.chip-bad { background: #ffebe9; color: var(--bad); border-color: var(--bad); }

.empty {
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 2rem;
  text-align: center;
  color: var(--muted);
  margin-top: 1rem;
}

.loading { text-align: center; color: var(--muted); }

@media (max-width: 600px) {
  .row { flex-direction: column; gap: 0; }
}
```

- [ ] **Step 4: Create `README.md`**

```markdown
# Sanctielijst Zoeker

Web-app om te zoeken in de EU sanctielijsten (personen en bedrijven), met fuzzy matching en per-kenmerk uitleg waarom een resultaat matcht. Optioneel ook wereldwijde screening via de OpenSanctions `/match`-API.

## Installatie

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data

Bij de eerste start downloadt de app de EU sanctielijst (XML 1.1, ~25 MB) van `data.europa.eu` en cacht deze in `data/`. De cache wordt automatisch ververst als deze ouder is dan 24 uur. Forceer verversen via `POST /api/refresh`.

## Starten

```bash
uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000.

## OpenSanctions (optioneel)

Vul een gratis API-key in (https://www.opensanctions.org/account/, vrij voor niet-commercieel gebruik):

```bash
cp .env.example .env
# zet je key in .env
```

De app leest `OPENSANCTIONS_API_KEY` uit de omgeving of `.env`.

## Tests

```bash
python -m pytest -v
```
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: 35 passed (1 smoke + 8 ingest + 13 matcher + 3 opensanctions + 10 main — check final count in output).

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/app.js static/style.css README.md
git commit -m "feat: Dutch web UI with match chips and source badges"
```

---
## Self-Review Notes

- Spec coverage: all spec sections map to tasks — data-pipeline (Tasks 2–3), match-scoring (Tasks 4–5), OpenSanctions (Task 6), API routes + combined results (Task 7), frontend + config + README (Task 8).
- Global constraints enforced via constants in code (weights, threshold, TTL, timeouts, URLs).
- OpenSanctions is optional end-to-end: no key → `opensanctions_active: false`, EU-only results.
- Final manual smoke test (outside automated tests): start the server, search "Abdul Hai Hazem", confirm a 100% card with chips renders.
