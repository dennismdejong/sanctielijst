import json
import os
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path("data/risk_countries.json")
_ISO2 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

_EMPTY = {"version": "", "updated_at": "", "fatf_blacklist": [], "fatf_greylist": [], "eu_high_risk": []}


def default_path() -> Path:
    env = os.environ.get("RISK_COUNTRIES")
    return Path(env) if env else DEFAULT_PATH


@lru_cache(maxsize=1)
def load_risk_countries(path: Path | None = None) -> dict:
    path = path or default_path()
    try:
        data = json.loads(path.read_text())
    except Exception:
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    return {
        "version": str(data.get("version", "")),
        "updated_at": str(data.get("updated_at", "")),
        "fatf_blacklist": [str(c).upper() for c in (data.get("fatf_blacklist") or [])],
        "fatf_greylist": [str(c).upper() for c in (data.get("fatf_greylist") or [])],
        "eu_high_risk": [str(c).upper() for c in (data.get("eu_high_risk") or [])],
    }


def risk_flags(country_codes: list[str], data: dict | None = None) -> list[dict]:
    data = data or load_risk_countries()
    lookup = {
        "fatf_blacklist": set(data["fatf_blacklist"]),
        "fatf_greylist": set(data["fatf_greylist"]),
        "eu_high_risk": set(data["eu_high_risk"]),
    }
    flags = []
    for code in country_codes:
        if not code:
            continue
        c = code.strip().upper()
        lists = [name for name, codes in lookup.items() if c in codes]
        if lists:
            flags.append({"code": c, "lists": lists})
    return flags


def validate(data: dict) -> list[str]:
    errors = []
    for key in ("fatf_blacklist", "fatf_greylist", "eu_high_risk"):
        values = data.get(key) or []
        if not isinstance(values, list):
            errors.append(f"{key}: moet een lijst zijn")
            continue
        seen = set()
        for code in values:
            if not isinstance(code, str) or len(code) != 2 or any(ch not in _ISO2 for ch in code.upper()):
                errors.append(f"{key}: ongeldige ISO2-code {code!r}")
            elif code.upper() in seen:
                errors.append(f"{key}: duplicaat {code.upper()}")
            else:
                seen.add(code.upper())
    return errors
