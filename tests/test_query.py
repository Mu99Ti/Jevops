from datetime import UTC, datetime

import httpx
import pytest

from jevops.config import Config
from jevops.jev import JevClient
from jevops.llm import LLMClient
from jevops.query import parse_time_arg, run_query
from jevops.store import Store

START = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
END = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)


def test_parse_time_arg_full_iso():
    assert parse_time_arg("2026-09-24T08:00:00Z") == START


def test_parse_time_arg_rejects_garbage():
    with pytest.raises(ValueError):
        parse_time_arg("not-a-time")


def _seed(store: Store) -> None:
    from jevops.models import LogEvent

    for i in range(12):
        store.add_log_line(
            LogEvent(
                id=f"l{i}",
                ts=f"2026-09-24T08:{i * 4 + 1:02d}:00Z",
                service="checkout-api",
                env="prod",
                level="ERROR",
                message=f"incident line {i} gateway timeout",
            )
        )
    store.add_log_line(
        LogEvent(
            id="early", ts="2026-09-24T07:00:00Z", service="api", env="prod", level="ERROR", message="outside range"
        )
    )


def _jev_handler(payload_rule):
    calls = {"n": 0, "payloads": []}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content.decode())
        calls["n"] += 1
        calls["payloads"].append(payload)
        ids = [c["id"] for c in payload["state"]["chunks"]]
        scores = payload_rule(ids, calls["n"])
        answers = {}
        for key in payload["questions"]:
            if key.startswith("imp_"):
                answers[key] = {"type": "noul", "noul": scores.get(key, 0.05)}
        path_scores = {cid: scores.get("imp_" + cid.replace(".", "_"), 0.05) for cid in ids}
        best = max(path_scores, key=path_scores.get) if path_scores else ids[0]
        answers["best"] = {
            "type": "choice",
            "choice": best,
            "probabilities": {c: (1.0 if c == best else 0.0) for c in ids},
            "confidence": 0.9,
        }
        answers["anything"] = {"type": "noul", "noul": 0.9 if max(path_scores.values() or [0]) >= 0.5 else 0.1}
        return httpx.Response(
            200, json={"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 5, "output_tokens": 1}}
        )

    return httpx.MockTransport(handler), calls


def test_run_query_end_to_end(tmp_path):
    import json as _json

    store = Store(tmp_path / "q.db")
    _seed(store)

    jev_t, jev_calls = _jev_handler(lambda ids, n: {("imp_" + cid.replace(".", "_")): 0.9 for cid in ids})

    llm_calls = {"n": 0}

    def llm_handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content.decode())
        llm_calls["n"] += 1
        user = [m for m in body["messages"] if m["role"] == "user"][0]["content"]
        assert "L0001" in user
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Gateway outage at 08:01 [L0001] and [L0012]."},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            },
        )

    config = Config(query_chunks=4, query_leaf=3, query_depth=2, query_imp=0.5)
    jev = JevClient(base_url="https://jev.test", api_key="k", transport=jev_t)
    llm = LLMClient("https://llm.test/v1", api_key="k", transport=httpx.MockTransport(llm_handler))
    result = run_query(store, jev, llm, "what is important?", START.isoformat(), END.isoformat(), config)

    assert result["lines_scanned"] == 12
    assert result["drilldown"]["jev_calls"] >= 1
    assert result["answer"]["cited"] == ["L0001", "L0012"]
    assert "Gateway outage" in result["answer"]["answer"]
    assert result["range"]["from"] == START.isoformat()
    assert jev_calls["n"] >= 1
    assert llm_calls["n"] == 1


def test_run_query_without_llm_still_returns_drilldown(tmp_path):
    store = Store(tmp_path / "q.db")
    _seed(store)
    jev_t, _ = _jev_handler(lambda ids, n: {("imp_" + cid.replace(".", "_")): 0.9 for cid in ids})
    config = Config(query_chunks=4, query_leaf=5)
    jev = JevClient(base_url="https://jev.test", api_key="k", transport=jev_t)
    result = run_query(store, jev, None, "q", START.isoformat(), END.isoformat(), config)
    assert result["drilldown"]["jev_calls"] >= 1
    assert result["answer"]["answer"] == "llm disabled"
    assert result["answer"]["cited"] == []


def test_run_query_rejects_inverted_range(tmp_path):
    store = Store(tmp_path / "q.db")
    jev_t, _ = _jev_handler(lambda ids, n: {})
    jev = JevClient(base_url="https://jev.test", api_key="k", transport=jev_t)
    with pytest.raises(ValueError):
        run_query(store, jev, None, "q", END.isoformat(), START.isoformat(), Config())
