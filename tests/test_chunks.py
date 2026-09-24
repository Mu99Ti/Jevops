from datetime import UTC, datetime

from jevops.chunks import digest, split_by_count, subdivide
from jevops.models import LogEvent

START = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
END = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)


def _line(i: int, ts: str = "", msg: str = "x") -> LogEvent:
    return LogEvent(id=f"f{i}", ts=ts, service="api", env="prod", level="ERROR", message=msg)


def test_split_by_count_groups_lines_into_reasonable_chunks():
    lines = [_line(i, f"2026-09-24T08:{i:02d}:00Z") for i in range(40)]
    chunks = split_by_count(lines, chunk_size=10, start=START, end=END)
    assert len(chunks) == 4
    assert [c.path for c in chunks] == ["C0", "C1", "C2", "C3"]
    assert all(len(c.lines) == 10 for c in chunks)
    assert chunks[0].lines[0].id == "f0"
    assert chunks[1].lines[0].id == "f10"
    assert chunks[0].start == datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
    assert chunks[-1].end > chunks[0].start


def test_split_by_count_last_chunk_holds_remainder():
    lines = [_line(i, f"2026-09-24T08:{i:02d}:00Z") for i in range(42)]
    chunks = split_by_count(lines, chunk_size=10, start=START, end=END)
    assert len(chunks) == 5
    assert len(chunks[-1].lines) == 2


def test_split_by_count_more_lines_than_size_scales_parts():
    lines = [_line(i, f"2026-09-24T08:00:{i % 60:02d}Z") for i in range(344)]
    chunks = split_by_count(lines, chunk_size=40, start=START, end=END)
    assert len(chunks) == 9
    assert all(len(c.lines) <= 40 for c in chunks)
    assert sum(len(c.lines) for c in chunks) == 344


def test_split_by_count_empty_range_returns_one_empty_chunk():
    chunks = split_by_count([], chunk_size=10, start=START, end=END)
    assert len(chunks) == 1
    assert chunks[0].lines == []
    assert chunks[0].start == START and chunks[0].end == END


def test_split_by_count_single_line_chunk():
    chunks = split_by_count([_line(0, "2026-09-24T08:30:00Z")], chunk_size=10, start=START, end=END)
    assert len(chunks) == 1
    assert len(chunks[0].lines) == 1
    assert chunks[0].start == datetime(2026, 9, 24, 8, 30, tzinfo=UTC)


def test_split_by_count_missing_ts_uses_range_bounds():
    chunks = split_by_count([_line(0, "")], chunk_size=10, start=START, end=END)
    assert chunks[0].start == START and chunks[0].end == END


def test_subdivide_balances_lines_by_count():
    lines = [_line(i, f"2026-09-24T08:{i:02d}:00Z") for i in range(8)]
    parent = split_by_count(lines, chunk_size=8, start=START, end=END)[0]
    kids = subdivide(parent, parts=4)
    assert len(kids) == 4
    assert [len(k.lines) for k in kids] == [2, 2, 2, 2]
    assert all(k.path.startswith("C0") for k in kids)
    assert kids[0].lines[0].id == "f0"
    assert kids[3].lines[-1].id == "f7"


def test_subdivide_drops_trailing_empty_groups():
    lines = [_line(i, f"2026-09-24T08:{i:02d}:00Z") for i in range(3)]
    parent = split_by_count(lines, chunk_size=3, start=START, end=END)[0]
    kids = subdivide(parent, parts=4)
    assert len(kids) == 3
    assert [len(k.lines) for k in kids] == [1, 1, 1]


def test_subdivide_empty_parent_keeps_time_tiling():
    parent = split_by_count([], chunk_size=5, start=START, end=END)[0]
    kids = subdivide(parent, parts=4)
    assert len(kids) == 4
    assert all(k.lines == [] for k in kids)
    assert kids[0].start == START
    assert kids[-1].end == END
    assert kids[1].start == kids[0].end


def test_digest_shape():
    lines = [
        LogEvent(id="a", ts="2026-09-24T08:01:00Z", service="search", env="prod", level="ERROR", message="disk 91"),
        LogEvent(id="b", ts="2026-09-24T08:02:00Z", service="search", env="prod", level="WARN", message="slow query"),
    ]
    chunk = split_by_count(lines, chunk_size=10, start=START, end=END)[0]
    d = digest(chunk)
    assert d["id"] == "C0"
    assert d["count"] == 2
    assert d["services"] == {"search": 2}
    assert d["levels"]["ERROR"] == 1
    assert any("disk 91" in m for m in d["samples"])


def test_chunk_leaf_detection():
    lines = [_line(i, "2026-09-24T08:01:00Z", f"m{i}") for i in range(5)]
    chunk = split_by_count(lines, chunk_size=5, start=START, end=END)[0]
    assert chunk.is_leaf(5)
    assert not chunk.is_leaf(4)
