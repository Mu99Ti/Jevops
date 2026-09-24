from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jevops.chunks import Chunk, digest, split_range, subdivide
from jevops.jev import JevClient
from jevops.models import LogEvent


def _qkey(path: str) -> str:
    return "imp_" + path.replace(".", "_")


@dataclass
class Leaf:
    chunk: Chunk
    imp: float

    @property
    def path(self) -> str:
        return self.chunk.path

    @property
    def lines(self) -> list[LogEvent]:
        return self.chunk.lines


@dataclass
class LevelTrace:
    depth: int
    labels: dict[str, float]
    selected: list[str]
    best: str


@dataclass
class Drilldown:
    levels: list[LevelTrace] = field(default_factory=list)
    leaves: list[Leaf] = field(default_factory=list)
    anything: float = 0.0
    jev_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace": [
                {
                    "depth": t.depth,
                    "selected": t.selected,
                    "best": t.best,
                    "labels": {k: round(v, 3) for k, v in t.labels.items()},
                }
                for t in self.levels
            ],
            "leaves": [
                {
                    "path": leaf.path,
                    "window": f"{leaf.chunk.start.isoformat()} .. {leaf.chunk.end.isoformat()}",
                    "imp": round(leaf.imp, 3),
                    "lines": len(leaf.lines),
                }
                for leaf in self.leaves
            ],
            "anything_important": round(self.anything, 3),
            "jev_calls": self.jev_calls,
        }


def label_chunks(
    jev: JevClient,
    question: str,
    chunks: list[Chunk],
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[dict[str, float], str, float]:
    non_empty = [c for c in chunks if c.lines]
    if not non_empty:
        return {}, "", 0.0
    state = {
        "question": question,
        "time_range": [start.isoformat(), end.isoformat()] if start and end else None,
        "chunks": [digest(c) for c in non_empty],
    }
    questions: dict[str, dict[str, Any]] = {}
    for c in non_empty:
        questions[_qkey(c.path)] = {
            "type": "noul",
            "instructions": (
                f"Chunk {c.path} contains something important for the question, such as an incident, "
                "outage, degradation, security event, or notable change, rather than routine operation"
            ),
            "criteria": {
                "true": "This chunk contains an important event relative to the question",
                "false": "Routine operation or noise",
            },
        }
    questions["best"] = {
        "type": "choice",
        "instructions": (
            f"Which chunk is the most important for the question: {question}? Rank all chunks by importance."
        ),
        "criteria": {c.path: None for c in non_empty},
    }
    questions["anything"] = {
        "type": "noul",
        "instructions": f"At least one chunk contains something important for the question: {question}",
    }
    data = jev.system_one(state=state, questions=questions)
    answers = data["answers"]
    best = jev._validate_choice(answers["best"], {c.path for c in non_empty})
    labels = {c.path: jev._noul(answers[_qkey(c.path)]) for c in non_empty}
    anything = jev._noul(answers["anything"])
    return labels, best, anything


def drill(
    jev: JevClient,
    lines: list[LogEvent],
    start: datetime,
    end: datetime,
    question: str,
    *,
    parts: int = 8,
    sub: int = 4,
    depth: int = 4,
    leaf_size: int = 5,
    imp_threshold: float = 0.5,
    max_leaves: int = 50,
) -> Drilldown:
    result = Drilldown()
    frontier = split_range(lines, start, end, parts=parts)
    level_depth = 0
    while frontier:
        labels, best, anything = label_chunks(jev, question, frontier, start, end)
        result.jev_calls += 1
        result.anything = anything
        if not labels:
            result.levels.append(LevelTrace(depth=level_depth, labels={}, selected=[], best=best))
            break
        selected = [c for c in frontier if c.lines and labels.get(c.path, 0.0) >= imp_threshold]
        if not any(c.path == best for c in selected):
            best_chunk = next((c for c in frontier if c.path == best and c.lines), None)
            if best_chunk:
                selected.append(best_chunk)
        selected_paths = sorted(c.path for c in selected)
        result.levels.append(LevelTrace(depth=level_depth, labels=labels, selected=selected_paths, best=best))
        ready = [c for c in selected if c.is_leaf(leaf_size) or level_depth >= depth]
        next_frontier: list[Chunk] = []
        for c in selected:
            if c in ready:
                result.leaves.append(Leaf(chunk=c, imp=labels.get(c.path, 0.0)))
            else:
                next_frontier.extend(subdivide(c, parts=sub))
        if not next_frontier:
            break
        frontier = next_frontier
        level_depth += 1
    result.leaves = [leaf for leaf in result.leaves if leaf.lines]
    result.leaves.sort(key=lambda leaf: leaf.imp, reverse=True)
    result.leaves = result.leaves[:max_leaves]
    return result
