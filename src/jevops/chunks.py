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


def _line_dt(line: LogEvent, fallback: datetime) -> datetime:
    ts = parse_ts(line.ts)
    if ts is None:
        return fallback
    if ts.tzinfo is None:
        return ts.astimezone()
    return ts


def split_by_count(
    lines: list[LogEvent],
    chunk_size: int,
    start: datetime,
    end: datetime,
    prefix: str = "C",
) -> list[Chunk]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if not lines:
        return [Chunk(path=f"{prefix}0", start=start, end=end)]
    chunks: list[Chunk] = []
    for i in range(0, len(lines), chunk_size):
        group = lines[i : i + chunk_size]
        chunks.append(
            Chunk(
                path=f"{prefix}{len(chunks)}",
                start=_line_dt(group[0], start),
                end=_line_dt(group[-1], end),
                lines=list(group),
            )
        )
    return chunks


def subdivide(chunk: Chunk, parts: int = 4) -> list[Chunk]:
    parts = max(1, parts)
    if not chunk.lines:
        step = (chunk.end - chunk.start).total_seconds() / parts
        return [
            Chunk(
                path=f"{chunk.path}.{i}",
                start=chunk.start + timedelta(seconds=step * i),
                end=chunk.start + timedelta(seconds=step * (i + 1)),
            )
            for i in range(parts)
        ]
    per = -(-len(chunk.lines) // parts)
    kids: list[Chunk] = []
    for i in range(0, len(chunk.lines), per):
        group = chunk.lines[i : i + per]
        kids.append(
            Chunk(
                path=f"{chunk.path}.{len(kids)}",
                start=_line_dt(group[0], chunk.start),
                end=_line_dt(group[-1], chunk.end),
                lines=list(group),
            )
        )
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
