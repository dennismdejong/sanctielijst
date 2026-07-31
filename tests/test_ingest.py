from app.ingest import parse_export

FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export" generationDate="2026-07-28T11:43:32+02:00" globalFileId="1">
  <sanctionEntity logicalId="L1" euReferenceNumber="EU.471.56" designationDate="2001-02-01" unitedNationId="TAL123">
    <subjectType code="person" classificationCode="P"/>
    <nameAlias firstName="Abdul" lastName="Hazem" wholeName="Abdul Hai Hazem Abdul Qader" strong="true" function="Diplomat"/>
    <nameAlias wholeName="Abdul Hai Hazem" strong="false"/>
    <citizenship countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <birthdate year="1971" birthdate="1971-02-15" place="Kabul" countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <address city="Kabul" street="Main St" region="Kabul Province" countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <identification number="D123" identificationTypeCode="passport" identificationTypeDescription="National passport" countryIso2Code="AF"/>
    <regulation numberTitle="2001/154/CFSP" publicationDate="2001-02-27" programme="AFG">
      <publicationUrl>https://eur-lex.europa.eu/example</publicationUrl>
    </regulation>
    <remark>Some remarks text</remark>
  </sanctionEntity>
  <sanctionEntity logicalId="L2" euReferenceNumber="EU.2" designationDate="2022-03-09">
    <subjectType code="enterprise" classificationCode="E"/>
    <nameAlias wholeName="Rosneft" strong="true"/>
    <citizenship countryIso2Code="RU" countryDescription="RUSSIAN FEDERATION"/>
  </sanctionEntity>
</export>
"""


def test_parse_export_person():
    entities = parse_export(FIXTURE)
    assert len(entities) == 2
    person = entities[0]
    assert person["logical_id"] == "L1"
    assert person["eu_reference_number"] == "EU.471.56"
    assert person["united_nations_id"] == "TAL123"
    assert person["designation_date"] == "2001-02-01"
    assert person["subject_type"] == "person"
    assert person["aliases"] == [
        {"whole_name": "Abdul Hai Hazem Abdul Qader", "first_name": "Abdul", "last_name": "Hazem", "strong": True, "function": "Diplomat", "title": ""},
        {"whole_name": "Abdul Hai Hazem", "first_name": "", "last_name": "", "strong": False, "function": "", "title": ""},
    ]
    assert person["citizenships"] == [{"iso2": "AF", "description": "AFGHANISTAN"}]
    assert person["birthdates"] == [{"date": "1971-02-15", "year": 1971, "year_from": None, "year_to": None, "city": "", "place": "Kabul", "iso2": "AF", "country": "AFGHANISTAN"}]
    assert person["addresses"] == [{"city": "Kabul", "street": "Main St", "region": "Kabul Province", "iso2": "AF", "country": "AFGHANISTAN"}]
    assert person["identifications"] == [{"number": "D123", "type_code": "passport", "type_description": "National passport", "iso2": "AF"}]
    assert person["regulations"] == [{"number_title": "2001/154/CFSP", "publication_date": "2001-02-27", "programme": "AFG", "publication_url": "https://eur-lex.europa.eu/example"}]
    assert person["remarks"] == ["Some remarks text"]


def test_parse_export_enterprise():
    entities = parse_export(FIXTURE)
    ent = entities[1]
    assert ent["subject_type"] == "enterprise"
    assert ent["aliases"][0]["whole_name"] == "Rosneft"
    assert ent["aliases"][0]["strong"] is True
    assert ent["citizenships"] == [{"iso2": "RU", "description": "RUSSIAN FEDERATION"}]
    assert ent["birthdates"] == []


import json
from pathlib import Path
import pytest
from app.ingest import download_xml, load_index, refresh


def write_cache(tmp_path: Path, xml: bytes, cached_at: int):
    (tmp_path / "eu_sanctions.xml").write_bytes(xml)
    meta = {"cached_at": cached_at, "generated_at": "2026-07-28T11:43:32+02:00", "entity_count": 2}
    (tmp_path / "cache_meta.json").write_text(json.dumps(meta))


def test_download_xml_calls_requests(monkeypatch):
    import requests
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def content(self):
            return b"<xml/>"

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "get", fake_get)
    result = download_xml()
    assert result == b"<xml/>"
    assert captured["url"] == "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
    assert captured["timeout"] == 120


def test_refresh_downloads_and_writes(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: xml)
    meta = refresh(tmp_path)
    assert (tmp_path / "eu_sanctions.xml").read_bytes() == xml
    assert (tmp_path / "cache_meta.json").exists()
    assert meta["entity_count"] == 2
    assert meta["generated_at"] == "2026-07-28T11:43:32+02:00"


def test_load_index_downloads_when_missing(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: xml)
    entities, meta = load_index(tmp_path, ttl=86400)
    assert len(entities) == 2
    assert meta["source"] == "fresh"


def test_load_index_uses_cache_when_fresh(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    write_cache(tmp_path, xml, cached_at=9999999999)
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: pytest.fail("should not download"))
    entities, meta = load_index(tmp_path, ttl=86400)
    assert len(entities) == 2
    assert meta["source"] == "cached"


def test_load_index_falls_back_to_cache_on_error(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    write_cache(tmp_path, xml, cached_at=1)
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    entities, meta = load_index(tmp_path, ttl=0)
    assert len(entities) == 2
    assert meta["source"] == "cached"
    assert "boom" in meta["error"]


def test_load_index_raises_when_no_cache_and_download_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        load_index(tmp_path, ttl=0)


def test_load_index_corrupt_meta_falls_back(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    (tmp_path / "eu_sanctions.xml").write_bytes(xml)
    (tmp_path / "cache_meta.json").write_text("{not json")
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: xml)
    entities, meta = load_index(tmp_path, ttl=86400)
    assert len(entities) == 2
    assert meta["source"] == "fresh"


def test_load_index_missing_xml_with_fresh_meta_redownloads(monkeypatch, tmp_path):
    xml = (Path(__file__).parent / "fixtures" / "eu_sample.xml").read_bytes()
    meta = {"cached_at": 9999999999, "generated_at": "2026-07-28T11:43:32+02:00", "entity_count": 2}
    (tmp_path / "cache_meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr("app.ingest.download_xml", lambda *a, **k: xml)
    entities, meta = load_index(tmp_path, ttl=86400)
    assert len(entities) == 2
    assert meta["source"] == "fresh"


from app.pep_ingest import write_datasets_meta


def test_write_datasets_meta(tmp_path):
    index = {"datasets": [
        {"name": "ar_parliament", "collections": ["peps"], "title": "Argentina Members of Parliament", "publisher": {"name": "HCDN", "country": "ar", "official": True}, "url": "https://parlament.ar"},
        {"name": "eu_fsf", "collections": ["default"], "title": "EU Sanctions", "publisher": {"name": "EU"}},
    ]}
    write_datasets_meta(index, tmp_path)
    meta = json.loads((tmp_path / "datasets.json").read_text())
    assert meta == {
        "ar_parliament": {"title": "Argentina Members of Parliament", "publisher": "HCDN", "country": "ar", "official": True, "url": "https://parlament.ar"},
    }


def test_write_datasets_meta_atomic_no_tmp_left(tmp_path):
    write_datasets_meta({"datasets": []}, tmp_path)
    assert (tmp_path / "datasets.json").exists()
    assert not (tmp_path / "datasets.json.tmp").exists()


def test_write_datasets_meta_skips_when_unchanged(tmp_path):
    index = {"datasets": [{"name": "ar_parliament", "collections": ["peps"], "title": "Argentina Parliament", "publisher": {"name": "HCDN", "country": "ar", "official": True}, "url": "x"}]}
    write_datasets_meta(index, tmp_path)
    before = (tmp_path / "datasets.json").stat().st_mtime_ns
    import time
    time.sleep(0.01)
    write_datasets_meta(index, tmp_path)
    after = (tmp_path / "datasets.json").stat().st_mtime_ns
    assert before == after
    assert not (tmp_path / "datasets.json.tmp").exists()


def test_write_datasets_meta_updates_when_changed(tmp_path):
    index = {"datasets": [{"name": "ar_parliament", "collections": ["peps"], "title": "Old", "publisher": {"country": "ar"}}]}
    write_datasets_meta(index, tmp_path)
    index["datasets"][0]["title"] = "New"
    write_datasets_meta(index, tmp_path)
    meta = json.loads((tmp_path / "datasets.json").read_text())
    assert meta["ar_parliament"]["title"] == "New"
