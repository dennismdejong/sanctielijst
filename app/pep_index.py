import json
import os
import pickle
import re
from datetime import datetime, timezone
from pathlib import Path

ENTITIES_FILENAME = "entities.ftm.json"
PEP_INDEX_FILENAME = "index.pkl"
DATASETS_FILENAME = "datasets.json"
INDEX_ENV = "PEP_INDEX_ENABLED"


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2]


def _extract_entity(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not data.get("target"):
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


def build_index(root_dir: Path) -> dict:
    entities = []
    token_map = {}
    datasets = {}
    skipped_lines = 0
    for ftm in sorted(root_dir.glob(f"*/{ENTITIES_FILENAME}")):
        with ftm.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entity = _extract_entity(line)
                if entity is None:
                    skipped_lines += 1
                    continue
                idx = len(entities)
                entities.append(entity)
                seen = set()
                for name in entity["names"]:
                    for token in _tokens(name):
                        if token not in seen:
                            seen.add(token)
                            token_map.setdefault(token, []).append(idx)
                for ds in entity["datasets"]:
                    datasets[ds] = datasets.get(ds, 0) + 1
    return {
        "entities": entities,
        "token_map": token_map,
        "datasets": datasets,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "skipped_lines": skipped_lines,
    }


def _newest_input_mtime(root_dir: Path) -> float:
    newest = 0.0
    for pattern in (f"*/{ENTITIES_FILENAME}", DATASETS_FILENAME):
        for path in root_dir.glob(pattern):
            newest = max(newest, path.stat().st_mtime)
    return newest


def save_index(root_dir: Path, index: dict) -> None:
    (root_dir / PEP_INDEX_FILENAME).write_bytes(pickle.dumps(index))


def load_index_cache(root_dir: Path) -> dict | None:
    pkl = root_dir / PEP_INDEX_FILENAME
    if not pkl.exists():
        return None
    if pkl.stat().st_mtime < _newest_input_mtime(root_dir):
        return None
    try:
        with pkl.open("rb") as fh:
            index = pickle.load(fh)
    except Exception:
        return None
    index["source"] = "cached"
    return index


def _load_datasets_meta(root_dir: Path) -> dict:
    path = root_dir / DATASETS_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_or_build_index(root_dir: Path, force: bool = False) -> dict | None:
    if not root_dir.exists():
        return None
    if not any(root_dir.glob(f"*/{ENTITIES_FILENAME}")):
        return None
    if not force:
        cached = load_index_cache(root_dir)
        if cached is not None:
            cached["datasets_meta"] = _load_datasets_meta(root_dir)
            return cached
    index = build_index(root_dir)
    index["source"] = "built"
    index["datasets_meta"] = _load_datasets_meta(root_dir)
    save_index(root_dir, index)
    return index


from rapidfuzz import fuzz

THRESHOLD = 60
MAX_RESULTS = 20


def _birth_year(value: str) -> int | None:
    match = re.match(r"(\d{4})", value)
    return int(match.group(1)) if match else None


def _name_score(names: list[str], query: str) -> tuple[int, str | None]:
    best = 0
    best_name = None
    q = query.strip()
    for name in names:
        if not name:
            continue
        score = fuzz.token_set_ratio(q, name)
        if score > best:
            best = score
            best_name = name
    return best, best_name


def search_pep(
    index: dict,
    name: str,
    birth_year: int | None = None,
    nationality: str | None = None,
    birth_place: str | None = None,
    entity_type: str | None = None,
    threshold: int = THRESHOLD,
    max_results: int = MAX_RESULTS,
) -> list[dict]:
    token_map = index.get("token_map", {})
    entities = index.get("entities", [])
    candidates = set()
    for token in _tokens(name):
        candidates.update(token_map.get(token, []))
    results = []
    for idx in candidates:
        entity = entities[idx]
        if entity_type == "person" and entity["schema"] != "Person":
            continue
        if entity_type == "enterprise" and entity["schema"] != "Company":
            continue
        n_score, matched = _name_score(entity["names"], name)
        weights = [60]
        details = [{
            "feature": "naam",
            "score": n_score,
            "label": f'Naam {n_score}% (via "{matched}")' if matched else "Naam 0%",
        }]
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
            details.append({
                "feature": "geboortejaar",
                "score": best,
                "label": "Geboortejaar exact" if best == 100 else f"Geboortejaar ({best}%)",
            })
        if nationality:
            q = nationality.strip().upper()
            best = max((100 for c in entity["citizenships"] if c.strip().upper() == q), default=0)
            weights.append(10)
            details.append({
                "feature": "nationaliteit",
                "score": best,
                "label": "Nationaliteit match" if best >= 85 else f"Nationaliteit ({best}%)",
            })
        if birth_place:
            best = max((fuzz.token_set_ratio(birth_place.strip(), p) for p in entity["birth_places"]), default=0)
            weights.append(10)
            details.append({"feature": "geboorteplaats", "score": best, "label": f"Geboorteplaats {best}%"})
        total = round(sum(w * d["score"] for w, d in zip(weights, details)) / sum(weights))
        if total < threshold:
            continue
        results.append({"entity": entity, "score": total, "matched_name": matched, "details": details})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]
