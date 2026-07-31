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
