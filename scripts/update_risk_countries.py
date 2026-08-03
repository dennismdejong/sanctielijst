import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import risk_countries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Risicolandenlijst (FATF/EU) valideren en bijwerken")
    parser.add_argument("--path", default=risk_countries.default_path(), help="pad naar de JSON (default: %(default)s)")
    args = parser.parse_args(argv)
    path = Path(args.path)
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        print(f"FATAAL: kan {path} niet lezen: {exc}", file=sys.stderr)
        return 1
    errors = risk_countries.validate(data)
    if errors:
        for error in errors:
            print(f"FOUT: {error}", file=sys.stderr)
        return 1
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    print(f"OK: {path} gevalideerd en bijgewerkt (updated_at={data['updated_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
