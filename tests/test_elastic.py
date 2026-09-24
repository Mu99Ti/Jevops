import json

import httpx

from jevops.elastic import ElasticClient

DECISIONS_MAPPINGS = {
    "properties": {
        "service": {"type": "keyword"},
        "action": {"type": "keyword"},
        "severity": {"type": "keyword"},
        "category": {"type": "keyword"},
        "at": {"type": "date"},
        "ts": {"type": "date"},
    }
}


def test_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    c = ElasticClient("https://es.test", api_key="abc", transport=httpx.MockTransport(handler))
    c._request("GET", "/")
    assert seen["auth"] == "ApiKey abc"


def test_basic_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    c = ElasticClient("https://es.test", username="elastic", password="pw", transport=httpx.MockTransport(handler))
    c._request("GET", "/")
    assert seen["auth"].startswith("Basic ")


def test_ensure_index_creates_when_missing_then_skips():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "HEAD":
            return httpx.Response(404)
        return httpx.Response(200, json={"acknowledged": True})

    c = ElasticClient("https://es.test", transport=httpx.MockTransport(handler))
    c.ensure_index("jevops-decisions", DECISIONS_MAPPINGS)
    c.ensure_index("jevops-decisions", DECISIONS_MAPPINGS)
    assert calls == [
        "HEAD /jevops-decisions",
        "PUT /jevops-decisions",
        "HEAD /jevops-decisions",
        "PUT /jevops-decisions",
    ]


def test_bulk_sends_ndjson_with_content_type():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ct"] = request.headers.get("content-type", "")
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"errors": False, "items": []})

    c = ElasticClient("https://es.test", transport=httpx.MockTransport(handler))
    c.bulk("jevops-decisions", [{"id": "e1", "service": "api"}, {"id": "e2", "service": "web"}])
    assert captured["ct"] == "application/x-ndjson"
    lines = captured["body"].strip().split("\n")
    assert json.loads(lines[0]) == {"index": {"_id": "e1"}}
    assert json.loads(lines[1])["service"] == "api"
    assert json.loads(lines[2]) == {"index": {"_id": "e2"}}
    assert captured["body"].endswith("\n")


def test_bulk_raises_on_item_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": True, "items": [{"index": {"status": 400, "error": {"reason": "bad"}}}]}
        )

    c = ElasticClient("https://es.test", transport=httpx.MockTransport(handler))
    try:
        c.bulk("idx", [{"id": "e1"}])
    except RuntimeError as e:
        assert "bad" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_search_since_builds_query_and_returns_hits_and_cursor():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {"_source": {"message": "boom", "service": "api"}, "sort": ["2026-09-24T13:40:12Z", "abc"]},
                        {"_source": {"message": "bang", "service": "web"}, "sort": ["2026-09-24T13:40:12Z", "def"]},
                    ]
                }
            },
        )

    c = ElasticClient("https://es.test", transport=httpx.MockTransport(handler))
    hits, cursor = c.search_since("logs-*", since="2026-09-24T13:00:00Z", size=2, search_after=None)
    assert [h["message"] for h in hits] == ["boom", "bang"]
    assert cursor == ["2026-09-24T13:40:12Z", "def"]
    body = captured["body"]
    assert body["query"]["range"]["@timestamp"]["gte"] == "2026-09-24T13:00:00Z"
    assert body["sort"] == [{"@timestamp": {"order": "asc"}}, {"_id": {"order": "asc"}}]
    assert "search_after" not in body


def test_search_since_passes_search_after():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"hits": {"hits": []}})

    c = ElasticClient("https://es.test", transport=httpx.MockTransport(handler))
    hits, cursor = c.search_since(
        "logs-*", since="2026-01-01T00:00:00Z", size=10, search_after=["2026-09-24T13:00:00Z", "abc"]
    )
    assert hits == []
    assert captured["body"]["search_after"] == ["2026-09-24T13:00:00Z", "abc"]
    assert cursor == ["2026-09-24T13:00:00Z", "abc"]


def test_ping_returns_version():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": {"number": "8.15.0"}})

    c = ElasticClient("https://es.test", transport=httpx.MockTransport(handler))
    assert c.ping() == "8.15.0"
