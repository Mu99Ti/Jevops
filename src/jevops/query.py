from __future__ import annotations

from datetime import datetime
from typing import Any

from jevops.chunks import parse_ts
from jevops.config import Config
from jevops.drilldown import drill
from jevops.jev import JevClient
from jevops.llm import LLMClient, QueryAnswer, explain
from jevops.models import LogEvent
from jevops.store import Store


def parse_time_arg(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"invalid time: {value!r}") from e
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _epoch(line: LogEvent, fallback: float) -> float:
    ts = parse_ts(line.ts)
    if ts is None:
        return fallback
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts.timestamp()


def run_query(
    store: Store,
    jev: JevClient,
    llm: LLMClient | None,
    question: str,
    start: str,
    end: str,
    config: Config,
) -> dict[str, Any]:
    start_dt = parse_time_arg(start)
    end_dt = parse_time_arg(end)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    lines = store.lines_between(start_dt.isoformat(), end_dt.isoformat())
    drilldown = drill(
        jev,
        lines,
        start_dt,
        end_dt,
        question,
        chunk_size=config.query_chunk_size,
        sub=config.query_sub,
        depth=config.query_depth,
        leaf_size=config.query_leaf,
        imp_threshold=config.query_imp,
        max_leaves=config.query_max_leaves,
    )

    order: dict[tuple[str, str], int] = {}
    by_key: dict[tuple[str, str], LogEvent] = {}
    for i, line in enumerate(lines):
        key = (line.id, line.ts)
        order.setdefault(key, i)
        by_key[key] = line
    leaf_keys = {(leaf_line.id, leaf_line.ts) for leaf in drilldown.leaves for leaf_line in leaf.lines}
    ordered_keys = sorted(leaf_keys, key=lambda k: order.get(k, 0))

    from jevops.llm import LineRef

    refs: dict[str, LineRef] = {}
    for i, key in enumerate(ordered_keys, start=1):
        line = by_key[key]
        lid = f"L{i:04d}"
        refs[lid] = LineRef(id=lid, ts=line.ts, service=line.service, level=line.level, message=line.message)

    if llm is None:
        answer = QueryAnswer(answer="llm disabled")
    elif not refs:
        answer = QueryAnswer(answer="no matching log lines in range")
    else:
        answer = explain(llm, question, refs, max_rounds=config.query_max_rounds)

    return {
        "question": question,
        "range": {"from": start_dt.isoformat(), "to": end_dt.isoformat()},
        "lines_scanned": len(lines),
        "drilldown": drilldown.to_dict(),
        "timeline": [ref.render() for ref in refs.values()],
        "answer": answer.to_dict(),
    }
