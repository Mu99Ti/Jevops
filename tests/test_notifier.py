import httpx

from jevops.models import LogEvent, PageDecision, TriageResult
from jevops.notifier import Notifier


def _decision(action: str = "page") -> PageDecision:
    event = LogEvent(
        id="f1", ts="2026-09-24T13:40:12Z", service="checkout-api", env="prod", level="ERROR", message="gateway down"
    )
    triage = TriageResult(
        event_id="f1",
        model="jev-1.13.0",
        severity="critical",
        severity_conf=0.99,
        severity_probs={"critical": 1.0},
        category="dependency",
        category_probs={"dependency": 0.9, "deployment": 0.1},
        needs_human=0.9,
        novelty=0.5,
        urgency=3.0,
        is_recovery=0.1,
        latency_ms=100,
        input_tokens=10,
        output_tokens=5,
    )
    return PageDecision(
        action=action,
        reason="test",
        composite=0.9,
        dedup_key="jevops-checkout-api-dependency",
        triage=triage,
        event=event,
    )


def test_slack_page_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, text="ok")

    n = Notifier(slack_webhook_url="https://hooks.slack.test/x", transport=httpx.MockTransport(handler))
    n.notify(_decision())
    assert captured["url"] == "https://hooks.slack.test/x"
    assert "checkout-api" in captured["body"]
    assert "[CRITICAL]" in captured["body"]
    assert "0.99" in captured["body"]


def test_pagerduty_trigger_and_resolve():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content.decode()))
        return httpx.Response(202, json={"dedup_key": "ignored"})

    n = Notifier(pagerduty_routing_key="P123", transport=httpx.MockTransport(handler))
    n.notify(_decision(action="page"))
    n.notify(_decision(action="resolve"))
    assert bodies[0]["event_action"] == "trigger"
    assert bodies[0]["routing_key"] == "P123"
    assert bodies[0]["dedup_key"] == "jevops-checkout-api-dependency"
    assert "severity" in bodies[0]["payload"]
    assert bodies[1]["event_action"] == "resolve"
    assert bodies[1]["dedup_key"] == "jevops-checkout-api-dependency"


def test_generic_webhook_receives_decision_json():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    n = Notifier(webhook_url="http://localhost:9999/hook", transport=httpx.MockTransport(handler))
    n.notify(_decision(action="digest"))
    assert captured["action"] == "digest"
    assert captured["triage"]["severity"] == "critical"


def test_dry_run_sends_nothing():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    n = Notifier(
        slack_webhook_url="https://hooks.slack.test/x",
        pagerduty_routing_key="P123",
        webhook_url="http://x/y",
        dry_run=True,
        transport=httpx.MockTransport(handler),
    )
    n.notify(_decision())
    assert calls["n"] == 0


def test_without_channels_is_noop():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    n = Notifier(transport=httpx.MockTransport(handler))
    n.notify(_decision())
