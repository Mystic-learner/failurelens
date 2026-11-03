from datetime import UTC, datetime

from app.services.chunking import build_chunks, make_chunk_hash
from app.services.parsing import LogRecord


def rec(
    text: str,
    level: str = "INFO",
    flags: tuple[str, ...] = (),
    ts: datetime | None = None,
    lines: int = 1,
) -> LogRecord:
    return LogRecord(text=text, level=level, timestamp=ts, flags=flags, line_count=lines)


def test_hash_is_stable_and_depends_on_source() -> None:
    assert make_chunk_hash("a", "s1") == make_chunk_hash("a", "s1")
    assert make_chunk_hash("a", "s1") != make_chunk_hash("a", "s2")
    assert make_chunk_hash("a", "s") != make_chunk_hash("b", "s")


def test_hash_ignores_trailing_whitespace_and_crlf() -> None:
    assert make_chunk_hash("a  \r\n", "s") == make_chunk_hash("a", "s")


def test_empty_input() -> None:
    assert build_chunks([], "s") == []


def test_info_records_are_packed_together() -> None:
    chunks = build_chunks([rec("x" * 100) for _ in range(10)], "s", max_chars=2000)
    assert len(chunks) == 1
    assert chunks[0].record_count == 10


def test_packing_respects_the_limit_and_loses_nothing() -> None:
    records = [rec(f"line {i} " + "x" * 90) for i in range(30)]
    chunks = build_chunks(records, "s", max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1000 for c in chunks)
    assert "\n".join(c.text for c in chunks) == "\n".join(r.text for r in records)


def test_no_line_lost_or_duplicated_with_mixed_levels() -> None:
    records = [
        rec(f"line {i} " + "x" * 50, level="ERROR" if i % 7 == 0 else "INFO") for i in range(100)
    ]
    chunks = build_chunks(records, "s", max_chars=500)
    assert "\n".join(c.text for c in chunks) == "\n".join(r.text for r in records)
    assert sum(c.record_count for c in chunks) == 100


def test_errors_get_their_own_chunk() -> None:
    chunks = build_chunks([rec("a"), rec("boom", level="ERROR"), rec("b")], "s")
    assert len(chunks) == 3
    assert chunks[1].level == "ERROR"
    assert chunks[1].text == "boom"


def test_two_unrelated_errors_are_never_merged() -> None:
    chunks = build_chunks([rec("db down", level="ERROR"), rec("disk full", level="ERROR")], "s")
    assert [c.text for c in chunks] == ["db down", "disk full"]


def test_big_stack_trace_stays_whole_even_above_the_limit() -> None:
    trace = "ERROR boom\n" + "\n".join(f'  File "a.py", line {i}' for i in range(300))
    chunks = build_chunks([rec(trace, level="ERROR", flags=("stack_trace",), lines=301)], "s")
    assert len(chunks) == 1
    assert chunks[0].text == trace
    assert chunks[0].line_count == 301


def test_chunk_timestamps_and_level() -> None:
    t1 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 1, 10, 0, 5, tzinfo=UTC)
    chunk = build_chunks([rec("a", ts=t2), rec("b", level="WARN", ts=t1)], "s")[0]
    assert chunk.first_ts == t1
    assert chunk.last_ts == t2
    assert chunk.level == "WARN"


def test_replay_gives_identical_hashes() -> None:
    records = [rec("a"), rec("boom", level="ERROR")]
    first = [c.chunk_hash for c in build_chunks(records, "s")]
    second = [c.chunk_hash for c in build_chunks(records, "s")]
    assert first == second