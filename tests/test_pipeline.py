import json

import httpx

from jevops.config import Config
from jevops.jev import JevClient
from jevops.notifier import Notifier
from jevops.pipeline import Pipeline
from jevops.store import Store


def _jev_handler_factory(triage_sequence: list[dict] | None = None):
    calls = {"rank": 0, "triage": 0}
    seq = list(triage_sequence or [])

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        questions = payload["questions"]
        if "worst" in questions:
            calls["rank"] += 1
            ids = sorted(questions["worst"]["criteria"].keys())
            top = ids[-1]
            probs = (
                {eid: (0.05 / (len(ids) - 1) if eid != top else 0.95) for eid in ids} if len(ids) > 1 else {top: 1.0}
            )
            return httpx.Response(
                200,
                json={
                    "model": "jev-1.13.0",
                    "answers": {
                        "worst": {"type": "choice", "choice": top, "probabilities": probs, "confidence": 0.9},
                        "page_worthy": {"type": "noul", "noul": 0.9},
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 5},
                },
            )
        calls["triage"] += 1
        override = seq.pop(0) if seq else {}
        sev = override.get("severity", "critical")
        sev_probs = {k: (1.0 if k == sev else 0.0) for k in ("info", "warning", "critical")}
        answers = {
            "severity": {
                "type": "choice",
                "choice": sev,
                "probabilities": sev_probs,
                "confidence": override.get("severity_conf", 0.99),
            },
            "category": {
                "type": "choice",
                "choice": override.get("category", "dependency"),
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
            "urgency": {
                "type": "score",
                "score": override.get("urgency", 3.0),
                "probabilities": {"3": 1.0},
                "confidence": 0.9,
            },
            "needs_human": {"type": "noul", "noul": override.get("needs_human", 0.9)},
            "novelty": {"type": "noul", "noul": override.get("novelty", 0.7)},
            "recovery": {"type": "noul", "noul": override.get("recovery", 0.05)},
        }
        return httpx.Response(
            200, json={"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 200, "output_tokens": 20}}
        )

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def _pd_handler_factory() -> tuple[httpx.MockTransport, list[dict]]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        return httpx.Response(202, json={})

    return httpx.MockTransport(handler), bodies


def _pipeline(tmp_path, triage_sequence=None):
    jev_handler = _jev_handler_factory(triage_sequence)
    pd_transport, bodies = _pd_handler_factory()
    config = Config(page_min_confidence=0.8, page_min_human=0.7, cooldown_seconds=1800, dry_run=False)
    store = Store(tmp_path / "p.db")
    jev = JevClient(
        base_url="https://jev.test", api_key="k", model="jev-latest", transport=httpx.MockTransport(jev_handler)
    )
    notifier = Notifier(pagerduty_routing_key="PKEY", transport=pd_transport)
    return Pipeline(config=config, store=store, jev=jev, notifier=notifier), bodies, jev_handler.calls, store


def _raw(msg="gateway timeout 3000ms", level="ERROR", service="checkout-api"):
    return {"@timestamp": "2026-09-24T13:40:12Z", "service": service, "env": "prod", "level": level, "message": msg}


def test_critical_event_pages_and_notifies(tmp_path):
    pipeline, pd_bodies, _, store = _pipeline(tmp_path)
    decisions = pipeline.process_events([_raw()])
    assert len(decisions) == 1
    assert decisions[0].action == "page"
    assert pd_bodies[0]["event_action"] == "trigger"
    assert pd_bodies[0]["dedup_key"] == "jevops-checkout-api-dependency"
    assert store.recent_decisions()[0]["action"] == "page"


def test_duplicate_event_is_ignored(tmp_path):
    pipeline, pd_bodies, _, _ = _pipeline(tmp_path)
    assert len(pipeline.process_events([_raw()])) == 1
    assert pipeline.process_events([_raw()]) == []
    assert len(pd_bodies) == 1


def test_info_level_filtered_before_model(tmp_path):
    pipeline, pd_bodies, jev_calls, _ = _pipeline(tmp_path)
    decisions = pipeline.process_events([_raw(level="INFO", msg="healthy heartbeat")])
    assert decisions == []
    assert jev_calls["rank"] == 0
    assert jev_calls["triage"] == 0
    assert pd_bodies == []


def test_burst_ranks_then_triages_top_k(tmp_path):
    pipeline, pd_bodies, jev_calls, _ = _pipeline(tmp_path)
    events = [_raw(msg=f"error type {'abcde'[i]} code failing") for i in range(5)]
    decisions = pipeline.process_events(events)
    assert jev_calls["rank"] == 1
    assert jev_calls["triage"] == 3
    assert len(decisions) == 3
    assert decisions[0].action == "page"
    assert [d.action for d in decisions[1:]] == ["digest", "digest"]
    assert len(pd_bodies) == 1


def test_cooldown_suppresses_repeat_pages(tmp_path):
    pipeline, pd_bodies, _, _ = _pipeline(tmp_path)
    first = pipeline.process_events([_raw(msg="failure one")])
    second = pipeline.process_events([_raw(msg="failure two")])
    assert first[0].action == "page"
    assert second[0].action == "digest"
    assert len(pd_bodies) == 1


def test_recovery_resolves_open_page(tmp_path):
    pipeline, pd_bodies, _, _ = _pipeline(
        tmp_path,
        triage_sequence=[{"severity": "critical"}, {"severity": "info", "recovery": 0.95}],
    )
    pipeline.process_events([_raw(msg="outage started")])
    resolved = pipeline.process_events([_raw(msg="health restored across all instances")])
    assert resolved[0].action == "resolve"
    assert pd_bodies[1]["event_action"] == "resolve"
    assert pd_bodies[1]["dedup_key"] == pd_bodies[0]["dedup_key"]


def test_exports_decision_to_elasticsearch(tmp_path):
    captured: list[dict] = []

    def es_handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if request.url.path.endswith("/_bulk"):
            lines = body.strip().split("\n")
            captured.append(json.loads(lines[1]))
            return httpx.Response(200, json={"errors": False})
        return httpx.Response(200, json={"version": {"number": "8.15.0"}})

    pipeline, pd_bodies, _, _ = _pipeline(tmp_path)
    from jevops.elastic import ElasticClient

    pipeline.es = ElasticClient("https://es.test", transport=httpx.MockTransport(es_handler))
    pipeline.process_events([_raw()])
    assert captured[0]["action"] == "page"
    assert captured[0]["severity"] == "critical"
    assert captured[0]["service"] == "checkout-api"
    assert "@timestamp" in captured[0]


def test_events_filtered_out_do_not_consume_triage_sequence(tmp_path):
    pipeline, _, _, _ = _pipeline(tmp_path, triage_sequence=[{"severity": "warning"}])
    decisions = pipeline.process_events([_raw(level="INFO", msg="ping"), _raw(msg="real error")])
    assert decisions[0].action == "digest"
