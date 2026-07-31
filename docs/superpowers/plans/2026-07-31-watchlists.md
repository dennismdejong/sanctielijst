# Watchlists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gebruikers kunnen een naam/query bewaren ("bewaken"); na elke data-update (en index-rebuild) wordt automatisch opnieuw gescreend; nieuwe matches ≥ drempel worden als in-app melding getoond. Zonder login: identiteit via een anonieme cookie-UUID per browser.

**Architecture:** `app/watchlist.py` gebruikt een client-sleutel (UUID in een cookie `watch_key`, gezet door een kleine middleware/route) als identiteit. `data/watchlists.sqlite` (env `WATCHLIST_DB`) bevat `watchlists(client_key, id, name, birth_year, nationality, birth_place, entity_type, created_at)` en `watchlist_hits(id, watchlist_id, ts, match_json)`. Na voltooiing van een index-rebuild (startup `_build_index` of `POST /api/refresh`) draait een stap `rescan_all(db, search_fn)` die elke watchlist opnieuw screent en nieuwe (nog niet eerder geziene) matches ≥ drempel als hits opslaat. In-app weergave via endpoints + een simpel UI-vak; e-mail later.

**Tech Stack:** Python 3.11, stdlib `sqlite3`/`uuid`/`json`; bestaande zoekpipeline; geen nieuwe dependencies.

## Global Constraints

- Identiteit: cookie `watch_key` (UUID v4, `HttpOnly`, levensduur bv. 1 jaar). Endpoints lezen/zetten de cookie.
- Watchlist-regel: `naam` (verplicht) + optionele velden (geboortejaar, nationaliteit, geboorteplaats, type).
- Dedup: een match wordt alleen een hit als de combinatie (watchlist_id, entity-id, score) nog niet eerder is opgeslagen; `match_json` bevat id, naam, score, bron, datasets.
- `rescan_all` draait na elke succesvolle index-rebuild (in dezelfde achtergrond-thread na `_build_index`) én op aanvraag via `POST /api/watchlists/rescan` (admin-token uit Fase 1, of gewoon voor de eigen client). Fouten per watchlist worden geslikt + `logger.warning`.
- Endpoints: `GET/POST /api/watchlists` (lijst/aanmaken), `DELETE /api/watchlists/{id}`, `GET /api/watchlists/hits` (eigen client), `POST /api/watchlists/rescan`.
- UI: klein "Bewaak deze naam"-knopje naast de zoekknop + een melding-vak (badge met aantal nieuwe hits); simpel, geen framework.
- UI-taal Nederlands; identifiers Engels. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- STAGE nooit via `git add .`; alleen eigen bestanden. Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: watchlist-module (identiteit + opslag + rescan)

**Files:**
- Create: `app/watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Produces:
  - `default_watchlist_db() -> Path` — `WATCHLIST_DB` of `data/watchlists.sqlite`.
  - `init_watchlist_db(db_path)` — tabellen `watchlists` + `watchlist_hits`.
  - `get_or_create_key(request, response) -> str` — leest cookie `watch_key`, anders nieuwe UUID + `set_cookie`.
  - `add_watchlist(db_path, client_key, name, **fields) -> dict`, `list_watchlists(db_path, client_key) -> list[dict]`, `delete_watchlist(db_path, client_key, watchlist_id) -> bool`.
  - `rescan_all(db_path, search_fn, threshold=90) -> dict` — per watchlist de query uitvoeren; nieuwe matches (dedup op watchlist_id+entity-id) als hits opslaan; retourneert `{scanned, hits}`.
  - `list_hits(db_path, client_key, since_id=None) -> list[dict]`.

**Tests:** cookie-identiteit (nieuw + bestaand); add/list/delete; rescan produceert hits; dedup (tweede rescan geeft geen duplicaten); threshold-respect; ontbrekende DB wordt aangemaakt.

### Task 2: endpoints + rescan-hook

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `app.watchlist`, `run_search`.
- Produces:
  - `_watchlist_middleware` of per-route cookie-handling: `get_or_create_key` in de watchlist-routes.
  - `GET /api/watchlists`, `POST /api/watchlists` (name + optionele velden), `DELETE /api/watchlists/{id}`, `GET /api/watchlists/hits`, `POST /api/watchlists/rescan`.
  - Hook: aan het einde van `_build_index` (bij succes) `watchlist.rescan_all(...)` met de echte `run_search`; mislukking van rescan breekt de rebuild niet.
  - De watchlist-routes gaan door de audit-log (Fase 1).

**Tests:** endpoints met TestClient (cookie gezet bij eerste call); rescan na een fake-`_build_index`; hits alleen voor eigen client; audit-gekoppeld.

### Task 3: UI

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/style.css`

**Interfaces:**
- Produces: knop "Bewaak deze naam" (toevoegt de huidige query), een melding-vak dat `GET /api/watchlists/hits` toont (badge met nieuw-aantal), en de mogelijkheid een watchlist te verwijderen. Simpele fetch-calls; geen framework.

**Verificatie:** `node --check`, volledige suite groen, handmatige test.

---

## Self-Review

**Spec-cover:** Fase 4 — anonieme identiteit (cookie), bewaakte namen, automatische rescan na data-update, dedup, in-app meldingen; e-mail expliciet later. **Placeholders:** geen. **Consistentie:** `get_or_create_key`/`add_watchlist`/`rescan_all`/`list_hits` identiek gebruikt in Task 2-3.
