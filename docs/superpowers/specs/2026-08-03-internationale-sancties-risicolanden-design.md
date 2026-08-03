# Design: Internationale sanctie-lijsten (VN/OFAC/VK/NL) + FATF-risicolanden

Datum: 2026-08-03
Status: goedgekeurd (ontwerp)

## Aanleiding

De Compliance Zoeker screent nu alleen de EU-sanctielijst (FSF-XML), de OpenSanctions
PEP-data en (optioneel, API) een beperkte OpenSanctions `/match`-bevraging. BNG als
Nederlandse bank is echter gehouden aan de Sanctiewet 1977, de Wwft en — expliciet
genoemd door BNG — de **Amerikaanse en Britse sanctiewetgeving**. De verplichte lijsten
voor Nederlandse financiële instellingen zijn: de EU-bevriezingslijst, de **Nederlandse
nationale terroristenlijst** en de **VN-geconsolideerde sanctielijst**; daarbovenop gelden
OFAC (VS) en VK-sancties.

Gaten in de huidige dekking die dit ontwerp dicht:

1. **Nederlandse nationale terroristenlijst** (`nl_terrorism_list`) — verplicht, maar
   nergens gedekt (niet in de `peps`-collectie en niet door de API-topics-filter).
2. **VN-geconsolideerde sanctielijst** (`un_sc_sanctions`) — alleen indirect.
3. **OFAC SDN** (`us_ofac_sdn`) — alleen via optionele API, alleen `Person`.
4. **VK-sancties** (`gb_fcdo_sanctions`) — alleen via optionele API, alleen `Person`.
5. **FATF / risicolanden** — niet aanwezig (hoort bij verscherpt cliëntonderzoek).

## Genomen beslissingen

1. **Aanpak vier lijsten:** download en indexeer de volledige OpenSanctions
   **`sanctions`-collectie** lokaal (consistent met de bestaande PEP-aanpak), met
   uitzondering van `eu_fsf` (al aanwezig via de officiële EU-XML). Dit dekt VN, OFAC,
   VK en de NL-terroristenlijst in één mechanisme en is toekomstbestendig (nieuwe
   regimes automatisch meegenomen).
2. **FATF/risicolanden:** een handmatig versiebeheerde landenlijst-JSON
   (`data/risk_countries.json`) met FATF-zwarte lijst, FATF-grijze lijst en EU
   high-risk derde landen. Geen scraping (FATF heeft geen stabiel machineleesbaar
   formaat).
3. **Bereik:** de nieuwe sanctie-data draait overal mee — UI, batch en watchlist —
   omdat het in dezelfde SQLite-index zit. Dit lost de bestaande gap op dat batch en
   watchlist de internationale lijsten misten.

## Architectuur-overzicht

```
scripts/update_sanctions.py        scripts/update_risk_countries.py
        │                                      │ (validatie + timestamp)
        ▼                                      ▼
app/sanctions_ingest.py        data/risk_countries.json
        │ (collection 'sanctions', excl. eu_fsf)      │
        ▼                                             ▼
data/sanctions/{ds}/entities.ftm.json         app/risk_countries.py
        │                                             │
        └──────────► app/search_index.py ◄────────────┘
                      (bron 'sanctie', FTS5)
                             │
                        app/main.py
                             │  run_search / _serialize_sanctions_result / watchlist
                             ▼
                   static/app.js · app/export.py
```

## Sectie 1: Data — sanctions-collectie downloaden

### `app/pep_ingest.py` refactoren naar generiek

De bestaande download-logica (index-ophalen, artifact-download met checksum/retry,
manifest, `datasets.json`) wordt gegeneraliseerd zodat de `sanctions`-collectie
dezelfde code hergebruikt:

- `list_collection_datasets(index, collection, exclude=()) -> list[dict]` — filtert
  op `collection` in `collections`, `type == "source"`, aanwezigheid van de resource
  `entities.ftm.json`, en sluit datasetnamen in `exclude` uit.
- `refresh_collection(root_dir, collection, *, index=None, force=False, dry_run=False,
  limit=None, logger=None, exclude=()) -> dict` — de huidige `refresh_pep`-logica met
  `collection` en `exclude` als parameters; schrijft `manifest.json` +
  `datasets.json` per root.
- `list_pep_datasets(index)` en `refresh_pep(...)` blijven bestaan als **dunne
  wrappers** met dezelfde signatures, zodat `scripts/update_pep.py` en
  `tests/test_pep_ingest.py` ongewijzigd groen blijven.

### `app/sanctions_ingest.py` (nieuw)

- `default_root() -> Path` — `Path(os.environ.get("SANCTIONS_DATA_DIR", "data/sanctions"))`.
- `SANCTIONS_COLLECTION = "sanctions"`, `EXCLUDE_DATASETS = ("eu_fsf",)`.
- `list_sanctions_datasets(index)` / `refresh_sanctions(root_dir, ...)` — wrappers om
  de generieke functies met de `sanctions`-collectie.
- Eigen `manifest.json` en `datasets.json` onder `data/sanctions/`.

Motivatie uitsluiting `eu_fsf`: de officiële EU-XML levert rijkere data (strong-aliases,
programma's, verordeningen); opname van `eu_fsf` zou tot duplicaten in de index leiden.

### `scripts/update_sanctions.py` (nieuw)

Spiegelt `scripts/update_pep.py`:
- `--once` / `--interval <uren>` (loop-modus), `--root`, `--force`, `--dry-run`.
- Roep `refresh_sanctions` aan, schrijf naar `data/sanctions/`.
- Cron (macOS/Linux) wekelijks maandag 04:00, parallel aan EU/PEP.

### Docker

- `docker-compose.yml`: nieuwe service `sanctions-downloader` (dezelfde image als
  `pep-downloader`/`eu-downloader`) + volume `sanctions-data`; de web-app-service
  mount hetzelfde volume en zet `SANCTIONS_DATA_DIR`.
- `Dockerfile.downloader` hoeft niet aangepast (draait al scripts uit de repo).

## Sectie 2: Index & zoekintegratie (bron `sanctie`)

### `app/search_index.py`

- **`SCHEMA_VERSION` 3 → 4** (eenmalige automatische rebuild bij deploy; tabelstructuur
  verandert niet).
- `_stream_pep()` generaliseren naar `_stream_ftm(db, root, source)` — indexeert FTM-
  regels met `target: true` en schema `Person`/`Company` (zelfde criteria als nu) met
  de gegeven bronwaarde. `_stream_pep` en nieuwe `_stream_sanctions` zijn wrappers.
- Nieuwe bronwaarde `'sanctie'`. Datasets (`us_ofac_sdn`, `nl_terrorism_list`, …)
  worden opgeslagen in de bestaande `datasets`-kolom (JSON-array); geen kolomwijziging.
- Signatures uitbreiden met `sanctions_root`:
  - `build_index(db_path, eu_entities, pep_root, sanctions_root, *, newest_input_mtime=None)`
  - `rebuild_index(db_path, eu_xml, pep_root, sanctions_root)`
  - `_newest_input_mtime(eu_xml, pep_root, sanctions_root)` — mtime van sanctie-data
    telt mee (aangrijpingspunt voor `data_version` en watchlist-her-screening)
  - `index_fresh(...)` / `ensure_index(...)` idem.

### `app/rebuild.py` en `app/main.py`

- `rebuild.py`: extra argument `--sanctions-root` (verplicht), doorgeven aan
  `rebuild_index`.
- `main.py` `_run_rebuild*()`: nieuwe parameter en subproces-argument.
- `create_app(...)` accepteert `sanctions_root` (default via env
  `SANCTIONS_DATA_DIR`); de enabled-check wordt `eu || pep || sanctions` (bestaande
  `_pep_enabled`-logica wordt gespiegeld met een `_sanctions_enabled`).

### Serialisatie & status (`app/main.py`)

- `_serialize_sanctions_result(result, datasets_meta)` — zelfde vorm als
  `_serialize_pep_result`, met `source: "sanctie"`, per-hit dataset-chips uit
  `datasets.json` (titel + land + opensanctions.org-link), score + details,
  risicoland-flag (zie sectie 3).
- `_to_watchlist_match()`: branch voor `source == "sanctie"` — `match_id = entity.id`,
  `datasets = entity.datasets` (spiegelt de `pep`-branch).
- `/api/status`: `index.sanctions_count`; `source_count` blijft het totaal aantal
  gebundelde bronnen; `data_version` gebaseerd op alle drie roots.

### Bereik

De sanctie-data zit in de index → draait automatisch mee in **UI**, **batch**
(`_batch_search_fn`) en **watchlist** (`_watchlist_search_fn`), zonder signature-
wijzigingen in `batch.py`/`watchlist.py`. De optionele OpenSanctions-`/match`-API
blijft ongewijzigd als extra laag.

## Sectie 3: FATF / risicolanden

### `data/risk_countries.json` (nieuw databestand)

Handmatig bijgehouden, versiebeheerd:
```json
{
  "version": "2026-08-03",
  "updated_at": "2026-08-03T00:00:00+00:00",
  "fatf_blacklist": ["KP", "IR", ...],
  "fatf_greylist": ["MM", "PS", ...],
  "eu_high_risk": ["CD", "IR", ...]
}
```
Pad overschrijfbaar met env `RISK_COUNTRIES`; default `data/risk_countries.json`.

### `app/risk_countries.py` (nieuw)

- `load_risk_countries() -> dict` — cached loader; bij ontbreken/ongeldig → lege
  lijsten + versie onbekend.
- `risk_flags(country_codes) -> list[dict]` — voor een reeks ISO2-codes per code terug
  op welke lijsten hij voorkomt: `[{"code": "IR", "lists": ["fatf_blacklist"]}]`.
- `validate(data) -> list[str]` — foutmeldingen (ISO2-format, geen duplicaten),
  gebruikt door het script.

### Integratie

- Serializers (`eu`, `pep`, `sanctie`): als de `citizenships`/landcodes van een match
  de risicolijst raken, krijgt het resultaat `risk_countries: [...]`.
- UI toont een waarschuwings-badge ("Risicoland: IR · FATF zwarte lijst").
- `/api/status` toont versie + aantallen per lijst.
- Export: flag mee in PDF-rapport en als regel in CSV/XLSX.

### `scripts/update_risk_countries.py` (nieuw)

- Valideert de JSON (`ISO2-format`, geen duplicaten) en schrijft een verse
  `updated_at`-timestamp. Geen scraping.

## Sectie 4: UI + export

### `static/app.js`

- Nieuwe `sanctCard(item)` — spiegelt `pepCard`: naam, badge **"Internationale
  sancties"**, totaalscore + detail-chips, geboorte/nationaliteit, dataset-chips
  (per bron, link naar opensanctions.org), risicoland-badge.
- `sourceBadge()` en `renderResults()` herkennen `source === "sanctie"`.
- Statusregel: toont ook het aantal sanctie-records.

### `app/export.py`

- Bronlabel `'sanctie'` → `"Sancties (int.)"` in `_result_paragraphs` (PDF) en
  `_EXPORT_BRONLABELS` (CSV/XLSX).
- PDF: sanctie-branch die datasets, details en risicoland-flags toont (spiegelt de
  PEP-branch); dataversie-sectie toont ook de sanctie-update en risicolijst-versie.
- Export-payload: `sanctions_meta` en `risk_meta` voor de audit-keten.

## Sectie 5: Tests, docs & ops

### Tests

- `tests/test_sanctions_ingest.py` (nieuw): collectie-listing met `eu_fsf`-uitsluiting,
  manifest/skip/checksum, dry-run.
- `tests/test_pep_ingest.py`: ongewijzigd groen (wrappers behouden gedrag).
- `tests/test_search_index.py`: index met `sanctions_root`, bron `sanctie` in
  resultaten, schema-version 4.
- `tests/test_main.py`: `_serialize_sanctions_result`, watchlist-branch, `/api/status`-
  counts, zoekresultaat met sanctie-hit.
- `tests/test_export.py`: sanctie-bron in PDF/CSV/XLSX + risicoland-flag.
- `tests/test_risk_countries.py` (nieuw): loader, `risk_flags`, validatie.
- Fixtures: kleine `entities.ftm.json` (o.a. `us_ofac_sdn`, `nl_terrorism_list`),
  `risk_countries.json`, aangepaste index-`datasets.json`.

### Docs & ops

- `README.md`: secties "Internationale sancties (VN/OFAC/VK/NL)" en "Risicolanden
  (FATF)" — data, cron, container.
- `.env.example`: `SANCTIONS_DATA_DIR`, `RISK_COUNTRIES`.
- `docker-compose.yml`: `sanctions-downloader` + `sanctions-data`-volume.

### Migratie

Geen handmatige stap: schema-bump naar v4 laat de app de index bij de eerste deploy
automatisch herbouwen (met sanctie-data). Risicoland-flag is puur een lees-operatie.

## Niet-scope

- Vessel/andere schema's in de sanctie-collectie (alleen Person/Company worden
  geïndexeerd, consistent met de huidige matching).
- Transactiescreening en meldplicht-werkstroom (DNB/FIU) — aparte werkstroom.
- Adverse media / negatieve publiciteit.
- Wijziging aan de optionele OpenSanctions-`/match`-API.
