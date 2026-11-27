from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.batch_worker import process_item
from app.services.detector import DetectorConfig
from app.services.ingest_queue import IngestItem
from tests.storagetools import count

NOW = datetime(2024, 1, 1, 10, 0, 10, tzinfo=UTC)
FIRST = "2024-01-01 10:00:00 ERROR payment 1 failed\n2024-01-01 10:00:05 INFO ok\n"


def item(data: str, seq: int, key: str, service: str = "svc", source: str = "src") -> IngestItem:
    return IngestItem(service, source, key, seq, data.encode())


def second_error(ts: str, info_ts: str) -> str:
    return (
        f"2024-01-01 {ts} ERROR database connection refused\n"
        f"2024-01-01 {info_ts} INFO ok\n"
    )


@pytest.mark.integration
async def test_second_signature_same_service_same_minute_is_grouped(engine: AsyncEngine) -> None:
    await process_item(engine, item(FIRST, 1, "k1"), now=NOW)
    out = await process_item(engine, item(second_error("10:00:30", "10:00:35"), 2, "k2"), now=NOW)
    assert await count(engine, "signatures") == 2
    assert out.incidents == ()
    assert await count(engine, "incidents") == 1


@pytest.mark.integration
async def test_second_signature_minutes_later_opens_its_own_incident(engine: AsyncEngine) -> None:
    await process_item(engine, item(FIRST, 1, "k1"), now=NOW)
    later = datetime(2024, 1, 1, 10, 5, 10, tzinfo=UTC)
    out = await process_item(engine, item(second_error("10:05:00", "10:05:05"), 2, "k2"), now=later)
    assert len(out.incidents) == 1
    assert await count(engine, "incidents") == 2


@pytest.mark.integration
async def test_second_signature_on_another_service_is_not_grouped(engine: AsyncEngine) -> None:
    await process_item(engine, item(FIRST, 1, "k1"), now=NOW)
    other = item(second_error("10:00:30", "10:00:35"), 2, "k2", service="other", source="src2")
    out = await process_item(engine, other, now=NOW)
    assert len(out.incidents) == 1
    assert await count(engine, "incidents") == 2


@pytest.mark.integration
async def test_grouping_can_be_switched_off(engine: AsyncEngine) -> None:
    cfg = DetectorConfig(group_window_s=0)
    await process_item(engine, item(FIRST, 1, "k1"), now=NOW, detector=cfg)
    out = await process_item(
        engine, item(second_error("10:00:30", "10:00:35"), 2, "k2"), now=NOW, detector=cfg
    )
    assert len(out.incidents) == 1
    assert await count(engine, "incidents") == 2


def test_invalid_grouping_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        DetectorConfig(group_window_s=-1)
    with pytest.raises(ValueError):
        DetectorConfig(group_recent_min=0)