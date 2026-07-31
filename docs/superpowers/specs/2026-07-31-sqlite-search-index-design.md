# Ontwerp — SQLite+FTS5 zoekindex voor EU én PEP

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker (design)

## Doel

Vervang de in-memory zoekindex (EU-lijst ~6000 entiteiten + PEP-index ~730K entiteiten, samen ~1.1GB RAM) door één SQLite-zoekindex op disk. De app gebruikt daarna ~30-50MB RAM, start instant (alleen DB openen) en kan rebuilds uitvoeren zonder downtime.

## Uitgangspunten

- Eén zoekindex voor **zowel EU- als PEP-data**; per entiteit een `source`-veld (`eu`/`pep`) voor de bronbadge.
- Gebruik de **standaardbibliotheek `sqlite3`** (inclusief FTS5 in CPython) — geen nieuwe dependency.
- Zoekresultaten blijven **identiek** aan de huidige aanpak: FTS5 geeft kandidaten, daarna dezelfde rapidfuzz-scoring (token-containment → 100, gewichten naam 60/geboortejaar 20/nationaliteit 10/geboorteplaats 10, drempel 90, max 20).
- Atomic rebuild: bouwen naar `search.sqlite.new`, daarna `os.replace` → de draaiende app blijft de oude DB gebruiken tijdens de rebuild (zero downtime).
- De downloaders (`eu_ingest`, `pep_ingest`) en de ruwe data (`data/eu/`, `data/pep/`) blijven ongewijzigd; alleen de **zoekindex** verandert van in-memory naar SQLite.

## Architectuur

### 1. `app/search_index.py` (nieuw; vervangt de opslaglaag van `app/pep_index.py`)

Databestand: `data/search.sqlite` (pad via env `SEARCH_DB` of `SEARCH_DATA_DIR`; default in dezelfde volume als `data/pep`/`data/eu`).

Schema:
```sql
CREATE TABLE entities (
  rowid INTEGER PRIMARY KEY,
  source TEXT NOT NULL,              -- 'eu' | 'pep'
  id TEXT NOT NULL,                  -- entity-id (NK-… / eu logical_id)
  caption TEXT NOT NULL,
  schema TEXT NOT NULL,              -- 'Person' | 'Company' | …
  names TEXT NOT NULL,               -- JSON-array (orig, voor fuzzy-score + weergave)
  names_folded TEXT NOT NULL,        -- accent-gevouwen + lowercase, space-joined (voor FTS)
  birth_dates TEXT NOT NULL,         -- JSON-array
  birth_places TEXT NOT NULL,        -- JSON-array
  citizenships TEXT NOT NULL,        -- JSON-array (ISO-codes)
  political TEXT NOT NULL,           -- JSON-array (PEP)
  topics TEXT NOT NULL,              -- JSON-array (PEP)
  datasets TEXT NOT NULL,            -- JSON-array (PEP; leeg voor EU)
  eu_ref TEXT,                       -- EU-referentienummer (EU)
  united_nations_id TEXT,            -- EU
  designation_date TEXT,             -- EU
  subject_type TEXT                  -- 'person'|'enterprise' (EU)
);
CREATE VIRTUAL TABLE names_fts USING fts5(names_folded, content='entities', content_rowid='rowid');
```

Functies:
- `fold(text) -> str` — NFKD + combining-strip + lowercase (verhuist van `pep_index`).
- `_eu_records(entities: list[dict]) -> list[dict]` — normaliseert EU-parse-uitvoer (aliassen, geboorte, nationaliteit, regulations→display) naar indexrecords.
- `_pep_records(pep_root: Path) -> list[dict]` — parseert FTM (hergebruikt `_extract_entity`-logica uit `pep_index`).
- `build_index(db_path: Path, eu_entities: list[dict] | None, pep_root: Path) -> None` — maakt schema, bulk-insert EU+PEP, vult `names_fts` (content-backed, `INSERT INTO names_fts(rowid, names_folded)`), schrijft naar `db_path.new`, `os.replace`. Retourneert stats (counts).
- `_newest_input_mtime(eu_root, pep_root, db_path) -> float` — max van EU-XML, PEP-`entities.ftm.json`, `datasets.json`.
- `ensure_index(db_path, eu_root, pep_root) -> dict` — opent `db_path`; als ontbreekt of ouder dan de inputs → (achtergrond) rebuild; retourneert `{"db": connection, "ready": bool, "stats": {...}}`.
- `search(db, name, birth_year=None, nationality=None, birth_place=None, entity_type=None, threshold=90, max_results=20) -> list[dict]` — FTS5-kandidaten (`names_fts MATCH <folded tokens AND>`), rijen ophalen, per kandidaat rapidfuzz-scoring (zelfde logica als nu), sorteer + cap. Retourneert `{"entity": <dict met alle display-velden + source>, "score", "matched_name", "details"}`.

### 2. `app/pep_index.py` → afbouwen

- `build_index`/`load_or_build_index`/`save_index`/`load_index_cache`/`token_map` verdwijnen (SQLite vervangt dit).
- `_extract_entity`, `_fold`, `_tokens`, `_birth_year`, `_name_score` en de scorelogica verhuizen naar `search_index.py` (of worden geïmporteerd).
- `search_pep` wordt vervangen door `search_index.search` (bronnen EU+PEP).
- `THRESHOLD`/`MAX_RESULTS`/`INDEX_ENV` blijven bestaan (drempel 90, max 20, `PEP_INDEX_ENABLED`).

### 3. App-integratie (`app/main.py`)

- `create_app(...)`:
  - `state = {"db": None, "db_loading": False}` — geen in-memory index meer.
  - Startup: `search_index.ensure_index(...)`; als de DB up-to-date is → direct `state["db"]` klaar (instant). Anders achtergrond-thread die rebuildt (atomic swap) en `state["db"]` omwisselt.
  - `_status()`: `entity_count` = EU+PEP count uit de DB (`SELECT count(*)`), `pep_index` → vervangen door `index: {enabled, status: ready|building|disabled, entity_count, pep_count, eu_count, source}`.
  - `/api/search`: één `search_index.search(...)` over EU+PEP; resultaat krijgt `source: "eu"` of `"pep"`; EU-serialisatie behoudt `eu_reference_number`/regulations, PEP-serialisatie datasets-metadata uit `datasets.json`.
  - `state["entities"]` (in-memory EU-lijst) verdwijnt.
- Frontend blijft ongewijzigd (bronbadges EU/PEP, versie-footer); `pep_index`-statusnaam in `app.js` wordt `index` (kleine aanpassing).

### 4. Config

- `SEARCH_DB` env (default `data/search.sqlite`), of `SEARCH_DATA_DIR`; in de container dezelfde volume als `data/pep`.
- `PEP_INDEX_ENABLED` blijft PEP aan/uit sturen (EU altijd aan).

## Rebuild-strategie (cron)

- De downloader-cron (wekelijks) schrijft nieuwe EU-XML/PEP-data.
- De app bouwt de SQLite-index in de achtergrond als de inputs nieuwer zijn dan `search.sqlite` (mtime-check) → atomic swap. Geen restart nodig na een download; bij een deploy start de app direct met de bestaande DB.
- `POST /api/refresh` kan naast EU-refresh óók de index-rebuild triggeren.

## Teststrategie

- `tests/test_search_index.py` (nieuw):
  - `build_index` uit kleine fixtures (EU-XML-parse-uitvoer + FTM-bestanden) → counts, schema, FTS gevuld.
  - `search`: exacte/fuzzy naam (incl. accent), geboortejaar/nationaliteit/geboorteplaats, `entity_type`-filter, drempel, max, sortering; resultaat bevat `source`.
  - `ensure_index`: bouwt bij als ontbreekt/verouderd; opent bestaande; atomic swap (`search.sqlite.new` verdwijnt).
  - FTS-kandidaten: `_tokens`/`fold` gedrag.
- `tests/test_main.py` aangepast: `create_app` gebruikt een tmp `search.sqlite`; status-`index`-velden; zoeken retourneert EU+PEP met bronnen.
- `tests/test_pep_index.py` verdwijnt (logica zit in `search_index`); scorefuncties worden daar getest.

## Foutafhandeling

- Corrupte/beschadigde `search.sqlite` → herbouwen in de achtergrond; de app serveert EU-only (of niets) tot de rebuild klaar is.
- Rebuild-fout → loggen, oude DB blijft actief, status `error`.
- Geen data (`data/eu` + `data/pep` ontbreken) → index disabled, status duidt dat aan.

## Buiten scope

- De `peps`-collectie/gecombineerde dedup; delta-updates op de index (full rebuild is eenvoudig en zero-downtime).
- OpenSanctions `/match`-API als derde bron.
- FTS-query-optimalisaties (prefix/tokenizer-tuning) — v1.0 gebruikt `AND`-over-tokens.
