import pytest
from fastapi.testclient import TestClient

from app import search_index
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_DB", str(tmp_path / "no-search.sqlite"))
    monkeypatch.setenv("EU_DATA_DIR", str(tmp_path / "eu"))
    monkeypatch.setenv(search_index.INDEX_ENV, "0")
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.delenv("AUDIT_ADMIN_TOKEN", raising=False)


def make_entity(eu_ref, whole_name, subject_type="person", year=None, country=None, place=None):
    return {
        "logical_id": eu_ref,
        "eu_reference_number": eu_ref,
        "united_nations_id": "",
        "designation_date": "2022-01-01",
        "subject_type": subject_type,
        "aliases": [{"whole_name": whole_name, "first_name": "", "last_name": "", "strong": True, "function": "Diplomat", "title": ""}],
        "citizenships": [{"iso2": country, "description": country}] if country else [],
        "birthdates": [{"date": "", "year": year, "year_from": None, "year_to": None, "city": "", "place": place, "iso2": country or "", "country": country or ""}] if year or place else [],
        "addresses": [],
        "identifications": [],
        "regulations": [{"number_title": "2022/123", "publication_date": "2022-02-01", "programme": "XX", "publication_url": "https://eur-lex.europa.eu/x"}],
        "remarks": [],
    }


ENTITIES = [
    make_entity("EU.471.56", "Abdul Hai Hazem Abdul Qader", year=1971, country="AF", place="Kabul"),
    make_entity("EU.2", "Rosneft", subject_type="enterprise", country="RU"),
]

def test_health():
    client = TestClient(create_app(entities=ENTITIES))
    assert client.get("/api/health").json() == {"status": "ok"}


def test_status_fields():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/status").json()
    assert data["entity_count"] == 2
    assert data["opensanctions_active"] is False
    assert "source" in data
    assert data["index"]["status"] == "disabled"
    assert data["index"]["enabled"] is False


def test_search_returns_eu_result():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Abdul Hai Hazem"}).json()
    assert data["results"]
    first = data["results"][0]
    assert first["source"] == "eu"
    assert first["eu"]["total_score"] == 100
    assert first["entity"]["eu_reference_number"] == "EU.471.56"
    assert any(d["feature"] == "naam" for d in first["eu"]["details"])


def test_search_birth_year_boosts():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Abdul", "birth_year": 1971}).json()
    scores = [r["score"] for r in data["results"] if r["source"] == "eu"]
    assert scores and scores[0] == 100


def test_search_entity_type_filter():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Rosneft", "entity_type": "enterprise"}).json()
    assert any(r["source"] == "eu" for r in data["results"])


def test_search_requires_name():
    client = TestClient(create_app(entities=ENTITIES))
    resp = client.get("/api/search")
    assert resp.status_code == 422


def test_search_whitespace_name_returns_422():
    client = TestClient(create_app(entities=ENTITIES))
    resp = client.get("/api/search", params={"name": "   "})
    assert resp.status_code == 422


def test_os_api_key_defaults_from_env(monkeypatch):
    monkeypatch.setenv("OPENSANCTIONS_API_KEY", "KEY")
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/status").json()
    assert data["opensanctions_active"] is True


def test_search_with_opensanctions_merges(monkeypatch):
    import app.main as main

    fake_os = [
        {
            "id": "NK-x",
            "caption": "Abdul Qader",
            "schema": "Person",
            "score": 0.8,
            "match": True,
            "explanations": {"name_match": {"score": 0.8}},
            "datasets": ["eu_fsf"],
            "properties": {},
            "url": "https://opensanctions.org/entities/NK-x",
        }
    ]
    monkeypatch.setattr(main.opensanctions, "match_opensanctions", lambda *a, **k: fake_os)
    client = TestClient(create_app(entities=ENTITIES, os_api_key="KEY"))
    data = client.get("/api/search", params={"name": "Abdul Qader"}).json()
    sources = {r["source"] for r in data["results"]}
    assert sources == {"eu", "opensanctions"}
    os_result = [r for r in data["results"] if r["source"] == "opensanctions"][0]
    assert os_result["score"] == 80
    assert os_result["opensanctions"]["url"] == "https://opensanctions.org/entities/NK-x"


def test_search_opensanctions_failure_adds_warning(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.opensanctions, "match_opensanctions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    client = TestClient(create_app(entities=ENTITIES, os_api_key="KEY"))
    data = client.get("/api/search", params={"name": "Abdul Hai Hazem"}).json()
    assert data["results"]
    assert any("OpenSanctions" in w for w in data["warnings"])


def test_search_no_match_returns_empty():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/search", params={"name": "Zzq Qqxx"}).json()
    assert data["results"] == []


def test_index_serves_html(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>hi</h1>")
    client = TestClient(create_app(entities=ENTITIES, static_dir=static))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text == "<h1>hi</h1>"


def test_audit_page_served(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "audit.html").write_text("<h1>audit</h1>")
    client = TestClient(create_app(entities=ENTITIES, static_dir=static))
    resp = client.get("/audit")
    assert resp.status_code == 200
    assert resp.text == "<h1>audit</h1>"


def _write_pep_fixture(root):
    import json
    for ds, entities in [
        ("ar_parliament", [
            {"id": "NK-x", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ar_parliament"],
             "properties": {"birthDate": ["1965-03-01"], "citizenship": ["ar"], "political": ["PRIMERO SAN LUIS"], "topics": ["role.pep"]}},
        ]),
    ]:
        p = root / ds / "entities.ftm.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as fh:
            for e in entities:
                fh.write(json.dumps(e) + "\n")
    (root / "datasets.json").write_text(json.dumps({"ar_parliament": {"title": "Argentina Members of Parliament", "publisher": "HCDN", "country": "ar", "official": True, "url": "https://parlament.ar"}}))


def _write_pep_fixture_with_positions(root):
    import json
    for ds, entities in [
        ("ar_parliament", [
            {"id": "NK-x", "caption": "JORGE FERNÁNDEZ", "schema": "Person", "target": True, "datasets": ["ar_parliament"],
             "properties": {"birthDate": ["1965-03-01"], "citizenship": ["ar"], "political": ["PRIMERO SAN LUIS"], "topics": ["role.pep"]}},
            {"id": "P-1", "caption": "Minister of Defence", "schema": "Position", "target": False, "datasets": ["ar_parliament"],
             "properties": {"name": ["Minister of Defence"]}},
            {"id": "O-1", "caption": "Occupancy", "schema": "Occupancy", "target": False, "datasets": ["ar_parliament"],
             "properties": {"holder": ["NK-x"], "post": ["P-1"], "status": ["current"], "startDate": ["2020-01-01"], "endDate": ["2024-01-01"]}},
        ]),
    ]:
        p = root / ds / "entities.ftm.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as fh:
            for e in entities:
                fh.write(json.dumps(e) + "\n")
    (root / "datasets.json").write_text(json.dumps({"ar_parliament": {"title": "Argentina Members of Parliament", "publisher": "HCDN", "country": "ar", "official": True, "url": "https://parlament.ar"}}))


def make_eu_entity():
    return {
        "logical_id": "EU.1", "eu_reference_number": "EU.1", "united_nations_id": "",
        "designation_date": "2022-01-01", "subject_type": "person",
        "aliases": [{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}],
        "citizenships": [], "birthdates": [], "addresses": [], "identifications": [],
        "regulations": [{"number_title": "2022/123", "publication_date": "2022-02-01", "programme": "XX", "publication_url": "https://eur-lex.europa.eu/x"}],
        "remarks": [],
    }


def _write_search_db(root):
    _write_pep_fixture(root)
    return search_index.build_index(root / "search.sqlite", [make_eu_entity()], root)


def build_index(db_path, eu_entities, root):
    return search_index.build_index(db_path, eu_entities, root)


def _decoded_text(data: bytes) -> bytes:
    import re
    import zlib

    from reportlab.pdfbase.pdfutils import asciiBase85Decode

    text = b""
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        stream = match.group(1)
        try:
            stream = asciiBase85Decode(stream)
        except Exception:
            pass
        try:
            stream = zlib.decompress(stream)
        except Exception:
            pass
        text += stream
    return text


def test_status_index_ready(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_search_db(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/status").json()
    assert data["index"]["status"] == "ready"
    assert data["index"]["pep_count"] == 1
    assert data["index"]["eu_count"] == 1


def test_search_while_building_serves_eu(tmp_path, monkeypatch):
    import threading

    import app.main as main

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    (eu_root / "eu_sanctions.xml").write_bytes(b"<export/>")
    started = threading.Event()
    release = threading.Event()

    def slow_rebuild(db_path, eu_xml, pep_root):
        started.set()
        release.wait(5)

    monkeypatch.setattr(main.search_index, "rebuild_index", slow_rebuild)
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root, search_db=tmp_path / "missing.sqlite"))
    started.wait(5)
    status = client.get("/api/status").json()
    assert status["index"]["status"] == "building"
    data = client.get("/api/search", params={"name": "Abdul Hai Hazem"}).json()
    assert data["results"]
    assert data["results"][0]["source"] == "eu"
    assert data["results"][0]["entity"]["eu_reference_number"] == "EU.471.56"
    assert any("opgebouwd" in w for w in data["warnings"])
    release.set()


def test_create_app_corrupt_db_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    (tmp_path / "eu_sanctions.xml").write_bytes(b"<export/>")
    (tmp_path / "search.sqlite").write_bytes(b"kapot")
    app = create_app(entities=ENTITIES, eu_root=tmp_path, search_db=tmp_path / "search.sqlite")
    client = TestClient(app)
    data = client.get("/api/status").json()
    assert data["index"]["status"] in ("building", "ready")


def test_search_db_merges_eu_and_pep(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_search_db(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()
    assert [r["source"] for r in data["results"]] == ["pep"]
    first = [r for r in data["results"] if r["source"] == "pep"][0]
    assert first["pep"]["datasets"][0]["id"] == "ar_parliament"
    data = client.get("/api/search", params={"name": "John Smith"}).json()
    eu = [r for r in data["results"] if r["source"] == "eu"][0]
    assert eu["eu"]["matched_alias"] == "John Smith"


def test_search_pep_result_contains_positions(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture_with_positions(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()
    pep = [r for r in data["results"] if r["source"] == "pep"][0]
    assert pep["entity"]["positions"] == [
        {"role": "Minister of Defence", "status": "current", "start": "2020-01-01", "end": "2024-01-01"}
    ]


def test_search_pep_result_positions_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_search_db(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()
    pep = [r for r in data["results"] if r["source"] == "pep"][0]
    assert pep["entity"]["positions"] == []


def test_search_eu_result_has_no_positions(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_search_db(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    data = client.get("/api/search", params={"name": "John Smith"}).json()
    eu = [r for r in data["results"] if r["source"] == "eu"][0]
    assert "positions" not in eu["entity"]


def test_default_pep_root_uses_env(monkeypatch):
    from pathlib import Path

    from app import main as main_module
    monkeypatch.setenv("PEP_DATA_DIR", "/data/pep")
    assert main_module.default_pep_root() == Path("/data/pep")
    monkeypatch.delenv("PEP_DATA_DIR", raising=False)
    assert main_module.default_pep_root() == main_module.PEP_ROOT


def test_default_eu_root_uses_env(monkeypatch):
    from pathlib import Path

    from app import main as main_module
    monkeypatch.setenv("EU_DATA_DIR", "/data/eu")
    assert main_module.default_eu_root() == Path("/data/eu")


def test_default_eu_root_falls_back_without_env(monkeypatch):
    from app import main as main_module
    monkeypatch.delenv("EU_DATA_DIR", raising=False)
    assert main_module.default_eu_root() == main_module.EU_ROOT


def test_refresh_success(tmp_path, monkeypatch):
    import app.main as main

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    manifest = {"status": "ok", "downloaded_at": "2026-07-31T12:00:00+00:00", "generation_date": "2026-07-28T11:43:32+02:00", "entity_count": 2}
    monkeypatch.setattr(main.eu_ingest, "refresh_eu", lambda *a, **k: manifest)
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    resp = client.post("/api/refresh")
    assert resp.status_code == 200
    assert resp.json()["source"] == "ok"


def test_refresh_head_failure_returns_503(tmp_path, monkeypatch):
    import app.main as main

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    monkeypatch.setattr(main.eu_ingest, "refresh_eu", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    resp = client.post("/api/refresh")
    assert resp.status_code == 503


def test_status_source_from_manifest(tmp_path):
    import json

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    (eu_root / "manifest.json").write_text(json.dumps({
        "status": "ok",
        "downloaded_at": "2026-07-31T12:00:00+00:00",
        "generation_date": "2026-07-28T11:43:32+02:00",
    }))
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    data = client.get("/api/status").json()
    assert data["source"] == "ok"
    assert data["generated_at"] == "2026-07-28T11:43:32+02:00"
    assert data["entity_count"] == 2


def test_startup_corrupt_xml_boots_with_error(tmp_path):
    import json

    (tmp_path / "eu_sanctions.xml").write_bytes(b"<garbage")
    (tmp_path / "manifest.json").write_text(json.dumps({"status": "ok"}))
    client = TestClient(create_app(eu_root=tmp_path))
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "error"
    assert data["entity_count"] == 0


def test_status_data_age_hours_tz_naive_ok(tmp_path):
    import json

    from app import main as main_module

    eu_root = tmp_path / "eu"
    eu_root.mkdir()
    (eu_root / "manifest.json").write_text(json.dumps({
        "status": "ok",
        "downloaded_at": "2026-07-31T12:00:00",
    }))
    client = TestClient(create_app(entities=ENTITIES, eu_root=eu_root))
    data = client.get("/api/status").json()
    assert data["source"] == "ok"
    assert data["data_age_hours"] is not None
    assert isinstance(data["data_age_hours"], float)
    assert main_module._data_age_hours("garbage") is None
    assert main_module._data_age_hours(None) is None


def test_export_returns_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "JORGE FERNANDEZ", "author": "Dennis"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:4] == b"%PDF"


def test_export_csv_format(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "JORGE FERNANDEZ", "format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:3] == b"\xef\xbb\xbf"
    body = resp.content.decode("utf-8-sig")
    assert "naam;score;bron;datasets;match-details;eu_referentie;geboortedata;nationaliteit;links" in body
    assert "JORGE FERNÁNDEZ" in body
    assert b".csv" in resp.headers["content-disposition"].encode()


def test_export_xlsx_format(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "JORGE FERNANDEZ", "format": "xlsx"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:2] == b"PK"
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(resp.content))
    ws = wb["Screening"]
    assert [c.value for c in ws[1]] == ["naam", "score", "bron", "datasets", "match-details", "eu_referentie", "geboortedata", "nationaliteit", "links"]
    assert ws["A2"].value == "JORGE FERNÁNDEZ"
    assert b".xlsx" in resp.headers["content-disposition"].encode()


def test_export_invalid_format_is_422(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "JORGE FERNANDEZ", "format": "onzin"})
    assert resp.status_code == 422


def test_export_requires_name():
    client = TestClient(create_app(entities=ENTITIES))
    assert client.get("/api/search/export").status_code == 422


def test_export_empty_results(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "Zzqqq Xxww"})
    assert resp.status_code == 200
    assert b"Geen overeenkomsten" in _decoded_text(resp.content)


def _last_audit_events(tmp_path):
    from app import audit

    return audit.list_events(tmp_path / "audit.sqlite")


def test_search_logs_audit_event(tmp_path):
    from app import matcher

    client = TestClient(create_app(entities=ENTITIES))
    client.get("/api/search", params={"name": "Abdul Hai Hazem"})
    events = _last_audit_events(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["ip"] == "testclient"
    assert event["user"] is None
    assert event["method"] == "GET"
    assert event["path"] == "/api/search"
    assert event["query"] == {"name": "Abdul Hai Hazem", "birth_year": None, "nationality": None, "birth_place": None, "entity_type": None}
    assert event["result_count"] >= 1
    assert event["sources"] == ["eu"]
    assert event["threshold"] == matcher.THRESHOLD
    assert event["user_agent"]


def test_search_logs_empty_results(tmp_path):
    client = TestClient(create_app(entities=ENTITIES))
    client.get("/api/search", params={"name": "Zzq Qqxx"})
    event = _last_audit_events(tmp_path)[0]
    assert event["result_count"] == 0
    assert event["sources"] == []


def test_export_logs_audit_event(tmp_path, monkeypatch):
    monkeypatch.setenv(search_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    build_index(tmp_path / "search.sqlite", [make_eu_entity()], tmp_path)
    client = TestClient(create_app(entities=ENTITIES, eu_root=tmp_path, pep_root=tmp_path, search_db=tmp_path / "search.sqlite"))
    resp = client.get("/api/search/export", params={"name": "JORGE FERNANDEZ"})
    assert resp.status_code == 200
    event = _last_audit_events(tmp_path)[0]
    assert event["path"] == "/api/search/export"
    assert event["result_count"] >= 1


def test_audit_endpoint_disabled_without_token():
    client = TestClient(create_app(entities=ENTITIES))
    resp = client.get("/api/audit")
    assert resp.status_code == 404


def test_audit_logs_422_blank_name(monkeypatch):
    monkeypatch.setenv("AUDIT_ADMIN_TOKEN", "secret")
    client = TestClient(create_app(entities=ENTITIES))
    assert client.get("/api/search", params={"name": "   "}).status_code == 422
    assert client.get("/api/search/export", params={"name": "   "}).status_code == 422
    resp = client.get("/api/audit", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(e["result_count"] == 0 for e in data["events"])


def test_audit_endpoint_401_without_or_bad_token(monkeypatch):
    monkeypatch.setenv("AUDIT_ADMIN_TOKEN", "secret")
    client = TestClient(create_app(entities=ENTITIES))
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_audit_endpoint_returns_events_with_token(monkeypatch):
    monkeypatch.setenv("AUDIT_ADMIN_TOKEN", "secret")
    client = TestClient(create_app(entities=ENTITIES))
    client.get("/api/search", params={"name": "Abdul Hai Hazem"})
    resp = client.get("/api/audit", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["events"]) == 1
    assert data["events"][0]["ip"] == "testclient"
    assert data["events"][0]["query"]["name"] == "Abdul Hai Hazem"


def test_audit_endpoint_pagination(monkeypatch):
    monkeypatch.setenv("AUDIT_ADMIN_TOKEN", "secret")
    client = TestClient(create_app(entities=ENTITIES))
    client.get("/api/search", params={"name": "Abdul Hai Hazem"})
    client.get("/api/search", params={"name": "Rosneft"})
    resp = client.get("/api/audit", params={"limit": 1, "offset": 1}, headers={"Authorization": "Bearer secret"})
    data = resp.json()
    assert data["total"] == 2
    assert len(data["events"]) == 1


def test_audit_failure_does_not_break_search(monkeypatch):
    import app.audit as audit_module

    def boom(*args, **kwargs):
        raise RuntimeError("disk vol")

    monkeypatch.setattr(audit_module, "log_event", boom)
    client = TestClient(create_app(entities=ENTITIES))
    resp = client.get("/api/search", params={"name": "Abdul Hai Hazem"})
    assert resp.status_code == 200
    assert resp.json()["results"]
