import hashlib
import os
import time
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


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_artifact(url: str, dest: Path, checksum: str, timeout: int = TIMEOUT, retries: int = 1) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    last_error = None
    for attempt in range(retries + 1):
        try:
            part.unlink(missing_ok=True)
            with requests.get(url, timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
            if _sha1(part) != checksum:
                raise ValueError(f"checksum mismatch voor {dest.name}")
            part.replace(dest)
            return
        except Exception as exc:
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(DOWNLOAD_PAUSE)
    raise last_error
