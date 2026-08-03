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

De index-rebuild streamt de datasets **sequentieel per dataset** (EU eerst, daarna PEP): er wordt nooit een volledige dataset in het geheugen geladen, waardoor het piekgeheugen laag en gebonden blijft. In de container draait de rebuild in een **apart subproces** (`PEP_INDEX_SUBPROCESS=1`), zodat een OOM of crash alleen de builder treft en niet de app. Zodra `data/pep/` of `data/eu/` verandert, herbouwt de app de index **automatisch** op de achtergrond — een restart is niet nodig.

## Starten

```bash
uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000.

## Export (PDF / CSV / Excel)

Vanuit het zoekscherm kun je resultaten exporteren, in **PDF**, **CSV** of **Excel** (`.xlsx`) — kies het formaat naast de exportknop. Endpoint: `GET /api/search/export?format=pdf|csv|xlsx` (default `pdf`). De rapporten bevatten de zoekopdracht (en optioneel de auteur), de uitvoeringsdatum/-tijd, de gebruikte dataversies (EU-lijstgeneratie en PEP-update), en de resultaten met scores, bronnen en match-details.

Het PDF-rapport wordt gegenereerd met **reportlab**; CSV/XLSX met **openpyxl** — beide in `requirements.txt` (pure Python, werken zonder extra systeempakketten, ook in de container-image).

## Audit-log

Elke zoekopdracht en export wordt gelogd (tijdstip, client-IP, query, aantal resultaten, bronnen) in `data/audit.sqlite` (env `AUDIT_DB`, gitignored). Beheer kan de log bekijken op `/audit`, beveiligd met `AUDIT_ADMIN_TOKEN` (Bearer-header; het endpoint is 404 zolang die env niet gezet is). Achter een reverse proxy: uvicorn met `--proxy-headers` zodat het echte client-IP wordt vastgelegd.

## Batch-screening

Upload een **CSV**- of **Excel** (`.xlsx`)-bestand met honderden namen in één keer: elke regel wordt met dezelfde scoring als de UI gescreend (drempel 90, max 20 matches). `POST /api/batch` (multipart `file`) retourneert een `batch_id`; de screening draait asynchroon op de achtergrond. `GET /api/batch/{id}` toont de voortgang en per regel de matches; `GET /api/batch/{id}/report.pdf` en `.../report.csv` leveren het overzichtsrapport.

- Kolommen: `naam` (verplicht), optioneel `geboortejaar`, `nationaliteit`, `geboorteplaats`, `type` (`person`/`enterprise`). De header-rij is hoofdlettergevoelig niet (`Naam`/`naam`/`NAME`).
- Max **5.000** regels per batch (413 bij overschrijding, uploads > 50 MB worden geweigerd); regels zonder naam zijn per-regel validatiefouten, geen afwijzing van de hele batch.
- Batch-aanmaak en rapport-downloads worden vastgelegd in de audit-log. Jobs staan in `data/batch.sqlite` (env `BATCH_DB`).

## Watchlists

Bewaken van namen (need-to-know): je bewaart een naam in de browser en de app meldt zodra een data-update nieuwe matches oplevert — **zonder dat de bewaakte naam ooit op de server wordt opgeslagen**. De server kent alleen een anonieme watch-ID (via de `watch_key`-cookie) en de publieke match-data.

- Knop **"Bewaak deze naam"** naast de zoekknop; de naam + criteria blijven in `localStorage` van de browser.
- De client polt `/api/status` (`data_version`) en her-screent bij elke data-wijziging; nieuwe hits verschijnen als badge + melding.
- Endpoints: `POST`/`GET`/`DELETE /api/watchlists`, `POST /api/watchlists/{id}/rescan`, `GET /api/watchlists/hits`.

## OpenSanctions (optioneel)

Naast de lokale EU-lijst en de gedownloade PEP-data kun je met een API-key extra screening doen via de OpenSanctions `/match`-API. Die draait op sanctie-topics (OFAC, VN, VK, etc.) — PEP-resultaten komen uit de lokaal gedownloade data, niet uit deze API. De key is gratis voor niet-commercieel gebruik via https://www.opensanctions.org/account/ (voor commercieel gebruik geldt een licentie):

```bash
cp .env.example .env
# zet je key in .env
```

De app leest `OPENSANCTIONS_API_KEY` uit de omgeving of `.env`. Zonder key draait de app volledig op de lokale EU- en PEP-data.

## Internationale sancties (VN, OFAC, VK, NL-terroristenlijst)

Naast de EU-lijst en de PEP-data download de app de volledige OpenSanctions
**`sanctions`-collectie** (OFAC, VN, VK, Nederlandse nationale terroristenlijst en
alle overige sanctieregimes; de EU-lijst `eu_fsf` wordt overgeslagen omdat we die
al via de officiële XML hebben) naar `data/sanctions/`:

```bash
.venv/bin/python scripts/update_sanctions.py --once
```

Deze data draait mee in de UI-zoekopdracht, batch-screening en watchlists. Zet
`SANCTIONS_INDEX_ENABLED=0` om uit te schakelen. In de container verzorgt de service
`sanctions-downloader` (volume `sanctions-data`) de wekelijkse update.

## Risicolanden (FATF / EU high-risk)

`data/risk_countries.json` (overschrijfbaar met `RISK_COUNTRIES`) bevat de FATF
zwarte en grijze lijst en de EU high-risk derde landen (ISO2-codes). De lijst is
handmatig te onderhouden; valideer en voorzie van een timestamp met:

```bash
.venv/bin/python scripts/update_risk_countries.py
```

In de container is dit startbestand in de image gebakken op
`/app/risk_countries.json` (zo ingesteld via `RISK_COUNTRIES`); operators kunnen
het overschrijven door een eigen bestand op die locatie te mounten.

Een match waarvan de nationaliteit op de lijst staat, krijgt in de UI en de
rapporten een 'Risicoland'-markering. De versie staat in `/api/status`.

## Login

De app kent eigen gebruikers (gebruikersnaam/wachtwoord) en **Microsoft Entra ID** als Identity Provider. Na succesvolle authenticatie geeft de app een eigen, ondertekende sessie-cookie uit (`session`, 12 uur geldig, HttpOnly, SameSite=lax). Rollen: `admin` (alles, inclusief audit-log en gebruikersbeheer), `analist` (zoeken + exporteren + batch) en `viewer` (alleen zoeken). Een ingelogde gebruiker wordt vastgelegd in de audit-log (`user`-kolom); anonieme zoekopdrachten worden als `null` gelogd.

### Eerste admin aanmaken (lokaal)

```bash
.venv/bin/python scripts/create_user.py --username admin --password '<sterk wachtwoord>' --role admin
```

Extra lokale gebruikers of een Entra-toewijzing:

```bash
.venv/bin/python scripts/create_user.py --username analist --password '<wachtwoord>' --role analist
.venv/bin/python scripts/create_user.py --username bob@example.com --entra-subject <sub> --role viewer
```

Met `--db <pad>` kies je een andere database (default: `AUTH_DB` of `data/auth.sqlite`). Administrators kunnen ook gebruikers aanmaken via `POST /api/auth/users`.

### Microsoft Entra ID

Maak een app-registratie aan in het [Microsoft Entra admin center](https://entra.microsoft.com) (App registrations). Noteer de tenant-id en client-id en maak een client-secret aan (Certificates & secrets). Stel als redirect-URI het Web-platform in op `<jouw-domein>/api/auth/callback` — die moet exact overeenkomen met `AUTH_ENTRA_REDIRECT_URI`. Zet vervolgens in `.env`:

```
AUTH_ENTRA_ENABLED=1
AUTH_ENTRA_TENANT=<tenant-id of 'organizations'>
AUTH_ENTRA_CLIENT_ID=<client-id>
AUTH_ENTRA_CLIENT_SECRET=<client-secret>
AUTH_ENTRA_REDIRECT_URI=https://sanctielijst.brakketak.nl/api/auth/callback
```

Een onbekende Entra-gebruiker wordt bij de eerste login automatisch aangemaakt met rol `AUTH_ENTRA_DEFAULT_ROLE` (default `viewer`). De rol van een bestaande gebruiker pas je aan in de database (`data/auth.sqlite`, kolom `role`).

### AUTH_REQUIRED

`AUTH_REQUIRED=1` vereist een login voor zoeken en exporteren; `AUTH_REQUIRED=0` (default) laat de zoekpagina open. In beide gevallen staat er een discreet "Inloggen"-link in de header; zodra een login vereist is, vervangt de app het zoekformulier door het login-paneel.

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

## Audit-log

Elke zoekopdracht en PDF-export wordt gelogd in een aparte SQLite-database, `data/audit.sqlite` (overschrijfbaar met env `AUDIT_DB`). Dit bestand is gitignored en staat los van de zoekindex, zodat de audit-historie niet verdwijnt bij een index-rebuild.

Een event bevat het tijdstip (UTC), het client-IP, de user-agent, methode en pad, de zoekquery, het aantal resultaten en de gebruikte bronnen. Achter een reverse proxy leest de app het client-IP uit `X-Forwarded-For` — draai uvicorn dan met `--proxy-headers`.

Beheerweergave: `GET /api/audit` retourneert de events, gesorteerd op tijdstip en met paginering via `limit`/`offset`. Het endpoint is alleen actief als env `AUDIT_ADMIN_TOKEN` is ingesteld; toegang vereist `Authorization: Bearer <AUDIT_ADMIN_TOKEN>`. Zonder token is het endpoint uitgeschakeld (404). Een simpele admin-pagina staat op `/audit` (link in de footer, alleen zichtbaar als het endpoint actief is) en vraagt om het token.

De kolom `user` wordt gevuld zodra iemand is ingelogd (lokaal of Microsoft Entra ID, zie "Login"); anonieme zoekopdrachten worden met `null` gelogd.

## Wekelijks bijwerken PEP-data (OpenSanctions)

Download alle individuele PEP-bronnen (~0.8 GB, `entities.ftm.json` per bron) naar `data/pep/`:

```bash
.venv/bin/python scripts/update_pep.py --once
```

- Manifest: `data/pep/manifest.json` (downloaddatum, versie, checksums, status per bron).
- Ongewijzigde bronnen worden overgeslagen; alleen gewijzigde worden herdownload.
- Kies een pad met `--root` of env `PEP_DATA_DIR`.

Wanneer de EU- of PEP-data verandert, herbouwt de app `data/search.sqlite` automatisch op de achtergrond — een restart is niet nodig. Na een downloader-run bouwt de app de index dus zelf opnieuw; de status is dan kort `building` en toont "Index wordt opgebouwd…" zolang de rebuild loopt.

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
