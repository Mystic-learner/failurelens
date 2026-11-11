from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.batch_worker import process_item
from app.services.detector import DetectorConfig
from app.services.ingest_queue import IngestItem
from app.services.worker_pool import flush_idle
from tests.storagetools import count, fetch_all, fetch_value

NOW = datetime(2024, 1, 1, 10, 0, 10, tzinfo=UTC)
ERR_THEN_INFO = "2024-01-01 10:00:00 ERROR payment 1 failed\n2024-01-01 10:00:05 INFO ok\n"


def item(data: str, seq: int, key: str, service: str = "svc", source: str = "src") -> IngestItem:
    return IngestItem(service, source, key, seq, data.encode())


def errors(hour: int, minute: int, n: int, start: int = 0) -> str:
    return "".join(
        f"2024-01-01 {hour:02d}:{minute:02d}:{s % 60:02d} ERROR payment {start + s} failed\n"
        for s in range(n)
    )


@pytest.mark.integration
async def test_new_error_signature_opens_one_incident_and_one_event(engine: AsyncEngine) -> None:
    out = await process_item(engine, item(ERR_THEN_INFO, 1, "k1"), now=NOW)
    assert len(out.incidents) == 1
    rows = await fetch_all(engine, "SELECT trigger, status, cooldown_until FROM incidents")
    assert [(r.trigger, r.status) for r in rows] == [("new_signature", "open")]
    assert await count(engine, "events") == 1
    assert await fetch_value(engine, "SELECT type FROM events") == "incident.opened"
    assert await fetch_value(engine, "SELECT payload->>'trigger' FROM events") == "new_signature"


@pytest.mark.integration
async def test_the_same_error_again_does_not_open_a_second_incident(engine: AsyncEngine) -> None:
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1"), now=NOW)
    again = "2024-01-01 10:01:00 ERROR payment 2 failed\n2024-01-01 10:01:05 INFO ok\n"
    out = await process_item(engine, item(again, 2, "k2"), now=NOW)
    assert out.incidents == ()
    assert await count(engine, "incidents") == 1


@pytest.mark.integration
async def test_info_only_input_opens_nothing(engine: AsyncEngine) -> None:
    raw = "2024-01-01 10:00:00 INFO a\n2024-01-01 10:00:01 INFO b\n2024-01-01 10:00:02 INFO c\n"
    out = await process_item(engine, item(raw, 1, "k1"), now=NOW)
    assert out.incidents == ()
    assert await count(engine, "incidents") == 0
    assert await count(engine, "events") == 0


@pytest.mark.integration
async def test_a_lone_pending_error_opens_its_incident_on_idle_flush(engine: AsyncEngine) -> None:
    out = await process_item(engine, item("2024-01-01 10:00:00 ERROR boom 1\n", 1, "k1"), now=NOW)
    assert out.incidents == ()  # the newest record is still pending
    assert await flush_idle(engine, 0) == 1
    assert await count(engine, "incidents") == 1
    assert await count(engine, "events") == 1


@pytest.mark.integration
async def test_same_error_in_two_services_is_two_incidents(engine: AsyncEngine) -> None:
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1", "alpha", "a"), now=NOW)
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1", "beta", "b"), now=NOW)
    assert await count(engine, "incidents") == 2
    assert await fetch_value(engine, "SELECT count(DISTINCT signature_id) FROM incidents") == 2


@pytest.mark.integration
async def test_10000_identical_errors_make_one_signature_and_one_incident(
    engine: AsyncEngine,
) -> None:
    def raw_for(batch: int) -> str:
        lines = []
        for j in range(1000):
            i = batch * 1000 + j
            h, m, s = 10 + i // 3600, (i % 3600) // 60, i % 60
            lines.append(f"2024-01-01 {h:02d}:{m:02d}:{s:02d} ERROR payment {i} failed")
        return "\n".join(lines) + "\n"

    await asyncio.gather(
        *(process_item(engine, item(raw_for(b), b + 1, f"k{b}"), now=NOW) for b in range(10))
    )
    await flush_idle(engine, 0)  # releases the very last pending record
    assert await count(engine, "signatures") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 10_000
    assert await count(engine, "incidents") == 1
    assert await count(engine, "events") == 1


async def _baseline(engine: AsyncEngine) -> None:
    """60 errors, one per minute from 09:00 to 09:59, stored without running the detector."""
    raw = "".join(f"2024-01-01 09:{m:02d}:00 ERROR payment {m} failed\n" for m in range(60))
    await process_item(engine, item(raw, 1, "base"), now=NOW, detector=None)


@pytest.mark.integration
async def test_rate_spike_above_baseline_opens_an_incident(engine: AsyncEngine) -> None:
    await _baseline(engine)
    burst = "".join(errors(10, m, 14, start=1000 * m) for m in (3, 4, 5))
    now = datetime(2024, 1, 1, 10, 5, 30, tzinfo=UTC)
    out = await process_item(engine, item(burst, 2, "burst"), now=now)
    assert len(out.incidents) == 1
    assert await fetch_value(engine, "SELECT trigger FROM incidents") == "rate_spike"


@pytest.mark.integration
async def test_a_small_burst_below_min_count_opens_nothing(engine: AsyncEngine) -> None:
    await _baseline(engine)
    now = datetime(2024, 1, 1, 10, 5, 30, tzinfo=UTC)
    out = await process_item(engine, item(errors(10, 4, 10), 2, "small"), now=now)
    assert out.incidents == ()
    assert await count(engine, "incidents") == 0


@pytest.mark.integration
async def test_cooldown_blocks_a_repeat_until_it_expires(engine: AsyncEngine) -> None:
    cfg = DetectorConfig(min_count=1, spike_factor=0.0, cooldown_min=10)
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1"), now=NOW, detector=cfg)
    await engine_exec(engine, "UPDATE incidents SET status = 'resolved', resolved_at = now()")

    during = "2024-01-01 10:05:00 ERROR payment 2 failed\n2024-01-01 10:05:05 INFO ok\n"
    now2 = datetime(2024, 1, 1, 10, 5, 10, tzinfo=UTC)
    blocked = await process_item(engine, item(during, 2, "k2"), now=now2, detector=cfg)
    assert blocked.incidents == ()
    assert await count(engine, "incidents") == 1

    after = "2024-01-01 10:18:00 ERROR payment 3 failed\n2024-01-01 10:18:05 INFO ok\n"
    now3 = datetime(2024, 1, 1, 10, 20, 10, tzinfo=UTC)
    reopened = await process_item(engine, item(after, 3, "k3"), now=now3, detector=cfg)
    assert len(reopened.incidents) == 1
    assert await count(engine, "incidents") == 2


async def engine_exec(engine: AsyncEngine, sql: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql))


def test_invalid_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        DetectorConfig(window_min=0)
    with pytest.raises(ValueError):
        DetectorConfig(min_count=0)
