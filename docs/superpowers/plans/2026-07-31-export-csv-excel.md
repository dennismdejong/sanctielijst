# CSV/Excel-export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zoekresultaten als tabel exporteren (CSV en .xlsx) naast de bestaande PDF, met dezelfde zoekpipeline zodat de inhoud identiek is aan de UI.

**Architecture:** Het bestaande `GET /api/search/export` krijgt een `format`-query-param (`pdf` default, `csv`, `xlsx`). `app/export.py` krijgt `render_search_csv(results, query) -> str` (stdlib `csv`) en `render_search_xlsx(results, query) -> bytes` (openpyxl). Beide gebruiken dezelfde `run_search`-helper. `openpyxl` is een nieuwe dependency.

**Tech Stack:** Python 3.11, bestaande FastAPI/PDF-pipeline, **openpyxl** (nieuw in `requirements.txt`, pin op latest).

## Global Constraints

- `GET /api/search/export?format=pdf|csv|xlsx` — default `pdf`; ongeldige waarde → 422.
- CSV: `text/csv` met `Content-Disposition: attachment; filename="screening-<datum>.csv"`; kolommen: `naam; score; bron; datasets; match-details; eu_referentie; geboortedata; nationaliteit; links`. Scheidingsteken `;` (Nederlandse Excel). UTF-8 met BOM (`\ufeff`) zodat Excel diakritische tekens goed toont.
- XLSX: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` met zelfde kolommen; kolombreedtes + vetgedrukte kop.
- Leeg resultaat → export met alleen de kopregel/headers en 0 datarijen (geen fout).
- De data komt uit `run_search` (identiek aan UI/PDF); de audit-log (Fase 1) logt ook deze exports.
- UI-taal Nederlands; identifiers Engels. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- STAGE nooit via `git add .`; alleen eigen bestanden. Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: CSV- en XLSX-renderers

**Files:**
- Modify: `requirements.txt`, `app/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Produces:
  - `_export_rows(results: list[dict]) -> list[list[str]]` — per resultaat een rij: naam, score, bronlabel (EU/PEP/OpenSanctions), datasets (PEP, `;`-gescheiden), match-details (labels, `;`-gescheiden), EU-referentie, geboortedata (`/`-gescheiden), nationaliteit, opensanctions-URL.
  - `render_search_csv(results: list[dict], query: dict) -> str` — `;`-gescheiden, BOM, `\r\n`.
  - `render_search_xlsx(results: list[dict], query: dict) -> bytes` — openpyxl `Workbook`, header-rij, kolombreedtes, `BytesIO`.

**Tests:** CSV heeft BOM + kopregel + datarij; speciale tekens (`;`, diakritische) correct; xlsx begint met het ZIP/XLSX-magic (`PK`) en opent in openpyxl met juiste cellen; leeg resultaat → alleen headers.

### Task 2: format-param in het export-endpoint

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `render_search_csv`, `render_search_xlsx`, `run_search`.
- Produces: `search_export(..., format: str = Query("pdf", pattern="^(pdf|csv|xlsx)$"))` — switcht op format; retourneert respectievelijk PDF-bytes, CSV-bytes, XLSX-bytes met juiste media-type + attachment-filename; audit-loggen van de export.

**Tests:** `format=csv` → `text/csv` + BOM; `format=xlsx` → xlsx-media-type + `PK`-magic; `format=onzin` → 422; default blijft `application/pdf`.

---

## Self-Review

**Spec-cover:** Fase 2a — CSV + Excel naast PDF, zelfde pipeline, lege-resultaat-gedrag, audit-integratie. **Placeholders:** geen. **Consistentie:** `render_search_csv`/`render_search_xlsx` worden in Task 2 met de `run_search`-output aangeroepen.
