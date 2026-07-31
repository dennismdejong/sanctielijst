import dataclasses
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
from . import pep_index

load_dotenv()

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def default_pep_root() -> Path:
    return Path(os.environ.get("PEP_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "pep")))


PEP_ROOT = default_pep_root()


def default_eu_root() -> Path:
    return Path(os.environ.get("EU_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "eu")))


EU_ROOT = default_eu_root()


def _data_age_hours(downloaded_at: str | None) -> float | None:
    if not downloaded_at:
        return None
    try:
        parsed = datetime.fromisoformat(downloaded_at)
    except ValueError:
        return None
    return round((datetime.now(timezone.utc) - parsed).total_seconds() / 3600, 1)

def _serialize_eu_result(result: matcher.EuMatchResult, query_name: str) -> dict:
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
    env = os.environ.get(pep_index.INDEX_ENV)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no")
    return pep_root.exists()


def _serialize_pep_result(result: dict, index: dict) -> dict:
    entity = result["entity"]
    ds_meta = index.get("datasets_meta", {})
    datasets = []
    for ds_id in entity["datasets"]:
        meta = ds_meta.get(ds_id, {})
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
            "name": entity["caption"],
            "schema": entity["schema"],
            "birth_dates": entity["birth_dates"],
            "birth_places": entity["birth_places"],
            "citizenships": entity["citizenships"],
            "political": entity["political"],
            "topics": entity["topics"],
        },
        "pep": {
            "id": entity["id"],
            "url": f"https://opensanctions.org/entities/{entity['id']}",
            "datasets": datasets,
            "matched_name": result["matched_name"],
            "details": result["details"],
        },
        "eu": None,
        "opensanctions": None,
    }


def _load_pep_index(state: dict, pep_root: Path) -> None:
    try:
        state["pep"] = pep_index.load_or_build_index(pep_root)
    except Exception:
        logger.exception("PEP-index laden mislukt")
        state["pep"] = None
    finally:
        state["pep_loading"] = False


def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    eu_root: Path = EU_ROOT,
    static_dir: Path = STATIC_DIR,
    pep_root: Path = PEP_ROOT,
    pep_sync: bool | None = None,
) -> FastAPI:
    meta = eu_ingest.load_eu_manifest(eu_root)
    if entities is None:
        xml_path = eu_root / eu_ingest.XML_FILENAME
        if xml_path.exists():
            entities = ingest.parse_export(xml_path.read_bytes())
            meta.setdefault("status", "ok")
        else:
            entities = []
            meta.setdefault("status", "missing")
    if os_api_key is None:
        os_api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    if pep_sync is None:
        pep_sync = os.environ.get("PEP_INDEX_SYNC", "").strip().lower() in ("1", "true", "yes")
    state = {"entities": entities, "meta": meta, "pep": None, "pep_loading": False}
    if _pep_enabled(pep_root):
        if pep_sync:
            state["pep"] = pep_index.load_or_build_index(pep_root)
        else:
            state["pep_loading"] = True
            threading.Thread(target=_load_pep_index, args=(state, pep_root), daemon=True).start()
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
        pep = state["pep"]
        pep_status = "loading" if state["pep_loading"] else ("ready" if pep is not None else "disabled")
        return {
            "version": os.environ.get("APP_VERSION", "dev"),
            "cached_at": meta.get("downloaded_at"),
            "generated_at": meta.get("generation_date"),
            "entity_count": len(state["entities"]),
            "data_age_hours": _data_age_hours(meta.get("downloaded_at")),
            "opensanctions_active": opensanctions_active,
            "source": meta.get("status", "unknown"),
            "pep_index": {
                "enabled": pep is not None or state["pep_loading"],
                "entity_count": len(pep.get("entities", [])) if pep else 0,
                "datasets_count": len(pep.get("datasets", {})) if pep else 0,
                "source": pep.get("source") if pep else None,
                "status": pep_status,
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
            xml_path = eu_root / eu_ingest.XML_FILENAME
            if xml_path.exists():
                state["entities"] = ingest.parse_export(xml_path.read_bytes())
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
        for r in matcher.search_eu(state["entities"], query):
            results.append(_serialize_eu_result(r, query.name))
        if state["pep"] is not None:
            for r in pep_index.search_pep(
                state["pep"],
                query.name,
                query.birth_year,
                query.nationality,
                query.birth_place,
                query.entity_type,
            ):
                results.append(_serialize_pep_result(r, state["pep"]))
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
