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
