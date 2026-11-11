import time
from datetime import UTC, datetime

import pytest

from app.services.parsing import BinaryInputError
from app.services.stream_processor import ProcessedChunk, StreamProcessor


def fixed_clock() -> datetime:
    return datetime(2030, 1, 1, tzinfo=UTC)


def make() -> StreamProcessor:
    return StreamProcessor("stream-1", clock=fixed_clock)


def run(p: StreamProcessor, *batches: str) -> list[ProcessedChunk]:
    out: list[ProcessedChunk] = []
    for batch in batches:
        out += p.feed_text(batch)
    out += p.flush()
    return out


def run_bytes(*batches: bytes) -> list[ProcessedChunk]:
    p = make()
    out: list[ProcessedChunk] = []
    for batch in batches:
        out += p.feed_bytes(batch)
    out += p.flush()
    return out


def all_text(chunks: list[ProcessedChunk]) -> str:
    return "\n".join(c.chunk.text for c in chunks)


def test_empty_and_whitespace_input() -> None:
    p = make()
    assert p.feed_bytes(b"") == []
    assert p.feed_bytes(b"  \n\n \t\r\n") == []
    assert p.feed_text("") == []
    assert p.flush() == []


def test_binary_input_raises() -> None:
    with pytest.raises(BinaryInputError):
        make().feed_bytes(b"\x00\x01\x02" * 3000)


def test_newest_record_is_held_until_flush() -> None:
    p = make()
    assert p.feed_text("2024-01-01 10:00:00 INFO started\n") == []
    out = p.flush()
    assert len(out) == 1
    assert out[0].chunk.text == "2024-01-01 10:00:00 INFO started"


def test_no_trailing_newline() -> None:
    p = make()
    assert p.feed_bytes(b"2024-01-01 10:00:00 ERROR boom") == []
    out = p.flush()
    assert len(out) == 1
    assert out[0].chunk.text.endswith("boom")


def test_crlf_and_lf_give_identical_hashes() -> None:
    lf = "2024-01-01 10:00:00 INFO a\n2024-01-01 10:00:01 ERROR b\n"
    crlf = lf.replace("\n", "\r\n")
    a = run_bytes(lf.encode())
    b = run_bytes(crlf.encode())
    assert [c.chunk.chunk_hash for c in a] == [c.chunk.chunk_hash for c in b]


def test_stack_trace_split_across_batches_stays_whole() -> None:
    lines = [
        "2024-01-01 10:00:00 ERROR request failed",
        "Traceback (most recent call last):",
        '  File "/app/a.py", line 10, in handler',
        "    do()",
        "ValueError: bad 5",
    ]
    p = make()
    assert p.feed_text("\n".join(lines[:3]) + "\n") == []
    out = p.feed_text("\n".join(lines[3:]) + "\n2024-01-01 10:00:01 INFO next\n")
    assert len(out) == 1
    assert out[0].chunk.text == "\n".join(lines)
    assert out[0].chunk.level == "ERROR"
    assert "stack_trace" in out[0].chunk.flags
    assert out[0].fingerprint is not None
    rest = p.flush()
    assert len(rest) == 1
    assert rest[0].fingerprint is None


def test_secrets_never_reach_chunks() -> None:
    raw = (
        "2024-01-01 10:00:00 ERROR login failed for alice@example.com password=hunter2 "
        "Bearer abc123def456ghi789 key sk-proj1234567890abcdefghij\n"
    )
    text = all_text(run(make(), raw))
    for secret in (
        "alice@example.com",
        "hunter2",
        "abc123def456ghi789",
        "sk-proj1234567890abcdefghij",
    ):
        assert secret not in text
    for placeholder in ("<EMAIL>", "<SECRET>", "<TOKEN>", "<API_KEY>"):
        assert placeholder in text


def test_secret_split_across_two_lines_is_redacted() -> None:
    raw = "2024-01-01 10:00:00 INFO key sk-abcdefgh\nijklmnopqrstu\n2024-01-01 10:00:01 INFO next\n"
    text = all_text(run(make(), raw))
    assert "abcdefgh" not in text
    assert "ijklmnopqrstu" not in text
    assert "<API_KEY>" in text
    assert "INFO next" in text


def test_ip_placeholders_are_stable_across_batches() -> None:
    b1 = "2024-01-01 10:00:00 ERROR upstream 10.0.0.1 down\n"
    b2 = "2024-01-01 10:00:05 ERROR upstream 10.0.0.1 and 10.0.0.2 down\n"
    texts = [c.chunk.text for c in run(make(), b1, b2)]
    assert texts == [
        "2024-01-01 10:00:00 ERROR upstream <IP_1> down",
        "2024-01-01 10:00:05 ERROR upstream <IP_1> and <IP_2> down",
    ]


def test_replayed_batch_gives_the_same_hashes() -> None:
    batch = (
        "2024-01-01 10:00:00 INFO a\n2024-01-01 10:00:01 ERROR b 7\n2024-01-01 10:00:02 INFO c\n"
    )
    p = make()
    first = [c.chunk.chunk_hash for c in run(p, batch)]
    second = [c.chunk.chunk_hash for c in run(p, batch)]
    third = [c.chunk.chunk_hash for c in run(make(), batch)]
    assert first == second == third


def test_errors_are_isolated_and_fingerprinted() -> None:
    batch = (
        "2024-01-01 10:00:00 INFO started\n"
        "2024-01-01 10:00:01 ERROR payment 1 failed\n"
        "2024-01-01 10:00:02 INFO working\n"
        "2024-01-01 10:00:03 ERROR payment 22 failed\n"
    )
    out = run(make(), batch)
    assert [c.chunk.level for c in out] == ["INFO", "ERROR", "INFO", "ERROR"]
    fps = [c.fingerprint for c in out]
    assert fps[0] is None
    assert fps[2] is None
    assert fps[1] is not None
    assert fps[1] == fps[3]


def test_error_codes_are_extracted() -> None:
    out = run(make(), "2024-01-01 10:00:00 ERROR connect ECONNRESET to upstream\n")
    assert "ECONNRESET" in out[0].error_codes


def test_invalid_utf8_is_replaced_and_counted() -> None:
    p = make()
    out = p.feed_bytes(b"2024-01-01 10:00:00 INFO caf\xe9 ok\n") + p.flush()
    assert p.stats.decode_replacements == 1
    assert "\ufffd" in out[0].chunk.text


def test_unicode_and_null_bytes() -> None:
    data = "2024-01-01 10:00:00 INFO h\u00e9llo \U0001f525\x00 ok\n".encode()
    text = all_text(run_bytes(data))
    assert "\U0001f525" in text
    assert "\x00" not in text


def test_one_megabyte_single_line_is_fast_and_bounded() -> None:
    start = time.perf_counter()
    out = run(make(), "2024-01-01 10:00:00 INFO " + "a" * 1_000_000 + "\n")
    assert time.perf_counter() - start < 10.0
    assert len(out) == 1
    assert len(out[0].chunk.text) <= 16_000


def test_very_deep_stack_trace_is_truncated_but_one_chunk() -> None:
    lines = ["2024-01-01 10:00:00 ERROR boom"]
    lines += [f"    at com.acme.C{i}.m(C.java:{i})" for i in range(500)]
    out = run(make(), "\n".join(lines) + "\n")
    assert len(out) == 1
    c = out[0].chunk
    assert c.record_count == 1
    assert c.line_count == 501
    assert "truncated" in c.flags
    assert "stack_trace" in c.flags
    assert out[0].fingerprint is not None


def test_unknown_format_without_timestamps() -> None:
    out = run(make(), "just some words\nmore words here\n")
    # Each non-indented line without a timestamp starts its own record, and the newest
    # record is only released by flush(), so these two records land in two chunks.
    assert [c.chunk.text for c in out] == ["just some words", "more words here"]
    for c in out:
        assert c.chunk.record_count == 1
        assert "no_timestamp" in c.chunk.flags
        assert c.fingerprint is None


def test_records_released_in_one_call_are_packed_into_one_chunk() -> None:
    out = run(make(), "just some words\nmore words here\nthird line\n")
    # The third line releases records 1 and 2 together (packed); record 3 waits for flush().
    assert [c.chunk.record_count for c in out] == [2, 1]
