import hashlib
import json
from pathlib import Path

import pytest
import requests

from app.eu_ingest import (
    EU_XML_URL,
    MANIFEST_FILENAME,
    TIMEOUT,
    XML_FILENAME,
    default_root,
    download_xml,
    fetch_headers,
    load_eu_manifest,
    refresh_eu,
)

FIXTURE_XML = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
LAST_MODIFIED = "Tue, 28 Jul 2026 09:50:13 GMT"


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("app.eu_ingest.DOWNLOAD_PAUSE", 0)


def test_constants():
    assert EU_XML_URL == "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
    assert XML_FILENAME == "eu_sanctions.xml"
    assert MANIFEST_FILENAME == "manifest.json"
    assert TIMEOUT == 120


def test_default_root_env(monkeypatch):
    monkeypatch.delenv("EU_DATA_DIR", raising=False)
    assert default_root() == Path("data/eu")
    monkeypatch.setenv("EU_DATA_DIR", "/data/eu")
    assert default_root() == Path("/data/eu")


def test_fetch_headers(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def headers(self):
            return {
                "Last-Modified": LAST_MODIFIED,
                "Content-Length": "24816725",
                "Content-Disposition": 'attachment; filename="20260728-FULL-1_1(xsd).xml"',
            }

    def fake_head(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "head", fake_head)
    result = fetch_headers()
    assert captured["url"] == EU_XML_URL
    assert captured["timeout"] == 120
    assert result == {
        "last_modified": LAST_MODIFIED,
        "content_length": "24816725",
        "content_disposition": 'attachment; filename="20260728-FULL-1_1(xsd).xml"',
    }


def test_fetch_headers_missing_values(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def headers(self):
            return {}

    monkeypatch.setattr(requests, "head", lambda *a, **k: FakeResp())
    assert fetch_headers() == {"last_modified": "", "content_length": "", "content_disposition": ""}


def make_headers(last_modified=LAST_MODIFIED):
    return {"last_modified": last_modified, "content_length": "123", "content_disposition": "attachment"}


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


def test_download_xml_writes_atomic(tmp_path, monkeypatch):
    data = b"<export/>"
    dest = tmp_path / "eu_sanctions.xml"
    captured = {}

    def fake_get(url, timeout, stream=False):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_xml("https://x/eu.xml", dest)
    assert dest.read_bytes() == data
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    assert captured["stream"] is True


def test_download_xml_retries_then_raises(tmp_path, monkeypatch):
    dest = tmp_path / "eu_sanctions.xml"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(requests.ConnectionError):
        download_xml("https://x", dest, retries=1)
    assert calls["n"] == 2
    assert not dest.exists()


def test_download_xml_retry_succeeds(tmp_path, monkeypatch):
    data = b"<export/>"
    dest = tmp_path / "eu_sanctions.xml"
    calls = {"n": 0}

    def fake_get(url, timeout, stream=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("down")
        return FakeStreamResp([data])

    monkeypatch.setattr(requests, "get", fake_get)
    download_xml("https://x", dest, retries=1)
    assert calls["n"] == 2
    assert dest.read_bytes() == data


def test_load_eu_manifest_missing(tmp_path):
    assert load_eu_manifest(tmp_path) == {}


def test_load_eu_manifest_reads(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"status": "ok"}))
    assert load_eu_manifest(tmp_path) == {"status": "ok"}


def test_load_eu_manifest_corrupt(tmp_path):
    (tmp_path / "manifest.json").write_text("{niet-json")
    assert load_eu_manifest(tmp_path) == {}


def test_refresh_eu_full_run(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())

    def fake_download(url, dest, **kw):
        dest.write_bytes(FIXTURE_XML)

    monkeypatch.setattr("app.eu_ingest.download_xml", fake_download)
    manifest = refresh_eu(tmp_path, logger=logs.append)
    assert manifest["stats"] == {"downloaded": 1, "skipped": 0, "failed": 0}
    assert manifest["status"] == "ok"
    assert manifest["last_modified"] == LAST_MODIFIED
    assert manifest["generation_date"] == "2026-07-28T11:43:32+02:00"
    assert manifest["entity_count"] == 2
    assert manifest["checksum"]
    assert manifest["size"] == len(FIXTURE_XML)
    assert manifest["downloaded_at"]
    assert (tmp_path / XML_FILENAME).read_bytes() == FIXTURE_XML
    assert (tmp_path / "manifest.json").exists()
    assert any("EU-lijst" in line for line in logs)


def test_refresh_eu_skips_unchanged(tmp_path, monkeypatch):
    (tmp_path / XML_FILENAME).write_bytes(FIXTURE_XML)
    previous = {
        "updated_at": "x",
        "last_modified": LAST_MODIFIED,
        "checksum": "abc",
        "size": len(FIXTURE_XML),
        "generation_date": "2026-07-28T11:43:32+02:00",
        "entity_count": 2,
        "downloaded_at": "t",
        "status": "ok",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(previous))
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr("app.eu_ingest.download_xml", lambda *a, **k: pytest.fail("should not download"))
    manifest = refresh_eu(tmp_path)
    assert manifest["stats"] == {"downloaded": 0, "skipped": 1, "failed": 0}
    assert manifest["status"] == "ok"


def test_refresh_eu_force_redownloads(tmp_path, monkeypatch):
    (tmp_path / XML_FILENAME).write_bytes(FIXTURE_XML)
    previous = {"updated_at": "x", "last_modified": LAST_MODIFIED, "status": "ok"}
    (tmp_path / "manifest.json").write_text(json.dumps(previous))
    calls = {"n": 0}

    def fake_download(url, dest, **kw):
        calls["n"] += 1
        dest.write_bytes(FIXTURE_XML)

    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr("app.eu_ingest.download_xml", fake_download)
    manifest = refresh_eu(tmp_path, force=True)
    assert calls["n"] == 1
    assert manifest["stats"]["downloaded"] == 1


def test_refresh_eu_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr("app.eu_ingest.download_xml", lambda *a, **k: pytest.fail("should not download"))
    manifest = refresh_eu(tmp_path, dry_run=True)
    assert manifest["stats"]["downloaded"] == 1
    assert manifest["status"] == "pending"
    assert not (tmp_path / XML_FILENAME).exists()
    assert not (tmp_path / "manifest.json").exists()


def test_refresh_eu_download_error_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.eu_ingest.fetch_headers", lambda *a, **k: make_headers())
    monkeypatch.setattr(
        "app.eu_ingest.download_xml",
        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")),
    )
    manifest = refresh_eu(tmp_path)
    assert manifest["stats"] == {"downloaded": 0, "skipped": 0, "failed": 1}
    assert manifest["status"] == "error"
    assert "down" in manifest["error"]
    assert (tmp_path / "manifest.json").exists()
