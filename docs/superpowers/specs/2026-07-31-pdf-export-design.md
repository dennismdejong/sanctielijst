# Ontwerp — Zoekresultaten exporteren naar PDF (screeningsrapport)

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker (design), ter uitvoering opgeslagen

## Doel

Een knop "Exporteer PDF" in de UI die het huidige zoekresultaat als PDF-screeningrapport downloadt. Het rapport bevat de ingegeven zoekvelden, datum/tijd van de zoekopdracht, dataversies, auteur (optioneel), de resultaten met scores en bronnen, en de essentiële verantwoordingsgegevens.

## Essentiële inhoud (zoals afgestemd met gebruiker)

De gebruiker vroeg om: zoekvelden, zoekdatum, resultaten met scores en bronnen. Aangevuld met (door mij voorgesteld en goedgekeurd):

1. **Datum/tijd inclusief tijdzone** van de zoekopdracht.
2. **Dataversies**: app-versie (`APP_VERSION`), EU `generation_date`/`last_modified` (uit het EU-manifest), PEP `updated_at` (uit het PEP-manifest) — zodat het rapport achteraf verifieerbaar is.
3. **Auteur/zoeker** — optioneel invoerveld "Uitgevoerd door" in de UI; ingevuld → getoond, leeg → weggelaten.
4. **Disclaimer**: score is een risico-indicatie, geen veroordeling; PEP is een risicocategorie; databronnen (EU FSF, OpenSanctions) met licenties; geen juridisch advies.
5. **Drempel- en cap-transparantie**: de 90%-drempel wordt vermeld, en of de max-20-resultaten-cap is bereikt (dan is het rapport mogelijk onvolledig).
6. **Match-details per resultaat**: wélk alias/kenmerk matchte en hoe sterk (uit `details`), niet alleen de totaalscore.
7. **Warnings** die tijdens de zoekopdracht optraden (bv. "OpenSanctions tijdelijk niet beschikbaar", "index werd opgebouwd").

## Techniek

- **`reportlab`** (nieuwe dependency in `requirements.txt`) — pure-Python, werkt in de `python:3.14-slim`-image zonder systeempakketten. Pin bij implementatie op de dan geldende latest-versie.
- Nieuwe module **`app/export.py`** met `render_search_pdf(payload: dict) -> bytes` (reportlab Platypus: `SimpleDocTemplate`, `Paragraph`, `Table`).
- Nieuw endpoint **`GET /api/search/export`** met dezelfde query-params als `/api/search` plus optioneel `author`. Het draait dezelfde zoekpipeline en retourneert `application/pdf` met `Content-Disposition: attachment; filename="screening-<datum>.pdf"`.
- **Refactor (DRY):** de zoeklogica uit de `search`-route in `main.py` wordt geëxtraheerd naar een helper `run_search(state, query, datasets_meta, os_api_key) -> (results, warnings)`, gebruikt door zowel `/api/search` als `/api/search/export`. Zo is het PDF-resultaat gegarandeerd identiek aan wat de gebruiker zag.

## Rapport-opbouw (PDF)

1. **Kop**: "Compliance Zoeker — Screeningsrapport" + versie.
2. **Zoekopdracht**: naam, geboortejaar, nationaliteit, geboorteplaats, type.
3. **Uitgevoerd op**: datum/tijd met tijdzone; auteur (indien ingevuld).
4. **Dataversies**: app-versie, EU-generatiedatum, PEP-updatedatum.
5. **Resultatenoverzicht**: totaal aantal getoonde resultaten, drempel (90%), "cap bereikt" ja/nee.
6. **Disclaimer** (korte paragraaf).
7. **Per resultaat** een blok:
   - Naam + totaalscore + bron(badge) (EU / PEP / OpenSanctions)
   - Match-details (per kenmerk: label + score)
   - Relevante velden afhankelijk van de bron:
     - EU: EU-referentie, VN-id, aliassen, geboortedata/-plaats, nationaliteit, functie, reglementen + publicatie-URL, opmerkingen
     - PEP: schema, geboortedata/-plaats, nationaliteit, partij/functie, risico-tags, dataset(s) (titel + land) + opensanctions.org-link
     - OpenSanctions: score (0-1), match-status, risico-tags, datasets, opensanctions-link
8. **Warnings** (indien aanwezig).
9. **Voettekst**: pagina-nummering.

## Frontend

- Een invoerveld "Uitgevoerd door" (optioneel) + knop "Exporteer PDF" naast de zoekknop.
- De knop bouwt de URL naar `/api/search/export` met de huidige formulierwaarden (+ `author` indien ingevuld) en opent die in een nieuw tabblad (download).

## Teststrategie

- `tests/test_export.py` (nieuw):
  - `render_search_pdf` retourneert geldige PDF-bytes (begint met `%PDF`, bevat verplichte secties als string in de ontsleutelde stream of via `PyPDF2`/`pypdf`-vrije heuristiek).
  - Bevat zoekvelden, datum, dataversies, resultaatblokken (naam, score, bron), disclaimer.
  - Leeg resultaat → nette "geen resultaten"-sectie.
  - Speciale tekens (diakritische tekens, `<>&`) worden correct ge-escaped voor reportlab-paragraphs.
  - Pagina's: meerdere resultaten pagineren zonder fout.
- `tests/test_main.py` uitbreiden:
  - `/api/search/export?name=...` retourneert `200` + `application/pdf` + attachment-header.
  - Zonder `name` → `422`.
  - Met `author` → auteur verschijnt in de PDF (via string-check op de bytes of in `render_search_pdf`-unit-test).
- Volledige suite blijft groen; Docker-image (reportlab in requirements) bouwt in de CI-workflow mee.

## Foutafhandeling

- Geen resultaten → PDF met "Geen overeenkomsten gevonden" + zoekopdracht.
- Index niet klaar/building → dezelfde warnings als de API; PDF wordt gewoon gegenereerd met EU-only resultaten (consistent met de zoekopdracht).
- PDF-generatiefout → HTTP 500 met duidelijke melding.

## Config

- `reportlab` toegevoegd aan `requirements.txt`; geen nieuwe env-vars.
- Filenaam: `screening-<YYYY-MM-DD>.pdf`.

## Buiten scope (voor nu)

- Handtekening/watermerk, meerdere zoekopdrachten in één rapport, e-mail/archief, dark-mode-PDF, meertalige templates.
