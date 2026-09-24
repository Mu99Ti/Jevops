from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from jevops.models import LogEvent


def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Chunk:
    path: str
    start: datetime
    end: datetime
    lines: list[LogEvent] = field(default_factory=list)

    def is_leaf(self, max_lines: int) -> bool:
        return len(self.lines) <= max_lines

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


def _assign(lines: list[LogEvent], start: datetime, end: datetime, buckets: list[Chunk]) -> None:
    span = (end - start).total_seconds() or 1.0
    for line in lines:
        ts = parse_ts(line.ts)
        if ts is None or ts < start:
            buckets[0].lines.append(line)
        elif ts >= end:
            buckets[-1].lines.append(line)
        else:
            idx = int((ts - start).total_seconds() // (span / len(buckets)))
            buckets[min(idx, len(buckets) - 1)].lines.append(line)


def split_range(
    lines: list[LogEvent],
    start: datetime,
    end: datetime,
    parts: int = 8,
    prefix: str = "C",
) -> list[Chunk]:
    if end <= start:
        raise ValueError("end must be after start")
    parts = max(1, parts)
    step = (end - start).total_seconds() / parts
    chunks = [
        Chunk(
            path=f"{prefix}{i}",
            start=start + timedelta(seconds=step * i),
            end=start + timedelta(seconds=step * (i + 1)),
        )
        for i in range(parts)
    ]
    _assign(lines, start, end, chunks)
    return chunks


def subdivide(chunk: Chunk, parts: int = 4) -> list[Chunk]:
    parts = max(1, parts)
    step = (chunk.end - chunk.start).total_seconds() / parts
    kids = [
        Chunk(
            path=f"{chunk.path}.{i}",
            start=chunk.start + timedelta(seconds=step * i),
            end=chunk.start + timedelta(seconds=step * (i + 1)),
        )
        for i in range(parts)
    ]
    _assign(chunk.lines, chunk.start, chunk.end, kids)
    return kids


def digest(chunk: Chunk) -> dict:
    levels: dict[str, int] = {}
    services: dict[str, int] = {}
    for line in chunk.lines:
        key = line.level.upper()
        levels[key] = levels.get(key, 0) + 1
        services[line.service] = services.get(line.service, 0) + 1
    samples = [line.message[:160] for line in chunk.lines[:3]]
    if len(chunk.lines) > 3:
        samples.append(chunk.lines[-1].message[:160])
    return {
        "id": chunk.path,
        "window": f"{chunk.start.isoformat()} .. {chunk.end.isoformat()}",
        "count": len(chunk.lines),
        "levels": levels,
        "services": services,
        "samples": samples,
    }
