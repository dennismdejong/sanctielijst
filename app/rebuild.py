import argparse
import json
import sys
from pathlib import Path

from .search_index import rebuild_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zoekindex opnieuw opbouwen (subproces)")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--eu-xml", required=True, type=Path)
    parser.add_argument("--pep-root", required=True, type=Path)
    args = parser.parse_args(argv)
    stats = rebuild_index(args.db, args.eu_xml, args.pep_root)
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
