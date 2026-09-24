import time
from pathlib import Path

from jevops.models import LogEvent, TriageResult
from jevops.store import Store


def _event(fid: str = "f1", service: str = "api") -> LogEvent:
    return LogEvent(id=fid, ts="2026-09-24T13:40:12Z", service=service, env="prod", level="ERROR", message="boom")


def _triage(event_id: str = "f1") -> TriageResult:
    return TriageResult(
        event_id=event_id,
        model="jev-1.13.0",
        severity="critical",
        severity_conf=0.99,
        severity_probs={"critical": 1.0},
        category="dependency",
        category_probs={"dependency": 1.0},
        needs_human=0.9,
        novelty=0.5,
        urgency=3.0,
        is_recovery=0.1,
        latency_ms=100,
        input_tokens=10,
        output_tokens=5,
    )


def test_add_event_deduplicates(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    assert store.add_event(_event()) is True
    assert store.add_event(_event()) is False
    assert store.add_event(_event("f2")) is True


def test_event_seen(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    assert not store.event_seen("f1")
    store.add_event(_event())
    assert store.event_seen("f1")


def test_save_and_fetch_triage(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.add_event(_event())
    store.save_triage(_triage())
    row = store.get_triage("f1")
    assert row is not None
    assert row["severity"] == "critical"
    assert row["category"] == "dependency"
    assert store.get_triage("missing") is None


def test_page_active_within_and_after_cooldown(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    assert not store.page_active("api", "dependency", cooldown_seconds=1800)
    store.add_event(_event())
    store.save_triage(_triage())
    store.mark_paged("f1", "jevops-api-dependency")
    assert store.page_active("api", "dependency", cooldown_seconds=1800)
    assert not store.page_active("api", "other", cooldown_seconds=1800)


def test_mark_resolved_clears_cooldown(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.add_event(_event())
    store.save_triage(_triage())
    store.mark_paged("f1", "jevops-api-dependency")
    store.mark_resolved("jevops-api-dependency")
    assert not store.page_active("api", "dependency", cooldown_seconds=1800)


def test_paged_flag_for_recovery(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.add_event(_event())
    store.save_triage(_triage())
    assert not store.paged_for("api")
    store.mark_paged("f1", "jevops-api-dependency")
    assert store.paged_for("api")


def test_recent_decisions_include_join(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.add_event(_event())
    store.save_triage(_triage())
    store.record_decision("f1", "page", "critical meets thresholds", 0.9)
    rows = store.recent_decisions(limit=10)
    assert len(rows) == 1
    assert rows[0]["action"] == "page"
    assert rows[0]["service"] == "api"
    assert rows[0]["severity"] == "critical"


def test_checkpoint_roundtrip(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    assert store.checkpoint_get("es") is None
    store.checkpoint_set("es", "2026-09-24T13:40:12Z")
    assert store.checkpoint_get("es") == "2026-09-24T13:40:12Z"
    store.checkpoint_set("es", "2026-09-24T14:00:00Z")
    assert store.checkpoint_get("es") == "2026-09-24T14:00:00Z"


def test_cooldown_respects_old_page_time(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.add_event(_event())
    store.save_triage(_triage())
    store.mark_paged("f1", "jevops-api-dependency")
    store._conn.execute("UPDATE pages SET paged_at = ?", (int(time.time()) - 5000,))
    store._conn.commit()
    assert not store.page_active("api", "dependency", cooldown_seconds=1800)
