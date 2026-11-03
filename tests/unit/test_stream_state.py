import json
from typing import Any

import pytest

from app.services.stream_processor import StreamProcessor

TRACE_1 = (
    "2024-01-01 10:00:00 ERROR request failed\n"
    "Traceback (most recent call last):\n"
    '  File "/app/a.py", line 10, in handler\n'
)
TRACE_2 = "    do()\nValueError: bad 5\n2024-01-01 10:00:01 INFO next\n"


def through_json(state: dict[str, Any]) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(json.dumps(state))
    return loaded


def test_trace_split_across_two_processors_stays_whole() -> None:
    first = StreamProcessor("s")
    assert first.feed_text(TRACE_1) == []
    second = StreamProcessor("s")
    second.import_state(through_json(first.export_state()))
    out = second.feed_text(TRACE_2)
    assert len(out) == 1
    assert out[0].chunk.text == TRACE_1 + "    do()\nValueError: bad 5"
    assert out[0].chunk.level == "ERROR"
    assert "stack_trace" in out[0].chunk.flags
    rest = second.flush()
    assert [c.chunk.text for c in rest] == ["2024-01-01 10:00:01 INFO next"]


def test_ip_placeholders_survive_a_restart() -> None:
    first = StreamProcessor("s")
    first.feed_text("2024-01-01 10:00:00 INFO from 10.0.0.1\n")
    second = StreamProcessor("s")
    second.import_state(through_json(first.export_state()))
    out = second.feed_text("2024-01-01 10:00:01 INFO from 10.0.0.2 and 10.0.0.1\n") + second.flush()
    # The restored pending record is released by feed_text and the new one by flush,
    # so they are two chunks. What matters is that the IP numbers carried over.
    assert [c.chunk.text for c in out] == [
        "2024-01-01 10:00:00 INFO from <IP_1>",
        "2024-01-01 10:00:01 INFO from <IP_2> and <IP_1>",
    ]

def test_empty_state_is_a_fresh_processor() -> None:
    p = StreamProcessor("s")
    p.import_state({})
    assert p.flush() == []


def test_state_after_flush_has_no_pending_record() -> None:
    p = StreamProcessor("s")
    p.feed_text(TRACE_1)
    p.flush()
    assert p.export_state()["parser"]["pending"] is None


def test_unknown_version_is_rejected() -> None:
    with pytest.raises(ValueError):
        StreamProcessor("s").import_state({"v": 2})


def test_corrupt_pending_record_is_rejected() -> None:
    bad = {"v": 1, "parser": {"max_ts": None, "pending": {"head": []}}, "ips": {}}
    with pytest.raises((KeyError, IndexError)):
        StreamProcessor("s").import_state(bad)