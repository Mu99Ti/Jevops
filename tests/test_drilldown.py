import json
from datetime import UTC, datetime

import httpx

from jevops.chunks import split_by_count
from jevops.drilldown import drill, label_chunks
from jevops.jev import JevClient
from jevops.models import LogEvent

START = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
END = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)


def _lines(n: int) -> list[LogEvent]:
    out = []
    for i in range(n):
        minute = (i * 59) // max(n - 1, 1) if n > 1 else 0
        out.append(
            LogEvent(
                id=f"f{i}",
                ts=f"2026-09-24T08:{minute:02d}:00Z",
                service="api",
                env="prod",
                level="ERROR",
                message=f"event number {i} happened",
            )
        )
    return out


def _handler_with_importance(rule):
    calls = {"n": 0, "payloads": []}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        calls["n"] += 1
        calls["payloads"].append(payload)
        chunk_ids = [c["id"] for c in payload["state"]["chunks"]]
        imp_keys = [k for k in payload["questions"] if k.startswith("imp_")]
        answers = {}
        scores = rule(payload, chunk_ids, calls["n"])
        for key in imp_keys:
            answers[key] = {"type": "noul", "noul": scores.get(key, 0.05)}
        path_scores = {cid: scores.get("imp_" + cid.replace(".", "_"), 0.05) for cid in chunk_ids}
        best = max(path_scores, key=path_scores.get) if path_scores else chunk_ids[0]
        answers["best"] = {
            "type": "choice",
            "choice": best,
            "probabilities": {cid: (1.0 if cid == best else 0.0) for cid in chunk_ids} if chunk_ids else {},
            "confidence": 0.9,
        }
        answers["anything"] = {"type": "noul", "noul": 0.9 if max(path_scores.values() or [0]) >= 0.5 else 0.1}
        return httpx.Response(
            200, json={"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}
        )

    return handler, calls


def _client(handler) -> JevClient:
    return JevClient(base_url="https://jev.test", api_key="k", transport=httpx.MockTransport(handler))


def test_label_chunks_single_request_maps_scores_by_path():
    def rule(payload, ids, n):
        return {("imp_" + cid.replace(".", "_")): (0.9 if cid == "C2" else 0.1) for cid in ids}

    handler, calls = _handler_with_importance(rule)
    chunks = split_by_count(_lines(16), chunk_size=4, start=START, end=END)
    labels, best, anything = label_chunks(_client(handler), "what broke?", chunks)
    assert calls["n"] == 1
    assert labels[best] == 0.9
    assert best == "C2"
    assert anything == 0.9
    assert set(labels) == {"C0", "C1", "C2", "C3"}
    questions = calls["payloads"][0]["questions"]
    assert "best" in questions and "anything" in questions
    assert sum(1 for k in questions if k.startswith("imp_")) == 4


def test_drill_descends_only_into_selected_chunk():
    def rule(payload, ids, n):
        if n == 1:
            return {("imp_" + cid.replace(".", "_")): (0.95 if cid == "C3" else 0.05) for cid in ids}
        return {("imp_" + cid.replace(".", "_")): 0.8 for cid in ids}

    handler, calls = _handler_with_importance(rule)
    result = drill(
        _client(handler),
        _lines(32),
        START,
        END,
        "what broke?",
        chunk_size=8,
        sub=4,
        depth=3,
        leaf_size=1,
        imp_threshold=0.5,
    )
    assert len(result.levels) >= 2
    assert result.levels[0].selected == ["C3"]
    second_chunk_ids = [c["id"] for c in calls["payloads"][1]["state"]["chunks"]]
    assert second_chunk_ids == ["C3.0", "C3.1", "C3.2", "C3.3"]
    assert all(leaf.path.startswith("C3") for leaf in result.leaves)


def test_drill_stops_at_leaf_size():
    def rule(payload, ids, n):
        return {("imp_" + cid.replace(".", "_")): 0.7 for cid in ids}

    handler, calls = _handler_with_importance(rule)
    result = drill(
        _client(handler), _lines(8), START, END, "q", chunk_size=4, sub=4, depth=5, leaf_size=5, imp_threshold=0.5
    )
    assert calls["n"] == 1
    assert len(result.levels) == 1
    assert result.leaves


def test_drill_respects_max_depth():
    def rule(payload, ids, n):
        return {("imp_" + cid.replace(".", "_")): 0.9 for cid in ids}

    handler, calls = _handler_with_importance(rule)
    result = drill(
        _client(handler), _lines(64), START, END, "q", chunk_size=16, sub=2, depth=2, leaf_size=1, imp_threshold=0.5
    )
    assert calls["n"] <= 3
    assert len(result.levels) <= 3
    assert result.levels[-1].depth == 2


def test_drill_caps_leaves_sorted_by_importance():
    def rule(payload, ids, n):
        scores = {("imp_" + cid.replace(".", "_")): 0.6 + (i * 0.01) for i, cid in enumerate(ids)}
        return scores

    handler, _ = _handler_with_importance(rule)
    result = drill(
        _client(handler),
        _lines(16),
        START,
        END,
        "q",
        chunk_size=4,
        sub=4,
        depth=3,
        leaf_size=1,
        imp_threshold=0.5,
        max_leaves=3,
    )
    assert len(result.leaves) == 3
    imps = [leaf.imp for leaf in result.leaves]
    assert imps == sorted(imps, reverse=True)


def test_drill_clamps_top_level_chunk_count_for_choice_limit():
    def rule(payload, ids, n):
        return {("imp_" + cid.replace(".", "_")): 0.9 for cid in ids}

    handler, _ = _handler_with_importance(rule)
    lines = _lines(300)
    result = drill(
        _client(handler), lines, START, END, "q", chunk_size=1, sub=2, depth=1, leaf_size=50, imp_threshold=0.5
    )
    assert result.jev_calls >= 1
    top = result.levels[0]
    assert len(top.labels) <= 200
    assert len(top.labels) >= 100
    assert result.leaves


def test_empty_range_still_returns_structure():
    handler, calls = _handler_with_importance(
        lambda payload, ids, n: {("imp_" + cid.replace(".", "_")): 0.05 for cid in ids}
    )
    result = drill(_client(handler), [], START, END, "q", chunk_size=10, leaf_size=3)
    assert result.levels
    assert calls["n"] == 0
    assert result.leaves == []
