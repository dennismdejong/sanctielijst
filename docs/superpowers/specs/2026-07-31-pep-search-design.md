# Ontwerp — PEP-zoekintegratie met bronherleiding

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker (design)

## Doel

De sanctielijst-app zoekt naast de EU-sanctielijst óók in de lokaal gedownloade OpenSanctions PEP-data (`data/pep/`, 178 bronnen, ~730K target-entiteiten). Elke PEP-hit is herleidbaar: de hit toont uit welke PEP-bron(nen) die komt (dataset-titel + land) en linkt naar de opensanctions-entity-pagina.

## Uitgangspunten

- Bestaande app (FastAPI, `app/ingest.py`, `app/matcher.py`, `app/main.py`, vanilla frontend) blijft zoals die is; PEP-zoeken is een parallelle bron naast EU.
- Scoring-filosofie blijft gelijk: naam 60 / geboortejaar 20 / nationaliteit 10 / geboorteplaats 10, drempel 90, alleen kenmerken meetellen die de gebruiker invulde.
- PEP-data is al gedownload (Task 1–5 van het downloader-plan); deze feature leest `data/pep/` en voegt metadata toe.
- Geen nieuwe dependencies (stdlib `sqlite3`/`pickle`/`json` + bestaande `rapidfuzz`, `requests`).

## Databron (FTM-formaat)

`data/pep/<dataset>/entities.ftm.json` is JSON Lines (één entiteit per regel) met velden:
- `id` (bv. `NK-…`), `caption` (weergavenaam), `schema` (`Person`, `Company`, `Occupancy`, …), `target` (bool)
- `datasets[]` (dataset-ids waar de entiteit in zit), `properties` (`name`, `alias`, `firstName`, `lastName`, `birthDate`, `birthPlace`, `citizenship`, `political`, `topics`, `gender`, …)

Alleen `target: true` + schema `Person`/`Company` wordt geïndexeerd (~730K entiteiten).

## Architectuur

### 1. `app/pep_index.py` (nieuw)

- `PEP_INDEX_FILENAME = "index.pkl"`, `DATASETS_FILENAME = "datasets.json"`, `INDEX_ENV = "PEP_INDEX_ENABLED"`
- Entiteitsrecord (genormaliseerd):
  ```python
  {
    "id": str, "caption": str, "schema": "Person"|"Company",
    "datasets": [str], "names": [str],          # caption + name/alias properties
    "birth_dates": [str], "birth_places": [str],
    "citizenships": [str], "political": [str], "topics": [str],
  }
  ```
- `build_index(root_dir: Path) -> dict` — parset alle `*/entities.ftm.json`, bouwt `{"entities": [...], "token_map": {token: [idx,...]}, "datasets": {...}, "built_at": iso}`; `token_map` is een lowercase token → lijst van entiteitsindices (inverted index, uit `names`).
- `save_index(root_dir, index)`, `load_index_cache(root_dir) -> dict | None` — pickle-cache; geldig als `index.pkl` nieuwer is dan alle `entities.ftm.json`-bestanden (mtime) én `datasets.json` bestaat.
- `load_or_build_index(root_dir: Path, force: bool = False) -> dict` — cache eerst, anders bouwen (met `source: "cached"|"built"` in de index).
- `search_pep(index: dict, name: str, birth_year: int | None = None, nationality: str | None = None, birth_place: str | None = None, entity_type: str | None = None, threshold: int = 90, max_results: int = 20) -> list[dict]`
  - Kandidaten via `token_map` (unie van query-tokens) → fuzzy-score met rapidfuzz `token_set_ratio` over `names` (zelfde `name_score`-logica als EU, inclusief best-of)
  - Gewogen totaalscore 0–100 (naam/geboortejaar/nationaliteit/geboorteplaats), drempel 90, `entity_type`-filter (`person`→`Person`, `enterprise`→`Company`)
  - Retourneert gesorteerde records met per record de score + gematchte naam + details

### 2. Dataset-metadata (uitbreiding downloader)

- `app/pep_ingest.py`: nieuw `write_datasets_meta(index: dict, root_dir: Path) -> None` — schrijft `data/pep/datasets.json` met per PEP-dataset `{name: {title, publisher, country, official, url}}` uit de opensanctions-hoofdindex.
- `refresh_pep(...)` roept dit aan na het doorlopen van de bronnen (ook in `--dry-run` niet schrijven; alleen bij een echte run). Als de metadata al gelijk is, wordt niet onnodig herschreven (optioneel; simpel: altijd schrijven).
- Fallback in de app: ontbreekt `datasets.json`, dan toont de app de dataset-id als titel en geen land/link naar de bron-datasetpagina.

### 3. App-integratie (`app/main.py`)

- `create_app(..., pep_root: Path = PEP_ROOT)`:
  - `PEP_ROOT = Path(...)` (default `data/pep`), aanwezigheid bepaalt default; env `PEP_INDEX_ENABLED` (`"0"` schakelt uit).
  - Als ingeschakeld: `pep_index.load_or_build_index(pep_root)` in `state`.
  - `GET /api/status` → extra veld `pep_index: {enabled, entity_count, datasets_count, source}`.
  - `GET /api/search`: naast EU ook `search_pep(...)` draaien (als index aanwezig); resultaten mergen, sorteren op score, bron-`source`-veld `"pep"`.
  - Serialisatie PEP-hit:
    ```python
    {
      "source": "pep",
      "score": int(0-100),
      "entity": {"name", "schema", "birth_dates", "birth_places", "citizenships", "political", "topics"},
      "pep": {
        "id", "url": "https://opensanctions.org/entities/<id>",
        "datasets": [{"id", "title", "country", "url": "https://www.opensanctions.org/datasets/<id>/"}],
        "matched_name", "details": [{"feature", "score", "label"}],
      },
      "eu": None, "opensanctions": None,
    }
    ```

### 4. Frontend (`static/app.js` + `index.html`)

- Bestaande resultaatkaart hergebruiken; voor `source === "pep"`:
  - Bronbadge **PEP**
  - Dataset-chips: per dataset `{title}` (+ `{country}` via landvlag), link naar de opensanctions-datasetpagina
  - Link "Open op opensanctions.org" naar de entity-pagina
  - Match-chips (zelfde stijl als EU: `Naam 92%`, `Geboortejaar exact`, …)

## Configuratie

- `PEP_INDEX_ENABLED` — `"0"`/`"false"` zet PEP-zoeken uit; default aan als `data/pep/` bestaat.
- Geen nieuwe dependency; `.env.example` wordt aangevuld met een commentaarregel.

## Teststrategie

- `tests/test_pep_index.py` (nieuw):
  - `build_index` uit kleine fixture-FTM-bestanden (2–3 datasets, incl. `target: false` en `Occupancy` die worden overgeslagen)
  - `search_pep`: exacte en fuzzy naam-match, geboortejaar/nationaliteit/geboorteplaats-scores, `entity_type`-filter, drempel, max_results, sortering
  - `token_map`: kandidatenreductie werkt
  - cache-logica: `load_index_cache` geldig/ongeldig op mtime, `load_or_build_index` source `cached`/`built`
- `tests/test_ingest.py` uitbreiden: `write_datasets_meta` schrijft verwachte metadata; `refresh_pep` schrijft `datasets.json`
- `tests/test_main.py` uitbreiden: `/api/search` retourneert PEP-hit met bronnen, `/api/status` toont `pep_index`, PEP uitgeschakeld → geen PEP in resultaten

## Foutafhandeling

- Index-bouwfout (corrupt FTM-bestand): regel overslaan, teller in de index (`skipped_lines`); app blijft werken met de rest.
- Geen `data/pep/` of geen index: PEP-zoeken uitgeschakeld, `pep_index.enabled: false` in status, geen fout.
- Corrupt `index.pkl`: opnieuw bouwen (mtime-check + try/except).

## Buiten scope (voor nu)

- Posities/functies uit `Occupancy`-entiteiten ophalen (join-work; `political` wordt wél getoond)
- Delta-updates of gecombineerde gededupliceerde `peps`-collectie
- De OpenSanctions `/match`-API als derde bron
- Resultaat-deduplicatie tussen EU en PEP (zelfde persoon in beide)
