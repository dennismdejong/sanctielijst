import json
import subprocess
import sys

from app.rebuild import main as rebuild_main


def test_rebuild_main_outputs_stats(tmp_path, capsys):
    db_path = tmp_path / "search.sqlite"
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    rc = rebuild_main(["--db", str(db_path), "--eu-xml", str(eu_xml), "--pep-root", str(tmp_path)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
    assert db_path.exists()


def test_rebuild_module_as_subprocess(tmp_path):
    db_path = tmp_path / "search.sqlite"
    eu_xml = tmp_path / "eu.xml"
    eu_xml.write_bytes(b"<export/>")
    proc = subprocess.run(
        [sys.executable, "-m", "app.rebuild", "--db", str(db_path), "--eu-xml", str(eu_xml), "--pep-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["total"] == 0
    assert db_path.exists()
