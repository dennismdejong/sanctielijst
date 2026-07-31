# Ontwerp — Authenticatie via Identity Provider (Entra ID + OIDC)

Datum: 2026-07-31
Status: Goedgekeurd door gebruiker (design)

## Doel

Zet een identity layer vóór de Compliance Zoeker-app zodat de **volledige app (web-UI én `/api/*`)** alleen toegankelijk is voor geautoriseerde gebruikers. Eisen:

- Inloggen voor **interne gebruikers** (bestaande Microsoft 365 / Entra-tenant) én **externe gebruikers** (partners/klanten).
- **MFA** verplicht.
- **Audit-logging**: per zoekactie en per refresh-actie wordt vastgelegd *wie* het deed.
- **RBAC**: rollen die bepalen wie mag zoeken en wie `POST /api/refresh` (datarefresh) mag aanroepen.
- Deployment blijft Docker Compose op een VPS.

## Gekozen aanpak

**Microsoft Entra ID (direct) + OIDC-integratie in de FastAPI-app.**

De app is de *resource server*: ze valideert zelf het ID/access token (JWT) bij elke request en haalt daaruit identiteit (`sub`, `email`), tenant en rollen. Een aparte reverse-proxy-auth-laag (oauth2-proxy) is bewust **niet** gekozen omdat audit-logging en RBAC in de app zelf nodig zijn en de app de gebruikersclaims dan alleen via onbetrouwbare headers zou krijgen.

Alternatieven en waarom niet:
- **Zelf-gehoste broker (Authentik/Keycloak)** — zie "Broker als alternatief" verderop; alleen zinvol als meerdere externe identiteitsbronnen of een eigen inlog-venster een harde eis wordt.
- **Reverse-proxy-auth (oauth2-proxy / Caddy forward-auth)** — beschermt de UI prima maar maakt RBAC/audit in de app fragiel (headers forgeable als de app ooit direct bereikbaar is).

## Architectuur

```
Browser
   │  1. GET / → 302 → Entra /authorize (PKCE + state + nonce)
   ▼
FastAPI-app (resource server) ── OIDC discovery + token validatie ──► Microsoft Entra ID
   │                                                                   (jullie bestaande tenant)
   │  gevalideerde id_token-claims in httponly session-cookie
   ▼
SQLite (search-index) + audit-log
```

- De app spreekt het **Authorization Code flow with PKCE** protocol met Entra (nieuwe standaard, geen client secret nodig bij PKCE).
- Na succesvolle login slaat de app het ID-token (of de claims) op in een **httponly, Secure, SameSite=Lax cookie**. Elke request valideert het token (signature, `iss`, `aud`, `exp`) uit de Entra JWKS.
- Externe gebruikers worden **Entra B2B-gasten** in dezelfde tenant — geen eigen gebruikersadministratie in de app.

## Volledige flow

### 1. Login (interactive, browser)

1. Gebruiker opent `https://<host>/` → app heeft geen geldige sessie → `302` naar `{tenant}/oauth2/v2.0/authorize` met `response_type=code`, `client_id`, `redirect_uri=https://<host>/auth/callback`, `scope=openid profile email`, PKCE-`code_challenge`, `state`, `nonce`.
2. Entra toont login + MFA (Conditional Access) en stuurt terug naar `/auth/callback?code=...&state=...`.
3. App valideert `state` (CSRF), ruilt `code` voor tokens via `POST {tenant}/oauth2/v2.0/token` (met `code_verifier`), valideert `nonce` in het ID-token, slaat claims op in de sessie-cookie en redirect naar `/`.
4. Interne gebruiker: bestaand Entra-account + MFA. Externe gebruiker: B2B-gastaccount (genodigd vanuit de tenant) + MFA indien ingesteld.

### 2. API-aanroep (server-to-server / script)

1. Client vraagt zelf een token aan bij Entra (client-credentials voor service accounts, of via een eigen login) en stuurt `Authorization: Bearer <token>`.
2. App valideert het access token (signature via JWKS, `iss`, `aud` = de app-registratie, `exp`, `nonce`/`scp`-claims).
3. Zelfde RBAC/audit als browser-flow — de app kijkt alleen naar gevalideerde claims, niet naar headers.

### 3. Uitloggen

- `POST /auth/logout` vernietigt de sessie-cookie en redirect naar Entra end-session endpoint (`logout?post_logout_redirect_uri=...`) zodat de SSO-sessie ook wordt beëindigd.

## Componenten in de code

### 1. `app/auth.py` (nieuw)

- `OIDCConfig` dataclass — `issuer`, `client_id`, `redirect_uri`, `scope`, `aud` (uit env `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_REDIRECT_URI`; issuer = `https://login.microsoftonline.com/{tenant}/v2.0`).
- `oidc_discovery()` — cached `.well-known/openid-configuration` + JWKS.
- `auth_url(state, code_verifier)` — bouwt de authorize-URL.
- `exchange_code(code, code_verifier)` — ruilt code → tokens, valideert `id_token` (signature, `iss`, `aud`, `nonce`, `exp`), retourneert claims.
- `verify_token(token, expected_aud)` — algemene JWT-validatie (JWKS, `iss`, `aud`, `exp`, `iat`/`nbf`) voor API bearer-tokens.
- `get_user_claims(request)` — leest de sessie-cookie uit, valideert en retourneert claims dict, of `None`.
- Sessie-cookie: naam `sid`, `httponly=True`, `secure=True` (alleen over HTTPS; in dev uit), `samesite="lax"`, max_age = token exp.

### 2. FastAPI-dependencies in `app/main.py`

- `require_auth` (dependency) — valideert sessie; zonder sessie: `307` redirect naar `/auth/login` voor browser-requests, `401` voor API-requests.
- `require_role(role)` (dependency-factory) — checkt claim `roles`/`appRoles`; `403` bij onvoldoende rechten.
- `optional_auth` — voor health/status die ook anoniem mogen (zie "Buiten scope"/"Openbaar").

### 3. Endpoints

- `GET /auth/login` — bouwt authorize-URL, slaat `state`+`code_verifier` in de sessie-cookie, redirect.
- `GET /auth/callback` — wisselt code, valideert, zet sessie, redirect `/`.
- `POST /auth/logout` — verwijdert sessie, redirect naar Entra end-session.
- `GET /api/me` — retourneert `{email, name, roles, tenant_id}` voor de frontend (user-menu).
- Bestaande endpoints krijgen de dependency: `/` en `/static/*` → `require_auth`; `/api/search` → `require_auth` + `require_role("Compliance.Viewer")`; `POST /api/refresh` → `require_auth` + `require_role("Compliance.Admin")`. `/api/health` blijft publiek (voor loadbalancer), `/api/status` wordt auth-beschermd.

### 4. Audit-log (`app/audit.py`, nieuw)

- SQLite-tabel in `data/audit.sqlite`:
  ```sql
  CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,        -- email / sub
    action TEXT NOT NULL,       -- 'search' | 'refresh' | 'login' | 'logout' | 'denied'
    detail TEXT NOT NULL,       -- JSON (query, filters, statuscode, redirect)
    remote_ip TEXT,
    user_agent TEXT
  );
  ```
- Geschreven in `/api/search` (query + filters), `POST /api/refresh` (trigger), `/auth/login`/`/auth/logout` en bij `403` (`denied` + benodigde rol).
- **Geen zoekresultaten loggen** (privacy) — alleen de query. Beleid: log best-effort, een audit-schrijffout mag de zoekactie niet blokkeren.

### 5. RBAC

- Rollen als **Entra App Roles** op de app-registratie (`Compliance.Viewer`, `Compliance.Admin`), toegewezen aan gebruikers/groepen (interne via Azure-groep, externe via directe B2B-toewijzing). App-rollen verschijnen als `roles`-claim in het token.
- Mapping: `Viewer` → mag zoeken; `Admin` → mag zoeken + refreshen.
- Fallback: als er geen rollen in het token zitten, geen toegang (fail-closed).

## Config & deployment

- Nieuwe env (in `.env.example` + docker-compose):
  - `AZURE_TENANT_ID`
  - `AZURE_CLIENT_ID`
  - `AZURE_REDIRECT_URI` (bijv. `https://<host>/auth/callback`)
  - `AZURE_CLIENT_SECRET` — alleen nodig als de code-flow *met* secret gebeurt; met PKCE optioneel/weglaten.
  - `AUTH_REQUIRED=1` (aan/uit; `0` voor lokale dev zonder login)
  - `AUDIT_DB` (default `data/audit.sqlite`)
  - `COOKIE_SECURE` (default `1`; `0` alleen voor dev over http)
- Docker: geen extra service nodig; `app`-container krijgt de nieuwe env-variabelen. Geen extra dependency in de container (gebruik `PyJWT` + `requests` of `httpx`, beide licht; voeg `PyJWT` toe aan `requirements.txt`).

## Teststrategie

- `tests/test_auth.py` — `verify_token`: geldig/verlopen/verkeerde `aud`/verkeerde `iss`/slechte signature (mock JWKS); `auth_url` bevat `code_challenge`/`state`; `get_user_claims` met/ zonder cookie.
- `tests/test_audit.py` — `log()` schrijft rijen; corrupte DB blokkeert search niet.
- `tests/test_main.py` (uitgebreid) — met `AUTH_REQUIRED=0` gedrag behouden (regressie); met auth-actief: `/` redirect naar login zonder sessie, `/api/search` `401` zonder token, `403` met verkeerde rol, `200` met juiste rol, `refresh` alleen voor Admin.
- Test-flow: `create_app` accepteert een `auth`-override (injectie van een fake token-verifier) zodat tests geen echte Entra-aanroep doen.

## Foutafhandeling

- Token verlopen/ongeldig → sessie wissen, redirect naar login (browser) / `401` (API).
- Entra onbereikbaar tijdens login → foutpagina "inloggen tijdelijk niet mogelijk", sessie niet half-wegzetten.
- `state`-mismatch of `nonce`-fout op callback → weigeren + audit `login`-fout, geen sessie.
- Audit-log onbeschrijfbaar → loggen naar stderr, request gewoon doorlaten.
- `AUTH_REQUIRED=0` (dev) → alles zonder login bereikbaar, audit blijft loggen als `actor=anonymous` (alleen als audit ingeschakeld).

## Broker als alternatief (gedocumenteerd, niet gekozen)

Als later meerdere externe identiteitsbronnen of een eigen inlog-venster nodig worden:

- Authentik (of Keycloak) draait als extra container op de VPS, eigen inlog-venster.
- Interne gebruikers koppelen via OIDC/SAML door naar Entra (saml/OIDC provider), externen als lokale accounts of via andere providers.
- De app verandert dan vrijwel niet: alleen de `issuer` in `OIDCConfig` wijzigt naar de broker-URL; het token-validatieverhaal blijft identiek (JWT + JWKS + rollen-claims).
- Kosten: een extra kritieke, zelf te patchen service en eigen user-admin.

Deze architectuur is er dus op voorbereid: de provider-afhandeling zit achter één config (`issuer`/`client_id`), dus een switch naar een broker is een configwijziging, geen rewrite.

## Buiten scope

- Zelf-gehoste broker als primaire oplossing (documentatie hierboven, implementatie alleen op verzoek).
- Entra B2C met eigen gebruikersregistratie.
- Fine-grained per-tenant toegang (meerdere klanttenants met aparte rollen) — v1: alle externen via B2B-gasten in dezelfde tenant.
- Auth voor de downloader-containers (die hebben geen UI).
