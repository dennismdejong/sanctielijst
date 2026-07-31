from dataclasses import dataclass

from rapidfuzz import fuzz

WEIGHT_NAME = 60
WEIGHT_BIRTH_YEAR = 20
WEIGHT_NATIONALITY = 10
WEIGHT_BIRTH_PLACE = 10
STRONG_BONUS = 1.2


@dataclass
class SearchQuery:
    name: str
    birth_year: int | None = None
    nationality: str | None = None
    birth_place: str | None = None
    entity_type: str | None = None


def name_score(query_name: str, aliases: list[dict]) -> tuple[int, str | None]:
    best_score = 0
    best_alias = None
    q = query_name.strip()
    for alias in aliases:
        candidate = alias["whole_name"] or f"{alias['first_name']} {alias['last_name']}".strip()
        if not candidate:
            continue
        score = fuzz.token_set_ratio(q, candidate)
        if alias["strong"]:
            score = min(100, int(score * STRONG_BONUS))
        if score > best_score:
            best_score = score
            best_alias = alias["whole_name"] or candidate
    return best_score, best_alias
