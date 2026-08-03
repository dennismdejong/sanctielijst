import hashlib
import json
from pathlib import Path

import pytest
import requests

from app import sanctions_ingest


def make_resource(url="https://data.opensanctions.org/artifacts/x/1/entities.ftm.json", checksum="abc", size=100):
    return {"name": "entities.ftm.json", "url": url, "checksum": checksum, "size": size, "mime_type": "application/json+ftm"}


def make_source(name, collections=("sanctions",), type_="source", version="v1", resources=None):
    return {
        "name": name, "type": type_, "collections": list(collections), "version": version,
        "resources": resources if resources is not None else [make_resource()],
    }


def make_index(datasets):
    return {"datasets": datasets}


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeStreamResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield from self._chunks


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("app.pep_ingest.DOWNLOAD_PAUSE", 0)


def test_default_root_env(monkeypatch):
    monkeypatch.delenv("SANCTIONS_DATA_DIR", raising=False)
    assert sanctions_ingest.default_root() == Path("data/sanctions")
    monkeypatch.setenv("SANCTIONS_DATA_DIR", "/data/sanctions")
    assert sanctions_ingest.default_root() == Path("/data/sanctions")


def test_list_sanctions_datasets_excludes_eu_fsf():
    datasets = [
        make_source("us_ofac_sdn"),
        make_source("nl_terrorism_list"),
        make_source("eu_fsf"),
        make_source("un_sc_sanctions"),
        make_source("gb_fcdo_sanctions"),
        make_source("only_pep", collections=("peps",)),
    ]
    names = [d["name"] for d in sanctions_ingest.list_sanctions_datasets(make_index(datasets))]
    assert names == ["gb_fcdo_sanctions", "nl_terrorism_list", "un_sc_sanctions", "us_ofac_sdn"]


def test_refresh_sanctions_full_run(tmp_path, monkeypatch):
    data = b"x"
    index = make_index([
        make_source("nl_terrorism_list", version="v1", resources=[make_resource(url="https://nl", checksum=sha1_bytes(data))]),
        make_source("eu_fsf", version="v1", resources=[make_resource(url="https://eu", checksum=sha1_bytes(data))]),
    ])
    logs = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeStreamResp([data]))
    manifest = sanctions_ingest.refresh_sanctions(tmp_path, index=index, logger=logs.append)
    assert manifest["collection"] == "sanctions"
    assert manifest["stats"] == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0, "bytes": 100}
    assert (tmp_path / "nl_terrorism_list" / "entities.ftm.json").read_bytes() == data
    assert not (tmp_path / "eu_fsf").exists()
    assert any("nl_terrorism_list" in line for line in logs)
