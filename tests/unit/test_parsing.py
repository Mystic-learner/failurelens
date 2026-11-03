from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.parsing import (
    MAX_LINE_CHARS,
    MAX_RECORD_CHARS,
    BinaryInputError,
    LogRecord,
    StreamParser,
    decode_bytes,
    parse_timestamp,
    split_lines,
)

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)

PY_TRACE = (
    "2024-01-01 10:00:00 ERROR request failed\n"
    "Traceback (most recent call last):\n"
    '  File "app.py", line 1, in <module>\n'
    "    boom()\n"
    "ValueError: bad\n"
    "2024-01-01 10:00:01 INFO next\n"
)

JAVA_TRACE = (
    "2024-01-01 10:00:00 ERROR handler failed\n"
    "java.lang.IllegalStateException: boom\n"
    "\tat com.acme.Foo.bar(Foo.java:10)\n"
    "\tat com.acme.Main.main(Main.java:5)\n"
    "Caused by: java.io.IOException: disk\n"
    "\tat com.acme.Io.read(Io.java:3)\n"
    "\t... 12 more\n"
    "2024-01-01 10:00:01 INFO ok\n"
)


def parse_all(text: str) -> list[LogRecord]:
    parser = StreamParser(clock=lambda: NOW)
    records = parser.feed(split_lines(decode_bytes(text.encode("utf-8")).text))
    records.extend(parser.flush())
    return records


# ---------------------------------------------------------------- decoding
def test_empty_and_whitespace_only() -> None:
    assert decode_bytes(b"").text == ""
    assert split_lines("") == []
    assert parse_all("") == []
    assert parse_all("   \n\t\n  \n") == []


def test_binary_is_rejected() -> None:
    with pytest.raises(BinaryInputError):
        decode_bytes(bytes(range(256)) * 4)


def test_null_bytes_are_dropped_not_fatal() -> None:
    assert decode_bytes(b"ab\x00cd\n").text == "abcd\n"


def test_invalid_utf8_and_mixed_encodings_are_replaced() -> None:
    result = decode_bytes(b"caf\xe9 ok\n")
    assert result.had_replacements
    assert "ok" in result.text
    mixed = decode_bytes(b"ok \xc3\xa9 and \xe9\n")
    assert "\u00e9" in mixed.text
    assert mixed.had_replacements


def test_bom_and_ansi_removed() -> None:
    assert decode_bytes(b"\xef\xbb\xbfhello").text == "hello"
    assert decode_bytes(b"\x1b[31mERROR\x1b[0m boom").text == "ERROR boom"


def test_line_endings_and_missing_trailing_newline() -> None:
    assert split_lines("a\r\nb\r\nc") == ["a", "b", "c"]
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\rb") == ["a", "b"]


def test_single_1mb_line_is_truncated() -> None:
    lines = split_lines("x" * 1_000_000)
    assert len(lines) == 1
    assert len(lines[0]) < MAX_LINE_CHARS + 50
    assert "truncated" in lines[0]


def test_unicode_and_emoji_preserved() -> None:
    records = parse_all("2024-01-01 10:00:00 INFO h\u00e9llo \U0001f525 \u65e5\u672c\u8a9e\n")
    assert len(records) == 1
    assert "\U0001f525" in records[0].text
    assert "\u65e5\u672c\u8a9e" in records[0].text


# ---------------------------------------------------------------- timestamps
def test_offsets_normalise_to_utc() -> None:
    expected = datetime(2024, 3, 10, 6, 30, tzinfo=UTC)
    assert parse_timestamp("2024-03-10T12:00:00+05:30 x", UTC, NOW) == expected
    a = parse_timestamp("2024-01-01T10:00:00-0500 x", UTC, NOW)
    b = parse_timestamp("2024-01-01T15:00:00Z x", UTC, NOW)
    assert a == b


def test_naive_time_uses_default_tz() -> None:
    plus2 = timezone(timedelta(hours=2))
    got = parse_timestamp("2024-01-01 10:00:00 INFO x", plus2, NOW)
    assert got == datetime(2024, 1, 1, 8, 0, tzinfo=UTC)


def test_other_timestamp_formats() -> None:
    got = parse_timestamp("2024-01-01 10:00:00,123 x", UTC, NOW)
    assert got is not None
    assert got.microsecond == 123000
    assert parse_timestamp("081109 203615 148 INFO dfs", UTC, NOW) == datetime(
        2008, 11, 9, 20, 36, 15, tzinfo=UTC
    )
    assert parse_timestamp("Nov  3 10:00:00 host x", UTC, NOW) == datetime(
        2026, 11, 3, 10, 0, tzinfo=UTC
    )
    assert parse_timestamp("Dec 31 23:59:59 host x", UTC, NOW) == datetime(
        2025, 12, 31, 23, 59, 59, tzinfo=UTC
    )


def test_leap_second_and_invalid_timestamps() -> None:
    assert parse_timestamp("2016-12-31T23:59:60Z x", UTC, NOW) == datetime(
        2016, 12, 31, 23, 59, 59, tzinfo=UTC
    )
    assert parse_timestamp("2024-13-45 10:00:00 x", UTC, NOW) is None
    assert parse_timestamp("1850-01-01 10:00:00 x", UTC, NOW) is None
    assert parse_timestamp("no timestamp here", UTC, NOW) is None


def test_future_timestamp_is_flagged() -> None:
    far = parse_all("2030-01-01 10:00:00 INFO x\n")[0]
    assert "future_timestamp" in far.flags
    soon = (NOW + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert "future_timestamp" not in parse_all(f"{soon} INFO x\n")[0].flags


def test_out_of_order_is_flagged() -> None:
    records = parse_all(
        "2024-01-01 10:00:10 INFO a\n2024-01-01 10:00:20 INFO b\n2024-01-01 10:00:00 INFO c\n"
    )
    assert [("out_of_order" in r.flags) for r in records] == [False, False, True]


def test_no_timestamp_and_unknown_format() -> None:
    records = parse_all("just some text\nanother plain line\n")
    assert len(records) == 2
    for r in records:
        assert "no_timestamp" in r.flags
        assert r.level == "UNKNOWN"


# ---------------------------------------------------------------- levels / JSON
@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("2024-01-01 10:00:00 WARNING x", "WARN"),
        ("2024-01-01 10:00:00 ERROR x", "ERROR"),
        ("2024-01-01 10:00:00 CRITICAL x", "FATAL"),
        ("081109 203615 148 INFO dfs.DataNode: ok", "INFO"),
        ("level=error msg=boom", "ERROR"),
        ('{"level":"warn","msg":"x"}', "WARN"),
        ("plain text", "UNKNOWN"),
    ],
)
def test_level_detection(line: str, level: str) -> None:
    assert parse_all(line + "\n")[0].level == level


def test_json_lines_timestamp_and_level() -> None:
    rec = parse_all('{"timestamp":"2024-01-01T10:00:00Z","level":"error","msg":"boom"}\n')[0]
    assert rec.level == "ERROR"
    assert rec.timestamp == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    epoch = parse_all('{"ts":1700000000000,"level":"info"}\n')[0]
    assert epoch.timestamp == datetime.fromtimestamp(1700000000, UTC)


# ---------------------------------------------------------------- multi-line records
def test_python_traceback_is_one_record() -> None:
    records = parse_all(PY_TRACE)
    assert len(records) == 2
    assert records[0].line_count == 5
    assert records[0].level == "ERROR"
    assert "stack_trace" in records[0].flags
    assert "ValueError: bad" in records[0].text
    assert records[1].level == "INFO"


def test_java_trace_with_caused_by() -> None:
    records = parse_all(JAVA_TRACE)
    assert len(records) == 2
    assert records[0].line_count == 7
    assert "Caused by: java.io.IOException" in records[0].text


def test_trace_split_across_batches_is_reassembled() -> None:
    lines = split_lines(PY_TRACE)
    parser = StreamParser(clock=lambda: NOW)
    assert parser.feed(lines[:3]) == []
    done = parser.feed(lines[3:])
    assert len(done) == 1
    assert done[0].line_count == 5
    assert "ValueError: bad" in done[0].text
    rest = parser.flush()
    assert len(rest) == 1
    assert rest[0].level == "INFO"
    assert parser.flush() == []


def test_very_deep_trace_is_truncated_but_keeps_the_end() -> None:
    lines = ["2024-01-01 10:00:00 ERROR boom", "Traceback (most recent call last):"]
    lines += [f'  File "a.py", line {i}' for i in range(5000)]
    lines.append("RecursionError: maximum recursion depth exceeded")
    parser = StreamParser(clock=lambda: NOW)
    parser.feed(lines)
    records = parser.flush()
    assert len(records) == 1
    rec = records[0]
    assert rec.line_count == len(lines)
    assert "truncated" in rec.flags
    assert "RecursionError" in rec.text
    assert "lines truncated" in rec.text
    assert len(rec.text) < MAX_RECORD_CHARS


def test_record_with_huge_lines_is_capped() -> None:
    lines = ["2024-01-01 10:00:00 ERROR big"] + ["  " + "x" * 4000 for _ in range(30)]
    parser = StreamParser(clock=lambda: NOW)
    parser.feed(lines)
    rec = parser.flush()[0]
    assert "truncated" in rec.flags
    assert len(rec.text) <= MAX_RECORD_CHARS + 100


def test_lone_indented_line_is_its_own_record() -> None:
    assert len(parse_all("  indented only\n")) == 1