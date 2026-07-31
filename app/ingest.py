import xml.etree.ElementTree as ET

NS = {"fsd": "http://eu.europa.ec/fpi/fsd/export"}


def _to_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_export(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entities = []
    for se in root.findall("fsd:sanctionEntity", NS):
        st = se.find("fsd:subjectType", NS)
        subject_code = st.get("code", "") if st is not None else ""
        aliases = []
        for na in se.findall("fsd:nameAlias", NS):
            aliases.append({
                "whole_name": na.get("wholeName", "").strip(),
                "first_name": na.get("firstName", "").strip(),
                "last_name": na.get("lastName", "").strip(),
                "strong": na.get("strong", "false") == "true",
                "function": na.get("function", "").strip(),
                "title": na.get("title", "").strip(),
            })
        citizenships = []
        for c in se.findall("fsd:citizenship", NS):
            citizenships.append({
                "iso2": c.get("countryIso2Code", "").strip().upper(),
                "description": c.get("countryDescription", "").strip().upper(),
            })
        birthdates = []
        for b in se.findall("fsd:birthdate", NS):
            birthdates.append({
                "date": b.get("birthdate", "").strip(),
                "year": _to_int(b.get("year", "")),
                "year_from": _to_int(b.get("yearRangeFrom", "")),
                "year_to": _to_int(b.get("yearRangeTo", "")),
                "city": b.get("city", "").strip(),
                "place": b.get("place", "").strip(),
                "iso2": b.get("countryIso2Code", "").strip().upper(),
                "country": b.get("countryDescription", "").strip().upper(),
            })
        addresses = []
        for a in se.findall("fsd:address", NS):
            addresses.append({
                "city": a.get("city", "").strip(),
                "street": a.get("street", "").strip(),
                "region": a.get("region", "").strip(),
                "iso2": a.get("countryIso2Code", "").strip().upper(),
                "country": a.get("countryDescription", "").strip().upper(),
            })
        identifications = []
        for i in se.findall("fsd:identification", NS):
            identifications.append({
                "number": i.get("number", "").strip(),
                "type_code": i.get("identificationTypeCode", "").strip(),
                "type_description": i.get("identificationTypeDescription", "").strip(),
                "iso2": i.get("countryIso2Code", "").strip().upper(),
            })
        regulations = []
        for r in se.findall("fsd:regulation", NS):
            pu = r.find("fsd:publicationUrl", NS)
            regulations.append({
                "number_title": r.get("numberTitle", "").strip(),
                "publication_date": r.get("publicationDate", "").strip(),
                "programme": r.get("programme", "").strip(),
                "publication_url": pu.text.strip() if pu is not None and pu.text else "",
            })
        remarks = []
        for rm in se.findall("fsd:remark", NS):
            if rm.text and rm.text.strip():
                remarks.append(rm.text.strip())
        entities.append({
            "logical_id": se.get("logicalId", ""),
            "eu_reference_number": se.get("euReferenceNumber", ""),
            "united_nations_id": se.get("unitedNationId", ""),
            "designation_date": se.get("designationDate", ""),
            "subject_type": "enterprise" if subject_code == "enterprise" else "person",
            "aliases": aliases,
            "citizenships": citizenships,
            "birthdates": birthdates,
            "addresses": addresses,
            "identifications": identifications,
            "regulations": regulations,
            "remarks": remarks,
        })
    return entities


import json
import time
from pathlib import Path

import requests

DATASET_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
CACHE_TTL = 24 * 60 * 60
XML_FILENAME = "eu_sanctions.xml"
META_FILENAME = "cache_meta.json"


def _read_generation_date(xml_bytes: bytes) -> str:
    return ET.fromstring(xml_bytes).get("generationDate", "")


def download_xml(url: str = DATASET_URL, timeout: int = 120) -> bytes:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def refresh(cache_dir: Path, url: str = DATASET_URL) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    content = download_xml(url)
    (cache_dir / XML_FILENAME).write_bytes(content)
    meta = {
        "cached_at": int(time.time()),
        "generated_at": _read_generation_date(content),
        "entity_count": len(parse_export(content)),
    }
    (cache_dir / META_FILENAME).write_text(json.dumps(meta))
    return meta


def load_index(cache_dir: Path, url: str = DATASET_URL, ttl: int = CACHE_TTL) -> tuple[list[dict], dict]:
    xml_path = cache_dir / XML_FILENAME
    meta_path = cache_dir / META_FILENAME
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    age = time.time() - meta.get("cached_at", 0) if meta.get("cached_at") else None
    stale = age is None or age > ttl
    if stale:
        try:
            meta = refresh(cache_dir, url)
            meta["source"] = "fresh"
        except Exception as exc:
            if xml_path.exists():
                meta = dict(meta)
                meta["source"] = "cached"
                meta["error"] = str(exc)
            else:
                raise
    else:
        meta = dict(meta)
        meta["source"] = "cached"
    return parse_export(xml_path.read_bytes()), meta
