import httpx
import pytest

from jevops.jev import JevClient, JevError, JevValidationError, RankItem, rank_event_state, triage_questions
from jevops.models import LogEvent


def _event(i: int = 0, msg: str = "boom") -> LogEvent:
    return LogEvent(id=f"f{i}", ts="2026-09-24T13:40:12Z", service="api", env="prod", level="ERROR", message=msg)


def test_validate_choice_rejects_extra_keys():
    with pytest.raises(JevValidationError):
        JevClient._validate_choice({"choice": "a", "probabilities": {"a": 1.0, "z": 0.0}}, {"a", "b"})


def test_validate_choice_rejects_bad_sum():
    with pytest.raises(JevValidationError):
        JevClient._validate_choice({"choice": "a", "probabilities": {"a": 0.5, "b": 0.3}}, {"a", "b"})


def test_validate_choice_rejects_choice_that_is_not_max():
    with pytest.raises(JevValidationError):
        JevClient._validate_choice({"choice": "a", "probabilities": {"a": 0.2, "b": 0.8}}, {"a", "b"})


def test_validate_choice_accepts_valid():
    assert JevClient._validate_choice({"choice": "b", "probabilities": {"a": 0.2, "b": 0.8}}, {"a", "b"}) == "b"


def test_rank_event_state_tags_lines():
    state = rank_event_state([_event(0, "one"), _event(1, "two")])
    assert "E000|" in state
    assert "E001|" in state
    assert "one" in state


def test_triage_questions_shape():
    q = triage_questions()
    assert q["severity"]["type"] == "choice"
    assert set(q["severity"]["criteria"]) == {"info", "warning", "critical"}
    assert q["needs_human"]["type"] == "noul"
    assert q["urgency"]["type"] == "score"
    assert len(q["urgency"]["criteria"]) == 4


def test_rank_burst_returns_scores_and_pageable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "worst": {
                        "type": "choice",
                        "choice": "E001",
                        "probabilities": {"E000": 0.25, "E001": 0.75},
                        "confidence": 0.9,
                    },
                    "page_worthy": {"type": "noul", "noul": 0.85},
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )

    client = JevClient(
        base_url="https://example.test", api_key="k", model="jev-latest", transport=httpx.MockTransport(handler)
    )
    events = [_event(0), _event(1)]
    ranked = client.rank_burst(events)
    assert isinstance(ranked[0], RankItem)
    assert ranked[0].relevance == 0.75
    assert ranked[0].event.id == "f1"
    assert ranked[0].pageable == 0.85


def test_triage_maps_answers_to_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13-free",
                "answers": {
                    "severity": {
                        "type": "choice",
                        "choice": "critical",
                        "probabilities": {"info": 0, "warning": 0, "critical": 1},
                        "confidence": 0.99,
                    },
                    "category": {
                        "type": "choice",
                        "choice": "deployment",
                        "probabilities": {
                            "deployment": 0.77,
                            "dependency": 0.2,
                            "capacity": 0.03,
                            "application_bug": 0,
                            "security": 0,
                            "data": 0,
                            "unknown": 0,
                        },
                        "confidence": 0.73,
                    },
                    "urgency": {
                        "type": "score",
                        "score": 2.99,
                        "probabilities": {"0": 0, "1": 0, "2": 0.01, "3": 0.99},
                        "confidence": 0.99,
                    },
                    "needs_human": {"type": "noul", "noul": 0.89},
                    "novelty": {"type": "noul", "noul": 0.54},
                    "recovery": {"type": "noul", "noul": 0.05},
                },
                "usage": {"input_tokens": 817, "output_tokens": 172},
            },
        )

    client = JevClient(
        base_url="https://example.test", api_key="k", model="jev-latest", transport=httpx.MockTransport(handler)
    )
    result = client.triage(_event(0))
    assert result.severity == "critical"
    assert result.severity_conf == 0.99
    assert result.category == "deployment"
    assert result.needs_human == 0.89
    assert result.urgency == 2.99
    assert result.is_recovery == 0.05
    assert result.model == "jev-1.13-free"
    assert result.input_tokens == 817
    assert result.event_id == "f0"


def test_systemone_sends_auth_and_model():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        body = request.content.decode()
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "model": "m",
                "answers": {
                    "severity": {
                        "type": "choice",
                        "choice": "info",
                        "probabilities": {"info": 1.0, "warning": 0, "critical": 0},
                        "confidence": 0.6,
                    },
                    "category": {
                        "type": "choice",
                        "choice": "unknown",
                        "probabilities": {
                            "deployment": 0,
                            "capacity": 0,
                            "dependency": 0,
                            "application_bug": 0,
                            "security": 0,
                            "data": 0,
                            "unknown": 1.0,
                        },
                        "confidence": 0.6,
                    },
                    "urgency": {"type": "score", "score": 0, "probabilities": {"0": 1}, "confidence": 0.6},
                    "needs_human": {"type": "noul", "noul": 0.1},
                    "novelty": {"type": "noul", "noul": 0.1},
                    "recovery": {"type": "noul", "noul": 0.1},
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = JevClient(
        base_url="https://example.test", api_key="secret", model="jev-latest", transport=httpx.MockTransport(handler)
    )
    client.triage(_event())
    assert seen["auth"] == "Bearer secret"
    assert '"model": "jev-latest"' in seen["body"] or '"model":"jev-latest"' in seen["body"]


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.5"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}, "model": "m"})

    client = JevClient(
        base_url="https://example.test",
        api_key="k",
        model="m",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    answers = client.system_one(state="x", questions={"a": {"type": "noul", "instructions": "q"}})
    assert calls["n"] == 2
    assert sleeps == [0.5]
    assert answers["answers"] == {}


def test_raises_jev_error_on_400_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "Choice question must have at least one choice"}})

    client = JevClient(base_url="https://example.test", api_key="k", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(JevError) as exc:
        client.system_one(state="x", questions={"severity": {"type": "choice", "criteria": {}}})
    assert calls["n"] == 1
    assert "at least one choice" in str(exc.value)
