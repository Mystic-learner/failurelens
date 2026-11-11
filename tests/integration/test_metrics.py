import json

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.batch_worker import process_item
from app.services.ingest_queue import IngestItem

pytestmark = pytest.mark.integration


async def add_incident(
    engine: AsyncEngine,
    n: int,
    status: str,
    *,
    seconds: int | None = None,
    diagnosis: dict[str, object] | None = None,
    deferred: bool = False,
) -> None:
    async with engine.begin() as conn:
        sig = (
            await conn.execute(
                text(
                    "INSERT INTO signatures (service, fingerprint) "
                    "VALUES ('svc', CAST(:fp AS text)) RETURNING id"
                ),
                {"fp": f"fp{n}"},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO incidents (signature_id, trigger, status, diagnosis, "
                "diagnosis_deferred, created_at, diagnosed_at) VALUES "
                "(CAST(:sig AS bigint), 'new_signature', CAST(:st AS text), "
                "CAST(:d AS jsonb), :deferred, "
                "now() - make_interval(secs => CAST(:secs AS integer)), "
                "CASE WHEN CAST(:secs AS integer) > 0 THEN now() ELSE NULL END)"
            ),
            {
                "sig": sig,
                "st": status,
                "d": json.dumps(diagnosis) if diagnosis is not None else None,
                "deferred": deferred,
                "secs": seconds or 0,
            },
        )


async def test_empty_database_gives_zeros_and_null_percentiles(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue"] == {"depth": 0, "max_size": 5}
    assert body["ingest"]["lines_per_s_1m"] == 0.0
    assert body["ingest"]["batches_committed_1h"] == 0
    assert body["ingest"]["queue_to_commit"] == {"count": 0, "p50_s": None, "p95_s": None}
    assert body["diagnosis"] == {
        "p50_s": None,
        "p95_s": None,
        "diagnosed_24h": 0,
        "inconclusive_rate": None,
    }
    assert body["incidents"] == {"open": 0, "diagnosing": 0, "deferred": 0}
    assert body["llm"]["tokens_used_this_hour"] == 0
    assert body["llm"]["hourly_cap"] > 0


async def test_ingest_rate_counts_committed_lines(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    data = b"".join(f"2024-01-01 10:00:0{i} ERROR boom {i}\n".encode() for i in range(6))
    await process_item(engine, IngestItem("svc", "src", "k1", 1, data))
    body = (await client.get("/metrics")).json()
    assert body["ingest"]["batches_committed_1h"] == 1
    assert body["ingest"]["lines_per_s_1m"] == pytest.approx(6 / 60.0)


async def test_incident_counts_and_diagnosis_statistics(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    await add_incident(engine, 1, "diagnosed", seconds=10, diagnosis={"status": "inconclusive"})
    await add_incident(
        engine,
        2,
        "diagnosed",
        seconds=20,
        diagnosis={"status": "diagnosed", "result": {"confidence": "high"}},
    )
    await add_incident(engine, 3, "open")
    await add_incident(engine, 4, "open", deferred=True)
    await add_incident(engine, 5, "diagnosing")

    body = (await client.get("/metrics")).json()
    assert body["incidents"] == {"open": 2, "diagnosing": 1, "deferred": 1}
    dia = body["diagnosis"]
    assert dia["diagnosed_24h"] == 2
    assert dia["inconclusive_rate"] == pytest.approx(0.5)
    assert dia["p50_s"] == pytest.approx(15.0, abs=0.05)
    assert dia["p95_s"] == pytest.approx(19.5, abs=0.05)


async def test_llm_tokens_used_this_hour(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO llm_usage (hour_bucket, tokens, count) VALUES "
                "(date_trunc('hour', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', 123, 1)"
            )
        )
    assert (await client.get("/metrics")).json()["llm"]["tokens_used_this_hour"] == 123


async def test_queue_to_commit_window_is_reported(client: httpx.AsyncClient) -> None:
    pool = client.app.state.pool  # type: ignore[attr-defined]
    for v in (1.0, 2.0, 3.0):
        pool.latency.record(v)
    snap = (await client.get("/metrics")).json()["ingest"]["queue_to_commit"]
    assert snap == {"count": 3, "p50_s": 2.0, "p95_s": 3.0}
