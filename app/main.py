import dataclasses
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import ingest, matcher, opensanctions

load_dotenv()

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"


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


def create_app(
    entities: list[dict] | None = None,
    os_api_key: str | None = None,
    cache_dir: Path = CACHE_DIR,
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    if entities is None:
        entities, meta = ingest.load_index(cache_dir)
    else:
        meta = {}
    if os_api_key is None:
        os_api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    state = {"entities": entities, "meta": meta}
    opensanctions_active = bool(os_api_key)

    app = FastAPI(title="Sanctielijst Zoeker")

    @app.get("/")
    def index():
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    def _status() -> dict:
        cached_at = state["meta"].get("cached_at")
        age_hours = round((time.time() - cached_at) / 3600, 1) if cached_at else None
        return {
            "cached_at": cached_at,
            "generated_at": state["meta"].get("generated_at"),
            "entity_count": len(state["entities"]),
            "data_age_hours": age_hours,
            "opensanctions_active": opensanctions_active,
            "source": state["meta"].get("source", "unknown"),
        }

    @app.get("/api/status")
    def status():
        return _status()

    @app.post("/api/refresh")
    def refresh():
        try:
            meta = ingest.refresh(cache_dir)
            meta["source"] = "fresh"
            state["entities"] = ingest.parse_export((cache_dir / ingest.XML_FILENAME).read_bytes())
            state["meta"] = meta
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
