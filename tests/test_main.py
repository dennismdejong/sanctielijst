import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(autouse=True)
def _isolate_os_env(monkeypatch):
    monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)


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


def test_refresh_reports_fresh_source(tmp_path, monkeypatch):
    import app.main as main

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / main.ingest.XML_FILENAME).write_bytes(b"<export/>")
    monkeypatch.setattr(main.ingest, "refresh", lambda *a, **k: {"cached_at": 1, "generated_at": "x", "entity_count": 2, "source": "fresh"})
    monkeypatch.setattr(main.ingest, "parse_export", lambda *a, **k: ENTITIES)
    client = TestClient(create_app(entities=ENTITIES, cache_dir=cache_dir))
    resp = client.post("/api/refresh")
    assert resp.status_code == 200
    assert resp.json()["source"] == "fresh"


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


import pytest

from app import pep_index


@pytest.fixture(autouse=True)
def pep_disabled(monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "0")


def test_default_pep_root_uses_env(monkeypatch):
    from pathlib import Path

    from app import main as main_module
    monkeypatch.setenv("PEP_DATA_DIR", "/data/pep")
    assert main_module.default_pep_root() == Path("/data/pep")
    monkeypatch.delenv("PEP_DATA_DIR", raising=False)
    assert main_module.default_pep_root() == main_module.PEP_ROOT


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


def test_status_pep_disabled():
    client = TestClient(create_app(entities=ENTITIES))
    data = client.get("/api/status").json()
    assert data["pep_index"]["enabled"] is False


def test_status_pep_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path, pep_sync=True))
    data = client.get("/api/status").json()
    assert data["pep_index"]["enabled"] is True
    assert data["pep_index"]["entity_count"] == 1
    assert data["pep_index"]["datasets_count"] == 1


def test_search_pep_hit_with_sources(tmp_path, monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path, pep_sync=True))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()
    pep_results = [r for r in data["results"] if r["source"] == "pep"]
    assert pep_results
    first = pep_results[0]
    assert first["score"] == 100
    assert first["pep"]["id"] == "NK-x"
    assert first["pep"]["url"] == "https://opensanctions.org/entities/NK-x"
    assert first["pep"]["datasets"][0]["id"] == "ar_parliament"
    assert first["pep"]["datasets"][0]["title"] == "Argentina Members of Parliament"
    assert first["pep"]["datasets"][0]["country"] == "ar"


def test_search_pep_entity_type_filter(tmp_path, monkeypatch):
    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path, pep_sync=True))
    data = client.get("/api/search", params={"name": "JORGE FERNANDEZ", "entity_type": "enterprise"}).json()
    assert not [r for r in data["results"] if r["source"] == "pep"]


def test_pep_background_load(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv(pep_index.INDEX_ENV, "1")
    _write_pep_fixture(tmp_path)
    client = TestClient(create_app(entities=ENTITIES, pep_root=tmp_path))
    assert client.get("/api/status").json()["pep_index"]["status"] == "loading"
    ready = False
    for _ in range(40):
        data = client.get("/api/status").json()
        if data["pep_index"]["status"] == "ready":
            ready = True
            break
        time.sleep(0.05)
    assert ready
    assert data["pep_index"]["entity_count"] == 1
    assert client.get("/api/search", params={"name": "JORGE FERNANDEZ"}).json()["results"][0]["source"] == "pep"
