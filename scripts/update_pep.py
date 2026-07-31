import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pep_ingest

_STOP = {"flag": False}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OpenSanctions PEP-lijsten downloaden")
    parser.add_argument("--root", default=pep_ingest.default_root(), help=f"data-map (default: %(default)s)")
    parser.add_argument("--force", action="store_true", help="alles opnieuw downloaden, ook ongewijzigde")
    parser.add_argument("--dry-run", action="store_true", help="plan alleen tonen, niets downloaden")
    parser.add_argument("--limit", type=int, default=None, help="maximaal aantal bronnen (testen)")
    parser.add_argument("--interval", type=float, default=0, help="blijf draaien, update elke N uren (Docker)")
    parser.add_argument("--log", default=None, help="schrijf logs ook naar dit bestand")
    return parser.parse_args(argv)


def _emit(args, text: str) -> None:
    print(text)
    if args.log:
        Path(args.log).open("a").write(text + "\n")


def run_once(args) -> int:
    try:
        index = pep_ingest.fetch_index()
    except Exception as exc:
        print(f"FATAAL: index download mislukt: {exc}", file=sys.stderr)
        return 1

    def log(msg: str) -> None:
        _emit(args, msg)

    manifest = pep_ingest.refresh_pep(
        Path(args.root),
        index=index,
        force=args.force,
        dry_run=args.dry_run,
        limit=args.limit,
        logger=log,
    )
    stats = manifest.get("stats", {})
    _emit(
        args,
        "Klaar: "
        f"{stats.get('downloaded', 0)} gedownload, "
        f"{stats.get('skipped', 0)} overgeslagen, "
        f"{stats.get('failed', 0)} mislukt "
        f"(totaal {stats.get('total', 0)})",
    )
    return 0


def _handle_stop(signum, frame):
    _STOP["flag"] = True


def run_loop(args) -> int:
    _STOP["flag"] = False
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    last_code = 0
    while not _STOP["flag"]:
        last_code = run_once(args)
        if _STOP["flag"]:
            break
        time.sleep(args.interval * 3600)
    return last_code


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.interval and args.interval > 0:
        return run_loop(args)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())
