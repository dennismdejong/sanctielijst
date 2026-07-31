# Compliance Zoeker

Web-app om te zoeken in de EU sanctielijsten (personen en bedrijven) én de OpenSanctions PEP-data (politiek prominente personen), met fuzzy matching en per-kenmerk uitleg waarom een resultaat matcht. Optioneel ook aanvullende wereldwijde sanctie-screening via de OpenSanctions `/match`-API (OFAC, VN, VK, etc.).

## Installatie

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data

De app leest de EU sanctielijst (XML 1.1, ~25 MB) uit `data/eu/` (env `EU_DATA_DIR`). De download gebeurt door de downloader (`scripts/update_eu.py`, zie "Wekelijks bijwerken EU-data"); de app downloadt niet meer zelf. `POST /api/refresh` downloadt de EU-lijst (via dezelfde manifest-check) én herbouwt daarna de zoekindex op de achtergrond.

Daarnaast zoekt de app in `data/search.sqlite`, een SQLite+FTS5-zoekindex over de EU- én de OpenSanctions PEP-data (zie "Wekelijks bijwerken PEP-data"). Het pad is te overschrijven met de `SEARCH_DB`-env (of `SEARCH_DATA_DIR`); default is `data/search.sqlite`. In de container hoort dit bestand op een persistent volume te staan, zodat de index niet bij elke herstart opnieuw wordt opgebouwd. Zet `PEP_INDEX_ENABLED=0` in `.env` om PEP-zoeken uit te schakelen (default: aan zolang `data/pep/` bestaat); de EU-lijst wordt altijd geïndexeerd. `POST /api/refresh` haalt de PEP-data zelf niet op — draai daarvoor `scripts/update_pep.py --once` (refresh herbouwt wél de zoekindex met de al gedownloade PEP-data).

## Starten

```bash
uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000.

## PDF-export

Vanuit het zoekscherm kun je resultaten exporteren als PDF-rapport (`GET /api/search/export`). Het rapport bevat de zoekopdracht (en optioneel de auteur), de uitvoeringsdatum/-tijd, de gebruikte dataversies (EU-lijstgeneratie en PEP-update), de resultaten met scores, bronnen en match-details, en een disclaimer.

Het rapport wordt gegenereerd met **reportlab** — een nieuwe dependency in `requirements.txt` (pure Python, werkt zonder extra systeempakketten, ook in de container-image).

## OpenSanctions (optioneel)

Naast de lokale EU-lijst en de gedownloade PEP-data kun je met een API-key extra screening doen via de OpenSanctions `/match`-API. Die draait op sanctie-topics (OFAC, VN, VK, etc.) — PEP-resultaten komen uit de lokaal gedownloade data, niet uit deze API. De key is gratis voor niet-commercieel gebruik via https://www.opensanctions.org/account/ (voor commercieel gebruik geldt een licentie):

```bash
cp .env.example .env
# zet je key in .env
```

De app leest `OPENSANCTIONS_API_KEY` uit de omgeving of `.env`. Zonder key draait de app volledig op de lokale EU- en PEP-data.

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

Wanneer de EU- of PEP-data verandert, herbouwt de app `data/search.sqlite` automatisch op de achtergrond — een restart is niet nodig.

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
