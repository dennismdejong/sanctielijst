# Changelog

Alle opvallende wijzigingen van Compliance Zoeker per release. Dit bestand wordt bij elke
release bijgewerkt en gepubliceerd op GitHub.

Opmaak gebaseerd op [Keep a Changelog](https://keepachangelog.com/nl/1.1.0/); versies volgen
[Semantic Versioning](https://semver.org/lang/nl/).

## [Niet-gepubliceerd]

## [v1.9.1] - 2026-08-03

### Opgelost
- UI-crash "Cannot read properties of null (reading 'details')" bij zoekresultaten: `euCard` gaat
  defensief om met ontbrekende detail-chips (overgebleven issue na verouderde browser-cache van
  `app.js`). Daarnaast worden `app.js` en `style.css` nu cache-busted geladen (`?v=1.9.1`), zodat
  browsers na een deploy gegarandeerd de nieuwe frontend ophalen.

## [v1.9.0] - 2026-08-03

### Toegevoegd
- **Internationale sanctie-lijsten (VN, OFAC, VK, NL-terroristenlijst):** lokale screening op de
  volledige OpenSanctions `sanctions`-collectie (`data/sanctions`, `scripts/update_sanctions.py`).
  De EU-lijst `eu_fsf` wordt overgeslagen (die hebben we al via de officiële XML). Nieuwe index-bron
  `sanctie` (schema v4) die automatisch meedraait in UI-zoekopdracht, batch-screening en watchlists.
  De Nederlandse nationale terroristenlijst (`nl_terrorism_list`) is hiermee ook gedekt.
- **Risicolanden (FATF / EU high-risk):** `data/risk_countries.json` (FATF zwarte en grijze lijst,
  EU high-risk derde landen) met validatie via `scripts/update_risk_countries.py`. Matches waarvan de
  nationaliteit op de lijst staat krijgen een 'Risicoland'-markering in UI en rapporten; de versie
  staat in `/api/status`.
- **Sanctions-downloader container-service** en week-cron (gespiegeld van de PEP-downloader).

### Gewijzigd
- `scripts/update_sanctions.py` voegt de `sanctions`-collectie toe naast EU/PEP; de downloader-image
  ondersteunt nu ook dit script.
- Zoekindex-schema v4 (bron `sanctie`); index wordt bij de eerste boot automatisch herbouwd.

## [v1.8.0] - 2026-08-01

### Toegevoegd
- **Watchlists (need-to-know, Fase 4):** namen bewaken zonder opslag op de server. De server kent alleen
  anonieme watch-IDs (cookie `watch_key`) en publieke match-data; de bewaakte naam + criteria blijven in
  `localStorage` van de browser. Endpoints `POST`/`GET`/`DELETE /api/watchlists`,
  `POST /api/watchlists/{id}/rescan`, `GET /api/watchlists/hits`. De client polt `/api/status`
  (`data_version`) en meldt nieuwe hits via badge + notificatie.
- **Batch-screening (Fase 2b):** `POST /api/batch` accepteert CSV en Excel (.xlsx) met maximaal 5.000
  namen; elke regel wordt met dezelfde scoring als de UI gescreend (drempel 90, max 20 matches).
  Asynchrone verwerking met `batch_id`, voortgang en per-regel resultaten; overzichtsrapporten als
  PDF en CSV. Per-regel validatiefouten, limieten (5.000 regels, 50 MB) en audit-logging.
- **Streaming zoekindex-build (Fase 5):** `build_index` streamt datasets sequentieel per regel naar
  SQLite i.p.v. alles in RAM te laden; statistieken uit een `meta`-tabel (O(1)); FTS5-vulling via
  één `INSERT ... SELECT`. Piekgeheugen gebonden (~260 MB i.p.v. ~1,3 GB per dataset).
- **Subproces-rebuild (Fase 5):** nieuwe `app/rebuild.py` als `python -m app.rebuild`; in de container
  draait de rebuild in een apart subproces (`PEP_INDEX_SUBPROCESS=1`) zodat een OOM/crash alleen de
  builder treft. Subprocess-timeout (600s) met duidelijke foutmelding.
- **Automatische herindex (Fase 5):** `_status()` triggert een achtergrond-rebuild zodra de data
  nieuwer is dan de index — na een downloader-run is geen restart meer nodig. Future-mtime-guard
  voorkomt rebuild-loops.

### Gewijzigd
- CSV-export genereert nu exact één BOM (`utf-8-sig`) in plaats van een dubbele.

### Opgelost
- Duplicate Position-IDs in OpenSanctions-data crashten de streaming build; `INSERT OR REPLACE`
  herstelt de oude last-wins-semantiek.
- Batch: ongeldig/niet-numeriek `geboortejaar` in CSV/Excel crashte de hele job; dit is nu een
  per-regel validatiefout. Excel-datums worden naar het jaartal genormaliseerd.
- Batch: corrupte of mislabeled `.xlsx`-uploads gaven een HTTP 500; nu een nette 400.
- Batch: lege/opgemaakte xlsx-rijen werden meegeteld (spook-errors, valse 413); nu overgeslagen.
- Batch: OpenSanctions-netwerkcalls werden per regel gemaakt; batch-screening gebruikt nu alleen de
  lokale EU+PEP-index.
- Batch: `pending`-jobs bleven na een herstart hangen; een startup-sweep markeert ze als `error`.

## [v1.7.1] - 2026-08-01

### Gewijzigd
- Auth-status (`auth.required` + `auth.methods`) wordt blootgelegd in `/api/status`; de UI verbergt de
  inlogknop wanneer `AUTH_REQUIRED=0` en toont de juiste login-methode (lokaal/Entra) wanneer vereist.
- `index-fresh` accepteert nu ook v3-databases van v1.8.0-dev (schema-check); roadmap Fase 5/7
  gedocumenteerd.

## [v1.7.0] - 2026-08-01

### Toegevoegd
- **Login (Fase 0):** lokale gebruikers (bcrypt, gesigneerde sessie-cookies) én **Microsoft Entra ID**
  als Identity Provider (OIDC v2.0 + PKCE). Rollen `admin`/`analist`/`viewer`, rol-gating op
  endpoints, `AUTH_REQUIRED`-schakelaar (default uit, backward compatible).
- `scripts/create_user.py` CLI om de eerste admin (of extra gebruikers/Entra-toewijzingen) aan te maken.
- Ingelogde gebruiker wordt vastgelegd in de audit-log (`user`-kolom).

### Opgelost
- Viewer kon exporteren zonder rechten (`viewer` is nu alleen zoeken).
- Entra username-collision gaf een onduidelijke fout; nu een nette 400.
- PDF-export met int-jaartal in geboortedata crashte.

## [v1.6.2] - 2026-07-31

### Toegevoegd
- Exportformaat-selector (PDF/CSV/Excel) in de UI naast de exportknop; audit-docs bijgewerkt.

## [v1.6.1] - 2026-07-31

### Opgelost
- Trust proxy headers zodat de audit-log het echte client-IP vastlegt achter een reverse proxy.

## [v1.6.0] - 2026-07-31

### Toegevoegd
- **Audit-log (Fase 1):** elke zoekopdracht en export wordt gelogd (tijdstip, client-IP, query,
  aantal resultaten, bronnen) in `data/audit.sqlite`; admin-pagina op `/audit` + `GET /api/audit`,
  beveiligd met `AUDIT_ADMIN_TOKEN`.
- **CSV/Excel-export (Fase 2a):** `GET /api/search/export?format=csv|xlsx` naast de bestaande PDF.
- **PEP-posities (Fase 3):** functies uit Occupancy-entiteiten worden geïndexeerd en getoond in
  zoekresultaten, PDF en UI.

### Opgelost
- Audit-log bleef leeg bij een lege `AUDIT_DB`-env; lege-naam 422-verzoeken worden nu ook gelogd;
  constante-tijd token-vergelijking.

## [v1.5.1] - 2026-07-31

### Gewijzigd
- Lege zoekvelden tonen "NVT" in het PDF-rapport i.p.v. lege cellen.

## [v1.5.0] - 2026-07-31

### Toegevoegd
- **PDF-screeningsrapport:** `GET /api/search/export` (PDF) met zoekopdracht, auteur, dataversies,
  resultaten met scores/bronnen/details en disclaimer (reportlab). Exportknop met auteur-veld in de UI.

## [v1.4.0] - 2026-07-31

### Gewijzigd
- Zoekindex verhuisd naar **SQLite+FTS5** (`data/search.sqlite`) i.p.v. de in-memory PEP-index:
  atomic rebuild, freshness-check, statistieken, EU-only tijdens rebuild, corrupte-db-fallback.
- Indexstatus + statistieken getoond in de UI; `POST /api/refresh` herbouwt de index.

### Opgelost
- EU-score-parity en FTS OR-gate voor token-zoeken; tz-naive `downloaded_at` in data-leeftijd.

## [v1.3.0] - 2026-07-31

### Toegevoegd
- **EU-sanctielijst-downloader** (CLI + `EU_DATA_DIR`-manifest met `Last-Modified`-skip);
  read-only EU-data-consumptie.
- `APP_VERSION` in `/api/status` + versie in de footer.

## [v1.2.0] - 2026-07-31

### Toegevoegd
- PEP-index wordt op de achtergrond geladen zodat de app direct start.
- EU-manifest-downloader met `Last-Modified`-skip (ontwerp + plan).

## [v1.1.2] - 2026-07-31

### Opgelost
- `datasets.json` wordt alleen herschreven bij wijziging (houdt de index-cache geldig).

## [v1.1.1] - 2026-07-31

### Opgelost
- `PEP_ROOT` respecteert `PEP_DATA_DIR` voor container-deployments; test-importfix.

## [v1.1.0] - 2026-07-31

### Gewijzigd
- CI bouwt nu multi-platform images (amd64 + arm64) via QEMU.

## [v1.0.0] - 2026-07-31

### Toegevoegd
- **PEP-zoeken (OpenSanctions):** downloader-pipeline met SHA-1-verificatie + retry en manifest,
  fuzzy matching met gewogen scoring, token-containment (100 bij exacte match), drempel 90.
- PEP-dataset-metadata getoond met bronbadges in de UI; "Compliance Zoeker"-branding.
- **Docker-packaging** (GHCR, weekly PEP-update), CLI met one-shot- en interval-modus.

### Opgelost
- Interruptible sleep, unbuffered logs, atomic manifest-schrijf; niet-dict-JSON-veiligheid;
  accent-gevoelige PEP-tokenisatie.

[v1.0.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.0.0
[v1.1.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.1.0
[v1.1.1]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.1.1
[v1.1.2]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.1.2
[v1.2.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.2.0
[v1.3.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.3.0
[v1.4.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.4.0
[v1.5.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.5.0
[v1.5.1]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.5.1
[v1.6.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.6.0
[v1.6.1]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.6.1
[v1.6.2]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.6.2
[v1.7.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.7.0
[v1.7.1]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.7.1
[v1.8.0]: https://github.com/dennismdejong/sanctielijst/releases/tag/v1.8.0
