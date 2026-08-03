import json

from scripts import update_risk_countries as cli


def test_main_valid_rewrites_updated_at(tmp_path):
    path = tmp_path / "risk.json"
    path.write_text(json.dumps({"version": "v1", "updated_at": "old", "fatf_blacklist": ["IR"], "fatf_greylist": [], "eu_high_risk": []}))
    assert cli.main(["--path", str(path)]) == 0
    data = json.loads(path.read_text())
    assert data["updated_at"] != "old"
    assert data["fatf_blacklist"] == ["IR"]


def test_main_invalid_iso2_fails(tmp_path, capsys):
    path = tmp_path / "risk.json"
    path.write_text(json.dumps({"version": "v1", "updated_at": "t", "fatf_blacklist": ["I"], "fatf_greylist": [], "eu_high_risk": []}))
    assert cli.main(["--path", str(path)]) == 1
    assert "FOUT" in capsys.readouterr().err


def test_main_non_dict_json_fails(tmp_path, capsys):
    path = tmp_path / "risk.json"
    path.write_text(json.dumps(["a", "b"]))
    assert cli.main(["--path", str(path)]) == 1
    assert "JSON-object" in capsys.readouterr().err


def test_main_missing_file_fails(tmp_path, capsys):
    assert cli.main(["--path", str(tmp_path / "nope.json")]) == 1
    assert "FATAAL" in capsys.readouterr().err
