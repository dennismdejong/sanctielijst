import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from .ingest import _read_generation_date, parse_export

EU_XML_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
XML_FILENAME = "eu_sanctions.xml"
MANIFEST_FILENAME = "manifest.json"
TIMEOUT = 120
DOWNLOAD_PAUSE = 0.5


def default_root() -> Path:
    return Path(os.environ.get("EU_DATA_DIR", "data/eu"))


def fetch_headers(url: str = EU_XML_URL, timeout: int = TIMEOUT) -> dict:
    resp = requests.head(url, timeout=timeout)
    resp.raise_for_status()
    headers = resp.headers
    return {
        "last_modified": headers.get("Last-Modified", ""),
        "content_length": headers.get("Content-Length", ""),
        "content_disposition": headers.get("Content-Disposition", ""),
    }


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_xml(data: bytes) -> None:
    parse_export(data)


def download_xml(
    url: str,
    dest: Path,
    timeout: int = TIMEOUT,
    retries: int = 1,
    validate: Callable[[bytes], None] | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.parent / f"{dest.name}.{uuid.uuid4().hex}.part"
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
            if validate is not None:
                validate(part.read_bytes())
            part.replace(dest)
            return
        except Exception as exc:
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(DOWNLOAD_PAUSE)
    raise last_error


def load_eu_manifest(root_dir: Path) -> dict:
    manifest_path = root_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(root_dir: Path, manifest: dict) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root_dir / MANIFEST_FILENAME
    tmp = manifest_path.with_suffix(manifest_path.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, manifest_path)


def refresh_eu(
    root_dir: Path,
    force: bool = False,
    dry_run: bool = False,
    logger: Callable[[str], None] | None = None,
) -> dict:
    headers = fetch_headers()
    last_modified = headers["last_modified"]
    dest = root_dir / XML_FILENAME
    manifest = load_eu_manifest(root_dir)
    skip = (
        not force
        and manifest.get("last_modified") == last_modified
        and manifest.get("status") == "ok"
        and dest.exists()
    )
    if skip:
        result = dict(manifest)
        result["updated_at"] = datetime.now(timezone.utc).isoformat()
        result["stats"] = {"downloaded": 0, "skipped": 1, "failed": 0}
        if logger:
            logger("EU-lijst: overgeslagen (ongewijzigd)")
        _write_manifest(root_dir, result)
        return result
    if dry_run:
        result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_modified": last_modified,
            "status": "pending",
            "stats": {"downloaded": 1, "skipped": 0, "failed": 0},
        }
        if logger:
            logger("EU-lijst: zou downloaden")
        return result
    try:
        download_xml(EU_XML_URL, dest, validate=_validate_xml)
        content = dest.read_bytes()
        result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_modified": last_modified,
            "checksum": _sha1(dest),
            "size": len(content),
            "generation_date": _read_generation_date(content),
            "entity_count": len(parse_export(content)),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "stats": {"downloaded": 1, "skipped": 0, "failed": 0},
        }
        if logger:
            logger("EU-lijst: gedownload")
    except Exception as exc:
        result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_modified": last_modified,
            "status": "error",
            "error": str(exc),
            "stats": {"downloaded": 0, "skipped": 0, "failed": 1},
        }
        if logger:
            logger(f"EU-lijst: fout ({exc})")
    _write_manifest(root_dir, result)
    return result
