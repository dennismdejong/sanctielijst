# Ontwerp — EU sanctielijst downloader (PEP-aanpak)

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker (design), ter review voorgelegd (spec)

## Doel

De EU sanctielijst op dezelfde manier verwerken en updaten als de PEP-lijsten: een aparte, manifest-gebaseerde downloader (`scripts/update_eu.py` + `app/eu_ingest.py`) met checksum-verificatie, skip-als-ongewijzigd, wekelijkse scheduling (cron/launchd nu, Docker later). De app wordt read-only: hij downloadt niet meer zelf bij opstart, maar leest de door de downloader neergezette data + manifest.

## Databron

- XML 1.1: `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw` (~25 MB, dagelijks ververst door DG FISMA).
- HTTP-headers van dit endpoint (geverifieerd via HEAD):
  - `Last-Modified` (bijv. `Tue, 28 Jul 2026 09:50:13 GMT`) → versheidssignaal
  - `Content-Length` (bijv. `24816725`) → grootte
  - `Content-Disposition: attachment; filename="20260728-FULL-1_1(xsd).xml"` → datum in bestandsnaam
- Geen externe checksum beschikbaar; de downloader berekent zelf SHA-1 en slaat die op in het manifest.
- XML-root draagt `generationDate` (bijv. `2026-07-28T11:43:32+02:00`), leesbaar via `_read_generation_date`.

## Opslag

```
data/eu/                    # gitignored
  eu_sanctions.xml
  manifest.json
```

- Pad configureerbaar via env `EU_DATA_DIR` (default `data/eu`) of CLI `--root`. In Docker wordt een volume op deze map gemount.

## Architectuur

```
sanctielijst/
  app/
    eu_ingest.py            # downloader-module (spiegelt app/pep_ingest.py)
    ingest.py               # behoudt parse_export + _read_generation_date; download/cache-logica verdwijnt
  scripts/
    update_eu.py            # CLI-wrapper (spiegelt scripts/update_pep.py)
  tests/
    test_eu_ingest.py       # pytest, gemockte HTTP (zelfde stijl als test_pep_ingest.py)
    test_update_eu.py       # CLI-tests (zelfde stijl als test_update_pep.py)
  Dockerfile.downloader     # gegeneraliseerd: entrypoint accepteert script
  docker-compose.yml        # + eu-downloader service, eu-data volume
  .gitignore                # + data/eu/
```

## `app/eu_ingest.py` — functies

- `EU_XML_URL` (const), `XML_FILENAME = "eu_sanctions.xml"`, `MANIFEST_FILENAME = "manifest.json"`, `TIMEOUT = 120`, `DOWNLOAD_PAUSE = 0.5`
- `default_root() -> Path` — `Path(os.environ.get("EU_DATA_DIR", "data/eu"))`
- `fetch_headers(url: str = EU_XML_URL, timeout: int = TIMEOUT) -> dict` — HEAD-request, retourneert `{last_modified, content_length, content_disposition}` (lege string/0 als afwezig)
- `_sha1(path: Path) -> str`
- `download_xml(url: str, dest: Path, timeout: int = TIMEOUT, retries: int = 1) -> None` — stream naar `<dest>.part`, bereken SHA-1, `rename` naar definitief (atomic), ruim `.part` op; retry met `DOWNLOAD_PAUSE`
- `refresh_eu(root_dir: Path, force: bool = False, dry_run: bool = False, logger: Callable | None = None) -> dict`:
  - HEAD → `last_modified`
  - skip als `not force` en manifest `last_modified` gelijk + `status == "ok"` + bestand aanwezig
  - anders download; vul manifest: `last_modified`, `checksum`, `size`, `generation_date`, `entity_count`, `downloaded_at`, `status` (`ok`/`error`)
  - schrijf manifest atomisch (`.tmp` + `os.replace`)
- `load_eu_manifest(root_dir: Path) -> dict` — leest manifest; leeg dict bij afwezig of corrupt

Manifest-formaat:

```json
{
  "updated_at": "2026-07-31T12:00:00Z",
  "last_modified": "Tue, 28 Jul 2026 09:50:13 GMT",
  "checksum": "9f36…",
  "size": 24816725,
  "generation_date": "2026-07-28T11:43:32+02:00",
  "entity_count": 6017,
  "downloaded_at": "2026-07-31T12:00:05Z",
  "status": "ok",
  "stats": {"downloaded": 1, "skipped": 0, "failed": 0}
}
```

## `scripts/update_eu.py` — CLI

- Args: `--root` (default `EU_DATA_DIR` of `data/eu`), `--force`, `--dry-run`, `--interval HOURS`, `--once`, `--log FILE` (geen `--limit`; er is één bestand)
- `run_once`: HEAD-fout → `FATAAL` + exit `1`; download-fout → `status: "error"` in manifest, exit `0`
- `run_loop`: met `--interval HOURS` blijft draaien, graceful shutdown op SIGTERM/SIGINT, slaap in segmenten van max 60s
- Logging naar stdout; optioneel `--log FILE`

## App (read-only)

- `app/main.py`:
  - `default_eu_root() -> Path` — `Path(os.environ.get("EU_DATA_DIR", .../data/eu))`; `EU_ROOT` module-constante (spiegel van `PEP_ROOT`)
  - `create_app`: leest `eu_sanctions.xml` (via `ingest.parse_export`) + manifest (via `eu_ingest.load_eu_manifest`); geen download bij opstart
  - `_status()`: `generated_at`, `data_age_hours` (uit `downloaded_at`), `entity_count`, `source` (`ok`/`missing`/`error`) uit manifest
  - `/api/refresh`: roept `eu_ingest.refresh_eu(...)` inline aan, her-parst de XML, werkt status bij; bij fout → 503
- `app/ingest.py`:
  - Behoudt: `parse_export`, `_read_generation_date`, `NS`, `_to_int`
  - Verwijderd: `download_xml`, `refresh`, `load_index`, `DATASET_URL`, `CACHE_TTL`, `XML_FILENAME`, `META_FILENAME`, `_read_generation_date` verhuist-gebruik naar `eu_ingest`

## Docker

- `Dockerfile.downloader` generaliseren: `ENTRYPOINT ["python"]`, default `CMD ["scripts/update_pep.py", "--interval", "168"]`; compose kan CMD overriden
- `docker-compose.yml`:
  - nieuwe service `eu-downloader`: zelfde image, `command: ["scripts/update_eu.py", "--interval", "168"]`, `EU_DATA_DIR=/data/eu`, volume `eu-data:/data/eu`
  - `app`-service: `EU_DATA_DIR=/data/eu` + volume `eu-data:/data/eu` naast `pep-data`

## Scheduling

- Host (macOS, nu): cron/launchd `0 4 * * 1` voor zowel `update_pep.py` als `update_eu.py` → `.venv/bin/python scripts/update_eu.py --once`
- Docker (later): `eu-downloader` service met `--interval 168`
- Skip-logica (zelfde `Last-Modified`) maakt wekelijkse runs goedkoop; alleen bij wijziging wordt de 25MB gedownload

## Error handling

- HEAD-fout → exit `1`, geen wijzigingen aan bestaande data (spiegel van PEP: index onbereikbaar)
- Download-fout (netwerk): 1 retry; blijft fout → `status: "error"` in manifest + error-veld; exit `0`
- `.part`-bestanden worden altijd opgeruimd; manifest wordt atomisch geschreven
- Corrupt manifest bij lezen → behandeld als leeg (geen crash)

## Teststrategie

- `tests/test_eu_ingest.py` (spiegel van `test_pep_ingest.py`):
  - `fetch_headers` parst HEAD-headers (aanwezig/afwezig)
  - `download_xml`: `.part` + atomic rename, `.part`-opruiming, retry-succes, retry-faal
  - `refresh_eu`: full run (bestand + manifest + entity_count/generation_date/checksum/size), skip-als-ongewijzigd (geen download), `force` herdownloadt, `dry_run` schrijft niets, download-fout → status error, corrupt manifest → leeg
  - `load_eu_manifest`: afwezig, lezen, corrupt
- `tests/test_update_eu.py` (spiegel van `test_update_pep.py`):
  - argparse-defaults (`--root` default `data/eu`), `--once`/`--interval` routing, `run_once` succes/fataal (HEAD-fout → exit 1), `run_loop` graceful stop + gesegmenteerde slaap
- `tests/test_main.py` aanpassen:
  - mocks van `ingest.refresh`/`ingest.load_index` → `eu_ingest.refresh_eu`/`load_eu_manifest` + `ingest.parse_export`
  - `/api/status` blijft dezelfde top-level velden retourneren, nu gevuld uit manifest: `cached_at` = manifest `downloaded_at`, `generated_at` = manifest `generation_date`, `entity_count` = manifest `entity_count` (of lengte van geparste entities), `data_age_hours` berekend uit `downloaded_at`, `source` = manifest `status` (`ok`/`missing`/`error`; `missing` als XML afwezig)
  - `POST /api/refresh` test → mock `eu_ingest.refresh_eu` (geeft manifest terug) en controleert 200 + bijgewerkte status; foutpad → mock gooit → 503
- `tests/test_ingest.py`: download/cache-tests (die `download_xml`, `refresh`, `load_index`, `DATASET_URL` testen) verwijderen; parse-tests behouden

## Buiten scope (voor nu)

- Backfill/historie van EU-snapshots
- Delta-updates voor de EU-XML
- Wijzigen van de wekelijkse cadans
