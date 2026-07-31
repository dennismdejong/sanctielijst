# Posities koppelen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per PEP-persoon tonen welke (politieke) functies hij/zij (heeft) bekleed, afgeleid uit de OpenSanctions Occupancy/Position-entiteiten die al in `data/pep/` staan.

**Architecture:** De `search_index`-build leest naast de target Person/Company-entiteiten óók de `Occupancy`- en `Position`-entiteiten. Een `Occupancy` verbindt een `holder` (persoon-id) met een `post` (positie-id) en heeft `status`/`startDate`/`endDate`. Een `Position`-entiteit heeft als `caption` de rolnaam. Per persoon wordt een `positions`-lijst `[{role, status, start, end}]` gebouwd. De `entities`-tabel krijgt een `positions`-kolom (JSON); de index wordt herbouwd (atomic + zero-downtime, bestaat al). Resultaten, frontend en PDF tonen de posities.

**Tech Stack:** Python 3.11, stdlib `sqlite3`/`json`; geen nieuwe dependencies.

## Global Constraints

- Schema-uitbreiding `entities.positions TEXT NOT NULL DEFAULT '[]'`; bestaande indexen worden bij deze feature herbouwd (de `build_index`-versie bump; de atomic-swap en mtime-check blijven werken).
- Position-rolnaam: uit de `Position`-entiteit (`caption`); als de post-id niet gevonden wordt → rol = post-id (fallback). Periodes: `startDate`/`endDate`; `status` meegeven.
- Alleen actieve én beëindigde posities tonen (met status); niet meer dan ~10 posities per persoon in de UI (oudste eerst weggelaten indien nodig).
- `_pep_records` wordt uitgebreid met `positions`; de PEP-serializer, `_result_paragraphs` (PDF) en `pepCard` (frontend) tonen posities.
- UI-taal Nederlands; identifiers Engels. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- STAGE nooit via `git add .`; alleen eigen bestanden. Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: positions-verrijking in de index-build

**Files:**
- Modify: `app/search_index.py`
- Test: `tests/test_search_index.py`

**Interfaces:**
- Consumes: bestaande `_pep_records`, `build_index`.
- Produces:
  - `_positions_by_holder(pep_root: Path) -> dict[str, list[dict]]` — parseert ALLE FTM-bestanden, verzamelt `Position`-entiteiten (id → caption) en `Occupancy`-entiteiten (`holder`, `post`, `status`, `startDate`, `endDate`); retourneert `{holder_id: [{"role", "status", "start", "end"}]}`.
  - `_pep_records(...)` krijgt een optionele `positions_map`-param en vult `positions` per record.
  - `build_index` roept `_positions_by_holder` aan, geeft het door, en voegt `positions` (JSON) toe aan de insert + SCHEMA.

**Tests:** fixture met een Position, een Occupancy (holder→post, start/end/status) en een Person; assert `positions` op de persoon; onbekende post-id → fallback; meerdere posities gesorteerd; schema heeft de kolom; bestaande tests blijven groen (default leeg).

### Task 2: tonen in resultaten, PDF en frontend

**Files:**
- Modify: `app/main.py`, `app/export.py`, `static/app.js`, `tests/test_main.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: `positions` op het PEP-record.
- Produces:
  - `_serialize_pep_result` voegt `entity["positions"]` toe.
  - `_result_paragraphs` (PDF): regel `Functies: <rol> (<status>, <start>-<end>)` per positie (max 5).
  - `pepCard` (frontend): lijstje `Functies` met rol + periode/status.

**Tests:** PEP-resultaat bevat positions; PDF toont een functieregel; frontend-verificatie via `node --check`; bestaande suite groen.

---

## Self-Review

**Spec-cover:** Fase 3 — posities uit Occupancy koppelen, schema-uitbreiding + rebuild, tonen in resultaten/PDF/UI. **Placeholders:** geen. **Consistentie:** `_positions_by_holder`/`positions`-veld identiek gebruikt in Task 1-2.
