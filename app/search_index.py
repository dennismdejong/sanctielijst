import dataclasses
import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz

THRESHOLD = 90
MAX_RESULTS = 20
INDEX_ENV = "PEP_INDEX_ENABLED"
DB_FILENAME = "search.sqlite"
FTM_FILENAME = "entities.ftm.json"
SCHEMA_VERSION = 2


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


def _positions_by_holder(pep_root: Path) -> dict[str, list[dict]]:
    positions = {}
    occupancies = []
    for ftm in sorted(pep_root.glob(f"*/{FTM_FILENAME}")):
        with ftm.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                schema = data.get("schema")
                if schema == "Position":
                    positions[data.get("id", "")] = data.get("caption") or ""
                elif schema == "Occupancy":
                    occupancies.append(data)
    by_holder: dict[str, list[dict]] = {}
    for occ in occupancies:
        props = occ.get("properties") or {}
        for holder in props.get("holder") or []:
            for post in props.get("post") or []:
                by_holder.setdefault(holder, []).append({
                    "role": positions.get(post) or post,
                    "status": (props.get("status") or [""])[0],
                    "start": (props.get("startDate") or [""])[0],
                    "end": (props.get("endDate") or [""])[0],
                })
    for entries in by_holder.values():
        entries.sort(key=lambda p: p["start"], reverse=True)
    return by_holder


def _pep_records(pep_root: Path, positions_map: dict[str, list[dict]] | None = None) -> list[dict]:
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
                    "positions": list(positions_map.get(entity["id"], [])) if positions_map else [],
                })
    return records


SCHEMA = """
CREATE TABLE entities (
  rowid INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  id TEXT NOT NULL,
  caption TEXT NOT NULL,
  schema TEXT NOT NULL,
  names TEXT NOT NULL,
  names_folded TEXT NOT NULL,
  birth_dates TEXT NOT NULL,
  birth_places TEXT NOT NULL,
  citizenships TEXT NOT NULL,
  political TEXT NOT NULL,
  topics TEXT NOT NULL,
  positions TEXT NOT NULL DEFAULT '[]',
  datasets TEXT NOT NULL,
  eu_ref TEXT,
  raw TEXT
);
CREATE VIRTUAL TABLE names_fts USING fts5(names_folded, content='entities', content_rowid='rowid');
"""


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _schema(db) -> int:
    return db.execute("SELECT count(*) FROM entities").fetchone()[0]


def build_index(db_path: Path, eu_entities: list[dict] | None, pep_root: Path) -> dict:
    eu_entities = eu_entities or []
    positions_map = _positions_by_holder(pep_root)
    records = _eu_records(eu_entities) + _pep_records(pep_root, positions_map)
    new_path = db_path.with_suffix(db_path.suffix + ".new")
    new_path.unlink(missing_ok=True)
    db = None
    try:
        db = _open(new_path)
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.executemany(
            "INSERT INTO entities (source, id, caption, schema, names, names_folded, birth_dates, birth_places, citizenships, political, topics, positions, datasets, eu_ref, raw) "
            "VALUES (:source, :id, :caption, :schema, :names, :names_folded, :birth_dates, :birth_places, :citizenships, :political, :topics, :positions, :datasets, :eu_ref, :raw)",
            [{
                "source": r["source"],
                "id": r["id"],
                "caption": r["caption"],
                "schema": r["schema"],
                "names": json.dumps(r["names"], ensure_ascii=False),
                "names_folded": " ".join(tokens(" ".join(r["names"]))),
                "birth_dates": json.dumps(r["birth_dates"], ensure_ascii=False),
                "birth_places": json.dumps(r["birth_places"], ensure_ascii=False),
                "citizenships": json.dumps(r["citizenships"], ensure_ascii=False),
                "political": json.dumps(r["political"], ensure_ascii=False),
                "topics": json.dumps(r["topics"], ensure_ascii=False),
                "positions": json.dumps(r.get("positions") or [], ensure_ascii=False),
                "datasets": json.dumps(r["datasets"], ensure_ascii=False),
                "eu_ref": r["eu_ref"],
                "raw": json.dumps(r["raw"], ensure_ascii=False) if r["raw"] is not None else None,
            } for r in records],
        )
        for idx, r in enumerate(records):
            names_folded = " ".join(tokens(" ".join(r["names"])))
            db.execute("INSERT INTO names_fts (rowid, names_folded) VALUES (?, ?)", (idx + 1, names_folded))
        db.commit()
        db.close()
        db = None
        new_path.replace(db_path)
    finally:
        if db is not None:
            db.close()
        new_path.unlink(missing_ok=True)
    counts = {"eu_count": sum(1 for r in records if r["source"] == "eu"), "pep_count": sum(1 for r in records if r["source"] == "pep")}
    counts["total"] = counts["eu_count"] + counts["pep_count"]
    return counts


def _birth_year(value: str) -> int | None:
    match = re.match(r"(\d{4})", value)
    return int(match.group(1)) if match else None


def _name_score(names: list[str], query: str) -> tuple[int, str | None]:
    best = 0
    best_name = None
    q = fold(query).strip()
    q_tokens = set(tokens(q))
    for name in names:
        if not name:
            continue
        c_tokens = set(tokens(name))
        if q_tokens and c_tokens and q_tokens <= c_tokens:
            score = 100
        else:
            score = fuzz.token_set_ratio(q, fold(name))
        if score > best:
            best = score
            best_name = name
    return best, best_name


def search(db, name, birth_year=None, nationality=None, birth_place=None, entity_type=None, threshold=THRESHOLD, max_results=MAX_RESULTS):
    query_tokens = tokens(name)
    if not query_tokens:
        return []
    match_expr = " OR ".join(f'"{t}"' for t in query_tokens)
    rows = db.execute(
        "SELECT e.rowid, e.source, e.id, e.caption, e.schema, e.names, e.birth_dates, e.birth_places, e.citizenships, e.political, e.topics, e.positions, e.datasets, e.eu_ref, e.raw "
        "FROM names_fts JOIN entities e ON e.rowid = names_fts.rowid WHERE names_fts MATCH ?",
        (match_expr,),
    ).fetchall()
    results = []
    for row in rows:
        entity = {
            "source": row["source"],
            "id": row["id"],
            "caption": row["caption"],
            "schema": row["schema"],
            "names": json.loads(row["names"]),
            "birth_dates": json.loads(row["birth_dates"]),
            "birth_places": json.loads(row["birth_places"]),
            "citizenships": json.loads(row["citizenships"]),
            "political": json.loads(row["political"]),
            "topics": json.loads(row["topics"]),
            "positions": json.loads(row["positions"]),
            "datasets": json.loads(row["datasets"]),
            "eu_ref": row["eu_ref"],
            "raw": json.loads(row["raw"]) if row["raw"] else None,
        }
        if entity_type == "person" and entity["schema"] != "Person":
            continue
        if entity_type == "enterprise" and entity["schema"] != "Company":
            continue
        if entity["source"] == "eu" and entity["raw"] is not None:
            eu_result = matcher.score_entity(
                entity["raw"],
                matcher.SearchQuery(name, birth_year, nationality, birth_place, entity_type),
            )
            if eu_result is None:
                continue
            results.append({
                "entity": entity,
                "score": eu_result.total_score,
                "matched_name": eu_result.matched_alias,
                "details": [dataclasses.asdict(d) for d in eu_result.details],
            })
            continue
        n_score, matched = _name_score(entity["names"], name)
        weights = [60]
        details = [{"feature": "naam", "score": n_score, "label": f'Naam {n_score}% (via "{matched}")' if matched else "Naam 0%"}]
        if birth_year is not None:
            best = 0
            for date in entity["birth_dates"]:
                year = _birth_year(date)
                if year is None:
                    continue
                diff = abs(birth_year - year)
                score = 100 if diff == 0 else 75 if diff == 1 else 50 if diff == 2 else 0
                best = max(best, score)
            weights.append(20)
            details.append({"feature": "geboortejaar", "score": best, "label": "Geboortejaar exact" if best == 100 else f"Geboortejaar ({best}%)"})
        if nationality:
            q = nationality.strip().upper()
            best = max((100 for c in entity["citizenships"] if c.strip().upper() == q), default=0)
            weights.append(10)
            details.append({"feature": "nationaliteit", "score": best, "label": "Nationaliteit match" if best >= 85 else f"Nationaliteit ({best}%)"})
        if birth_place:
            best = max((fuzz.token_set_ratio(birth_place.strip(), fold(p)) for p in entity["birth_places"]), default=0)
            weights.append(10)
            details.append({"feature": "geboorteplaats", "score": best, "label": f"Geboorteplaats {best}%"})
        total = round(sum(w * d["score"] for w, d in zip(weights, details)) / sum(weights))
        if total < threshold:
            continue
        results.append({"entity": entity, "score": total, "matched_name": matched, "details": details})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


from . import ingest
from . import matcher


def _newest_input_mtime(eu_xml: Path, pep_root: Path) -> float:
    newest = 0.0
    for path in [eu_xml, pep_root / "datasets.json"]:
        if path.exists():
            newest = max(newest, path.stat().st_mtime)
    for ftm in pep_root.glob(f"*/{FTM_FILENAME}"):
        newest = max(newest, ftm.stat().st_mtime)
    return newest


def index_fresh(db_path: Path, eu_xml: Path, pep_root: Path) -> bool:
    if not db_path.exists():
        return False
    if db_path.stat().st_mtime < _newest_input_mtime(eu_xml, pep_root):
        return False
    db = None
    try:
        db = _open(db_path)
        version = db.execute("PRAGMA user_version").fetchone()[0]
    except Exception:
        return False
    finally:
        if db is not None:
            db.close()
    return version >= SCHEMA_VERSION


def load_stats(db) -> dict:
    eu = db.execute("SELECT count(*) FROM entities WHERE source = 'eu'").fetchone()[0]
    pep = db.execute("SELECT count(*) FROM entities WHERE source = 'pep'").fetchone()[0]
    sources = db.execute("SELECT count(DISTINCT json_each.value) FROM entities, json_each(datasets) WHERE source = 'pep'").fetchone()[0]
    return {"eu_count": eu, "pep_count": pep, "total": eu + pep, "source_count": sources}


def ensure_index(db_path: Path, eu_xml: Path, pep_root: Path) -> dict:
    if not index_fresh(db_path, eu_xml, pep_root):
        return {"db": None, "ready": False, "stats": None}
    db = None
    try:
        db = _open(db_path)
        stats = load_stats(db)
    except Exception:
        if db is not None:
            db.close()
        return {"db": None, "ready": False, "stats": None}
    return {"db": db, "ready": True, "stats": stats}


def rebuild_index(db_path: Path, eu_xml: Path, pep_root: Path) -> dict:
    entities = ingest.parse_export(eu_xml.read_bytes()) if eu_xml.exists() else []
    return build_index(db_path, entities, pep_root)
