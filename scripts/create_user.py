import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Gebruiker aanmaken voor de Compliance Zoeker")
    parser.add_argument("--username", required=True, help="inlognaam")
    parser.add_argument("--password", default=None, help="wachtwoord (verplicht voor lokale gebruikers)")
    parser.add_argument("--role", default="viewer", choices=auth.ROLES, help=f"rol (default: %(default)s)")
    parser.add_argument("--entra-subject", default=None, help="Microsoft Entra sub-claim (IdP-gebruiker)")
    parser.add_argument("--db", default=None, help="auth-database (default: AUTH_DB of data/auth.sqlite)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.password is None and args.entra_subject is None:
        print("Fout: --password of --entra-subject is verplicht", file=sys.stderr)
        return 1
    if args.db:
        os.environ["AUTH_DB"] = args.db
    try:
        user = auth.create_user(
            auth.default_auth_db(),
            username=args.username,
            password=args.password,
            role=args.role,
            idp="entra" if args.entra_subject else None,
            idp_subject=args.entra_subject,
        )
    except ValueError as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        return 1
    print(f"Gebruiker aangemaakt: {user['username']}")
    print(f"id: {user['id']}")
    print(f"rol: {user['role']}")
    if user["idp"]:
        print(f"idp: {user['idp']} (subject: {user['idp_subject']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
