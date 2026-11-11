import time

import pytest

from app.services.fingerprint import (
    MAX_ERROR_CODES,
    MAX_SIGNATURE_LINES,
    extract_error_codes,
    make_fingerprint,
    normalize_line,
    signature_lines,
    stable_error_codes,
)


def test_same_failure_with_different_ids_and_timestamps() -> None:
    a = "2024-01-01 10:00:00 ERROR payment 123 failed for user 77 from 10.0.0.1 trace 0xdeadbeef"
    b = "2025-06-30 23:59:59 ERROR payment 98765 failed for user 4 from <IP_3> trace 0x1f"
    assert make_fingerprint(a) == make_fingerprint(b)


def test_different_failures_differ() -> None:
    a = "ERROR connection refused to db"
    b = "ERROR disk full on volume"
    assert make_fingerprint(a) != make_fingerprint(b)


def test_http_status_is_part_of_the_identity() -> None:
    a = "ERROR upstream returned HTTP/1.1 500"
    b = "ERROR upstream returned HTTP/1.1 404"
    assert make_fingerprint(a) != make_fingerprint(b)


def test_log_level_does_not_change_the_fingerprint() -> None:
    assert make_fingerprint("WARN cache miss storm") == make_fingerprint("ERROR cache miss storm")


def test_hdfs_block_ids_and_pids_are_ignored() -> None:
    a = (
        "081109 203615 148 INFO dfs.DataNode$PacketResponder: "
        "PacketResponder 1 for block blk_38865049064139660 terminating"
    )
    b = (
        "081110 101010 22 INFO dfs.DataNode$PacketResponder: "
        "PacketResponder 2 for block blk_-1608999687919862906 terminating"
    )
    assert make_fingerprint(a) == make_fingerprint(b)


def test_syslog_prefix_and_pid_are_ignored() -> None:
    a = "Nov  3 10:00:00 host app[123]: boom"
    b = "Dec 25 01:02:03 host app[999]: boom"
    assert make_fingerprint(a) == make_fingerprint(b)


def test_python_traceback_ignores_line_numbers_and_values() -> None:
    def trace(req: int, line: int, bad: int, exc: str = "ValueError") -> str:
        return (
            f"ERROR request {req} failed\n"
            "Traceback (most recent call last):\n"
            f'  File "/app/a.py", line {line}, in handler\n'
            "    do()\n"
            f"{exc}: bad {bad}\n"
        )

    assert make_fingerprint(trace(1, 10, 5)) == make_fingerprint(trace(2, 99, 9))
    assert make_fingerprint(trace(1, 10, 5)) != make_fingerprint(trace(1, 10, 5, "KeyError"))


def test_frames_beyond_the_limit_are_ignored() -> None:
    head = "ERROR boom\n" + "\n".join(f"\tat com.acme.C{i}.m(C.java:{i})" for i in range(5))
    a = head + "\n\tat X.y(X.java:1)\n\tat X.z(X.java:2)"
    b = head + "\n\tat Z.q(Z.java:9)"
    assert make_fingerprint(a) == make_fingerprint(b)


def test_signature_lines_are_capped() -> None:
    text = "ERROR boom\n" + "\n".join(f"KeyError: k{i}" for i in range(100))
    assert len(signature_lines(text)) == MAX_SIGNATURE_LINES


def test_placeholders_survive_normalisation() -> None:
    assert normalize_line("<EMAIL> <SECRET> failed") == "<EMAIL> <SECRET> failed"


def test_extract_error_codes() -> None:
    text = (
        "Error: connect ECONNRESET 10.0.0.1:443 errno -104 HTTP/1.1 503 code=404 "
        "0xC0000005 java.lang.IllegalStateException ValueError"
    )
    codes = extract_error_codes(text)
    for expected in (
        "ECONNRESET",
        "errno_104",
        "HTTP_503",
        "HTTP_404",
        "0xc0000005",
        "java.lang.IllegalStateException",
        "ValueError",
    ):
        assert expected in codes


def test_no_false_positives_on_ordinary_text() -> None:
    assert extract_error_codes("INFO all good 200 ok") == []
    assert extract_error_codes("ERROR something EMPTY ENDED") == []


def test_hex_codes_are_searchable_but_not_stable() -> None:
    assert stable_error_codes("crash at 0xdeadbeef ECONNRESET") == ["ECONNRESET"]
    assert "0xdeadbeef" in extract_error_codes("crash at 0xdeadbeef ECONNRESET")


def test_error_code_count_is_capped() -> None:
    names = [f"Foo{chr(97 + i // 26)}{chr(97 + i % 26)}Error" for i in range(100)]
    assert len(extract_error_codes(" ".join(names))) == MAX_ERROR_CODES


def test_empty_input() -> None:
    assert signature_lines("") == []
    assert extract_error_codes("") == []
    assert len(make_fingerprint("")) == 64


@pytest.mark.parametrize(
    "text",
    [
        "a." * 8000,
        "1." * 8000,
        "a1" * 8000,
        "ab" * 8000,
        "0x" * 8000,
        "Error" * 3000,
        "<IP_1>" * 2500,
    ],
)
def test_pathological_inputs_are_fast(text: str) -> None:
    start = time.perf_counter()
    make_fingerprint(text)
    extract_error_codes(text)
    assert time.perf_counter() - start < 2.0
