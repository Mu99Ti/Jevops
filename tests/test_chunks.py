from datetime import UTC, datetime

from jevops.chunks import digest, split_range, subdivide
from jevops.models import LogEvent


def _line(i: int, ts: str, msg: str = "x") -> LogEvent:
    return LogEvent(id=f"f{i}", ts=ts, service="api", env="prod", level="ERROR", message=msg)


def test_split_range_covers_window_with_equal_buckets():
    lines = [_line(i, f"2026-09-24T08:{i:02d}:00Z") for i in range(40)]
    chunks = split_range(
        lines, datetime(2026, 9, 24, 8, 0, tzinfo=UTC), datetime(2026, 9, 24, 9, 0, tzinfo=UTC), parts=4
    )
    assert len(chunks) == 4
    assert chunks[0].start.hour == 8 and chunks[0].start.minute == 0
    assert chunks[-1].end.hour == 9
    assert all(c.start < c.end for c in chunks)
    assert all(c.path for c in chunks)
    total = sum(len(c.lines) for c in chunks)
    assert total == 40


def test_split_range_puts_out_of_range_lines_in_nearest_bucket():
    early = [_line(0, "2026-09-24T07:00:00Z")]
    chunks = split_range(
        early, datetime(2026, 9, 24, 8, 0, tzinfo=UTC), datetime(2026, 9, 24, 9, 0, tzinfo=UTC), parts=2
    )
    assert sum(len(c.lines) for c in chunks) == 1


def test_missing_timestamp_goes_to_first_bucket():
    chunks = split_range(
        [_line(0, "")], datetime(2026, 9, 24, 8, 0, tzinfo=UTC), datetime(2026, 9, 24, 9, 0, tzinfo=UTC), parts=3
    )
    assert len(chunks[0].lines) == 1
    assert all(len(c.lines) == 0 for c in chunks[1:])


def test_subdivide_partitions_parent_window():
    chunks = split_range([], datetime(2026, 9, 24, 8, 0, tzinfo=UTC), datetime(2026, 9, 24, 9, 0, tzinfo=UTC), parts=2)
    kids = subdivide(chunks[0], parts=4)
    assert len(kids) == 4
    assert kids[0].start == chunks[0].start
    assert kids[-1].end == chunks[0].end
    assert kids[1].start == kids[0].end
    assert all(k.path.startswith(chunks[0].path) for k in kids)


def test_digest_shape():
    lines = [
        LogEvent(id="a", ts="2026-09-24T08:01:00Z", service="search", env="prod", level="ERROR", message="disk 91"),
        LogEvent(id="b", ts="2026-09-24T08:02:00Z", service="search", env="prod", level="WARN", message="slow query"),
    ]
    chunks = split_range(
        lines, datetime(2026, 9, 24, 8, 0, tzinfo=UTC), datetime(2026, 9, 24, 9, 0, tzinfo=UTC), parts=2
    )
    d = digest(chunks[0])
    assert d["id"] == chunks[0].path
    assert d["count"] == 2
    assert d["services"] == {"search": 2}
    assert d["levels"]["ERROR"] == 1
    assert any("disk 91" in m for m in d["samples"])


def test_chunk_leaf_detection():
    lines = [_line(i, "2026-09-24T08:01:00Z", f"m{i}") for i in range(5)]
    chunks = split_range(
        lines, datetime(2026, 9, 24, 8, 0, tzinfo=UTC), datetime(2026, 9, 24, 9, 0, tzinfo=UTC), parts=1
    )
    assert len(chunks[0].lines) == 5
    assert chunks[0].is_leaf(5)
    assert not chunks[0].is_leaf(4)
