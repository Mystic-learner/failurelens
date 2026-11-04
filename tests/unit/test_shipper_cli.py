import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shipper.backoff import Backoff
from shipper.cli import build_parser, cut_batches, pump, resolve, run_loop
from shipper.forwarder import Forwarder
from shipper.reader import Batch, LogReader
from shipper.sender import SendResult
from shipper.spool import Spool
from shipper.state import StateStore


class FakeClient:
    """Accepts everything and reports every sent key as committed."""

    def __init__(self) -> None:
        self.sent: list[Batch] = []

    async def send(self, batch: Batch) -> SendResult:
        self.sent.append(batch)
        return SendResult("accepted", 202)

    async def committed_keys(self, keys: list[str]) -> set[str] | None:
        sent = {b.key for b in self.sent}
        return {k for k in keys if k in sent}


def parts(
    tmp_path: Path, max_wait_s: float = 0.0, spool_bytes: int = 10_000_000
) -> tuple[LogReader, Spool, Forwarder, FakeClient]:
    reader = LogReader(
        tmp_path / "app.log",
        "src",
        StateStore(tmp_path / "state" / "state.json", fsync=False),
        max_wait_s=max_wait_s,
    )
    spool = Spool(tmp_path / "spool", max_bytes=spool_bytes, fsync=False)
    client = FakeClient()
    forwarder = Forwarder(spool, client, Backoff(base_s=0.01, cap_s=0.05), poll_s=0.01, idle_s=0.01)
    return reader, spool, forwarder, client


async def wait_until(predicate: object, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():  # type: ignore[operator]
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_lines_written_to_a_file_are_shipped_and_the_spool_empties(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_bytes(b"one\ntwo\nthree\n")
    reader, spool, forwarder, client = parts(tmp_path)
    stop = asyncio.Event()
    task = asyncio.create_task(run_loop(reader, spool, forwarder, stop, tick_s=0.02))
    await wait_until(lambda: client.sent and len(spool) == 0)
    stop.set()
    await task
    assert b"".join(b.data for b in client.sent) == b"one\ntwo\nthree\n"


@pytest.mark.asyncio
async def test_shutdown_cuts_and_delivers_lines_that_were_still_buffered(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_bytes(b"late line\n")
    reader, spool, forwarder, client = parts(tmp_path, max_wait_s=1000.0)  # never "due"
    stop = asyncio.Event()
    stop.set()
    await run_loop(reader, spool, forwarder, stop, tick_s=0.02, drain_timeout_s=5.0)
    assert [b.data for b in client.sent] == [b"late line\n"]
    assert len(spool) == 0


@pytest.mark.asyncio
async def test_undeliverable_batches_stay_in_the_spool_after_the_drain_timeout(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_bytes(b"a\n")
    reader, spool, forwarder, client = parts(tmp_path)

    async def down(batch: Batch) -> SendResult:
        return SendResult("retry", 503)

    client.send = down  # type: ignore[method-assign]
    stop = asyncio.Event()
    stop.set()
    await run_loop(reader, spool, forwarder, stop, tick_s=0.02, drain_timeout_s=0.3)
    assert len(spool) == 1
    again = Spool(tmp_path / "spool", fsync=False)  # a new run finds it
    assert again.pending_seqs() == [1]


def test_full_spool_pauses_reading_without_losing_the_batch(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_bytes(b"x" * 100 + b"\n" + b"y" * 100 + b"\n")
    reader = LogReader(
        tmp_path / "app.log", "src", StateStore(tmp_path / "st.json", fsync=False), max_lines=1
    )
    spool = Spool(tmp_path / "spool", max_bytes=150, fsync=False)
    assert cut_batches(reader, spool, force=True) == 1  # second batch does not fit
    spool.pop(1)
    assert cut_batches(reader, spool, force=True) == 1
    assert spool.get(2) is not None and spool.get(2).data == b"y" * 100 + b"\n"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_pump_appends_lines_and_adds_missing_newlines(tmp_path: Path) -> None:
    async def lines() -> AsyncIterator[bytes]:
        yield b"a\n"
        yield b"b"
        yield b"c\r\n"

    await pump(lines(), tmp_path / "x" / "feed.log")
    assert (tmp_path / "x" / "feed.log").read_bytes() == b"a\nb\nc\r\n"


def test_resolve_file_mode_defaults(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--file", str(tmp_path / "web.log"), "--state-dir", str(tmp_path / "s")])
    service, source_id, state_dir, tail_from = resolve(args)
    assert service == "web"
    assert source_id == str((tmp_path / "web.log").resolve())
    assert state_dir == tmp_path / "s"
    assert tail_from == (tmp_path / "web.log").resolve()


def test_resolve_stdin_and_docker_use_a_feed_file(tmp_path: Path) -> None:
    a = build_parser().parse_args(["--stdin", "--state-dir", str(tmp_path)])
    assert resolve(a)[0::3] == ("stdin", tmp_path / "feed.log")
    d = build_parser().parse_args(["--docker", "api", "--service", "shop", "--state-dir", str(tmp_path)])
    service, source_id, _, tail_from = resolve(d)
    assert (service, source_id, tail_from) == ("shop", "docker:api", tmp_path / "feed.log")


def test_default_state_dir_is_stable_per_source_and_differs_between_sources() -> None:
    def state(argv: list[str]) -> Path:
        return resolve(build_parser().parse_args(argv))[2]

    assert state(["--docker", "a"]) == state(["--docker", "a"])
    assert state(["--docker", "a"]) != state(["--docker", "b"])


def test_exactly_one_source_is_required() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--stdin", "--docker", "x"])


def test_parser_returns_a_namespace() -> None:
    assert isinstance(build_parser().parse_args(["--stdin"]), argparse.Namespace)