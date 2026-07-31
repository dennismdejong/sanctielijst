import json
import os
import re
import unicodedata
from pathlib import Path

THRESHOLD = 90
MAX_RESULTS = 20
INDEX_ENV = "PEP_INDEX_ENABLED"
DB_FILENAME = "search.sqlite"
FTM_FILENAME = "entities.ftm.json"


def default_db_path() -> Path:
    env = os.environ.get("SEARCH_DB")
    if env:
        return Path(env)
    return Path(os.environ.get("SEARCH_DATA_DIR", "data")) / DB_FILENAME


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", fold(text)) if len(t) >= 2]


def _eu_records(entities: list[dict]) -> list[dict]:
    records = []
    for e in entities:
        names = [a["whole_name"] for a in e["aliases"] if a["whole_name"]]
        caption = names[0] if names else e.get("logical_id", "")
        records.append({
            "source": "eu",
            "id": e.get("logical_id", ""),
            "caption": caption,
            "schema": "Company" if e.get("subject_type") == "enterprise" else "Person",
            "names": names,
            "birth_dates": [b["date"] for b in e.get("birthdates", []) if b.get("date")],
            "birth_places": [b.get("place") or b.get("city") for b in e.get("birthdates", []) if b.get("place") or b.get("city")],
            "citizenships": [c["iso2"] for c in e.get("citizenships", []) if c.get("iso2")],
            "political": [],
            "topics": [],
            "datasets": [],
            "eu_ref": e.get("eu_reference_number", ""),
            "raw": e,
        })
    return records


def _extract_entity(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("target"):
        return None
    schema = data.get("schema")
    if schema not in ("Person", "Company"):
        return None
    props = data.get("properties") or {}
    names = list((props.get("name") or []) + (props.get("alias") or []))
    caption = data.get("caption") or ""
    if caption and caption not in names:
        names.insert(0, caption)
    return {
        "id": data.get("id", ""),
        "caption": caption,
        "schema": schema,
        "datasets": data.get("datasets") or [],
        "names": names,
        "birth_dates": props.get("birthDate") or [],
        "birth_places": props.get("birthPlace") or [],
        "citizenships": props.get("citizenship") or [],
        "political": props.get("political") or [],
        "topics": props.get("topics") or [],
    }


def _pep_records(pep_root: Path) -> list[dict]:
    records = []
    for ftm in sorted(pep_root.glob(f"*/{FTM_FILENAME}")):
        with ftm.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entity = _extract_entity(line)
                if entity is None:
                    continue
                records.append({
                    "source": "pep",
                    "id": entity["id"],
                    "caption": entity["caption"],
                    "schema": entity["schema"],
                    "names": entity["names"],
                    "birth_dates": entity["birth_dates"],
                    "birth_places": entity["birth_places"],
                    "citizenships": entity["citizenships"],
                    "political": entity["political"],
                    "topics": entity["topics"],
                    "datasets": entity["datasets"],
                    "eu_ref": "",
                    "raw": None,
                })
    return records
