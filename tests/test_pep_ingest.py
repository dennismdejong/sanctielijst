from pathlib import Path

import pytest

from app.pep_ingest import (
    INDEX_URL,
    RESOURCE_NAME,
    TIMEOUT,
    default_root,
    fetch_index,
    list_pep_datasets,
)


def make_resource(name=RESOURCE_NAME, url="https://data.opensanctions.org/artifacts/x/1/entities.ftm.json", checksum="abc", size=100):
    return {"name": name, "url": url, "checksum": checksum, "size": size, "mime_type": "application/json+ftm"}


def make_source(name, collections=("peps",), type_="source", version="v1", resources=None):
    return {
        "name": name,
        "type": type_,
        "collections": list(collections),
        "version": version,
        "resources": resources if resources is not None else [make_resource()],
    }


def make_index(datasets):
    return {"datasets": datasets}


def test_constants():
    assert INDEX_URL == "https://data.opensanctions.org/datasets/latest/index.json"
    assert RESOURCE_NAME == "entities.ftm.json"
    assert TIMEOUT == 120


def test_default_root_env(monkeypatch):
    monkeypatch.delenv("PEP_DATA_DIR", raising=False)
    assert default_root() == Path("data/pep")
    monkeypatch.setenv("PEP_DATA_DIR", "/data/pep")
    assert default_root() == Path("/data/pep")


def test_fetch_index(monkeypatch):
    import requests

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"datasets": []}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "get", fake_get)
    assert fetch_index() == {"datasets": []}
    assert captured["url"] == INDEX_URL
    assert captured["timeout"] == 120


def test_list_pep_datasets_filters():
    datasets = [
        make_source("al_kuvendi"),
        make_source("eu_meps", collections=("default", "peps")),
        make_source("br_pep", collections=("peps",)),
        make_source("eu_fsf", collections=("default",)),
        make_source("wd_peps", type_="external", collections=("peps",)),
        make_source("no_ftm", collections=("peps",), resources=[make_resource(name="senzing.json")]),
        make_source("empty", collections=("peps",), resources=[]),
    ]
    pep = list_pep_datasets(make_index(datasets))
    names = [d["name"] for d in pep]
    assert names == ["al_kuvendi", "br_pep", "eu_meps"]
    al = pep[0]
    assert al["resource"]["name"] == RESOURCE_NAME
    assert al["version"] == "v1"


def test_list_pep_datasets_dict_index():
    index = {"datasets": {
        "al_kuvendi": make_source("al_kuvendi"),
        "eu_fsf": make_source("eu_fsf", collections=("default",)),
    }}
    assert [d["name"] for d in list_pep_datasets(index)] == ["al_kuvendi"]


def test_list_pep_datasets_missing_key():
    assert list_pep_datasets({}) == []
