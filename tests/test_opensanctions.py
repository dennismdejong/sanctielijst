from app.opensanctions import match_opensanctions


SAMPLE_RESPONSE = {
    "limit": 5,
    "responses": {
        "q": {
            "status": 200,
            "results": [
                {
                    "id": "NK-abc123",
                    "caption": "Aleksandr ZAKHAROV",
                    "schema": "Person",
                    "score": 0.85,
                    "match": True,
                    "explanations": {"name_match": {"score": 0.9}},
                    "datasets": ["eu_fsf", "us_ofac_sdn"],
                    "properties": {"birthDate": ["1965"], "citizenship": ["ru"]},
                }
            ],
            "total": {"value": 1, "relation": "eq"},
            "query": {},
        }
    },
}


def test_match_opensanctions_sends_expected_payload(monkeypatch):
    import requests

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE_RESPONSE

    def fake_post(url, headers, params, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post)
    results = match_opensanctions("KEY123", "Aleksandr Zakharov", birth_year=1965, nationality="RU")
    assert captured["url"] == "https://api.opensanctions.org/match/default"
    assert captured["headers"]["Authorization"] == "ApiKey KEY123"
    assert captured["params"]["threshold"] == 0.7
    assert captured["params"]["limit"] == 10
    assert captured["timeout"] == 30
    query = captured["json"]["queries"]["q"]
    assert query["schema"] == "Person"
    assert query["properties"]["firstName"] == ["Aleksandr"]
    assert query["properties"]["lastName"] == ["Zakharov"]
    assert query["properties"]["birthDate"] == ["1965"]
    assert query["properties"]["nationality"] == ["RU"]


def test_match_opensanctions_parses_results(monkeypatch):
    import requests

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE_RESPONSE

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    results = match_opensanctions("KEY123", "Aleksandr Zakharov")
    assert len(results) == 1
    r = results[0]
    assert r["id"] == "NK-abc123"
    assert r["caption"] == "Aleksandr ZAKHAROV"
    assert r["score"] == 0.85
    assert r["match"] is True
    assert r["url"] == "https://opensanctions.org/entities/NK-abc123"


def test_match_opensanctions_raises_on_http_error(monkeypatch):
    import requests

    class FakeResp:
        def raise_for_status(self):
            raise requests.HTTPError("401")

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    try:
        match_opensanctions("BAD", "x")
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass
