import json
import urllib.parse

import httpx

from jevops.grafana import GrafanaClient


def _json_requests(
    responses: dict[str, list[tuple[int, dict]]],
) -> tuple[httpx.MockTransport, list[tuple[str, str, dict]]]:
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        path = urllib.parse.unquote(
            request.url.raw_path.decode() if isinstance(request.url.raw_path, bytes) else request.url.raw_path
        )
        seen.append((request.method, path, body))
        seq = responses.get(path)
        if seq:
            status, payload = seq.pop(0)
            return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler), seen


def test_bearer_token_auth():
    transport, seen = _json_requests({"/api/health": [(200, {"database": "ok"})]})
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    g.health()
    assert g._headers["Authorization"] == "Bearer tok"


def test_basic_auth_when_no_token():
    transport, seen = _json_requests({"/api/health": [(200, {})]})
    g = GrafanaClient("http://g.test", user="admin", password="pw", transport=transport)
    g.health()
    assert g._headers["Authorization"].startswith("Basic ")


def test_health_returns_ok():
    transport, _ = _json_requests({"/api/health": [(200, {"database": "ok", "version": "11.2.2"})]})
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    assert g.health()["database"] == "ok"


def test_ensure_datasource_creates_then_updates():
    responses = {
        "/api/datasources": [(200, {"datasource": {"id": 7, "uid": "ds-uid"}, "id": 7, "message": "Datasource added"})],
        "/api/datasources/name/Jevops Elasticsearch": [
            (404, {"message": "Data source not found"}),
            (200, {"id": 7, "uid": "ds-uid"}),
        ],
        "/api/datasources/7": [(200, {"datasource": {"id": 7, "uid": "ds-uid"}})],
    }
    transport, seen = _json_requests(responses)
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    ds = g.ensure_datasource("Jevops Elasticsearch", "http://es.test:9200", "jevops-decisions")
    assert ds["uid"] == "ds-uid"
    assert seen[0][0] == "GET" and seen[0][1].startswith("/api/datasources/name/")
    assert seen[1][0] == "POST" and seen[1][1] == "/api/datasources"
    assert seen[1][2]["type"] == "elasticsearch"
    assert seen[1][2]["jsonData"]["index"] == "jevops-decisions"
    g.ensure_datasource("Jevops Elasticsearch", "http://es.test:9200", "jevops-decisions")
    assert seen[3][0] == "PUT" and seen[3][1] == "/api/datasources/7"


def test_ensure_contact_point_creates():
    responses = {
        "/api/v1/provisioning/contact-points": [(201, {"uid": "cp1"})],
    }
    transport, seen = _json_requests(responses)
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    cp = g.ensure_contact_point("jevops-webhook", "http://jevops:8080/notify")
    assert cp["uid"] == "cp1"
    body = seen[0][2]
    assert body["type"] == "webhook"
    assert body["settings"]["url"] == "http://jevops:8080/notify"
    assert body["name"] == "jevops-webhook"


def test_ensure_folder_creates_when_missing():
    responses = {
        "/api/folders": [(200, {"uid": "f1", "title": "Jevops"})],
    }
    transport, seen = _json_requests(responses)
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    folder = g.ensure_folder("Jevops")
    assert folder["uid"] == "f1"
    assert seen[0][0] == "POST"
    assert seen[0][2]["title"] == "Jevops"


def test_ensure_dashboard_posts_db_payload_with_panels():
    responses = {
        "/api/dashboards/db": [(200, {"uid": "dash1", "version": 1})],
    }
    transport, seen = _json_requests(responses)
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    result = g.ensure_dashboard("ds-uid", "f1")
    assert result["uid"] == "dash1"
    body = seen[0][2]
    assert body["overwrite"] is True
    assert body["folderUid"] == "f1"
    dashboard = body["dashboard"]
    assert dashboard["uid"] == "jevops-sre-triage"
    assert len(dashboard["panels"]) >= 4
    titles = [p["title"] for p in dashboard["panels"]]
    assert "Pages by severity" in titles
    assert "Triage decisions over time" in titles


def test_ensure_alert_rule_posts_rule_with_contact_point():
    responses = {
        "/api/v1/provisioning/alert-rules": [(201, {"uid": "rule1"})],
    }
    transport, seen = _json_requests(responses)
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    rule = g.ensure_alert_rule("f1", "ds-uid", "cp1")
    assert rule["uid"] == "rule1"
    body = seen[0][2]
    assert body["folderUID"] == "f1"
    assert body["notification_settings"]["receiver"] == "cp1"
    assert body["title"]
    assert body["data"][0]["datasourceUid"] == "ds-uid"
    assert body["condition"] == "B"


def test_ensure_alert_rule_updates_on_conflict():
    responses = {
        "/api/v1/provisioning/alert-rules": [(412, {"message": "rule already exists"})],
        "/api/v1/provisioning/alert-rules/jevops-critical-pages": [(200, {"uid": "jevops-critical-pages"})],
    }
    transport, seen = _json_requests(responses)
    g = GrafanaClient("http://g.test", token="tok", transport=transport)
    rule = g.ensure_alert_rule("f1", "ds-uid", "cp1")
    assert rule["uid"] == "jevops-critical-pages"
    assert seen[1][0] == "PUT"
