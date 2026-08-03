import json

import pytest

from app import risk_countries


@pytest.fixture(autouse=True)
def clear_cache():
    risk_countries.load_risk_countries.cache_clear()
    yield
    risk_countries.load_risk_countries.cache_clear()


def write(tmp_path, name, **over):
    data = {
        "version": "v1",
        "updated_at": "t",
        "fatf_blacklist": ["IR"],
        "fatf_greylist": ["MM"],
        "eu_high_risk": [],
    }
    data.update(over)
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_default_path_env(monkeypatch):
    monkeypatch.delenv("RISK_COUNTRIES", raising=False)
    assert risk_countries.default_path() == __import__("pathlib").Path("data/risk_countries.json")
    monkeypatch.setenv("RISK_COUNTRIES", "/tmp/risk.json")
    assert risk_countries.default_path() == __import__("pathlib").Path("/tmp/risk.json")


def test_load_missing_returns_empty(tmp_path):
    data = risk_countries.load_risk_countries(tmp_path / "none.json")
    assert data["fatf_blacklist"] == []
    assert data["version"] == ""


def test_load_normalises_upper(tmp_path):
    path = write(tmp_path, "risk.json", fatf_blacklist=["ir", "KP"])
    data = risk_countries.load_risk_countries(path)
    assert data["fatf_blacklist"] == ["IR", "KP"]


def test_risk_flags(tmp_path):
    path = write(tmp_path, "risk.json", fatf_blacklist=["IR", "KP"], eu_high_risk=["IR"])
    data = risk_countries.load_risk_countries(path)
    flags = risk_countries.risk_flags(["IR", "NL", "kp"], data=data)
    assert flags == [
        {"code": "IR", "lists": ["fatf_blacklist", "eu_high_risk"]},
        {"code": "KP", "lists": ["fatf_blacklist"]},
    ]


def test_risk_flags_empty_codes():
    assert risk_countries.risk_flags([]) == []


def test_validate_ok():
    assert risk_countries.validate({"fatf_blacklist": ["IR"], "fatf_greylist": [], "eu_high_risk": []}) == []


def test_validate_errors():
    errs = risk_countries.validate({
        "fatf_blacklist": ["I", "IR", "IR", 5],
        "fatf_greylist": "niet-lijst",
        "eu_high_risk": [],
    })
    assert any("ISO2" in e for e in errs)
    assert any("duplicaat" in e for e in errs)
    assert any("moet een lijst" in e for e in errs)


def test_validate_falsy_non_list_is_error():
    errs = risk_countries.validate({"fatf_blacklist": None, "fatf_greylist": [], "eu_high_risk": []})
    assert any("moet een lijst" in e for e in errs)
