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


THRESHOLD = 60
MAX_RESULTS = 20


@dataclass
class MatchDetail:
    feature: str
    score: int
    label: str


@dataclass
class EuMatchResult:
    entity: dict
    total_score: int
    details: list[MatchDetail]
    matched_alias: str | None = None


def birth_year_score(query_year: int, birthdates: list[dict]) -> int:
    best = 0
    for b in birthdates:
        if b.get("year") is not None:
            diff = abs(query_year - b["year"])
            if diff == 0:
                score = 100
            elif diff == 1:
                score = 75
            elif diff == 2:
                score = 50
            else:
                score = 0
        elif b["year_from"] is not None and b["year_to"] is not None:
            score = 75 if b["year_from"] <= query_year <= b["year_to"] else 0
        else:
            score = 0
        best = max(best, score)
    return best


def _fuzzy_threshold(ratio: int) -> int:
    if ratio >= 85:
        return 100
    if ratio >= 70:
        return 50
    return 0


def nationality_score(query: str, citizenships: list[dict]) -> int:
    q = query.strip().upper()
    best = 0
    for c in citizenships:
        if c["iso2"] == q:
            best = max(best, 100)
        if c["description"]:
            best = max(best, _fuzzy_threshold(fuzz.token_set_ratio(q, c["description"])))
    return best


def birth_place_score(query: str, birthdates: list[dict]) -> int:
    q = query.strip()
    best = 0
    for b in birthdates:
        for candidate in (b["place"], b["city"]):
            if candidate:
                score = fuzz.token_set_ratio(q, candidate)
                best = max(best, score if score >= 70 else 0)
    return best


def score_entity(entity: dict, query: SearchQuery) -> EuMatchResult | None:
    if query.entity_type and entity["subject_type"] != query.entity_type:
        return None
    weights = []
    details = []
    name_score_value, matched_alias = name_score(query.name, entity["aliases"])
    weights.append(WEIGHT_NAME)
    label = f'Naam {name_score_value}% (via "{matched_alias}")' if matched_alias else "Naam 0%"
    details.append(MatchDetail("naam", name_score_value, label))
    if query.birth_year is not None:
        s = birth_year_score(query.birth_year, entity["birthdates"])
        weights.append(WEIGHT_BIRTH_YEAR)
        label = "Geboortejaar exact" if s == 100 else f"Geboortejaar ({s}%)"
        details.append(MatchDetail("geboortejaar", s, label))
    if query.nationality:
        s = nationality_score(query.nationality, entity["citizenships"])
        weights.append(WEIGHT_NATIONALITY)
        label = "Nationaliteit match" if s >= 85 else f"Nationaliteit ({s}%)"
        details.append(MatchDetail("nationaliteit", s, label))
    if query.birth_place:
        s = birth_place_score(query.birth_place, entity["birthdates"])
        weights.append(WEIGHT_BIRTH_PLACE)
        details.append(MatchDetail("geboorteplaats", s, f"Geboorteplaats {s}%"))
    if not weights:
        return None
    total = round(sum(w * d.score for w, d in zip(weights, details)) / sum(weights))
    if total < THRESHOLD:
        return None
    return EuMatchResult(entity=entity, total_score=total, details=details, matched_alias=matched_alias)


def search_eu(entities: list[dict], query: SearchQuery) -> list[EuMatchResult]:
    results = []
    for entity in entities:
        result = score_entity(entity, query)
        if result is not None:
            results.append(result)
    results.sort(key=lambda r: r.total_score, reverse=True)
    return results[:MAX_RESULTS]
