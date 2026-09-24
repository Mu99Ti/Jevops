from jevops.models import LogEvent, TriageResult
from jevops.policy import Policy, ServicePolicy


def _event(service: str = "api") -> LogEvent:
    return LogEvent(
        id="f1", ts="2026-09-24T13:40:12Z", service=service, env="prod", level="ERROR", message="gateway down"
    )


def _triage(
    severity: str = "critical",
    conf: float = 0.95,
    human: float = 0.9,
    novelty: float = 0.6,
    urgency: float = 3.0,
    recovery: float = 0.05,
    category: str = "dependency",
) -> TriageResult:
    return TriageResult(
        event_id="f1",
        model="jev-1.13.0",
        severity=severity,
        severity_conf=conf,
        severity_probs={severity: 1.0},
        category=category,
        category_probs={category: 1.0},
        needs_human=human,
        novelty=novelty,
        urgency=urgency,
        is_recovery=recovery,
        latency_ms=100,
        input_tokens=10,
        output_tokens=5,
    )


def test_pages_critical_high_confidence():
    d = Policy().decide(_event(), _triage(), cooldown_active=False, paged=False)
    assert d.action == "page"
    assert d.dedup_key == "jevops-api-dependency"
    assert d.composite > 0.8


def test_cooldown_downgrades_page_to_digest():
    d = Policy().decide(_event(), _triage(), cooldown_active=True, paged=True)
    assert d.action == "digest"
    assert "cooldown" in d.reason


def test_low_confidence_is_never_paged():
    d = Policy().decide(_event(), _triage(conf=0.5), cooldown_active=False, paged=False)
    assert d.action == "digest"
    assert "confidence" in d.reason


def test_warning_is_digest():
    d = Policy().decide(_event(), _triage(severity="warning"), cooldown_active=False, paged=False)
    assert d.action == "digest"


def test_info_is_ignored():
    d = Policy().decide(_event(), _triage(severity="info", human=0.1), cooldown_active=False, paged=False)
    assert d.action == "ignore"


def test_recovery_resolves_paged_incident():
    d = Policy().decide(_event(), _triage(recovery=0.9), cooldown_active=False, paged=True)
    assert d.action == "resolve"
    assert d.dedup_key == "jevops-api-dependency"


def test_recovery_without_active_page_falls_through_to_page_rules():
    d = Policy().decide(_event(), _triage(recovery=0.9), cooldown_active=False, paged=False)
    assert d.action == "page"


def test_service_policy_override_tightens_threshold():
    policy = Policy(services={"checkout-api": ServicePolicy(min_confidence=0.99)})
    t = _triage(conf=0.95)
    assert policy.decide(_event("checkout-api"), t, cooldown_active=False, paged=False).action == "digest"
    assert policy.decide(_event("web"), t, cooldown_active=False, paged=False).action == "page"


def test_page_disabled_service_digests():
    policy = Policy(services={"web": ServicePolicy(page_enabled=False)})
    assert policy.decide(_event("web"), _triage(), cooldown_active=False, paged=False).action == "digest"
