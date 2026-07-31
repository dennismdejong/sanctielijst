# Sanctielijst Zoeker

Web-app om te zoeken in de EU sanctielijsten (personen en bedrijven), met fuzzy matching en per-kenmerk uitleg waarom een resultaat matcht. Optioneel ook wereldwijde screening via de OpenSanctions `/match`-API.

## Installatie

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data

Bij de eerste start downloadt de app de EU sanctielijst (XML 1.1, ~25 MB) van `data.europa.eu` en cacht deze in `data/`. De cache wordt automatisch ververst als deze ouder is dan 24 uur. Forceer verversen via `POST /api/refresh`.

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

## Tests

```bash
python -m pytest -v
```
