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
