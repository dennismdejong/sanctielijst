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
        name = ds.get("name")
        if not name:
            continue
        result.append({
            "name": name,
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


import json
from datetime import datetime, timezone
from typing import Callable


def load_pep_manifest(root_dir: Path) -> dict:
    manifest_path = root_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _source_entry(version: str, resource: dict, status: str, error: str = "") -> dict:
    entry = {
        "version": version,
        "checksum": resource.get("checksum", ""),
        "size": resource.get("size", 0),
        "downloaded_at": None,
        "status": status,
    }
    if error:
        entry["error"] = error
    return entry


def refresh_pep(
    root_dir: Path,
    index: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    logger: Callable[[str], None] | None = None,
) -> dict:
    if index is None:
        index = fetch_index()
    datasets = list_pep_datasets(index)
    if limit is not None:
        datasets = datasets[:limit]
    manifest = load_pep_manifest(root_dir)
    sources = dict(manifest.get("sources", {}))
    stats = {"total": len(datasets), "downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0}
    for ds in datasets:
        name = ds["name"]
        resource = ds["resource"]
        version = ds.get("version", "")
        entry = sources.get(name, {})
        dest = root_dir / name / RESOURCE_NAME
        skip = (
            not force
            and entry.get("version") == version
            and entry.get("status") == "ok"
            and entry.get("checksum") == resource.get("checksum")
            and dest.exists()
        )
        if skip:
            sources[name] = dict(entry)
            stats["skipped"] += 1
            if logger:
                logger(f"{name}: overgeslagen (ongewijzigd)")
            continue
        if dry_run:
            sources[name] = _source_entry(version, resource, "pending")
            stats["downloaded"] += 1
            if logger:
                logger(f"{name}: zou downloaden")
            continue
        try:
            download_artifact(resource["url"], dest, resource.get("checksum", ""))
            entry = _source_entry(version, resource, "ok")
            entry["downloaded_at"] = datetime.now(timezone.utc).isoformat()
            sources[name] = entry
            stats["downloaded"] += 1
            stats["bytes"] += resource.get("size", 0)
            if logger:
                logger(f"{name}: gedownload")
        except Exception as exc:
            sources[name] = _source_entry(version, resource, "error", str(exc))
            stats["failed"] += 1
            if logger:
                logger(f"{name}: fout ({exc})")
        if not dry_run:
            time.sleep(DOWNLOAD_PAUSE)
    result = {"updated_at": datetime.now(timezone.utc).isoformat(), "sources": sources, "stats": stats}
    if not dry_run:
        root_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = root_dir / MANIFEST_FILENAME
        tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        tmp.write_text(json.dumps(result, indent=2))
        os.replace(tmp, manifest_path)
        write_datasets_meta(index, root_dir)
    return result


def write_datasets_meta(index: dict, root_dir: Path) -> None:
    raw = index.get("datasets") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    meta = {}
    for ds in raw:
        if not isinstance(ds, dict):
            continue
        if PEP_COLLECTION not in (ds.get("collections") or []):
            continue
        name = ds.get("name")
        if not name:
            continue
        pub = ds.get("publisher") or {}
        meta[name] = {
            "title": ds.get("title", ""),
            "publisher": pub.get("name", ""),
            "country": pub.get("country", ""),
            "official": bool(pub.get("official")),
            "url": ds.get("url", ""),
        }
    root_dir.mkdir(parents=True, exist_ok=True)
    path = root_dir / "datasets.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
