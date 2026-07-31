import requests

API_URL = "https://api.opensanctions.org/match/default"
TIMEOUT = 30
THRESHOLD = 0.7
LIMIT = 10
TOPICS = ["sanction", "sanction.linked", "debarment"]


def match_opensanctions(
    api_key: str,
    name: str,
    birth_year: int | None = None,
    nationality: str | None = None,
    birth_place: str | None = None,
) -> list[dict]:
    parts = name.split()
    properties = {"name": [name]}
    if parts:
        properties["firstName"] = [parts[0]]
        if len(parts) > 1:
            properties["lastName"] = [" ".join(parts[1:])]
    if birth_year is not None:
        properties["birthDate"] = [str(birth_year)]
    if nationality:
        properties["nationality"] = [nationality]
    if birth_place:
        properties["birthPlace"] = [birth_place]
    query = {"schema": "Person", "properties": properties}
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"ApiKey {api_key}"},
        params={"threshold": THRESHOLD, "limit": LIMIT, "topics": TOPICS},
        json={"queries": {"q": query}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    response = data.get("responses", {}).get("q", {})
    return [
        {
            "id": r.get("id", ""),
            "caption": r.get("caption", ""),
            "schema": r.get("schema", ""),
            "score": r.get("score", 0.0),
            "match": r.get("match", False),
            "explanations": r.get("explanations", {}),
            "datasets": r.get("datasets", []),
            "properties": r.get("properties", {}),
            "url": f"https://opensanctions.org/entities/{r.get('id', '')}" if r.get("id") else "",
        }
        for r in response.get("results", [])
    ]
