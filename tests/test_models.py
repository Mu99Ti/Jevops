from jevops.models import LogEvent, PageDecision, TriageResult


def test_log_event_from_mapping_elk_style():
    e = LogEvent.from_mapping(
        {
            "@timestamp": "2026-09-24T13:40:12Z",
            "service": "checkout-api",
            "environment": "prod",
            "level": "ERROR",
            "message": "payment-gateway timeout",
            "trace_id": "abc",
        }
    )
    assert e.service == "checkout-api"
    assert e.env == "prod"
    assert e.level == "ERROR"
    assert e.message == "payment-gateway timeout"
    assert e.ts.startswith("2026-09-24")
    assert e.fields["trace_id"] == "abc"


def test_log_event_from_mapping_alternate_keys():
    e = LogEvent.from_mapping(
        {
            "time": "2026-09-24T13:40:12.000Z",
            "service_name": "api",
            "env": "staging",
            "levelname": "warning",
            "log": "slow query",
        }
    )
    assert e.service == "api"
    assert e.env == "staging"
    assert e.level == "warning"
    assert e.message == "slow query"


def test_log_event_defaults():
    e = LogEvent.from_mapping({"message": "hello"})
    assert e.service == "unknown"
    assert e.env == "unknown"
    assert e.level == "INFO"
    assert e.ts == ""


def test_triage_result_fields():
    t = TriageResult(
        event_id="e1",
        model="jev-1.13.0",
        severity="critical",
        severity_conf=0.99,
        severity_probs={"critical": 1.0},
        category="deployment",
        category_probs={"deployment": 0.77, "dependency": 0.2},
        needs_human=0.89,
        novelty=0.54,
        urgency=2.99,
        is_recovery=0.05,
        latency_ms=1340,
        input_tokens=817,
        output_tokens=172,
    )
    assert t.severity == "critical"
    assert t.urgency == 2.99
    assert t.input_tokens == 817


def test_page_decision_defaults():
    d = PageDecision(action="digest", reason="warning")
    assert d.action == "digest"
    assert d.dedup_key == ""
    assert d.composite == 0.0
