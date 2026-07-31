from app.matcher import STRONG_BONUS, WEIGHT_NAME, name_score


def alias(whole, strong=False):
    return {"whole_name": whole, "first_name": "", "last_name": "", "strong": strong, "function": "", "title": ""}


def test_name_exact_100():
    aliases = [alias("John Smith", strong=True)]
    score, matched = name_score("John Smith", aliases)
    assert score == 100
    assert matched == "John Smith"


def test_name_fuzzy_high():
    aliases = [alias("John Smith", strong=False)]
    score, _ = name_score("Jhon Smit", aliases)
    assert 80 <= score <= 99


def test_name_strong_bonus_beats_weak():
    aliases = [alias("Jon Smit", strong=False), alias("John Smith", strong=True)]
    strong_score, strong_alias = name_score("John Smith", aliases)
    weak_score, weak_alias = name_score("Jon Smit", aliases)
    assert strong_alias == "John Smith"
    assert weak_alias == "Jon Smit"
    assert weak_score >= 80
    assert strong_score >= weak_score


def test_name_token_containment_is_100():
    assert name_score("John Smith", [alias("John Michael Smith", strong=False)])[0] == 100
    assert name_score("Rosneft", [alias("Rosneft Oil Company", strong=False)])[0] == 100
    assert name_score("Vladimir Putin", [alias("Vladimir Vladimirovich PUTIN", strong=False)])[0] == 100
    assert name_score("John Smith", [alias("Xavier Xyzzy", strong=False)])[0] < 90


from app.matcher import (
    MAX_RESULTS,
    THRESHOLD,
    SearchQuery,
    birth_place_score,
    birth_year_score,
    nationality_score,
    score_entity,
    search_eu,
)


def make_entity(**overrides):
    entity = {
        "logical_id": "1",
        "eu_reference_number": "EU.1",
        "united_nations_id": "",
        "designation_date": "",
        "subject_type": "person",
        "aliases": [],
        "citizenships": [],
        "birthdates": [],
        "addresses": [],
        "identifications": [],
        "regulations": [],
        "remarks": [],
    }
    entity.update(overrides)
    return entity


def test_birth_year_scores():
    bd = [{"year": 1971}]
    assert birth_year_score(1971, bd) == 100
    assert birth_year_score(1972, bd) == 75
    assert birth_year_score(1973, bd) == 50
    assert birth_year_score(1975, bd) == 0
    assert birth_year_score(1971, []) == 0


def test_birth_year_range():
    bd = [{"year_from": 1950, "year_to": 1960}]
    assert birth_year_score(1955, bd) == 75
    assert birth_year_score(1940, bd) == 0


def test_nationality_scores():
    cit = [{"iso2": "AF", "description": "AFGHANISTAN"}]
    assert nationality_score("AF", cit) == 100
    assert nationality_score("af", cit) == 100
    assert nationality_score("Afghanistan", cit) == 100
    assert nationality_score("NL", cit) == 0


def test_birth_place_scores():
    bd = [{"place": "Kabul", "city": ""}]
    assert birth_place_score("Kabul", bd) == 100
    assert birth_place_score("Kabol", bd) >= 70
    assert birth_place_score("Amsterdam", bd) == 0


def test_score_entity_name_only():
    entity = make_entity(aliases=[{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}])
    result = score_entity(entity, SearchQuery(name="John Smith"))
    assert result is not None
    assert result.total_score == 100
    assert result.matched_alias == "John Smith"
    assert result.details[0].feature == "naam"


def test_score_entity_weighted_combination():
    entity = make_entity(
        aliases=[{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}],
        birthdates=[{"year": 1971}],
    )
    result = score_entity(entity, SearchQuery(name="John Smith", birth_year=1971))
    assert result is not None
    expected = round((60 * 100 + 20 * 100) / 80)
    assert result.total_score == expected
    assert len(result.details) == 2


def test_score_entity_below_threshold_returns_none():
    entity = make_entity(aliases=[{"whole_name": "Xavier Xyzzy", "first_name": "", "last_name": "", "strong": False, "function": "", "title": ""}])
    result = score_entity(entity, SearchQuery(name="John Smith"))
    assert result is None


def test_score_entity_entity_type_filter():
    entity = make_entity(subject_type="person", aliases=[{"whole_name": "John Smith", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}])
    assert score_entity(entity, SearchQuery(name="John Smith", entity_type="enterprise")) is None
    assert score_entity(entity, SearchQuery(name="John Smith", entity_type="person")) is not None


def test_search_eu_sorts_and_caps():
    entities = []
    for i in range(30):
        entities.append(make_entity(
            logical_id=str(i),
            aliases=[{"whole_name": "Name Number", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}],
        ))
    query = SearchQuery(name="Name Number")
    results = search_eu(entities, query)
    assert len(results) <= MAX_RESULTS
    scores = [r.total_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_eu_empty_when_no_match():
    entity = make_entity(aliases=[{"whole_name": "Rosneft", "first_name": "", "last_name": "", "strong": True, "function": "", "title": ""}])
    assert search_eu([entity], SearchQuery(name="Completely Unrelated Name")) == []
