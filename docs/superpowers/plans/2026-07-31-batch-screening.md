# Batch-screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Een bestand (CSV of .xlsx) met honderden namen in één keer screenen tegen de lokale EU+PEP-index → één overzichtsrapport (PDF + CSV/JSON), asynchroon met een job-id.

**Architecture:** `app/batch.py` parseert CSV/xlsx (openpyxl) tot regels `{naam, geboortejaar, nationaliteit, geboorteplaats, type}`. Elke regel wordt door de bestaande `run_search`-helper (uit `main.py`) gescreend. Jobs leven in `data/batch.sqlite` (tabel `batch_jobs` + `batch_results`); een achtergrond-thread verwerkt de job; `POST /api/batch` geeft een `batch_id`, `GET /api/batch/{id}` geeft status + resultaat. Het PDF-overzichtsrapport bouwt voort op de bestaande renderer.

**Tech Stack:** Python 3.11, bestaande zoek/PDF-pipeline, **openpyxl** (batch + Fase 2a delen de dependency), stdlib `sqlite3`, `threading`.

## Global Constraints

- Upload: `POST /api/batch` (multipart `file`), CSV (`text/csv`) of `.xlsx`. Kolommen: `naam` (verplicht), optioneel `geboortejaar`, `nationaliteit`, `geboorteplaats`, `type` (`person|enterprise`). Header-rij verwacht (naam hoofdlettergevoelig niet — accepteer `Naam`/`naam`/`NAME`).
- Limiet: max **5.000** regels per batch (413 bij overschrijding). Regels zonder naam → per-regel validatiefout (niet de hele batch afkeuren).
- Per regel: dezelfde scoring als de UI (drempel 90, max 20); opgeslagen resultaat = naam, top-matches ≥ drempel met score/bron/details.
- Output: `GET /api/batch/{id}` → `{status: pending|running|done|error, progress, rows: [...], errors: [...]}`; plus `GET /api/batch/{id}/report.pdf` en `.../report.csv`.
- Jobs blijven in `data/batch.sqlite` (env `BATCH_DB`); retentie later.
- Audit-log (Fase 1) logt batch-aanmaak + rapport-downloads.
- UI-taal Nederlands; identifiers Engels. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- STAGE nooit via `git add .`; alleen eigen bestanden. Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: batch-module (parser + job-store + verwerking)

**Files:**
- Create: `app/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `run_search` (dependency-injection: de batch-module krijgt een `search_fn`-callable, zodat `main.py` zijn eigen `run_search` doorgeeft en tests een fake gebruiken).
- Produces:
  - `default_batch_db() -> Path` — `BATCH_DB` of `data/batch.sqlite`.
  - `init_batch_db(db_path)` — tabellen `batch_jobs(id, status, created_at, finished_at, progress, total, errors, error_text)` en `batch_results(batch_id, row_index, row_json, matches_json)`.
  - `parse_input(filename: str, content: bytes) -> tuple[list[dict], list[dict]]` — regels + per-regel-fouten (openpyxl voor `.xlsx`, `csv`-module voor `.csv`).
  - `create_job(db_path, filename, rows) -> str` (uuid).
  - `process_job(db_path, job_id, search_fn, row_limit=5000)` — loopt regels, roept `search_fn(naam, ...)` aan, schrijft `batch_results`, werkt `progress`/`status`.
  - `get_job(db_path, job_id) -> dict | None`, `get_results(db_path, job_id) -> list[dict]`.

**Tests:** parse CSV (BOM, `;`- en `,`-scheiding, diakritische tekens) en xlsx; ongeldige/lege naam → per-regel-fout; create+process (fake search_fn) → status done + progress; limiet 5000 → 413-signaal (parse-level); get_job/get_results.

### Task 2: endpoints + achtergrondverwerking

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `app.batch`, `run_search`.
- Produces:
  - `POST /api/batch` — ontvangt `UploadFile`, leest bytes, `parse_input`, `create_job`, start daemon-thread `process_job(...)` met de echte `run_search`; retourneert `{batch_id}`. 413 bij >5000 regels, 400 bij leeg bestand/geen naam-kolom.
  - `GET /api/batch/{batch_id}` — status + (zodra done) per-regel resultaten/errors.
  - `GET /api/batch/{batch_id}/report.pdf` en `.../report.csv` — overzichtsrapport: per regel de naam + matches (maakt gebruik van een `render_batch_pdf`/`render_batch_csv` in `app/export.py`, toegevoegd in deze taak; `_result_paragraphs` wordt hergebruikt).
  - Batch-aanmaak en rapport-downloads gaan door de audit-log.

**Tests:** upload CSV → batch_id; status pending→done; resultaat bevat de juiste matches (via echte kleine index, zoals de bestaande `_write_search_db`-testhelpers); 413 bij te veel regels; report.pdf is `%PDF`, report.csv heeft BOM.

---

## Self-Review

**Spec-cover:** Fase 2b — CSV én Excel upload, async job, overzichtsrapport PDF+CSV, limiet, per-regel-validatie, audit. **Placeholders:** geen. **Consistentie:** `run_search`-signatuur wordt doorgegeven; `parse_input`/`create_job`/`process_job`/`get_job` in Task 2 identiek gebruikt.
