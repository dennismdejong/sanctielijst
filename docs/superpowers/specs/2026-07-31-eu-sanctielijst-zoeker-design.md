# Ontwerp — Sanctielijst Zoeker

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker (design), ter review voorgelegd (spec)

## Doel

Een web-app waarmee gebruikers personen en bedrijven kunnen zoeken in de EU-sanctielijsten, met eenvoudig zoekformulier en gelijkende resultaten. Resultaten hoeven geen 100% match te zijn; de app toont per resultaat **waarom** het matcht (welke kenmerken overeenkomen en hoe sterk).

Twee databronnen:

1. **EU sanctielijst** via data.europa.eu (primaire bron, lokaal geïndexeerd).
2. **OpenSanctions** `/match`-API (optioneel, vereist API-key).

## Technische stapel

- **Python 3.11+ / FastAPI + Uvicorn** — server, API-routes.
- **rapidfuzz** — fuzzy naam-matching voor de EU-lijst.
- **requests** — download EU-XML en OpenSanctions-calls.
- **python-dotenv** — `.env`-config.
- Frontend: **vanilla HTML/CSS/JS** (geen framework), Nederlandse UI.
- Tests: **pytest** voor matcher en ingest.

## Databronnen

### 1. EU sanctielijst (data.europa.eu)

- XML 1.1: `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw` (~25 MB)
- Namespace: `http://eu.europa.ec/fpi/fsd/export`, root `<export>`, ~6.000 `<sanctionEntity>` records (4.421 personen + 1.596 bedrijven).
- Relevante velden per entity:
  - Attr: `euReferenceNumber` (bijv. `EU.471.56`), `unitedNationId`, `designationDate`, `logicalId`
  - `subjectType` (`code=person|enterprise`)
  - `nameAlias` (kind: `firstName`, `middleName`, `lastName`, `wholeName`, `function`, `gender`, `title`, `strong`="true" = primaire naam)
  - `birthdate` (kind: `birthdate`, `dayOfMonth`, `monthOfYear`, `year`, `yearRangeFrom`, `yearRangeTo`, `circa`, `city`, `place`, `countryIso2Code`, `countryDescription`)
  - `citizenship` (`countryIso2Code`, `countryDescription`)
  - `address` (`city`, `street`, `region`, `countryIso2Code`, `countryDescription`)
  - `identification` (`number`, `identificationTypeCode`, `identificationTypeDescription`, `countryIso2Code`)
  - `regulation` (`numberTitle`, `publicationUrl`, `publicationDate`, `programme`)
  - `remark` (tekst)
- Een persoon heeft **meerdere** nameAlias-, birthdate-, address-, identification- en citizenship-elementen.

### 2. OpenSanctions (optioneel)

- Endpoint: `POST https://api.opensanctions.org/match/default`
- Auth: header `Authorization: ApiKey <KEY>`, key uit env `OPENSANCTIONS_API_KEY`.
- Request-body: `{"queries": {"q": {"schema": "Person", "properties": {"firstName": [...], "lastName": [...], "birthDate": [...], "nationality": [...], "birthPlace": [...]}}}}`
- Params: `threshold=0.9`, `limit=10`, `topics=sanction&topics=sanction.linked&topics=debarment`
- Response: `responses.q.results[]` met `id`, `caption`, `schema`, `properties` (alias, birthDate, birthPlace, citizenship, programId, sourceUrl, topics, …), `datasets`, `score` (0–1), `match`, `explanations` (per feature: `name_match`, `dob_year_disjoint`, …).
- `default`-dataset bevat o.a. `eu_fsf` (dezelfde EU-lijst) plus OFAC, VN, VK, etc.
- **Geen key?** De app draait EU-only en toont een melding. OpenSanctions schakelt in zodra de key aanwezig is.

## Architectuur

```
sanctielijst/
  app/
    main.py            # FastAPI-app + routes
    ingest.py          # EU-XML downloaden, parsen, index bouwen, cache-beheer
    matcher.py         # EU-scoring (rapidfuzz + kenmerk-scores)
    opensanctions.py   # OpenSanctions /match-client (optioneel)
  static/
    index.html         # Nederlandse UI
    app.js             # zoekformulier + resultaatrendering
    style.css
  tests/
    test_matcher.py
    test_ingest.py
  data/                # gecachete XML + index (gitignored)
  .env.example
  requirements.txt
  README.md
```

## Data-pipeline (EU-lijst)

1. Bij opstart: check cache in `data/eu_sanctions.xml` + `data/cache_meta.json` (timestamps).
2. Is cache ouder dan **24 uur** (of afwezig): download XML 1.1, sla op, update timestamp.
3. Parse XML via `xml.etree.ElementTree`, bouw **in-memory index**:
   - Lijst van entities, elk een dict met genormaliseerde velden + kind-collecties.
4. Download/parse-fout → gebruik bestaande cache + waarschijnlijkheidswaarschuwing; geen data → 503 met duidelijke foutmelding.

## Zoek-API

- `GET /` → HTML-pagina
- `GET /api/search?name=…&birth_year=…&nationality=…&birth_place=…&entity_type=person|enterprise`
  - `name` verplicht; rest optioneel.
  - Draait lokaal op EU-index én (als key aanwezig) parallel een OpenSanctions `/match`.
  - Response: één gecombineerde lijst `results[]`, elk resultaat met `sources: ["eu", "opensanctions"]` en de relevante match-informatie per bron. Frontend rendert bronbadges.
- `GET /api/status` → data-generatiedatum, cacheleeftijd, opensanctions actief ja/nee.
- `POST /api/refresh` → forceert herdownload.
- `GET /api/health` → simpele healthcheck.

## Match-scoring EU (lokaal)

Totaalscore 0–100, **drempel ≥ 90** toont resultaat (geen resultaat → "Geen overeenkomsten gevonden").

Gewichten (alleen kenmerken meetellen die de gebruiker invulde):

| Kenmerk | Gewicht | Scoring |
|---|---|---|
| Naam | 60% | rapidfuzz `token_set_ratio` over alle aliassen; `strong`-aliassen ×1,2. Beste alias-score genomen. |
| Geboortejaar | 20% | exact = 100; ±1 jaar = 75; ±2 jaar = 50; anders 0. |
| Nationaliteit | 10% | ISO-landcode exact = 100; anders 0. |
| Geboorteplaats | 10% | token-overlap (`token_set_ratio`) tegen alle birthdate-plaatsen; beste score. |

Per resultaat wordt de match-verklaring geleverd: welke alias matchte, welk percentage, en welke kenmerken exact/nabij overeenkwamen.

## Resultaatweergave

- Eén gecombineerde resultatenlijst. Elke kaart toont: naam (vet), bronbadge(s) (**EU sanctielijst** / **OpenSanctions**), EU-refnr, aliassen, geboortedatum/plaats, nationaliteit, functie, reglement(en) met publicatie-URL, en **match-chips**:
  - EU: `Naam 92% (via "alias")` · `Geboortejaar exact` · `Nationaliteit match` · `Geboorteplaats match` · totaalscore.
  - OpenSanctions: score (0–1) + vertaalde `explanations`-features.
- Sortering: aflopend op score.
- Geen resultaten → lege-staatmelding + suggesties.

## Foutafhandeling

- EU-downloadfout → cachedata gebruiken + gele waarschuwingsbanner met foutmelding.
- Geen EU-data en geen cache → foutpagina met instructies.
- Geen OpenSanctions-key → app draait EU-only, statuspagina toont "OpenSanctions niet actief".
- OpenSanctions-API-fout (HTTP/timeout) → EU-resultaten tonen + melding "OpenSanctions tijdelijk niet beschikbaar".
- Timeouts: EU-download 120s; OpenSanctions-call 30s, faalt de call → sla over (niet de hele zoekopdracht laten falen).

## Configuratie

`.env.example`:
```
OPENSANCTIONS_API_KEY=
```
Cache-TTL (24u) en OpenSanctions threshold/limit als constanten in code.

## Teststrategie

- `test_matcher.py`: naam-scoring (exact, fuzzy, strong-alias-gewicht), geboortejaar-scores (±1/±2), nationaliteit, geboorteplaats, drempel, geen-resultaat.
- `test_ingest.py`: parsen van een kleine XML-fixture (1 persoon met aliassen + geboortedatum + nationaliteit), cache-TTL-logica (mock download), download-fout-gedrag.
- OpenSanctions-client: gemockt (geen live API in tests).

## Buiten scope (voor nu)

- OpenSanctions-merge/deduplicatie van overlap met EU-lijst (later, zodra key aanwezig).
- PEP-data, maritiem, KYB.
- Auth/multi-user.
- Deploy/containerisatie.
