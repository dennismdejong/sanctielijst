# Watchlists Implementation Plan (variant 2: geen naam-opslag)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gebruikers kunnen namen "bewaken" en zien of een data-update nieuwe matches oplevert — zonder dat de bewaakte namen ooit op de server worden opgeslagen (need-to-know). De server kent alleen **opaque watch-IDs** per eigenaar; de naam (en zoekcriteria) blijven in de browser en worden per rescan meegezonden, nooit gepersisteerd.

**Architecture:** `app/watchlist.py` gebruikt een eigenaar-sleutel als identiteit — nu een anonieme cookie-UUID (`watch_key`), later het account (`owner` = user_id; de kolom is er al voor voorbereid). De server slaat per watchlist alleen `{id, owner, created_at, label (optioneel, niet-gevoelig)}` op. De client bewaart `{watch_id, naam, criteria, bekendeHits}` in localStorage. Bij een data-update (de client polt `data_version` uit `/api/status`) vraagt de client per watchlist een rescan aan met de naam; de server screent (bestaande `run_search`), slaat hits op (publieke match-data) met dedup op watch-id+entity-id, en retourneert ze. De naam staat nooit in de DB, geen logs, geen request-body-persistentie.

**Tech Stack:** Python 3.11, stdlib `sqlite3`/`uuid`/`json`; bestaande zoekpipeline; geen nieuwe dependencies.

## Global Constraints

- **Geen naam-/criteriakolommen.** Schema: `watchlists(id TEXT PK, owner TEXT NOT NULL, label TEXT DEFAULT '', created_at TEXT)` en `watchlist_hits(id INTEGER PK AUTOINCREMENT, watchlist_id TEXT NOT NULL, owner TEXT NOT NULL, ts TEXT, match_json TEXT)`.
- Identiteit: cookie `watch_key` (UUID v4, HttpOnly). `owner` = de cookie-key nu; bij accounts later wordt `owner` de `user_id` (zelfde kolom, zelfde endpoints — alleen de identiteitsbron verandert).
- De client bewaart de naam + criteria + eerder-geziene matches in **localStorage**; de server vraagt die nooit op en slaat ze nooit op.
- Rescan is **client-getriggerd**: de client polt `data_version` (nieuw veld in `/api/status`, bv. max mtime van de index-inputs of een hash) en roept bij wijziging `POST /api/watchlists/{id}/rescan` aan met `{name, birth_year, nationality, birth_place, entity_type}` in de body. De body wordt niet gelogd en niet opgeslagen.
- Dedup: een hit wordt alleen opgeslagen als (watchlist_id, entity-id, score) nog niet eerder voor die watchlist bestaat; `match_json` = `{id, naam, score, bron, datasets}`.
- Owner-only: endpoints retourneren/verwijderen alleen records van de `owner`; andere eigenaren zien niets.
- `POST /api/watchlists` accepteert een optioneel `label` (niet-gevoelige aanduiding, mag leeg); geen naam-veld.
- Audit-log (Fase 1) logt watchlist-acties (aanmaken/verwijderen/rescan-aanvraag) — met het aantal hits, nooit de naam.
- UI-taal Nederlands; identifiers Engels. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- STAGE nooit via `git add .`; alleen eigen bestanden. Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: watchlist-module (opaque IDs, geen naam)

**Files:**
- Create: `app/watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Produces:
  - `default_watchlist_db() -> Path` — `WATCHLIST_DB` of `data/watchlists.sqlite`.
  - `init_watchlist_db(db_path)` — schema (bovenstaand), idempotent.
  - `get_or_create_key(request, response) -> str` — cookie `watch_key` lezen/zetten (UUID v4).
  - `add_watchlist(db_path, owner, label="") -> dict` — nieuwe opaque `{id, owner, label, created_at}`.
  - `list_watchlists(db_path, owner) -> list[dict]`, `delete_watchlist(db_path, owner, watchlist_id) -> bool` (owner-check).
  - `rescan_watch(db_path, owner, watchlist_id, name, fields: dict, search_fn, threshold=90) -> dict` — controleert dat de watchlist van `owner` is; draait `search_fn(name, **fields)`; slaat nieuwe matches (dedup op watchlist_id+entity-id+score) als hits; retourneert `{watchlist_id, hits: [...], new: n}`. De `name` wordt NIET opgeslagen.
  - `list_hits(db_path, owner, watchlist_id=None) -> list[dict]`.

**Tests:** cookie-identiteit (nieuw + bestaand); add/list/delete met owner-check (andere owner ziet niets, kan niet verwijderen); `rescan_watch` met fake `search_fn` produceert hits; dedup (tweede rescan → geen nieuwe); threshold; de naam staat nergens in de DB (assert dat geen enkele tabel/kolom naam bevat en de body-`name` niet in de DB-landt).

### Task 2: endpoints + data_version

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `app.watchlist`, `run_search`.
- Produces:
  - `POST /api/watchlists` (body `{label?}`), `GET /api/watchlists`, `DELETE /api/watchlists/{id}` — owner uit de `watch_key`-cookie.
  - `POST /api/watchlists/{id}/rescan` — body `{name, birth_year?, nationality?, birth_place?, entity_type?}` (naam verplicht); roept `rescan_watch(...)` met de echte `run_search`; retourneert nieuwe hits. Body wordt niet gelogd (audit logt alleen count).
  - `GET /api/watchlists/hits` — hits van de eigenaar.
  - `/api/status` krijgt `data_version` (bijv. de `_newest_input_mtime` of een korte hash van de index-inputs), zodat de client weet wanneer opnieuw te screenen.
  - Audit: aanmaken/verwijderen/rescan (aantal hits).

**Tests:** cookie-gebaseerde endpoints; rescan met echte kleine index produceert + retourneert hits; hits alleen voor eigenaar; `data_version` in status verandert als de data verandert (mtime-bump); body-naam komt niet in de DB terecht (controle op `watchlists`- en `watchlist_hits`-tabellen).

### Task 3: UI (localStorage + polling)

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/style.css`

**Interfaces:**
- Produces:
  - localStorage-sleutel `watchlist.<watch_id>` → `{name, birth_year, nationality, birth_place, entity_type, known: {entityId: score}}`.
  - Knop "Bewaak deze naam" naast de zoekknop (voegt de huidige query toe; maakt eerst een opaque ID aan via `POST /api/watchlists`).
  - Poll `GET /api/status` (elke ~60s of bij pageload); als `data_version` verandert → voor elke watchlist `POST /api/watchlists/{id}/rescan` met de opgeslagen naam; nieuwe hits → badge + melding-vak.
  - Lijst van watchlists (label + aantal hits) met verwijder-knop; naam wordt alleen uit localStorage getoond.

**Verificatie:** `node --check`, volledige suite groen, handmatige test (bewaken → data-verversing simuleren → badge toont nieuwe hit).

---

## Self-Review

**Spec-cover:** Fase 4, variant 2 — namen nooit opgeslagen (opaque IDs, client stuurt naam per rescan), accounts-klaar (`owner`-kolom), client-side polling via `data_version`, dedup, in-app meldingen. **Placeholders:** geen. **Consistentie:** `get_or_create_key`/`add_watchlist`/`rescan_watch`/`list_hits` identiek gebruikt in Task 2-3. **Need-to-know:** geen naam in schema, geen body-logging, owner-only access — expliciet getest.
