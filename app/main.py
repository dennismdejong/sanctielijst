import dataclasses
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import eu_ingest, ingest, matcher, opensanctions
from . import search_index

load_dotenv()

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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


def _load_datasets_meta(pep_root: Path) -> dict:
    path = pep_root / "datasets.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _build_index(state: dict, db_path: Path, eu_xml: Path, pep_root: Path) -> None:
    try:
        search_index.rebuild_index(db_path, eu_xml, pep_root)
        db = search_index._open(db_path)
        try:
            state["index_stats"] = search_index.load_stats(db)
        finally:
            db.close()
        state["index_status"] = "ready"
        state["index_error"] = None
    except Exception:
        logger.exception("Index-rebuild mislukt")
        state["index_status"] = "error"
        state["index_error"] = "Index-rebuild mislukt"


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
    enabled = _pep_enabled(pep_root) or eu_xml.exists()
    state = {"db_path": db_path, "index_status": "disabled", "index_stats": None, "index_error": None, "entities": entities, "meta": meta}
    datasets_meta = _load_datasets_meta(pep_root)
    if enabled:
        result = search_index.ensure_index(db_path, eu_xml, pep_root)
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

    app = FastAPI(title="Compliance Zoeker")

    @app.get("/")
    def index():
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    def _status() -> dict:
        meta = state["meta"]
        stats = state["index_stats"] or {}
        return {
            "version": os.environ.get("APP_VERSION", "dev"),
            "cached_at": meta.get("downloaded_at"),
            "generated_at": meta.get("generation_date"),
            "entity_count": stats.get("total", len(state["entities"])),
            "data_age_hours": _data_age_hours(meta.get("downloaded_at")),
            "opensanctions_active": opensanctions_active,
            "source": meta.get("status", "unknown"),
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
            if state["index_status"] != "disabled":
                state["index_status"] = "building"
                threading.Thread(target=_build_index, args=(state, state["db_path"], eu_xml, pep_root), daemon=True).start()
            return _status()
        except Exception:
            logger.exception("Verversen mislukt")
            raise HTTPException(status_code=503, detail="Verversen mislukt")

    @app.get("/api/search")
    def search(
        name: str = Query(..., min_length=1),
        birth_year: int | None = Query(None, ge=1900, le=2100),
        nationality: str | None = None,
        birth_place: str | None = None,
        entity_type: str | None = Query(None, pattern="^(person|enterprise)$"),
    ):
        query = matcher.SearchQuery(
            name=name.strip(),
            birth_year=birth_year,
            nationality=(nationality or "").strip() or None,
            birth_place=(birth_place or "").strip() or None,
            entity_type=entity_type,
        )
        if not query.name:
            raise HTTPException(status_code=422, detail="Naam is verplicht")
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
            warnings.append("Zoekindex wordt opgebouwd; probeer het zo nog eens")
        else:
            for r in matcher.search_eu(state["entities"], query):
                results.append(_serialize_eu_result_from_dict(r, query.name))
        if opensanctions_active:
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

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
