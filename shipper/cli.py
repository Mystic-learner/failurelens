"""failurelens-tail: ship logs from a file, stdin or `docker logs` to FailureLens.

    python -m shipper --file app.log --service payments
    some-command 2>&1 | python -m shipper --stdin --service batch-job
    python -m shipper --docker my-container --service api

Pipeline: source -> LogReader (cuts numbered batches) -> Spool (durable, on disk) ->
Forwarder (sends, retries with backoff, deletes a batch only when the server confirms its
key as committed). A batch is acknowledged to the reader as soon as it is in the spool, so a
server outage never stops reading; the spool buffers (bounded, see Spool).

stdin and docker output are appended to <state-dir>/feed.log, and that file is tailed like
any other. So restart/resume, rotation handling and the disk buffer work the same way.
Known limit: feed.log is never truncated. When stdin or the docker stream ends, the shipper
flushes what it has and exits. Ctrl+C / SIGTERM: cut the buffered lines, then try to deliver
for up to --drain-timeout seconds; whatever is left stays in the spool for the next run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import BinaryIO

from shipper.backoff import Backoff
from shipper.forwarder import Forwarder
from shipper.reader import LogReader
from shipper.sender import IngestClient
from shipper.spool import Spool
from shipper.state import StateStore

logger = logging.getLogger("failurelens.shipper")


# ------------------------------------------------------------------ core loop
def cut_batches(reader: LogReader, spool: Spool, force: bool) -> int:
    """Move every ready batch from the reader into the spool. Returns how many."""
    moved = 0
    while True:
        batch = reader.next_batch(force=force)
        if batch is None:
            return moved
        if not spool.put(batch):
            logger.warning("spool is full, reading paused until the server catches up")
            return moved
        reader.ack(batch)
        moved += 1


async def run_loop(
    reader: LogReader,
    spool: Spool,
    forwarder: Forwarder,
    stop: asyncio.Event,
    *,
    tick_s: float = 0.2,
    drain_timeout_s: float = 10.0,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    next_forward = 0.0
    while not stop.is_set():
        cut_batches(reader, spool, force=False)
        now = clock()
        if now >= next_forward:
            next_forward = now + await forwarder.step()
        try:
            await asyncio.wait_for(stop.wait(), tick_s)
        except TimeoutError:
            pass

    cut_batches(reader, spool, force=True)
    deadline = clock() + drain_timeout_s
    while len(spool) and clock() < deadline:
        delay = await forwarder.step()
        if len(spool) == 0:
            break
        await asyncio.sleep(max(min(delay, 0.2), 0.05))
    if len(spool):
        logger.warning("%d batch(es) stay in the spool for the next run", len(spool))


# ------------------------------------------------------------------ feed sources
def _open_feed(feed: Path) -> BinaryIO:
    feed.parent.mkdir(parents=True, exist_ok=True)
    return open(feed, "ab")  # noqa: SIM115 - closed by pump()

async def pump(lines: AsyncIterator[bytes], feed: Path) -> None:
    """Append every line to the feed file (adding a newline if missing)."""
    fh = _open_feed(feed)
    try:
        async for line in lines:
            fh.write(line if line.endswith(b"\n") else line + b"\n")
            fh.flush()
    finally:
        fh.close()


async def stdin_lines() -> AsyncIterator[bytes]:
    """Read stdin in a daemon thread so Ctrl+C never hangs waiting for a last line."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def reader_thread() -> None:
        while True:
            line = sys.stdin.buffer.readline()
            loop.call_soon_threadsafe(queue.put_nowait, line or None)
            if not line:
                return

    threading.Thread(target=reader_thread, daemon=True).start()
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


async def docker_lines(container: str) -> AsyncIterator[bytes]:
    proc = await asyncio.create_subprocess_exec(
        "docker", "logs", "--follow", "--tail", "0", container,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    try:
        async for line in proc.stdout:
            yield line
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()


# ------------------------------------------------------------------ arguments
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="failurelens-tail", description=__doc__.split("\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="log file to tail")
    src.add_argument("--stdin", action="store_true", help="read lines from stdin")
    src.add_argument("--docker", metavar="CONTAINER", help="follow `docker logs` of a container")
    p.add_argument("--url", default=os.environ.get("FAILURELENS_URL", "http://localhost:8000"))
    p.add_argument("--service", help="service name (default: derived from the source)")
    p.add_argument("--source-id", help="stable id of this log source (default: derived)")
    p.add_argument("--state-dir", type=Path, help="where offsets and the spool live")
    p.add_argument("--from-end", action="store_true", help="file mode: skip existing content")
    p.add_argument("--max-lines", type=int, default=500)
    p.add_argument(
        "--max-wait", type=float, default=1.0, help="seconds before a partial batch is cut"
    )
    p.add_argument("--drain-timeout", type=float, default=10.0)
    p.add_argument("--spool-mb", type=int, default=256)
    p.add_argument("--verbose", action="store_true")
    return p


def resolve(args: argparse.Namespace) -> tuple[str, str, Path, Path]:
    """(service, source_id, state_dir, file_to_tail)."""
    if args.file is not None:
        path = Path(args.file).resolve()
        service = args.service or path.stem
        source_id = args.source_id or str(path)
        tail_from = path
    elif args.stdin:
        service = args.service or "stdin"
        source_id = args.source_id or "stdin"
        tail_from = Path()
    else:
        service = args.service or str(args.docker)
        source_id = args.source_id or f"docker:{args.docker}"
        tail_from = Path()
    digest = hashlib.sha256(f"{service}|{source_id}".encode()).hexdigest()[:16]
    state_dir = Path(args.state_dir) if args.state_dir else Path.home() / ".failurelens" / digest
    if args.file is None:
        tail_from = state_dir / "feed.log"
    return service, source_id, state_dir, tail_from


# ------------------------------------------------------------------ main
async def amain(args: argparse.Namespace) -> None:
    service, source_id, state_dir, tail_from = resolve(args)
    state_dir.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        try:
            signal.signal(getattr(signal, name), lambda *_: loop.call_soon_threadsafe(stop.set))
        except (ValueError, OSError, AttributeError):
            pass

    store = StateStore(state_dir / "state.json")
    reader = LogReader(
        tail_from,
        source_id,
        store,
        max_lines=args.max_lines,
        max_wait_s=args.max_wait,
        from_beginning=not args.from_end,
    )
    if reader.load_error:
        logger.warning("%s; shipping from the start of the file", reader.load_error)
    spool = Spool(state_dir / "spool", max_bytes=args.spool_mb * 1024 * 1024)
    client = IngestClient(args.url, service, source_id)
    forwarder = Forwarder(spool, client, Backoff())

    pump_task: asyncio.Task[None] | None = None
    if args.stdin:
        pump_task = asyncio.create_task(pump(stdin_lines(), tail_from))
    elif args.docker:
        pump_task = asyncio.create_task(pump(docker_lines(args.docker), tail_from))
    if pump_task is not None:
        pump_task.add_done_callback(lambda _t: stop.set())

    logger.info("shipping %s (service=%s) to %s", tail_from, service, args.url)
    try:
        await run_loop(reader, spool, forwarder, stop, drain_timeout_s=args.drain_timeout)
    finally:
        if pump_task is not None and not pump_task.done():
            pump_task.cancel()
        if pump_task is not None:
            await asyncio.gather(pump_task, return_exceptions=True)
        reader.close()
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
    return 0