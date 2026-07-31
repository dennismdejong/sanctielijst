import json
import os
import time
from pathlib import Path

import pytest

from app.pep_index import (
    build_index,
    load_index_cache,
    load_or_build_index,
    save_index,
    _tokens,
)


def write_ftm(root, dataset, entities):
    path = root / dataset / "entities.ftm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in entities:
            fh.write(json.dumps(e) + "\n")


def person(id_, caption, target=True, datasets=("ds1",), **props):
    return {"id": id_, "caption": caption, "schema": "Person", "target": target, "datasets": list(datasets), "properties": props}


def company(id_, caption, target=True, datasets=("ds1",), **props):
    return {"id": id_, "caption": caption, "schema": "Company", "target": target, "datasets": list(datasets), "properties": props}


FIXTURE = [
    person("NK-1", "JORGE FERNANDEZ", birthDate=["1965-03-01"], citizenship=["ar"], political=["PRIMERO SAN LUIS"], topics=["role.pep"]),
    person("NK-2", "Maria Lopez", target=False),
    person("NK-3", "GUILLERMO CESAR AGUERO", birthDate=["1970"]),
    company("NK-4", "Yacimientos Petroliferos"),
    {"id": "NK-5", "caption": "Occupancy", "schema": "Occupancy", "target": True, "datasets": ["ds1"], "properties": {"holder": ["Q1"]}},
    person("NK-6", "Jorge Luis"),
]


def test_tokens():
    assert _tokens("JORGE FERNANDEZ") == ["jorge", "fernandez"]
    assert _tokens("a b !! c-de") == ["de"]
    assert _tokens("") == []


def test_build_index_filters(tmp_path):
    path = tmp_path / "ds1" / "entities.ftm.json"
    path.parent.mkdir(parents=True)
    with path.open("w") as fh:
        for e in FIXTURE:
            fh.write(json.dumps(e) + "\n")
        fh.write("dit is geen geldige json\n")
    index = build_index(tmp_path)
    ids = [e["id"] for e in index["entities"]]
    assert ids == ["NK-1", "NK-3", "NK-4", "NK-6"]
    assert index["skipped_lines"] == 3
    jorge = index["entities"][0]
    assert jorge["names"] == ["JORGE FERNANDEZ"]
    assert jorge["birth_dates"] == ["1965-03-01"]
    assert jorge["citizenships"] == ["ar"]
    assert "jorge" in index["token_map"]
    assert "fernandez" in index["token_map"]


def test_build_index_no_ftm(tmp_path):
    assert build_index(tmp_path)["entities"] == []


def test_load_or_build_index_none_when_empty(tmp_path):
    assert load_or_build_index(tmp_path) is None
    assert load_or_build_index(tmp_path / "niet-bestaand") is None


def test_load_or_build_index_caches(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ")])
    first = load_or_build_index(tmp_path)
    assert first["source"] == "built"
    assert (tmp_path / "index.pkl").exists()
    second = load_or_build_index(tmp_path)
    assert second["source"] == "cached"
    assert [e["id"] for e in second["entities"]] == ["NK-1"]


def test_cache_stale_when_ftm_newer(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ")])
    load_or_build_index(tmp_path)
    future = time.time() + 1000
    os.utime(tmp_path / "ds1" / "entities.ftm.json", (future, future))
    assert load_index_cache(tmp_path) is None


def test_cache_corrupt_pickle(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ")])
    (tmp_path / "index.pkl").write_bytes(b"kapot")
    assert load_index_cache(tmp_path) is None


def test_datasets_meta_attached(tmp_path):
    write_ftm(tmp_path, "ds1", [person("NK-1", "JORGE FERNANDEZ", datasets=("ar_parliament",))])
    (tmp_path / "datasets.json").write_text(json.dumps({"ar_parliament": {"title": "Argentina Parliament", "country": "ar"}}))
    index = load_or_build_index(tmp_path)
    assert index["datasets_meta"]["ar_parliament"]["title"] == "Argentina Parliament"
    assert index["datasets"]["ar_parliament"] == 1


from app.pep_index import MAX_RESULTS, THRESHOLD, search_pep


@pytest.fixture
def pep_index_data(tmp_path):
    write_ftm(tmp_path, "ds1", FIXTURE)
    return build_index(tmp_path), tmp_path


def test_search_exact_top(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE FERNANDEZ")
    assert results[0]["entity"]["id"] == "NK-1"
    assert results[0]["score"] == 100
    assert results[0]["matched_name"] == "JORGE FERNANDEZ"
    assert results[0]["details"][0]["feature"] == "naam"


def test_search_fuzzy(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE FERNÁNDEZ")
    assert results and results[0]["score"] >= 80


def test_search_birth_year_boosts(pep_index_data):
    index, _ = pep_index_data
    exact = search_pep(index, "JORGE", birth_year=1965)
    wrong = search_pep(index, "JORGE", birth_year=1999)
    assert exact and wrong
    assert exact[0]["score"] >= wrong[0]["score"]


def test_search_nationality_match(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE", nationality="ar")
    assert any(d["feature"] == "nationaliteit" and d["score"] == 100 for r in results for d in r["details"])


def test_search_entity_type_filter(pep_index_data):
    index, _ = pep_index_data
    people = search_pep(index, "JORGE", entity_type="person")
    enterprises = search_pep(index, "JORGE", entity_type="enterprise")
    assert people and not enterprises
    comps = search_pep(index, "Yacimientos", entity_type="enterprise")
    assert comps and comps[0]["entity"]["schema"] == "Company"


def test_search_threshold_and_max(pep_index_data):
    index, _ = pep_index_data
    low = search_pep(index, "JORGE", threshold=0)
    assert len(low) >= 2
    capped = search_pep(index, "JORGE", threshold=0, max_results=1)
    assert len(capped) == 1
    assert THRESHOLD == 60
    assert MAX_RESULTS == 20


def test_search_no_candidates(pep_index_data):
    index, _ = pep_index_data
    assert search_pep(index, "Zzqqq Xxww") == []
    assert search_pep(index, "!!") == []


def test_search_sorts_desc(pep_index_data):
    index, _ = pep_index_data
    results = search_pep(index, "JORGE", threshold=0)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
