# Ontwerp — OpenSanctions PEP-lijsten wekelijks downloaden

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker

## Doel

Alle individuele PEP-bronnen van OpenSanctions (`/pep/`) downloaden via de bulk-download-artifacts, opslaan als ongestructureerde bestanden, wekelijks bijgewerkt via script (cron/launchd nu, Docker later). De downloader volgt de bestaande patronen van de sanctielijst-app en is Docker-ready.

## Databron

- Hoofdindex: `https://data.opensanctions.org/datasets/latest/index.json`
- Filter: datasets waar `collections` de waarde `"peps"` bevat, `type == "source"`, en een `entities.ftm.json`-resource aanwezig is.
- Resultaat: **189 bronnen** (o.a. `al_kuvendi`, `br_pep`, `fr_maires`, `eu_meps`), ~**0.84 GB** totaal voor `entities.ftm.json` alleen.
- Per bron levert de index: `version`, resource-`url`, SHA-1-`checksum`, `size`. Stabiele `latest`-URL, dagelijks bijgewerkt.

## Opslag (ongestructureerd)

```
data/pep/                      # gitignored
  <dataset>/entities.ftm.json  # bv. data/pep/al_kuvendi/entities.ftm.json
  manifest.json                # metadata van laatste run
```

- Pad configureerbaar via env `PEP_DATA_DIR` (default `data/pep`) of CLI `--root`. In Docker wordt een volume op deze map gemount.
- Formaat per bron: alleen `entities.ftm.json` (FollowTheMoney-entities, meest complete data).

## Architectuur

```
sanctielijst/
  app/
    pep_ingest.py          # downloader-module (herbruikbaar, testbaar; naast ingest.py)
  scripts/
    update_pep.py          # dunne CLI-wrapper
  tests/
    test_pep_ingest.py     # pytest, gemockte HTTP (zelfde stijl als test_ingest.py)
  Dockerfile
  docker-compose.yml
  .dockerignore
```

`app/pep_ingest.py` — functies (zelfde stijl als `app/ingest.py`):

- `INDEX_URL` (const), `RESOURCE_NAME = "entities.ftm.json"`, `TIMEOUT = 120`, `DOWNLOAD_PAUSE = 0.5`, `MANIFEST_FILENAME = "manifest.json"`
- `fetch_index(url: str = INDEX_URL, timeout: int = TIMEOUT) -> dict` — downloadt en parsed de hoofdindex
- `list_pep_datasets(index: dict) -> list[dict]` — filtert PEP-bronnen met `entities.ftm.json`-resource
- `download_artifact(url: str, dest: Path, checksum: str, timeout: int, retries: int = 1) -> None` — download naar `<dest>.part`, verifieert SHA-1, `rename` naar definitief (atomic), ruimt `.part` op
- `refresh_pep(root_dir: Path, index: dict | None = None, force: bool = False, dry_run: bool = False) -> dict` — doorloopt bronnen, skipt ongewijzigde, schrijft manifest, retourneert manifest
- `load_pep_manifest(root_dir: Path) -> dict` — leest manifest (leeg dict als afwezig)

Manifest-formaat:

```json
{
  "updated_at": "2026-07-31T12:00:00Z",
  "sources": {
    "al_kuvendi": {
      "version": "20260729142001-bbc",
      "checksum": "872f…",
      "size": 132743,
      "downloaded_at": "2026-07-31T12:00:05Z",
      "status": "ok"
    }
  },
  "stats": {"total": 189, "downloaded": 0, "skipped": 187, "failed": 2, "bytes": 0}
}
```

`scripts/update_pep.py` — CLI:

- Args: `--root` (default `PEP_DATA_DIR` of `data/pep`), `--force`, `--dry-run`, `--limit N`, `--interval HOURS`, `--log FILE`
- Standaard eenmalig draaien (`--once`); met `--interval HOURS` blijft het draaien en update elke X uur, met graceful shutdown op SIGTERM (Docker-servicemodus)
- Logging naar stdout (Docker capturet dit); optioneel `--log FILE` voor op de host
- Exit-code: `0` bij succes (ook bij per-bron-fouten), `1` bij fatale fout (index onbereikbaar)

## Scheduling

- **Host (macOS, nu):** cron `0 4 * * 1` of launchd → `.venv/bin/python scripts/update_pep.py --once`
- **Docker (later):** compose-service `pep-downloader` met `--interval 168`, volume `pep-data:/data/pep`. Zelfde script, geen code-wijzigingen nodig.
- Skip-logica (zelfde `version` + checksum) maakt wekelijkse runs goedkoop; alleen gewijzigde bronnen worden herdownload.

## Docker

- `Dockerfile`: `python:3.11-slim`, installeert `requirements.txt`, entrypoint = `update_pep.py`
- `docker-compose.yml`: service `pep-downloader`, env `PEP_DATA_DIR=/data/pep`, volume `pep-data`, restart-beleid
- `.dockerignore`: `.venv`, `data/`, `__pycache__`, `.git`, etc.
- Geen lokale bestandspaden hardcoded; alles via env/CLI zodat host- en container-gebruik identiek zijn.

## Error handling

- Index download/parse-fout → exit `1`, geen wijzigingen aan bestaande data
- Per-bron-fout (netwerk/checksum): 1 retry; blijft fout → `status: "error"` in manifest, ga door met volgende bron
- Checksum-mismatch → herdownload; `.part`-bestanden worden altijd opgeruimd
- Timeout 120s per download; korte pauze tussen bronnen om de server niet te overbelasten

## Teststrategie

- pytest met gemockte HTTP (`monkeypatch` op `requests.get`, zelfde stijl als `tests/test_ingest.py`)
- Cases: filteren van PEP-bronnen (incl. niet-PEP, externe, bronnen zonder resource), download + checksum-verificatie, temp+rename, skip-als-ongewijzigd (zelfde versie), manifest schrijven/lezen, foutpaden (index-fout, checksum-mismatch, netwerkfout, retry).

## Buiten scope (voor nu)

- Delta-updates (`delta.json`) en snapshot-versiehistorie bewaren
- De geconsolideerde `peps`-collectie (bestaat al als één download) en het `default`-dataset
- Zoekfunctionaliteit op PEP-data in de app (later; `entities.ftm.json` is hetzelfde FollowTheMoney-formaat)
- Deploy naar een echte containerregisery/omgeving

## Integratie-notitie

De andere agent bouwt parallel de sanctielijst-app (`app/main.py`, `app/ingest.py`, etc.). Deze downloader raakt geen bestaande bestanden aan: nieuw zijn `app/pep_ingest.py`, `scripts/update_pep.py`, `tests/test_pep_ingest.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, plus een regel in `.gitignore` (`data/pep/`). Indien de andere agent tegelijk `.gitignore` wijzigt, wordt de regel handmatig gemerged.
