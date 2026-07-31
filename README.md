# Compliance Zoeker

Web-app om te zoeken in de EU sanctielijsten (personen en bedrijven) én de OpenSanctions PEP-data (politiek prominente personen), met fuzzy matching en per-kenmerk uitleg waarom een resultaat matcht. Optioneel ook wereldwijde screening via de OpenSanctions `/match`-API.

## Installatie

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data

De app leest de EU sanctielijst (XML 1.1, ~25 MB) uit `data/eu/` (env `EU_DATA_DIR`). De download gebeurt door de downloader (`scripts/update_eu.py`, zie "Wekelijks bijwerken EU-data"); de app downloadt niet meer zelf. `POST /api/refresh` voert dezelfde manifest-refresh direct uit.

Daarnaast zoekt de app in de PEP-index in `data/pep/` (OpenSanctions PEP-bronnen, zie "Wekelijks bijwerken PEP-data"). De index wordt bij het opstarten automatisch herbouwd wanneer `data/pep/` is gewijzigd en wordt gecacht als `data/pep/index.pkl`. `POST /api/refresh` ververst alleen de EU-lijst, niet de PEP-index; verfris de PEP-index door de downloader uit te voeren: `.venv/bin/python scripts/update_pep.py --once`. Zet `PEP_INDEX_ENABLED=0` in `.env` om PEP-zoeken uit te schakelen (default: aan zolang `data/pep/` bestaat).

## Starten

```bash
uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000.

## OpenSanctions (optioneel)

Vul een gratis API-key in (https://www.opensanctions.org/account/, vrij voor niet-commercieel gebruik):

```bash
cp .env.example .env
# zet je key in .env
```

De app leest `OPENSANCTIONS_API_KEY` uit de omgeving of `.env`.

## Wekelijks bijwerken EU-data (data.europa.eu)

Download de EU sanctielijst (XML 1.1, ~25 MB) naar `data/eu/`:

```bash
.venv/bin/python scripts/update_eu.py --once
```

- Manifest: `data/eu/manifest.json` (`Last-Modified`, checksum, grootte, generatiedatum, aantal records, status).
- Via een HEAD-verzoek wordt gecontroleerd of de lijst is gewijzigd; ongewijzigd = overgeslagen, alleen bij wijziging wordt de 25 MB gedownload.
- Kies een pad met `--root` of env `EU_DATA_DIR`.

**Cron (macOS/Linux), wekelijks maandag 04:00:**

```cron
0 4 * * 1 cd /pad/naar/sanctielijst && mkdir -p data/eu && .venv/bin/python scripts/update_eu.py --once >> data/eu/update.log 2>&1
```

**Container:** de service `eu-downloader` in `docker-compose.yml` draait hetzelfde script in loop-modus (`--interval 168`) met data op het `eu-data`-volume.

## Tests

```bash
python -m pytest -v
```

## Wekelijks bijwerken PEP-data (OpenSanctions)

Download alle individuele PEP-bronnen (~0.8 GB, `entities.ftm.json` per bron) naar `data/pep/`:

```bash
.venv/bin/python scripts/update_pep.py --once
```

- Manifest: `data/pep/manifest.json` (downloaddatum, versie, checksums, status per bron).
- Ongewijzigde bronnen worden overgeslagen; alleen gewijzigde worden herdownload.
- Kies een pad met `--root` of env `PEP_DATA_DIR`.

**Cron (macOS/Linux), wekelijks maandag 04:00:**

```cron
0 4 * * 1 cd /pad/naar/sanctielijst && .venv/bin/python scripts/update_pep.py --once >> data/pep/update.log 2>&1
```

**Container (podman-compose):** de service `pep-downloader` in `docker-compose.yml` draait hetzelfde script in loop-modus (`--interval 168`) met data op een volume. Zonder Docker kun je dit draaien met podman/podman-compose:

```bash
podman-compose up -d --build
podman logs -f pep-downloader
```

## Container images (GHCR)

Bij elke git-tag (`v*`) bouwt GitHub Actions twee images en pusht die naar GitHub Container Registry:

- `ghcr.io/dennismdejong/sanctielijst:<tag>` en `:latest` — de web-app (uvicorn, Python 3.14)
- `ghcr.io/dennismdejong/sanctielijst-downloader:<tag>` en `:latest` — de downloader (zowel `scripts/update_pep.py` als `scripts/update_eu.py`, kies via het commando-argument)

```bash
# lokaal draaien
podman run --rm -p 8000:8000 ghcr.io/dennismdejong/sanctielijst:latest
podman run --rm ghcr.io/dennismdejong/sanctielijst-downloader:latest scripts/update_pep.py --once
podman run --rm ghcr.io/dennismdejong/sanctielijst-downloader:latest scripts/update_eu.py --once
```

`docker-compose.yml` bevat de services `app`, `pep-downloader` en `eu-downloader` met de volumes `pep-data` en `eu-data`.
