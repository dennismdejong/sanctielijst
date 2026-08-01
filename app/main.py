import dataclasses
import json
import logging
import secrets
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit
from . import auth
from . import batch
from . import eu_ingest, ingest, matcher, opensanctions
from . import pep_ingest
from . import search_index
from . import watchlist
from .export import render_batch_csv, render_batch_pdf, render_search_csv, render_search_pdf, render_search_xlsx

load_dotenv()

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
REBUILD_SUBPROCESS_TIMEOUT = 600


def default_pep_root() -> Path:
    return Path(os.environ.get("PEP_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "pep")))


PEP_ROOT = default_pep_root()


def default_eu_root() -> Path:
    return Path(os.environ.get("EU_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "eu")))


EU_ROOT = default_eu_root()


def default_search_db() -> Path:
    return search_index.default_db_path()


SEARCH_DB = default_search_db()


def _data_age_hours(downloaded_at: str | None) -> float | None:
    if not downloaded_at:
        return None
    try:
        parsed = datetime.fromisoformat(downloaded_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - parsed).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None

def _serialize_eu_result(result: dict, query_name: str) -> dict:
    entity = result["entity"]
    raw = entity.get("raw") or {}
    aliases = [a["whole_name"] for a in raw.get("aliases", []) if a.get("whole_name")]
    return {
        "source": "eu",
        "score": result["score"],
        "entity": {
            "name": result["matched_name"] or query_name,
            "eu_reference_number": raw.get("eu_reference_number", entity.get("eu_ref", "")),
            "united_nations_id": raw.get("united_nations_id", ""),
            "subject_type": raw.get("subject_type", ""),
            "designation_date": raw.get("designation_date", ""),
            "aliases": aliases,
            "citizenships": raw.get("citizenships", []),
            "birthdates": raw.get("birthdates", []),
            "addresses": raw.get("addresses", []),
            "identifications": raw.get("identifications", []),
            "regulations": raw.get("regulations", []),
            "function": next((a["function"] for a in raw.get("aliases", []) if a.get("function")), ""),
            "remarks": raw.get("remarks", []),
        },
        "eu": {
            "total_score": result["score"],
            "matched_alias": result["matched_name"],
            "details": result["details"],
        },
        "opensanctions": None,
    }


def _serialize_eu_result_from_dict(result: matcher.EuMatchResult, query_name: str) -> dict:
    entity = result.entity
    return {
        "source": "eu",
        "score": result.total_score,
        "entity": {
            "name": result.matched_alias or query_name,
            "eu_reference_number": entity["eu_reference_number"],
            "united_nations_id": entity["united_nations_id"],
            "subject_type": entity["subject_type"],
            "designation_date": entity["designation_date"],
            "aliases": [a["whole_name"] for a in entity["aliases"] if a["whole_name"]],
            "citizenships": [{"iso2": c["iso2"], "description": c["description"]} for c in entity["citizenships"]],
            "birthdates": entity["birthdates"],
            "addresses": entity["addresses"],
            "identifications": entity["identifications"],
            "regulations": entity["regulations"],
            "function": next((a["function"] for a in entity["aliases"] if a["function"]), ""),
            "remarks": entity["remarks"],
        },
        "eu": {
            "total_score": result.total_score,
            "matched_alias": result.matched_alias,
            "details": [dataclasses.asdict(d) for d in result.details],
        },
        "opensanctions": None,
    }


def _serialize_os_result(result: dict) -> dict:
    props = result["properties"]
    return {
        "source": "opensanctions",
        "score": round(result["score"] * 100),
        "entity": {
            "name": result["caption"],
            "schema": result["schema"],
            "aliases": list(dict.fromkeys(props.get("alias", []) + props.get("name", [])))[:10],
            "birthdates": [{"date": d, "year": None, "year_from": None, "year_to": None, "city": "", "place": "", "iso2": "", "country": ""} for d in props.get("birthDate", [])],
            "citizenships": [{"iso2": c.upper(), "description": c.upper()} for c in props.get("citizenship", [])],
            "countries": props.get("country", []),
            "topics": props.get("topics", []),
            "program_ids": props.get("programId", []),
            "source_urls": props.get("sourceUrl", [])[:3],
        },
        "eu": None,
        "opensanctions": result,
    }


def _pep_enabled(pep_root: Path) -> bool:
    env = os.environ.get(search_index.INDEX_ENV)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no")
    return pep_root.exists()


def _serialize_pep_result(result: dict, datasets_meta: dict) -> dict:
    entity = result["entity"]
    datasets = []
    for ds_id in entity.get("datasets", []):
        meta = datasets_meta.get(ds_id, {})
        datasets.append({
            "id": ds_id,
            "title": meta.get("title") or ds_id,
            "country": meta.get("country", ""),
            "url": f"https://www.opensanctions.org/datasets/{ds_id}/",
        })
    return {
        "source": "pep",
        "score": result["score"],
        "entity": {
            "name": entity.get("caption", ""),
            "schema": entity.get("schema", ""),
            "birth_dates": entity.get("birth_dates", []),
            "birth_places": entity.get("birth_places", []),
            "citizenships": entity.get("citizenships", []),
            "political": entity.get("political", []),
            "topics": entity.get("topics", []),
            "positions": entity.get("positions") or [],
        },
        "pep": {
            "id": entity.get("id", ""),
            "url": f"https://opensanctions.org/entities/{entity.get('id', '')}",
            "datasets": datasets,
            "matched_name": result["matched_name"],
            "details": result["details"],
        },
        "eu": None,
        "opensanctions": None,
    }


def _to_watchlist_match(result: dict) -> dict | None:
    """Map a serialized run_search result to the watchlist match contract.

    Returns ``None`` when the match carries no stable public entity id, so no
    hit is persisted for it. The ``naam`` is always derived from public match
    data (never the watched query name).
    """
    source = result.get("source")
    entity = result.get("entity") or {}
    if source == "eu":
        match_id = entity.get("eu_reference_number") or ""
        datasets = ["eu"]
        naam = (result.get("eu") or {}).get("matched_alias") or match_id
    elif source == "pep":
        match_id = (result.get("pep") or {}).get("id") or ""
        datasets = [d.get("id") for d in ((result.get("pep") or {}).get("datasets") or []) if d.get("id")]
        naam = entity.get("name") or ""
    else:
        match_id = (result.get("opensanctions") or {}).get("id") or ""
        datasets = list((result.get("opensanctions") or {}).get("datasets") or [])
        naam = entity.get("name") or ""
    if not match_id:
        return None
    return {
        "id": match_id,
        "naam": naam,
        "score": result.get("score", 0),
        "bron": source or "",
        "datasets": datasets,
    }


def _load_datasets_meta(pep_root: Path) -> dict:
    path = pep_root / "datasets.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _run_rebuild_subprocess(db_path: Path, eu_xml: Path, pep_root: Path) -> dict:
    cmd = [sys.executable, "-m", "app.rebuild",
           "--db", str(db_path), "--eu-xml", str(eu_xml), "--pep-root", str(pep_root)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=REBUILD_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = (exc.stderr or "").strip() or "geen stderr"
        raise RuntimeError(
            f"index rebuild subproces timeout na {REBUILD_SUBPROCESS_TIMEOUT}s: {stderr[-500:]}"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"index rebuild subproces exit {proc.returncode}: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def _run_rebuild(db_path: Path, eu_xml: Path, pep_root: Path) -> dict:
    if os.environ.get("PEP_INDEX_SUBPROCESS", "").strip().lower() in ("1", "true", "yes"):
        return _run_rebuild_subprocess(db_path, eu_xml, pep_root)
    return search_index.rebuild_index(db_path, eu_xml, pep_root)


def _build_index(state: dict, db_path: Path, eu_xml: Path, pep_root: Path) -> None:
    try:
        state["index_stats"] = _run_rebuild(db_path, eu_xml, pep_root)
        state["index_status"] = "ready"
        state["index_error"] = None
    except Exception as exc:
        logger.exception("Index-rebuild mislukt")
        state["index_status"] = "error"
        state["index_error"] = f"Index-rebuild mislukt: {exc}"


def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    eu_root: Path | None = None,
    static_dir: Path | None = None,
    pep_root: Path | None = None,
    pep_sync: bool | None = None,
    search_db: Path | None = None,
) -> FastAPI:
    eu_root = eu_root or default_eu_root()
    static_dir = static_dir or STATIC_DIR
    pep_root = pep_root or default_pep_root()
    meta = eu_ingest.load_eu_manifest(eu_root)
    eu_xml = eu_root / eu_ingest.XML_FILENAME
    if entities is None:
        if eu_xml.exists():
            try:
                entities = ingest.parse_export(eu_xml.read_bytes())
            except Exception:
                logger.exception("EU XML ongeldig")
                entities = []
                meta["status"] = "error"
            else:
                meta.setdefault("status", "ok")
        else:
            entities = []
            meta.setdefault("status", "missing")
    if os_api_key is None:
        os_api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    if pep_sync is None:
        pep_sync = os.environ.get("PEP_INDEX_SYNC", "").strip().lower() in ("1", "true", "yes")
    db_path = search_db if search_db is not None else default_search_db()
    audit_db = audit.default_audit_db()
    audit_admin_token = (os.environ.get("AUDIT_ADMIN_TOKEN") or "").strip()
    enabled = _pep_enabled(pep_root) or eu_xml.exists()
    state = {"db_path": db_path, "index_status": "disabled", "index_stats": None, "index_error": None, "entities": entities, "meta": meta, "build_lock": threading.Lock()}
    datasets_meta = _load_datasets_meta(pep_root)
    if enabled:
        try:
            result = search_index.ensure_index(db_path, eu_xml, pep_root)
        except Exception:
            logger.exception("Zoekindex ongeldig; opnieuw opbouwen")
            result = {"db": None, "ready": False, "stats": None}
        if result["ready"]:
            state["index_status"] = "ready"
            state["index_stats"] = result["stats"]
            if result.get("db") is not None:
                result["db"].close()
        elif pep_sync:
            _build_index(state, db_path, eu_xml, pep_root)
        else:
            state["index_status"] = "building"
            threading.Thread(target=_build_index, args=(state, db_path, eu_xml, pep_root), daemon=True).start()
    opensanctions_active = bool(os_api_key)
    auth_secret = os.environ.get("AUTH_SECRET")
    auth_db = auth.default_auth_db()
    auth_required = os.environ.get("AUTH_REQUIRED", "0").strip().lower() in ("1", "true", "yes")
    local_enabled = os.environ.get("AUTH_LOCAL_ENABLED", "1").strip().lower() not in ("0", "false", "no")
    if auth_required and not auth_secret:
        raise RuntimeError("AUTH_REQUIRED=1 vereist AUTH_SECRET in de omgeving")
    try:
        entra_config = auth.entra_config()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if entra_config is not None and not auth_secret:
        raise RuntimeError("Entra-login vereist AUTH_SECRET in de omgeving")

    app = FastAPI(title="Compliance Zoeker")

    def get_current_user(request: Request) -> dict | None:
        if auth_secret is None:
            return None
        return auth.current_user(request, auth_db, auth_secret)

    def require_role(*roles):
        def dependency(user: dict | None = Depends(get_current_user)) -> dict:
            if user is None:
                raise HTTPException(status_code=401, detail="Niet ingelogd")
            if user["role"] not in roles:
                raise HTTPException(status_code=403, detail="Onvoldoende rechten")
            return user

        return dependency

    def _check_roles(user: dict | None, roles: tuple[str, ...]) -> None:
        if not auth_required:
            return
        if user is None:
            raise HTTPException(status_code=401, detail="Niet ingelogd")
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Onvoldoende rechten")

    @app.get("/")
    def index():
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/audit")
    def audit_page():
        return FileResponse(str(static_dir / "audit.html"))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    def _status() -> dict:
        if state["index_status"] == "ready" and not search_index.index_fresh(state["db_path"], eu_xml, pep_root):
            with state["build_lock"]:
                if state["index_status"] == "ready":
                    state["index_status"] = "building"
                    threading.Thread(target=_build_index, args=(state, state["db_path"], eu_xml, pep_root), daemon=True).start()
        meta = state["meta"]
        stats = state["index_stats"] or {}
        methods = []
        if local_enabled:
            methods.append("local")
        if entra_config is not None:
            methods.append("entra")
        return {
            "version": os.environ.get("APP_VERSION", "dev"),
            "cached_at": meta.get("downloaded_at"),
            "generated_at": meta.get("generation_date"),
            "entity_count": stats.get("total", len(state["entities"])),
            "data_age_hours": _data_age_hours(meta.get("downloaded_at")),
            "data_version": round(search_index._newest_input_mtime(eu_xml, pep_root), 3),
            "opensanctions_active": opensanctions_active,
            "source": meta.get("status", "unknown"),
            "auth": {"required": auth_required, "methods": methods},
            "index": {
                "enabled": state["index_status"] != "disabled",
                "status": state["index_status"],
                "eu_count": stats.get("eu_count", 0),
                "pep_count": stats.get("pep_count", 0),
                "source_count": stats.get("source_count", 0),
                "error": state["index_error"],
            },
        }

    @app.get("/api/status")
    def status():
        return _status()

    @app.post("/api/refresh")
    def refresh():
        try:
            manifest = eu_ingest.refresh_eu(eu_root)
            state["meta"] = manifest
            if eu_xml.exists():
                state["entities"] = ingest.parse_export(eu_xml.read_bytes())
            stats = manifest.get("stats") or {}
            unchanged = stats.get("downloaded") == 0
            if state["index_status"] != "disabled" and not (unchanged and state["index_status"] == "ready"):
                with state["build_lock"]:
                    if state["index_status"] != "building":
                        state["index_status"] = "building"
                        threading.Thread(target=_build_index, args=(state, state["db_path"], eu_xml, pep_root), daemon=True).start()
            return _status()
        except Exception:
            logger.exception("Verversen mislukt")
            raise HTTPException(status_code=503, detail="Verversen mislukt")

    def run_search(query: matcher.SearchQuery, include_opensanctions: bool = True) -> tuple[list[dict], list[str]]:
        results = []
        warnings = []
        if state["index_status"] == "ready":
            db = search_index._open(state["db_path"])
            try:
                for r in search_index.search(db, query.name, query.birth_year, query.nationality, query.birth_place, query.entity_type):
                    if r["entity"]["source"] == "eu":
                        results.append(_serialize_eu_result(r, query.name))
                    else:
                        results.append(_serialize_pep_result(r, datasets_meta))
            finally:
                db.close()
        elif state["index_status"] == "building":
            warnings.append("Zoekindex wordt opgebouwd; EU-resultaten getoond")
            for r in matcher.search_eu(state["entities"], query):
                results.append(_serialize_eu_result_from_dict(r, query.name))
        else:
            for r in matcher.search_eu(state["entities"], query):
                results.append(_serialize_eu_result_from_dict(r, query.name))
        if include_opensanctions and opensanctions_active:
            try:
                for r in opensanctions.match_opensanctions(
                    os_api_key, query.name, query.birth_year, query.nationality, query.birth_place
                ):
                    results.append(_serialize_os_result(r))
            except Exception:
                logger.exception("OpenSanctions match failed")
                warnings.append("OpenSanctions tijdelijk niet beschikbaar")
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:matcher.MAX_RESULTS]
        return results, warnings

    def _log_search(request: Request, query: matcher.SearchQuery, results: list[dict], warnings: list[str], user: str | None = None) -> None:
        try:
            audit.log_event(
                audit_db,
                ip=request.client.host if request.client else "",
                user=user,
                user_agent=request.headers.get("user-agent", ""),
                method=request.method,
                path=request.url.path,
                query=dataclasses.asdict(query),
                result_count=len(results),
                sources=sorted({r.get("source", "") for r in results}),
                threshold=matcher.THRESHOLD,
            )
        except Exception:
            logger.warning("Audit-log mislukt", exc_info=True)

    def _log_batch(request: Request, query: dict, result_count: int, user: str | None = None) -> None:
        try:
            audit.log_event(
                audit_db,
                ip=request.client.host if request.client else "",
                user=user,
                user_agent=request.headers.get("user-agent", ""),
                method=request.method,
                path=request.url.path,
                query=query,
                result_count=result_count,
                sources=[],
                threshold=matcher.THRESHOLD,
            )
        except Exception:
            logger.warning("Audit-log mislukt", exc_info=True)

    def _batch_search_fn(naam, geboortejaar=None, nationaliteit=None, geboorteplaats=None, type=None):
        query = matcher.SearchQuery(
            name=naam,
            birth_year=geboortejaar,
            nationality=nationaliteit,
            birth_place=geboorteplaats,
            entity_type=type,
        )
        results, _warnings = run_search(query, include_opensanctions=False)
        return results

    def _log_watchlist(request: Request, watchlist_id: str, action: str, count: int, user: str | None = None) -> None:
        try:
            audit.log_event(
                audit_db,
                ip=request.client.host if request.client else "",
                user=user,
                user_agent=request.headers.get("user-agent", ""),
                method=request.method,
                path=request.url.path,
                query={"watchlist_id": watchlist_id, "action": action},
                result_count=count,
                sources=[],
                threshold=matcher.THRESHOLD,
            )
        except Exception:
            logger.warning("Audit-log mislukt", exc_info=True)

    def _watchlist_search_fn(name, birth_year=None, nationality=None, birth_place=None, entity_type=None):
        query = matcher.SearchQuery(
            name=name,
            birth_year=birth_year,
            nationality=nationality,
            birth_place=birth_place,
            entity_type=entity_type,
        )
        results, _warnings = run_search(query, include_opensanctions=False)
        return [m for r in results if (m := _to_watchlist_match(r)) is not None]

    @app.get("/api/audit")
    def audit_events(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        authorization: str | None = Header(None),
        user: dict | None = Depends(get_current_user),
    ):
        admin_session = user is not None and user["role"] == "admin"
        if not audit_admin_token and not admin_session:
            raise HTTPException(status_code=404, detail="Audit-endpoint uitgeschakeld")
        valid_token = bool(audit_admin_token) and secrets.compare_digest(authorization or "", f"Bearer {audit_admin_token}")
        if not (admin_session or valid_token):
            raise HTTPException(status_code=401, detail="Niet geautoriseerd")
        return {"events": audit.list_events(audit_db, limit=limit, offset=offset), "total": audit.count_events(audit_db)}

    @app.get("/api/auth/login")
    async def auth_login(request: Request):
        if entra_config is not None:
            client = auth.entra_client(entra_config)
            url, code_verifier, state = await auth.entra_authorize_url(client, state_secret=auth_secret)
            response = RedirectResponse(url)
            response.set_cookie("auth_code_verifier", code_verifier, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
            return response
        methods = ["local"] if local_enabled else []
        return {"methods": methods}

    @app.post("/api/auth/login")
    async def auth_login_local(request: Request, payload: dict | None = Body(default=None)):
        if not local_enabled:
            raise HTTPException(status_code=404, detail="Lokale login uitgeschakeld")
        if auth_secret is None:
            raise HTTPException(status_code=503, detail="AUTH_SECRET niet geconfigureerd")
        username = (payload or {}).get("username")
        password = (payload or {}).get("password")
        if not username or not password:
            raise HTTPException(status_code=401, detail="Ongeldige gebruikersnaam of wachtwoord")
        user = auth.find_by_credentials(auth_db, username, password)
        if user is None:
            raise HTTPException(status_code=401, detail="Ongeldige gebruikersnaam of wachtwoord")
        token = auth.create_session(user, auth_secret)
        response = JSONResponse({"username": user["username"], "role": user["role"]})
        response.set_cookie("session", token, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/", max_age=auth.SESSION_MAX_AGE)
        return response

    @app.get("/api/auth/callback")
    async def auth_callback(
        request: Request,
        code: str = Query(...),
        state: str | None = Query(None),
        error: str | None = Query(None),
    ):
        if entra_config is None:
            raise HTTPException(status_code=400, detail="Entra-login niet geconfigureerd")
        if error:
            raise HTTPException(status_code=400, detail="Entra-login mislukt")
        code_verifier = request.cookies.get("auth_code_verifier")
        if not code_verifier:
            raise HTTPException(status_code=400, detail="Geen geldige login-sessie")
        try:
            client = auth.entra_client(entra_config)
            info = await auth.entra_exchange(client, code, code_verifier, state, auth_secret)
            try:
                user = auth.find_or_create_idp_user(
                    auth_db,
                    "entra",
                    info["sub"],
                    default_role=entra_config["default_role"],
                    username=info.get("username"),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Gebruiker bestaat al") from exc
        except HTTPException:
            raise
        except Exception:
            logger.warning("Entra-exchange mislukt", exc_info=True)
            raise HTTPException(status_code=400, detail="Entra-login mislukt")
        token = auth.create_session(user, auth_secret)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("auth_code_verifier", path="/")
        response.set_cookie("session", token, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/", max_age=auth.SESSION_MAX_AGE)
        return response

    @app.post("/api/auth/logout")
    def auth_logout():
        response = Response(status_code=204)
        response.delete_cookie("session", path="/")
        return response

    @app.get("/api/auth/me")
    def auth_me(user: dict | None = Depends(get_current_user)):
        if user is None:
            raise HTTPException(status_code=401, detail="Niet ingelogd")
        return {"username": user["username"], "role": user["role"]}

    class CreateUserBody(BaseModel):
        username: str
        password: str | None = None
        role: str = "viewer"
        idp_subject: str | None = None

    @app.post("/api/auth/users")
    def auth_create_user(body: CreateUserBody, user: dict = Depends(require_role("admin"))):
        try:
            created = auth.create_user(
                auth_db,
                body.username,
                password=body.password,
                role=body.role,
                idp="entra" if body.idp_subject else None,
                idp_subject=body.idp_subject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": created["id"], "username": created["username"], "role": created["role"]}

    @app.get("/api/search")
    def search(
        request: Request,
        user: dict | None = Depends(get_current_user),
        name: str = Query(..., min_length=1),
        birth_year: int | None = Query(None, ge=1900, le=2100),
        nationality: str | None = None,
        birth_place: str | None = None,
        entity_type: str | None = Query(None, pattern="^(person|enterprise)$"),
    ):
        _check_roles(user, ("admin", "analist", "viewer"))
        query = matcher.SearchQuery(
            name=name.strip(),
            birth_year=birth_year,
            nationality=(nationality or "").strip() or None,
            birth_place=(birth_place or "").strip() or None,
            entity_type=entity_type,
        )
        audit_user = user["username"] if user else None
        if not query.name:
            _log_search(request, query, [], [], user=audit_user)
            raise HTTPException(status_code=422, detail="Naam is verplicht")
        results, warnings = run_search(query)
        _log_search(request, query, results, warnings, user=audit_user)
        return {
            "query": {
                "name": query.name,
                "birth_year": query.birth_year,
                "nationality": query.nationality,
                "birth_place": query.birth_place,
                "entity_type": query.entity_type,
            },
            "results": results,
            "warnings": warnings,
            "opensanctions_active": opensanctions_active,
        }

    @app.get("/api/search/export")
    def search_export(
        request: Request,
        user: dict | None = Depends(get_current_user),
        name: str = Query(..., min_length=1),
        birth_year: int | None = Query(None, ge=1900, le=2100),
        nationality: str | None = None,
        birth_place: str | None = None,
        entity_type: str | None = Query(None, pattern="^(person|enterprise)$"),
        author: str | None = None,
        format: str = Query("pdf", pattern="^(pdf|csv|xlsx)$"),
    ):
        _check_roles(user, ("admin", "analist"))
        query = matcher.SearchQuery(name=name.strip(), birth_year=birth_year, nationality=(nationality or "").strip() or None, birth_place=(birth_place or "").strip() or None, entity_type=entity_type)
        audit_user = user["username"] if user else None
        if not query.name:
            _log_search(request, query, [], [], user=audit_user)
            raise HTTPException(status_code=422, detail="Naam is verplicht")
        results, warnings = run_search(query)
        _log_search(request, query, results, warnings, user=audit_user)
        now = datetime.now().astimezone()
        generated = now.strftime("%Y-%m-%d %H:%M %Z")
        payload = {
            "query": {"name": query.name, "birth_year": query.birth_year, "nationality": query.nationality, "birth_place": query.birth_place, "entity_type": query.entity_type},
            "results": results, "warnings": warnings,
            "meta": state["meta"], "pep_meta": pep_ingest.load_pep_manifest(pep_root),
            "version": os.environ.get("APP_VERSION", "dev"),
            "author": author, "generated_at": generated,
            "threshold": matcher.THRESHOLD, "max_results": matcher.MAX_RESULTS,
        }
        if format == "csv":
            try:
                content = render_search_csv(results, payload["query"]).encode("utf-8-sig")
            except Exception:
                logger.exception("CSV-generatie mislukt")
                raise HTTPException(status_code=500, detail="CSV-generatie mislukt")
            media_type = "text/csv; charset=utf-8"
            extension = "csv"
        elif format == "xlsx":
            try:
                content = render_search_xlsx(results, payload["query"])
            except Exception:
                logger.exception("XLSX-generatie mislukt")
                raise HTTPException(status_code=500, detail="XLSX-generatie mislukt")
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            extension = "xlsx"
        else:
            try:
                content = render_search_pdf(payload)
            except Exception:
                logger.exception("PDF-generatie mislukt")
                raise HTTPException(status_code=500, detail="PDF-generatie mislukt")
            media_type = "application/pdf"
            extension = "pdf"
        filename = f"screening-{now.strftime('%Y-%m-%d')}.{extension}"
        return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/batch")
    async def create_batch(
        request: Request,
        user: dict | None = Depends(get_current_user),
        file: UploadFile = File(...),
    ):
        _check_roles(user, ("admin", "analist"))
        audit_user = user["username"] if user else None
        filename = file.filename or "lijst.csv"
        if file.size is not None and file.size > batch.MAX_BATCH_BYTES:
            raise HTTPException(status_code=413, detail="Bestand is te groot (max 50 MB)")
        content = await file.read(batch.MAX_BATCH_BYTES + 1)
        if len(content) > batch.MAX_BATCH_BYTES:
            raise HTTPException(status_code=413, detail="Bestand is te groot (max 50 MB)")
        try:
            rows, errors = batch.parse_input(filename, content)
        except batch.RowLimitExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except batch.BatchInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        batch_db = batch.default_batch_db()
        job_id = batch.create_job(batch_db, filename, rows, errors=errors)
        _log_batch(request, {"batch_id": job_id, "filename": filename, "rows": len(rows), "errors": len(errors)}, len(rows), user=audit_user)
        threading.Thread(target=batch.process_job, args=(batch_db, job_id, _batch_search_fn), daemon=True).start()
        return {"batch_id": job_id}

    @app.get("/api/batch/{batch_id}")
    def get_batch(batch_id: str, user: dict | None = Depends(get_current_user)):
        _check_roles(user, ("admin", "analist"))
        batch_db = batch.default_batch_db()
        job = batch.get_job(batch_db, batch_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Batch niet gevonden")
        return {
            "status": job["status"],
            "progress": job["progress"],
            "total": job["total"],
            "created_at": job["created_at"],
            "finished_at": job["finished_at"],
            "error_text": job["error_text"],
            "errors": job["errors"],
            "rows": batch.get_results(batch_db, batch_id),
        }

    def _batch_report(batch_id: str, format: str) -> tuple[bytes, str, str]:
        batch_db = batch.default_batch_db()
        job = batch.get_job(batch_db, batch_id)
        if job is None or job["status"] != "done":
            raise HTTPException(status_code=404, detail="Rapport niet beschikbaar")
        results = batch.get_results(batch_db, batch_id)
        if format == "csv":
            content = render_batch_csv(job, results).encode("utf-8-sig")
            media_type = "text/csv; charset=utf-8"
            extension = "csv"
        else:
            content = render_batch_pdf(job, results, state["meta"])
            media_type = "application/pdf"
            extension = "pdf"
        filename = f"batch-rapport-{datetime.now().astimezone().strftime('%Y-%m-%d')}.{extension}"
        return content, media_type, filename

    @app.get("/api/batch/{batch_id}/report.pdf")
    def batch_report_pdf(
        request: Request,
        batch_id: str,
        user: dict | None = Depends(get_current_user),
    ):
        _check_roles(user, ("admin", "analist"))
        try:
            content, media_type, filename = _batch_report(batch_id, "pdf")
        except HTTPException:
            raise
        except Exception:
            logger.exception("PDF-generatie mislukt")
            raise HTTPException(status_code=500, detail="PDF-generatie mislukt")
        audit_user = user["username"] if user else None
        _log_batch(request, {"batch_id": batch_id, "format": "pdf"}, 1, user=audit_user)
        return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/batch/{batch_id}/report.csv")
    def batch_report_csv(
        request: Request,
        batch_id: str,
        user: dict | None = Depends(get_current_user),
    ):
        _check_roles(user, ("admin", "analist"))
        try:
            content, media_type, filename = _batch_report(batch_id, "csv")
        except HTTPException:
            raise
        except Exception:
            logger.exception("CSV-generatie mislukt")
            raise HTTPException(status_code=500, detail="CSV-generatie mislukt")
        audit_user = user["username"] if user else None
        _log_batch(request, {"batch_id": batch_id, "format": "csv"}, 1, user=audit_user)
        return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/watchlists")
    def create_watchlist(
        request: Request,
        response: Response,
        user: dict | None = Depends(get_current_user),
        payload: dict | None = Body(default=None),
    ):
        _check_roles(user, ("admin", "analist", "viewer"))
        owner = watchlist.get_or_create_key(request, response)
        label = ((payload or {}).get("label") or "").strip()
        record = watchlist.add_watchlist(watchlist.default_watchlist_db(), owner, label=label)
        _log_watchlist(request, record["id"], "create", 0, user=user["username"] if user else None)
        return {"watchlist": record}

    @app.get("/api/watchlists")
    def get_watchlists(
        request: Request,
        response: Response,
        user: dict | None = Depends(get_current_user),
    ):
        _check_roles(user, ("admin", "analist", "viewer"))
        owner = watchlist.get_or_create_key(request, response)
        return {"watchlists": watchlist.list_watchlists(watchlist.default_watchlist_db(), owner)}

    @app.delete("/api/watchlists/{watchlist_id}")
    def delete_watchlist(
        request: Request,
        response: Response,
        watchlist_id: str,
        user: dict | None = Depends(get_current_user),
    ):
        _check_roles(user, ("admin", "analist", "viewer"))
        owner = watchlist.get_or_create_key(request, response)
        if not watchlist.delete_watchlist(watchlist.default_watchlist_db(), owner, watchlist_id):
            raise HTTPException(status_code=404, detail="Watchlist niet gevonden")
        _log_watchlist(request, watchlist_id, "delete", 0, user=user["username"] if user else None)
        response.status_code = 204
        return response

    @app.post("/api/watchlists/{watchlist_id}/rescan")
    def rescan_watchlist(
        request: Request,
        response: Response,
        watchlist_id: str,
        user: dict | None = Depends(get_current_user),
        payload: dict | None = Body(default=None),
    ):
        _check_roles(user, ("admin", "analist", "viewer"))
        owner = watchlist.get_or_create_key(request, response)
        data = payload or {}
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Naam is verplicht")
        db_path = watchlist.default_watchlist_db()
        owned = {w["id"] for w in watchlist.list_watchlists(db_path, owner)}
        if watchlist_id not in owned:
            raise HTTPException(status_code=404, detail="Watchlist niet gevonden")
        fields = {}
        birth_year = data.get("birth_year")
        if birth_year is not None and str(birth_year).strip():
            if isinstance(birth_year, bool):
                raise HTTPException(status_code=422, detail="Ongeldig geboortejaar")
            if isinstance(birth_year, (int, float)):
                fields["birth_year"] = int(birth_year)
            else:
                try:
                    fields["birth_year"] = int(str(birth_year).strip())
                except ValueError:
                    raise HTTPException(status_code=422, detail="Ongeldig geboortejaar")
        for key in ("nationality", "birth_place", "entity_type"):
            if data.get(key) is not None:
                fields[key] = data[key]
        result = watchlist.rescan_watch(db_path, owner, watchlist_id, name, fields, _watchlist_search_fn, threshold=matcher.THRESHOLD)
        _log_watchlist(request, watchlist_id, "rescan", result["new"], user=user["username"] if user else None)
        return {"watchlist_id": result["watchlist_id"], "hits": result["hits"], "new": result["new"]}

    @app.get("/api/watchlists/hits")
    def get_watchlist_hits(
        request: Request,
        response: Response,
        user: dict | None = Depends(get_current_user),
        watchlist_id: str | None = Query(None),
    ):
        _check_roles(user, ("admin", "analist", "viewer"))
        owner = watchlist.get_or_create_key(request, response)
        hits = watchlist.list_hits(watchlist.default_watchlist_db(), owner, watchlist_id=watchlist_id)
        return {"hits": hits}

    try:
        batch.mark_stale_jobs(batch.default_batch_db())
    except Exception:
        logger.exception("Startup sweep van verweesde batch-jobs mislukt")

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
