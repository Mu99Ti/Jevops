import json

import httpx

from jevops.llm import LineRef, LLMClient, explain


def _lines() -> dict[str, LineRef]:
    return {
        "L0001": LineRef(
            id="L0001",
            ts="2026-09-24T09:14:00Z",
            service="checkout-api",
            level="ERROR",
            message="payment-gateway timeout 3000ms",
        ),
        "L0002": LineRef(
            id="L0002",
            ts="2026-09-24T09:15:00Z",
            service="checkout-api",
            level="ERROR",
            message="circuit breaker opening",
        ),
    }


def _tool_call_response(tool_call_id: str, ids: list[str]) -> dict:
    return {
        "id": "cmpl-1",
        "model": "mimo",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": "read_log_lines", "arguments": json.dumps({"ids": ids})},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _final_response(content: str) -> dict:
    return {
        "model": "mimo",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
    }


def test_client_sends_bearer_model_and_tools():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_final_response("done"))

    client = LLMClient("https://llm.test/v1", api_key="k", model="mimo", transport=httpx.MockTransport(handler))
    client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "f"}}])
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["model"] == "mimo"
    assert captured["body"]["tools"][0]["function"]["name"] == "f"


def test_explain_executes_tool_call_then_answers():
    calls = {"n": 0}
    seen_second = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content.decode())
        if calls["n"] == 1:
            return httpx.Response(200, json=_tool_call_response("call-1", ["L0001", "L0002"]))
        seen_second["messages"] = body["messages"]
        return httpx.Response(
            200,
            json=_final_response(
                "Checkout outage started at 09:14 after gateway timeouts [L0001] and circuit breaker [L0002]."
            ),
        )

    client = LLMClient("https://llm.test/v1", api_key="k", transport=httpx.MockTransport(handler))
    answer = explain(client, "what is important?", _lines())
    assert calls["n"] == 2
    tool_msgs = [m for m in seen_second["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "L0001" in tool_msgs[0]["content"]
    assert "payment-gateway timeout" in tool_msgs[0]["content"]
    assert answer.cited == ["L0001", "L0002"]
    assert answer.rounds == 2
    assert answer.usage["completion_tokens"] == 35
    assert answer.usage["prompt_tokens"] == 30


def test_unknown_ids_are_rejected_and_not_cited():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if not any(m.get("role") == "tool" for m in body["messages"]):
            return httpx.Response(200, json=_tool_call_response("call-1", ["L0001", "L9999"]))
        seen["tool"] = [m for m in body["messages"] if m.get("role") == "tool"][0]["content"]
        return httpx.Response(200, json=_final_response("I saw [L9999] but it does not exist."))

    client = LLMClient("https://llm.test/v1", api_key="k", transport=httpx.MockTransport(handler))
    answer = explain(client, "q", _lines())
    assert "unknown" in seen["tool"].lower()
    assert answer.cited == []


def test_answer_without_tool_calls_is_single_round():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_final_response("GC pauses grew [L0001]."))

    client = LLMClient("https://llm.test/v1", api_key="k", transport=httpx.MockTransport(handler))
    answer = explain(client, "q", _lines())
    assert calls["n"] == 1
    assert answer.rounds == 1
    assert answer.cited == ["L0001"]
    assert not answer.truncated


def test_tool_loop_is_bounded_by_max_rounds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_tool_call_response("call-x", ["L0001"]))

    client = LLMClient("https://llm.test/v1", api_key="k", transport=httpx.MockTransport(handler))
    answer = explain(client, "q", _lines(), max_rounds=3)
    assert calls["n"] == 3
    assert answer.truncated
    assert answer.rounds == 3


def test_timeline_is_included_in_user_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen["user"] = [m for m in body["messages"] if m["role"] == "user"][0]["content"]
        return httpx.Response(200, json=_final_response("nothing unusual"))

    client = LLMClient("https://llm.test/v1", api_key="k", transport=httpx.MockTransport(handler))
    explain(client, "what is important?", _lines())
    assert "L0001" in seen["user"] and "payment-gateway timeout" in seen["user"]
    assert "what is important?" in seen["user"]
