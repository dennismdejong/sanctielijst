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
SCHEMA_VERSION = 3


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
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE _positions (id TEXT PRIMARY KEY, caption TEXT NOT NULL);
CREATE TABLE _occupancies (holder TEXT NOT NULL, post TEXT NOT NULL, status TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL);
CREATE INDEX _occupancies_holder ON _occupancies(holder);
"""


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _schema(db) -> int:
    return db.execute("SELECT count(*) FROM entities").fetchone()[0]


INSERT_SQL = (
    "INSERT INTO entities (source, id, caption, schema, names, names_folded, birth_dates, birth_places, "
    "citizenships, political, topics, positions, datasets, eu_ref, raw) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _entity_row(r: dict) -> tuple:
    names = r["names"]
    return (
        r["source"],
        r["id"],
        r["caption"],
        r["schema"],
        json.dumps(names, ensure_ascii=False),
        " ".join(tokens(" ".join(names))),
        json.dumps(r["birth_dates"], ensure_ascii=False),
        json.dumps(r["birth_places"], ensure_ascii=False),
        json.dumps(r["citizenships"], ensure_ascii=False),
        json.dumps(r["political"], ensure_ascii=False),
        json.dumps(r["topics"], ensure_ascii=False),
        json.dumps(r.get("positions") or [], ensure_ascii=False),
        json.dumps(r["datasets"], ensure_ascii=False),
        r["eu_ref"],
    )


def _insert_eu(db, eu_entities: list[dict]) -> int:
    rows = []
    for r in _eu_records(eu_entities):
        rows.append(_entity_row(r) + (json.dumps(r["raw"], ensure_ascii=False),))
    db.executemany(INSERT_SQL, rows)
    return len(rows)


def _stream_pep(db, pep_root: Path) -> tuple[int, int]:
    pep_count = 0
    sources: set[str] = set()
    pos_buf: list[tuple] = []
    occ_buf: list[tuple] = []
    ent_buf: list[tuple] = []

    def flush() -> None:
        nonlocal pos_buf, occ_buf, ent_buf
        if pos_buf:
            db.executemany("INSERT OR REPLACE INTO _positions (id, caption) VALUES (?,?)", pos_buf)
            pos_buf = []
        if occ_buf:
            db.executemany("INSERT INTO _occupancies (holder, post, status, start, end) VALUES (?,?,?,?,?)", occ_buf)
            occ_buf = []
        if ent_buf:
            db.executemany(INSERT_SQL, ent_buf)
            ent_buf = []

    for ftm in sorted(pep_root.glob(f"*/{FTM_FILENAME}")):
        dataset = ftm.parent.name
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
                    pos_buf.append((data.get("id", ""), data.get("caption") or ""))
                elif schema == "Occupancy":
                    props = data.get("properties") or {}
                    occ_buf.append((
                        (props.get("holder") or [""])[0],
                        (props.get("post") or [""])[0],
                        (props.get("status") or [""])[0],
                        (props.get("startDate") or [""])[0],
                        (props.get("endDate") or [""])[0],
                    ))
                elif schema in ("Person", "Company") and data.get("target"):
                    props = data.get("properties") or {}
                    names = list((props.get("name") or []) + (props.get("alias") or []))
                    caption = data.get("caption") or ""
                    if caption and caption not in names:
                        names.insert(0, caption)
                    folded = " ".join(tokens(" ".join(names)))
                    ent_buf.append((
                        "pep",
                        data.get("id", ""),
                        caption,
                        schema,
                        json.dumps(names, ensure_ascii=False),
                        folded,
                        json.dumps(props.get("birthDate") or [], ensure_ascii=False),
                        json.dumps(props.get("birthPlace") or [], ensure_ascii=False),
                        json.dumps(props.get("citizenship") or [], ensure_ascii=False),
                        json.dumps(props.get("political") or [], ensure_ascii=False),
                        json.dumps(props.get("topics") or [], ensure_ascii=False),
                        "[]",
                        json.dumps(data.get("datasets") or [], ensure_ascii=False),
                        "",
                        None,
                    ))
                    pep_count += 1
                    sources.add(dataset)
                if len(pos_buf) + len(occ_buf) + len(ent_buf) >= 20000:
                    flush()
    flush()
    return pep_count, len(sources)


def _fill_positions(db) -> None:
    db.execute(
        """
        UPDATE entities
        SET positions = COALESCE((
          SELECT json_group_array(json_object(
            'role', COALESCE(p.caption, o.post),
            'status', o.status,
            'start', o.start,
            'end', o.end
          ) ORDER BY o.start DESC)
          FROM _occupancies o LEFT JOIN _positions p ON p.id = o.post
          WHERE o.holder = entities.id
        ), '[]')
        WHERE source = 'pep'
        """
    )


def _fill_fts(db) -> None:
    db.execute("INSERT INTO names_fts (rowid, names_folded) SELECT rowid, names_folded FROM entities")


def _write_meta(db, eu_count: int, pep_count: int, source_count: int, newest_input_mtime: float) -> None:
    db.executemany(
        "INSERT INTO meta (key, value) VALUES (?,?)",
        [
            ("eu_count", str(eu_count)),
            ("pep_count", str(pep_count)),
            ("source_count", str(source_count)),
            ("newest_input_mtime", str(newest_input_mtime)),
        ],
    )


def build_index(db_path: Path, eu_entities: list[dict] | None, pep_root: Path, *, newest_input_mtime: float | None = None) -> dict:
    eu_entities = eu_entities or []
    if newest_input_mtime is None:
        newest_input_mtime = _newest_pep_mtime(pep_root)
    new_path = db_path.with_suffix(db_path.suffix + ".new")
    new_path.unlink(missing_ok=True)
    db = None
    try:
        db = _open(new_path)
        db.execute("PRAGMA page_size = 4096")
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.execute("PRAGMA cache_size = -64000")
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        eu_count = _insert_eu(db, eu_entities)
        pep_count, source_count = _stream_pep(db, pep_root)
        _fill_positions(db)
        _fill_fts(db)
        _write_meta(db, eu_count, pep_count, source_count, newest_input_mtime)
        db.execute("DROP TABLE _occupancies")
        db.execute("DROP TABLE _positions")
        db.commit()
        db.close()
        db = None
        new_path.replace(db_path)
    finally:
        if db is not None:
            db.close()
        new_path.unlink(missing_ok=True)
    return {"eu_count": eu_count, "pep_count": pep_count, "total": eu_count + pep_count, "source_count": source_count}


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


def _newest_pep_mtime(pep_root: Path) -> float:
    newest = 0.0
    datasets = pep_root / "datasets.json"
    if datasets.exists():
        newest = max(newest, datasets.stat().st_mtime)
    for ftm in pep_root.glob(f"*/{FTM_FILENAME}"):
        newest = max(newest, ftm.stat().st_mtime)
    return newest


def _newest_input_mtime(eu_xml: Path, pep_root: Path) -> float:
    newest = _newest_pep_mtime(pep_root)
    if eu_xml.exists():
        newest = max(newest, eu_xml.stat().st_mtime)
    return newest


def index_fresh(db_path: Path, eu_xml: Path, pep_root: Path) -> bool:
    if not db_path.exists():
        return False
    db = None
    try:
        db = _open(db_path)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute("SELECT value FROM meta WHERE key = 'newest_input_mtime'").fetchone()
        acknowledged = float(row[0]) if row is not None else db_path.stat().st_mtime
    except Exception:
        return False
    finally:
        if db is not None:
            db.close()
    if acknowledged < _newest_input_mtime(eu_xml, pep_root):
        return False
    return version >= SCHEMA_VERSION


def load_stats(db) -> dict:
    row = dict(db.execute("SELECT key, value FROM meta").fetchall())
    eu = int(row.get("eu_count", 0))
    pep = int(row.get("pep_count", 0))
    return {
        "eu_count": eu,
        "pep_count": pep,
        "total": eu + pep,
        "source_count": int(row.get("source_count", 0)),
    }


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
    return build_index(db_path, entities, pep_root, newest_input_mtime=_newest_input_mtime(eu_xml, pep_root))
