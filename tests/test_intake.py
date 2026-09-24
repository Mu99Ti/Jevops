import json
import threading
import urllib.request

from jevops.config import Config
from jevops.intake import build_server
from jevops.jev import JevClient
from jevops.notifier import Notifier
from jevops.pipeline import Pipeline
from jevops.store import Store


def _make_pipeline(tmp_path, transport=None) -> Pipeline:
    if transport is None:
        transport = httpx_mock_noop()
    return Pipeline(
        config=Config(dry_run=True),
        store=Store(tmp_path / "i.db"),
        jev=JevClient(base_url="https://jev.test", api_key="k", transport=transport),
        notifier=Notifier(dry_run=True, transport=transport),
    )


def httpx_mock_noop():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        payload = _json.loads(request.content.decode())
        if "worst" in payload.get("questions", {}):
            ids = sorted(payload["questions"]["worst"]["criteria"].keys())
            probs = {eid: 1.0 / len(ids) for eid in ids}
            top = ids[0]
            probs[top] = 0.6
            rest = (1.0 - 0.6) / (len(ids) - 1) if len(ids) > 1 else 0.0
            for k in probs:
                if k != top:
                    probs[k] = rest
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "worst": {"choice": top, "probabilities": probs, "confidence": 0.9},
                        "page_worthy": {"noul": 0.9},
                    },
                    "usage": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "answers": {
                    "severity": {
                        "choice": "warning",
                        "probabilities": {"info": 0, "warning": 1, "critical": 0},
                        "confidence": 0.9,
                    },
                    "category": {
                        "choice": "dependency",
                        "probabilities": {
                            "deployment": 0,
                            "capacity": 0,
                            "dependency": 1,
                            "application_bug": 0,
                            "security": 0,
                            "data": 0,
                            "unknown": 0,
                        },
                        "confidence": 0.9,
                    },
                    "urgency": {"score": 1.0, "probabilities": {"1": 1}},
                    "needs_human": {"noul": 0.3},
                    "novelty": {"noul": 0.2},
                    "recovery": {"noul": 0.05},
                },
                "usage": {},
                "model": "jev-1.13.0",
            },
        )

    return httpx.MockTransport(handler)


def test_healthz(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    server = build_server(pipeline, "127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["status"] == "ok"
    finally:
        server.shutdown()


def test_ingest_list_of_events(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    server = build_server(pipeline, "127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(
            {
                "events": [
                    {"@timestamp": "2026-09-24T13:40:12Z", "service": "api", "level": "ERROR", "message": "boom one"},
                    {"@timestamp": "2026-09-24T13:40:13Z", "service": "api", "level": "ERROR", "message": "boom two"},
                ]
            }
        ).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/ingest",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
        assert resp.status == 200
        assert payload["accepted"] == 2
        assert len(payload["decisions"]) == 2
    finally:
        server.shutdown()


def test_ingest_single_event_object(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    server = build_server(pipeline, "127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"service": "api", "level": "ERROR", "message": "single boom"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/ingest", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
        assert payload["accepted"] == 1
        assert len(payload["decisions"]) == 1
    finally:
        server.shutdown()


def test_query_endpoint_with_injected_fn(tmp_path):
    pipeline = _make_pipeline(tmp_path)

    def fake_query(payload):
        return {"question": payload.get("question"), "answer": {"answer": "mock", "cited": []}}

    server = build_server(pipeline, "127.0.0.1", 0, query_fn=fake_query)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(
            {"question": "what matters?", "from": "2026-09-24T08:00:00Z", "to": "2026-09-24T09:00:00Z"}
        ).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/query", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
        assert payload["question"] == "what matters?"
        assert payload["answer"]["answer"] == "mock"
    finally:
        server.shutdown()


def test_decisions_endpoint(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    pipeline.process_events([{"service": "api", "level": "ERROR", "message": "boom"}])
    server = build_server(pipeline, "127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/decisions?limit=5") as resp:
            rows = json.loads(resp.read())
        assert len(rows) == 1
        assert rows[0]["action"] == "digest"
    finally:
        server.shutdown()


def test_query_endpoint_404_when_not_configured(tmp_path):
    import urllib.error

    pipeline = _make_pipeline(tmp_path)
    server = build_server(pipeline, "127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"question": "q"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/query", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
