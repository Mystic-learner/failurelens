"""End to end: shipper (reader, spool, forwarder, HTTP client) against the real app,
worker pool and Postgres, including a server outage and a restart mid-stream."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from shipper.backoff import Backoff
from shipper.cli import run_loop
from shipper.forwarder import Forwarder
from shipper.reader import LogReader
from shipper.sender import IngestClient
from shipper.spool import Spool
from shipper.state import StateStore
from tests.appclient import make_settings, running_client
from tests.storagetools import fetch_value

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def line(i: int) -> bytes:
    return f"2024-01-01 10:{i // 60:02d}:{i % 60:02d} ERROR payment {i} failed\n".encode()


def append_lines(path: Path, start: int, end: int) -> None:
    with open(path, "ab") as fh:
        for i in range(start, end):
            fh.write(line(i))


async def ship(tmp_path: Path, http: httpx.AsyncClient, drain_s: float) -> Spool:
    """One shipper run: read everything now available, then deliver for up to drain_s."""
    reader = LogReader(
        tmp_path / "app.log",
        "src",
        StateStore(tmp_path / "state" / "state.json", fsync=False),
        max_lines=50,
        max_wait_s=0.0,
    )
    spool = Spool(tmp_path / "spool", fsync=False)
    client = IngestClient("http://test", "svc", "src", http=http)
    forwarder = Forwarder(spool, client, Backoff(base_s=0.01, cap_s=0.05), poll_s=0.05, idle_s=0.05)
    stop = asyncio.Event()
    stop.set()  # one pass: cut what is there, then drain
    await run_loop(reader, spool, forwarder, stop, tick_s=0.01, drain_timeout_s=drain_s)
    reader.close()
    return spool


def dead_server() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("server is down")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


async def test_outage_and_restart_lose_nothing_and_duplicate_nothing(
    migrated_db_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    log = tmp_path / "app.log"
    settings = make_settings(
        database_url=migrated_db_url,
        worker_count=2,
        worker_flush_interval_s=60,
        stream_idle_s=60,
    )

    # Run 1: server up, 150 lines (3 batches).
    append_lines(log, 0, 150)
    async with running_client(settings) as app_client:
        spool = await ship(tmp_path, app_client, drain_s=10.0)
        assert len(spool) == 0
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 150

    # Run 2: server DOWN, 150 more lines. Nothing is lost: they wait in the disk spool.
    append_lines(log, 150, 300)
    async with dead_server() as down:
        spool = await ship(tmp_path, down, drain_s=0.5)
    assert len(spool) == 3
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 150

    # Run 3: a restarted server (new app, same database). The shipper process also restarts.
    async with running_client(settings) as app_client:
        spool = await ship(tmp_path, app_client, drain_s=10.0)
        assert len(spool) == 0
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 300
    assert await fetch_value(engine, "SELECT count(DISTINCT chunk_hash) FROM chunks") == 300
    assert await fetch_value(engine, "SELECT count FROM signatures") == 300
    assert await fetch_value(engine, "SELECT count(*) FROM signatures") == 1


async def test_a_batch_sent_twice_by_the_shipper_is_stored_once(
    migrated_db_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    """Server restarts after the 202 but before the shipper saw the commit: shipper resends."""
    log = tmp_path / "app.log"
    append_lines(log, 0, 50)
    settings = make_settings(
        database_url=migrated_db_url,
        worker_count=2,
        worker_flush_interval_s=60,
        stream_idle_s=60,
    )
    async with running_client(settings) as app_client:
        await ship(tmp_path, app_client, drain_s=10.0)

    # Simulate "the shipper never learned about the commit": put the same batch back.
    spool = Spool(tmp_path / "spool", fsync=False)
    reader = LogReader(
        log,
        "src",
        StateStore(tmp_path / "other" / "state.json", fsync=False),
        max_lines=50,
        max_wait_s=0.0,
    )
    batch = reader.next_batch(force=True)
    assert batch is not None
    assert spool.put(batch)
    async with running_client(settings) as app_client:
        client = IngestClient("http://test", "svc", "src", http=app_client)
        forwarder = Forwarder(spool, client, Backoff(base_s=0.01, cap_s=0.05), poll_s=0.05)
        await forwarder.step()  # key already committed: confirmed and removed, never resent
        assert len(spool) == 0
    reader.close()
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 50
    assert await fetch_value(engine, "SELECT count FROM signatures") == 50
