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


import hashlib

import requests

from app.pep_ingest import download_artifact


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("app.pep_ingest.DOWNLOAD_PAUSE", 0)


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeStreamResp:
    def __init__(self, chunks, ok=True):
        self._chunks = chunks
        self._ok = ok

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("500")

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_artifact_writes_and_verifies(tmp_path, monkeypatch):
    data = b'{"entities": []}'
    dest = tmp_path / "al_kuvendi" / "entities.ftm.json"
    captured = {}

    def fake_get(url, timeout, stream=False):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_artifact("https://x/entities.ftm.json", dest, sha1_bytes(data))
    assert dest.read_bytes() == data
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    assert captured["stream"] is True


def test_download_artifact_checksum_mismatch_raises(tmp_path, monkeypatch):
    dest = tmp_path / "x" / "entities.ftm.json"
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeStreamResp([b"data"]))
    with pytest.raises(ValueError, match="checksum"):
        download_artifact("https://x", dest, "ffff")
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_artifact_retries_then_raises(tmp_path, monkeypatch):
    dest = tmp_path / "x" / "entities.ftm.json"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(requests.ConnectionError):
        download_artifact("https://x", dest, "abc", retries=1)
    assert calls["n"] == 2


def test_download_artifact_retry_succeeds(tmp_path, monkeypatch):
    data = b"data"
    dest = tmp_path / "x" / "entities.ftm.json"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("down")
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_artifact("https://x", dest, sha1_bytes(data), retries=1)
    assert calls["n"] == 2
    assert dest.read_bytes() == data
