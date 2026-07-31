# PDF-export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exporteer zoekresultaten als een PDF-screeningsrapport met zoekvelden, datum/tijd (met tijdzone), dataversies, auteur (optioneel), resultaten met scores + bronnen + match-details, drempel/cap-transparantie, warnings en disclaimer.

**Architecture:** `app/export.py` rendert het rapport met **reportlab** (Platypus). De zoeklogica uit `main.py`'s `search`-route wordt geëxtraheerd naar `run_search(...)` (gebruikt door `/api/search` én het nieuwe `GET /api/search/export`-endpoint, dat `application/pdf` retourneert). Frontend krijgt een optioneel auteur-veld + "Exporteer PDF"-knop.

**Tech Stack:** Python 3.11+, **reportlab** (nieuwe dependency), bestaande fastapi/rapidfuzz; pytest.

## Global Constraints

- Python 3.11+; alleen `reportlab` als nieuwe dependency toegevoegd aan `requirements.txt` (pin op de dan geldende latest).
- `GET /api/search/export` accepteert dezelfde query-params als `/api/search` (`name` verplicht, `birth_year`, `nationality`, `birth_place`, `entity_type`) plus optioneel `author`; retourneert `application/pdf` met `Content-Disposition: attachment; filename="screening-<YYYY-MM-DD>.pdf"`.
- Het PDF-resultaat is gegarandeerd identiek aan `/api/search`: beide routes gebruiken dezelfde `run_search`-helper.
- Datum/tijd in het rapport: lokaal + tijdzone (bijv. `2026-07-31 15:40 CET`); dataversies uit `APP_VERSION`, EU-manifest (`generation_date`/`last_modified`) en PEP-manifest (`updated_at`).
- Disclaimer-tekst in het Nederlands: score is een risico-indicatie, geen veroordeling; PEP is een risicocategorie; databronnen EU FSF + OpenSanctions (licenties); geen juridisch advies.
- Drempel (90) en cap (max 20) worden vermeld; bij 20 resultaten: "cap bereikt: mogelijk meer resultaten".
- Speciale tekens in namen/velden worden voor reportlab-paragraphs XML-ge-escaped.
- UI-taal Nederlands. Geen code-commentaar tenzij niet-voor-de-hand liggend.
- STAGE nooit via `git add .`; alleen eigen bestanden. Testsuite: `.venv/bin/python -m pytest -v`.

---

### Task 1: reportlab-dependency + PDF-renderer

**Files:**
- Modify: `requirements.txt`
- Create: `app/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: resultaatrecords uit `run_search` (zie Task 2) — shape `results: list[dict]` (zoals `/api/search` retourneert) en `warnings: list[str]`; `meta` (EU-manifest) en `pep_manifest` voor dataversies; `version`.
- Produces:
  - `render_search_pdf(payload: dict) -> bytes` — payload = `{"query": {...}, "results": [...], "warnings": [...], "meta": {...}, "pep_meta": {...}, "version": str, "author": str|None, "generated_at": str, "threshold": int, "max_results": int}`. Retourneert geldige PDF-bytes (reportlab `SimpleDocTemplate` → `BytesIO`).
  - `_escape(text) -> str` — XML-escaping voor reportlab-paragraphs (`&`, `<`, `>`, `"`).

- [ ] **Step 1: Write the failing tests**

`tests/test_export.py`:
```python
from app.export import render_search_pdf, _escape


def _payload(**over):
    payload = {
        "query": {"name": "JORGE FERNANDEZ", "birth_year": None, "nationality": None, "birth_place": None, "entity_type": None},
        "results": [
            {
                "source": "pep",
                "score": 100,
                "entity": {"name": "JORGE FERNÁNDEZ", "schema": "Person", "birth_dates": ["1965-03-01"], "birth_places": [], "citizenships": ["ar"], "political": ["PRIMERO SAN LUIS"], "topics": ["role.pep"]},
                "pep": {"id": "NK-1", "url": "https://opensanctions.org/entities/NK-1", "datasets": [{"id": "ar_parliament", "title": "Argentina Members of Parliament", "country": "ar", "url": "https://www.opensanctions.org/datasets/ar_parliament/"}], "matched_name": "JORGE FERNÁNDEZ", "details": [{"feature": "naam", "score": 100, "label": "Naam 100% (via \"JORGE FERNÁNDEZ\")"}]},
                "eu": None, "opensanctions": None,
            }
        ],
        "warnings": ["OpenSanctions tijdelijk niet beschikbaar"],
        "meta": {"generation_date": "2026-07-28T11:43:32", "last_modified": "Tue, 28 Jul 2026 11:00:00 GMT"},
        "pep_meta": {"updated_at": "2026-07-31T14:20:01"},
        "version": "v1.5.0",
        "author": "Dennis",
        "generated_at": "2026-07-31 15:40 CET",
        "threshold": 90,
        "max_results": 20,
    }
    payload.update(over)
    return payload


def test_escape():
    assert _escape("JORGE <FERNÁNDEZ> & \"Co\"") == "JORGE &lt;FERNÁNDEZ&gt; &amp; &quot;Co&quot;"


def test_render_returns_pdf_bytes():
    data = render_search_pdf(_payload())
    assert data[:4] == b"%PDF"
    assert b"%%EOF" in data[-20:]


def test_render_contains_required_sections():
    data = render_search_pdf(_payload())
    # Alle tekst staat gecodeerd in de PDF-stream; controles op platte substrings werken
    # voor ASCII-delen (zoekveld, datum, auteur, disclaimer-kernwoorden).
    for needle in [b"JORGE FERNANDEZ", b"Uitgevoerd", b"Dennis", b"OpenSanctions", b"Disclaimer", b"90"]:
        assert needle in data


def test_render_empty_results():
    data = render_search_pdf(_payload(results=[], warnings=[]))
    assert data[:4] == b"%PDF"
    assert b"Geen overeenkomsten" in data


def test_render_many_results_paginates():
    payload = _payload(results=[])
    for i in range(30):
        payload["results"].append({
            "source": "eu",
            "score": 100,
            "entity": {"name": f"Persoon {i}", "eu_reference_number": f"EU.{i}"},
            "eu": {"matched_alias": f"Persoon {i}", "details": [{"feature": "naam", "score": 100, "label": f"Naam 100% (via \"Persoon {i}\")"}]},
            "opensanctions": None, "pep": None,
        })
    data = render_search_pdf(payload)
    assert data[:4] == b"%PDF"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: FAIL met `ModuleNotFoundError: No module named 'app.export'`.

- [ ] **Step 3: Voeg reportlab toe en schrijf de renderer**

`requirements.txt` → voeg toe: `reportlab==<latest bij implementatie>` (check `curl -s https://pypi.org/pypi/reportlab/json | jq -r .info.version`).

`app/export.py`:
```python
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_TITLE = "Compliance Zoeker — Screeningsrapport"
_DISCLAIMER = (
    "Disclaimer: een match-score is een risico-indicatie, geen veroordeling. "
    "Een 'Politically Exposed Person'-vermelding is een risicocategorie, geen beschuldiging. "
    "De gegevens komen uit de EU-sanctielijst (FSF) en OpenSanctions (CC BY-NC 4.0). "
    "Dit rapport vormt geen juridisch advies."
)


def _escape(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _query_lines(query: dict) -> list[str]:
    lines = [f"Naam: {_escape(query.get('name', ''))}"]
    labels = [("birth_year", "Geboortejaar"), ("nationality", "Nationaliteit"), ("birth_place", "Geboorteplaats"), ("entity_type", "Type")]
    for key, label in labels:
        value = query.get(key)
        if value:
            lines.append(f"{label}: {_escape(value)}")
    return lines


def _result_paragraphs(result: dict, styles) -> list:
    source = result.get("source", "?")
    source_label = {"eu": "EU sanctielijst", "pep": "PEP", "opensanctions": "OpenSanctions"}.get(source, source)
    parts = [Paragraph(f"<b>{_escape(result['entity'].get('name', ''))}</b> — score {result.get('score', 0)}/100 ({_escape(source_label)})", styles["h3"])]
    details = result.get(source, {}).get("details") or result.get("pep", {}).get("details") or []
    for d in details:
        parts.append(Paragraph(f"&bull; {_escape(d.get('label', ''))}", styles["body"]))
    entity = result.get("entity", {})
    if result.get("eu") is not None:
        parts.append(Paragraph(f"EU-referentie: {_escape(entity.get('eu_reference_number', ''))}", styles["body"]))
    if result.get("pep") is not None:
        for ds in result["pep"].get("datasets", []):
            parts.append(Paragraph(f"Bron: {_escape(ds.get('title', ''))} ({_escape(ds.get('country', '').upper())}) — {_escape(ds.get('url', ''))}", styles["body"]))
        parts.append(Paragraph(f"Details: {_escape(result['pep'].get('url', ''))}", styles["body"]))
    parts.append(Spacer(1, 4))
    return parts


def render_search_pdf(payload: dict) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=_TITLE)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    story = [
        Paragraph(f"<b>{_TITLE}</b>", styles["Title"]),
        Paragraph(f"Versie: {_escape(payload.get('version', 'dev'))}", body),
        Spacer(1, 4 * mm),
    ]
    story.append(Paragraph("<b>Zoekopdracht</b>", styles["h2"]))
    for line in _query_lines(payload["query"]):
        story.append(Paragraph(line, body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("<b>Uitgevoerd</b>", styles["h2"]))
    story.append(Paragraph(f"Op: {_escape(payload.get('generated_at', ''))}", body))
    if payload.get("author"):
        story.append(Paragraph(f"Door: {_escape(payload['author'])}", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("<b>Dataversies</b>", styles["h2"]))
    meta = payload.get("meta", {}) or {}
    story.append(Paragraph(f"EU-lijst generatie: {_escape(meta.get('generation_date', 'onbekend'))}", body))
    pep_meta = payload.get("pep_meta", {}) or {}
    story.append(Paragraph(f"PEP-update: {_escape(pep_meta.get('updated_at', 'onbekend'))}", body))
    story.append(Spacer(1, 4 * mm))
    results = payload.get("results", [])
    capped = len(results) >= payload.get("max_results", 20)
    story.append(Paragraph("<b>Resultaten</b>", styles["h2"]))
    story.append(Paragraph(f"Getoond: {len(results)} | drempel: {payload.get('threshold', 90)}%{ ' | LET OP: cap bereikt, mogelijk meer resultaten' if capped else ''}", body))
    story.append(Spacer(1, 2 * mm))
    if not results:
        story.append(Paragraph("Geen overeenkomsten gevonden.", body))
    for result in results:
        story.extend(_result_paragraphs(result, styles))
    for warning in payload.get("warnings", []):
        story.append(Paragraph(f"<b>Waarschuwing:</b> {_escape(warning)}", body))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"<i>{_DISCLAIMER}</i>", body))
    doc.build(story)
    return buffer.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: 6 passed. (Installeer eerst reportlab in de venv: `.venv/bin/python -m pip install -r requirements.txt`.)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/export.py tests/test_export.py
git commit -m "feat: PDF screening report renderer with reportlab"
```

---

### Task 2: zoeklogica-helper + export-endpoint

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `render_search_pdf`; de bestaande search-route-logica.
- Produces:
  - `run_search(state, query, datasets_meta, os_api_key) -> tuple[list[dict], list[str]]` — de geëxtraheerde zoeklogica uit de huidige `search`-route (EU via index of fallback, PEP, OpenSanctions, sorteer + cap).
  - `GET /api/search/export` — zelfde params + `author: str | None = None`; roept `run_search` aan, bouwt de `payload`, retourneert `Response(content=render_search_pdf(payload), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="screening-<datum>.pdf"'})`.
  - `GET /api/search` gebruikt voortaan `run_search` (gedrag ongewijzigd).

- [ ] **Step 1: Write the failing tests**

Append aan `tests/test_main.py`:
```python
def test_export_returns_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "JORGE FERNANDEZ", "author": "Dennis"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:4] == b"%PDF"


def test_export_requires_name():
    client = TestClient(create_app(entities=ENTITIES))
    assert client.get("/api/search/export").status_code == 422


def test_export_empty_results(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "Zzqqq Xxww"})
    assert resp.status_code == 200
    assert b"Geen overeenkomsten" in resp.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_main.py -k export -v`
Expected: FAIL met `404 Not Found` voor `/api/search/export`.

- [ ] **Step 3: Implement**

In `app/main.py`:
- Importeer `render_search_pdf` (`from .export import render_search_pdf`) en `Response`/`json`/`datetime`.
- Extraheer de bestaande search-route-logica in een module-functie `run_search` (binnen `create_app` closure of als aparte functie met `state`/`datasets_meta`/`os_api_key`), zodat `/api/search` en `/api/search/export` dezelfde code gebruiken. Behoud de fallback (index `disabled` → `matcher.search_eu(state["entities"])`, `building` → EU + warning).
- Voeg het endpoint toe:
```python
    @app.get("/api/search/export")
    def search_export(
        name: str = Query(..., min_length=1),
        birth_year: int | None = Query(None, ge=1900, le=2100),
        nationality: str | None = None,
        birth_place: str | None = None,
        entity_type: str | None = Query(None, pattern="^(person|enterprise)$"),
        author: str | None = None,
    ):
        query = matcher.SearchQuery(name=name.strip(), birth_year=birth_year, nationality=(nationality or "").strip() or None, birth_place=(birth_place or "").strip() or None, entity_type=entity_type)
        if not query.name:
            raise HTTPException(status_code=422, detail="Naam is verplicht")
        results, warnings = run_search(query)
        generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        payload = {
            "query": {"name": query.name, "birth_year": query.birth_year, "nationality": query.nationality, "birth_place": query.birth_place, "entity_type": query.entity_type},
            "results": results, "warnings": warnings,
            "meta": state["meta"], "pep_meta": load_pep_manifest(pep_root),
            "version": os.environ.get("APP_VERSION", "dev"),
            "author": author, "generated_at": generated,
            "threshold": matcher.THRESHOLD, "max_results": matcher.MAX_RESULTS,
        }
        pdf = render_search_pdf(payload)
        filename = f"screening-{datetime.now().astimezone().strftime('%Y-%m-%d')}.pdf"
        return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```
Let op: `load_pep_manifest` uit `pep_ingest` importeren; `run_search` moet `datasets_meta` en de EU/PEP-serializers correct doorgeven. De `generated_at` is de zoektijd (niet buildtijd).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: alle bestaande + 3 nieuwe tests groen.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: search PDF export endpoint sharing the search pipeline"
```

---

### Task 3: Frontend — auteur-veld + export-knop

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/style.css`

**Interfaces:**
- Consumes: `GET /api/search/export?...&author=...` (PDF-download).
- Produces: optioneel invoerveld "Uitgevoerd door" + knop "Exporteer PDF" die een nieuw tabblad opent met de export-URL van de huidige formulierwaarden.

- [ ] **Step 1: Update `static/index.html`**

Voeg na het zoekformulier (binnen `<form id="search-form">`, vóór de submit-knop) toe:
```html
        <div class="row">
          <div class="field">
            <label for="author">Uitgevoerd door (optioneel)</label>
            <input type="text" id="author" name="author" autocomplete="name" placeholder="bijv. J. Jansen">
          </div>
          <div class="field">
            <button type="button" id="export-btn">Exporteer PDF</button>
          </div>
        </div>
```
(Verplaats de bestaande submit-knop indien nodig naar een eigen rij.)

- [ ] **Step 2: Update `static/app.js`**

Voeg aan het einde toe:
```js
const exportBtn = document.getElementById("export-btn");
exportBtn.addEventListener("click", () => {
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
  const author = document.getElementById("author").value.trim();
  if (author) params.set("author", author);
  window.open(`/api/search/export?${params}`, "_blank");
});
```

- [ ] **Step 3: Update `static/style.css`**

Zorg dat `#export-btn` past bij de bestaande knoppenstijl (zelfde regels als de submit-knop; eventueel een `.btn-secondary`-klasse). Controleer de bestaande `button`-stijl en pas aan.

- [ ] **Step 4: Verify**

Run: `node --check static/app.js` en `.venv/bin/python -m pytest -q` (alles groen). Optioneel: lokale server starten en handmatig `/api/search/export?name=Rosneft` testen.

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat: author field and PDF export button in UI"
```

---

### Task 4: Docker + docs + verificatie

**Files:**
- Modify: `Dockerfile` (niet nodig — reportlab zit in requirements), `README.md`
- Verify: volledige suite + podman build

**Interfaces:**
- Consumes: alles uit Task 1-3.
- Produces: werkende PDF-export in de container.

- [ ] **Step 1: Update `README.md`**

- Vermeld de "Exporteer PDF"-functionaliteit (zoekopdracht + datum/tijd + dataversies + resultaten met scores/bronnen + disclaimer).
- Vermeld dat reportlab een nieuwe dependency is.

- [ ] **Step 2: Verify — full suite + Docker**

Run:
```bash
.venv/bin/python -m pytest -v
podman build -f Dockerfile -t sanctielijst-app:test .
podman run --rm -p 8001:8000 sanctielijst-app:test & sleep 6
curl -s -o /tmp/screen.pdf -w "%{http_code} %{content_type}\n" "http://localhost:8001/api/search/export?name=Rosneft"
head -c 4 /tmp/screen.pdf
```
Expected: volledige suite groen; build slaagt; export retourneert 200 + `application/pdf` en een `%PDF`-bestand.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document PDF export feature"
```

---

## Self-Review

**Spec coverage:** zoekvelden (Task 2-3), datum/tijd + tijdzone (Task 2 `generated_at`), dataversies (Task 1-2 `meta`/`pep_meta`/`version`), auteur (Task 2-3), resultaten met scores + bronnen + match-details (Task 1 `_result_paragraphs`), drempel/cap (Task 1), warnings (Task 1), disclaimer (Task 1) — alles gedekt. ✔

**Placeholders:** geen TBD/TODO; code per stap (reportlab-versie wordt bij implementatie gecheckt).

**Type-consistentie:** `render_search_pdf(payload)` (Task 1) wordt in Task 2 met dezelfde payload-shape aangeroepen; `run_search(query) -> (results, warnings)` wordt door beide routes gebruikt; `author`/`generated_at`/`threshold`/`max_results` in payload.
