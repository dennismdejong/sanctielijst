from scripts import create_user as cli


def test_parse_args_defaults():
    args = cli.parse_args(["--username", "alice"])
    assert args.username == "alice"
    assert args.password is None
    assert args.role == "viewer"
    assert args.entra_subject is None
    assert args.db is None


def test_parse_args_full():
    args = cli.parse_args(
        ["--username", "alice", "--password", "geheim", "--role", "admin", "--entra-subject", "sub-1", "--db", "/tmp/x.sqlite"]
    )
    assert args.role == "admin"
    assert args.entra_subject == "sub-1"
    assert args.db == "/tmp/x.sqlite"


def test_main_creates_local_user(tmp_path, capsys):
    db = tmp_path / "auth.sqlite"
    assert cli.main(["--db", str(db), "--username", "alice", "--password", "geheim", "--role", "admin"]) == 0
    from app import auth

    user = auth.find_by_credentials(db, "alice", "geheim")
    assert user is not None
    assert user["role"] == "admin"
    assert "alice" in capsys.readouterr().out


def test_main_creates_entra_user(tmp_path):
    db = tmp_path / "auth.sqlite"
    assert cli.main(["--db", str(db), "--username", "bob@example.com", "--entra-subject", "sub-1"]) == 0
    from app import auth

    user = auth.find_or_create_idp_user(db, "entra", "sub-1")
    assert user["username"] == "bob@example.com"
    assert user["role"] == "viewer"
    assert user["idp_subject"] == "sub-1"


def test_main_requires_password_or_entra_subject(tmp_path, capsys):
    db = tmp_path / "auth.sqlite"
    assert cli.main(["--db", str(db), "--username", "alice"]) == 1
    assert "verplicht" in capsys.readouterr().err


def test_main_duplicate_username_errors(tmp_path, capsys):
    db = tmp_path / "auth.sqlite"
    assert cli.main(["--db", str(db), "--username", "alice", "--password", "geheim"]) == 0
    assert cli.main(["--db", str(db), "--username", "alice", "--password", "anders"]) == 1
    assert "bestaat al" in capsys.readouterr().err
