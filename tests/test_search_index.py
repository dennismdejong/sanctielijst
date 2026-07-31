import json
from pathlib import Path

import pytest

from app.search_index import (
    THRESHOLD,
    MAX_RESULTS,
    fold,
    tokens,
    _eu_records,
    _pep_records,
)


def test_constants():
    assert THRESHOLD == 90
    assert MAX_RESULTS == 20


def test_fold_accents():
    assert fold("JORGE FERNÁNDEZ") == "jorge fernandez"
    assert fold("MÜLLER") == "muller"


def test_tokens():
    assert tokens("JORGE FERNÁNDEZ") == ["jorge", "fernandez"]
    assert tokens("a b !! c-de") == ["de"]


def eu_entity(eu_ref="EU.1", name="John Smith", subject_type="person", birthdates=None, citizenships=None):
    return {
        "logical_id": eu_ref,
        "eu_reference_number": eu_ref,
        "united_nations_id": "",
        "designation_date": "2022-01-01",
        "subject_type": subject_type,
        "aliases": [{"whole_name": name, "first_name": "", "last_name": "", "strong": True, "function": "Diplomat", "title": ""}],
        "citizenships": citizenships or [],
        "birthdates": birthdates or [],
        "addresses": [],
        "identifications": [],
        "regulations": [{"number_title": "2022/123", "publication_date": "2022-02-01", "programme": "XX", "publication_url": "https://eur-lex.europa.eu/x"}],
        "remarks": ["let op"],
    }


def test_eu_records_normalise():
    records = _eu_records([eu_entity(birthdates=[{"date": "1971-02-15", "year": 1971, "year_from": None, "year_to": None, "city": "", "place": "Kabul", "iso2": "AF", "country": "AFGHANISTAN"}], citizenships=[{"iso2": "AF", "description": "AFGHANISTAN"}])])
    r = records[0]
    assert r["source"] == "eu"
    assert r["schema"] == "Person"
    assert r["names"] == ["John Smith"]
    assert r["birth_dates"] == ["1971-02-15"]
    assert r["birth_places"] == ["Kabul"]
    assert r["citizenships"] == ["AF"]
    assert r["eu_ref"] == "EU.1"
    assert "regulations" in r["raw"]


def test_eu_records_enterprise():
    r = _eu_records([eu_entity(name="Rosneft", subject_type="enterprise")])[0]
    assert r["schema"] == "Company"
    assert r["names"] == ["Rosneft"]


def write_ftm(root, dataset, entities):
    path = root / dataset / "entities.ftm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in entities:
            fh.write(json.dumps(e) + "\n")


def test_pep_records_filters():
    import tempfile
    root = Path(tempfile.mkdtemp())
    write_ftm(root, "ds1", [
        {"id": "NK-1", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ds1"],
         "properties": {"name": ["JORGE FERNÁNDEZ"], "birthDate": ["1965-03-01"], "citizenship": ["ar"], "topics": ["role.pep"]}},
        {"id": "NK-2", "caption": "Maria", "schema": "Person", "target": False, "datasets": ["ds1"], "properties": {}},
        {"id": "NK-3", "caption": "X", "schema": "Occupancy", "target": True, "datasets": ["ds1"], "properties": {}},
    ])
    write_ftm(root, "ds2", [
        {"id": "NK-4", "caption": "ACME", "schema": "Company", "target": True, "datasets": ["ds2"], "properties": {"name": ["ACME"]}},
    ])
    write_ftm(root, "ds3", [])  # empty dataset file must not collide
    records = _pep_records(root)
    ids = [r["id"] for r in records]
    assert ids == ["NK-1", "NK-4"]
    jorge = records[0]
    assert jorge["source"] == "pep"
    assert jorge["names"] == ["JORGE FERNÁNDEZ"]
    assert jorge["birth_dates"] == ["1965-03-01"]
    assert jorge["citizenships"] == ["ar"]
    assert jorge["datasets"] == ["ds1"]


def test_pep_record_raw_is_none():
    import tempfile
    root = Path(tempfile.mkdtemp())
    write_ftm(root, "ds1", [
        {"id": "NK-1", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ds1"],
         "properties": {"name": ["JORGE FERNÁNDEZ"]}},
    ])
    r = _pep_records(root)[0]
    assert r["raw"] is None


import sqlite3

from app.search_index import _open, _schema, build_index, search, _name_score


def build_fixture(root, db_path, include_eu=True, include_pep=True):
    eu = [eu_entity()] if include_eu else []
    if include_pep:
        write_ftm(root, "ds1", [
            {"id": "NK-1", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ds1"],
             "properties": {"name": ["JORGE FERNÁNDEZ"], "birthDate": ["1965-03-01"], "citizenship": ["ar"]}},
        ])
    return build_index(db_path, eu, root)


def test_build_index_counts(tmp_path):
    stats = build_fixture(tmp_path, tmp_path / "search.sqlite")
    assert stats["eu_count"] == 1
    assert stats["pep_count"] == 1
    assert stats["total"] == 2
    assert (tmp_path / "search.sqlite").exists()
    assert not (tmp_path / "search.sqlite.new").exists()


def test_build_index_fresh_overwrites(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    build_fixture(tmp_path, db_path)
    assert _schema(_open(db_path)) == 2


def test_search_exact_and_fuzzy(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "JORGE FERNANDEZ")
    assert results and results[0]["entity"]["source"] == "pep"
    assert results[0]["score"] == 100
    assert results[0]["matched_name"] == "JORGE FERNÁNDEZ"
    results = search(db, "JORGE FERNÁNDEZ")
    assert results and results[0]["entity"]["id"] == "NK-1"


def test_name_score_fuzzy_token_set_branch():
    score, matched = _name_score(["JORGE FERNÁNDEZ"], "JORGE FERNANDZ")
    assert matched == "JORGE FERNÁNDEZ"
    assert score != 100
    assert 80 <= score < 100
    assert score >= THRESHOLD


def test_search_eu_source_and_raw(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "John Smith")
    assert results and results[0]["entity"]["source"] == "eu"
    assert results[0]["entity"]["raw"]["eu_reference_number"] == "EU.1"


def test_search_entity_type_filter(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    assert [r["entity"]["source"] for r in search(db, "JORGE", entity_type="enterprise")] == []
    assert search(db, "JORGE", entity_type="person")[0]["entity"]["source"] == "pep"


def test_search_threshold_and_max(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    assert search(db, "JORGE", threshold=0, max_results=1)
    assert len(search(db, "JORGE", threshold=0, max_results=1)) == 1
    assert search(db, "Zzqqq Xxww") == []
    assert search(db, "!!") == []


def test_search_birth_year_and_nationality(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "JORGE", birth_year=1965, nationality="ar", threshold=60)
    assert results and results[0]["score"] >= 90


import os
import time

from app.search_index import ensure_index, index_fresh, load_stats, rebuild_index


EU_EXPORT = (
    b'<export xmlns:fsd="http://eu.europa.ec/fpi/fsd/export">'
    b'<fsd:sanctionEntity logicalId="EU.1" euReferenceNumber="EU.1">'
    b'<fsd:subjectType code="person"/>'
    b'<fsd:nameAlias wholeName="John Smith"/>'
    b"</fsd:sanctionEntity></export>"
)


def test_index_fresh_logic(tmp_path):
    db_path = tmp_path / "search.sqlite"
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(EU_EXPORT)
    build_fixture(tmp_path, db_path)
    assert index_fresh(db_path, eu_xml, tmp_path) is True
    future = time.time() + 1000
    os.utime(eu_xml, (future, future))
    assert index_fresh(db_path, eu_xml, tmp_path) is False


def test_ensure_index_opens_fresh(tmp_path):
    db_path = tmp_path / "search.sqlite"
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(EU_EXPORT)
    build_fixture(tmp_path, db_path)
    result = ensure_index(db_path, eu_xml, tmp_path)
    assert result["ready"] is True
    assert result["stats"]["total"] == 2


def test_ensure_index_not_ready_when_missing(tmp_path):
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    result = ensure_index(tmp_path / "search.sqlite", eu_xml, tmp_path)
    assert result["ready"] is False
    assert result["db"] is None


def test_ensure_index_corrupt_db_not_ready(tmp_path):
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(EU_EXPORT)
    db_path = tmp_path / "search.sqlite"
    db_path.write_bytes(b"kapot")
    assert index_fresh(db_path, eu_xml, tmp_path) is True
    result = ensure_index(db_path, eu_xml, tmp_path)
    assert result["ready"] is False
    assert result["db"] is None
    assert result["stats"] is None


def test_load_stats(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    stats = load_stats(_open(db_path))
    assert stats["eu_count"] == 1
    assert stats["pep_count"] == 1


def test_rebuild_index(tmp_path):
    db_path = tmp_path / "search.sqlite"
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(EU_EXPORT)
    build_fixture(tmp_path, db_path)
    stats = rebuild_index(db_path, eu_xml, tmp_path)
    assert stats["total"] == 2
    assert index_fresh(db_path, eu_xml, tmp_path)


def test_search_typo_still_finds_candidate(tmp_path):
    db_path = tmp_path / "search.sqlite"
    build_fixture(tmp_path, db_path)
    db = _open(db_path)
    results = search(db, "JORGE FERNANDZ", threshold=0)
    assert results and results[0]["entity"]["id"] == "NK-1"
