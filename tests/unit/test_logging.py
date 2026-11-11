import io
import json
import logging
import sys
from collections.abc import Iterator

import pytest

from app.core.logging import JsonFormatter, configure_logging
from app.core.request_id import request_id_var


def make_record(
    message: str = "hello %s", args: tuple[object, ...] = ("world",)
) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, message, args, None)


def test_formats_one_json_object_with_request_id() -> None:
    token = request_id_var.set("req-123")
    try:
        data = json.loads(JsonFormatter().format(make_record()))
    finally:
        request_id_var.reset(token)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert data["logger"] == "t"
    assert data["request_id"] == "req-123"
    assert data["ts"].endswith("+00:00")


def test_request_id_defaults_to_dash_outside_a_request() -> None:
    assert json.loads(JsonFormatter().format(make_record()))["request_id"] == "-"


def test_unicode_is_not_escaped() -> None:
    out = JsonFormatter().format(make_record("caf\u00e9 \U0001f525", ()))
    assert "caf\u00e9 \U0001f525" in out


def test_exception_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    data = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in data["exception"]


@pytest.fixture
def clean_root() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_configure_logging_is_idempotent(clean_root: logging.Logger) -> None:
    configure_logging("INFO")
    configure_logging("DEBUG")
    ours = [h for h in clean_root.handlers if getattr(h, "_failurelens_handler", False)]
    assert len(ours) == 1
    assert clean_root.level == logging.DEBUG


def test_configure_logging_writes_json_to_stdout(
    clean_root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    configure_logging("INFO")
    logging.getLogger("x").info("ping")
    line = buffer.getvalue().strip().splitlines()[-1]
    assert json.loads(line)["message"] == "ping"
