import os
from pathlib import Path

import requests

INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"
RESOURCE_NAME = "entities.ftm.json"
TIMEOUT = 120
DOWNLOAD_PAUSE = 0.5
MANIFEST_FILENAME = "manifest.json"
PEP_COLLECTION = "peps"


def default_root() -> Path:
    return Path(os.environ.get("PEP_DATA_DIR", "data/pep"))


def fetch_index(url: str = INDEX_URL, timeout: int = TIMEOUT) -> dict:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_pep_datasets(index: dict) -> list[dict]:
    raw = index.get("datasets") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    result = []
    for ds in raw:
        if not isinstance(ds, dict):
            continue
        if PEP_COLLECTION not in (ds.get("collections") or []):
            continue
        if ds.get("type") != "source":
            continue
        resource = next(
            (r for r in (ds.get("resources") or []) if r.get("name") == RESOURCE_NAME),
            None,
        )
        if resource is None:
            continue
        result.append({
            "name": ds["name"],
            "version": ds.get("version", ""),
            "resource": resource,
        })
    result.sort(key=lambda d: d["name"])
    return result
