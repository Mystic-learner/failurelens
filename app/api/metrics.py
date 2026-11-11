"""GET /metrics: queue, ingest rate, latency, incident and LLM-budget numbers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.services.ingest_queue import IngestQueue
from app.services.llm_budget import used_this_hour
from app.services.worker_pool import WorkerPool

router = APIRouter()

_INGEST_SQL = text(
    "SELECT COALESCE(SUM(line_count) "
    "FILTER (WHERE created_at > now() - interval '60 seconds'), 0), "
    "COUNT(*) FILTER (WHERE created_at > now() - interval '1 hour') FROM ingest_batches"
)

_DURATION = "EXTRACT(EPOCH FROM (diagnosed_at - created_at))"
_DIAGNOSIS_SQL = text(
    "SELECT "
    f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DURATION}), "
    f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {_DURATION}), "
    "COUNT(*), "
    "COUNT(*) FILTER (WHERE diagnosis->>'status' = 'inconclusive' "
    "OR diagnosis->'result'->>'confidence' = 'inconclusive') "
    "FROM incidents WHERE diagnosed_at IS NOT NULL AND diagnosed_at > now() - interval '24 hours'"
)
_INCIDENTS_SQL = text(
    "SELECT COUNT(*) FILTER (WHERE status = 'open'), "
    "COUNT(*) FILTER (WHERE status = 'diagnosing'), "
    "COUNT(*) FILTER (WHERE diagnosis_deferred AND status <> 'resolved') FROM incidents"
)


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine: AsyncEngine = request.app.state.engine
    queue: IngestQueue = request.app.state.queue
    pool: WorkerPool = request.app.state.pool

    async with engine.connect() as conn:
        ing = (await conn.execute(_INGEST_SQL)).one()
        dia = (await conn.execute(_DIAGNOSIS_SQL)).one()
        inc = (await conn.execute(_INCIDENTS_SQL)).one()
    async with AsyncSession(engine) as session:
        tokens = await used_this_hour(session)

    diagnosed = int(dia[2])
    inconclusive = int(dia[3])
    return {
        "queue": {"depth": queue.depth, "max_size": queue.max_items},
        "ingest": {
            "lines_per_s_1m": float(ing[0]) / 60.0,
            "batches_committed_1h": int(ing[1]),
            "worker_processed": pool.processed,
            "worker_failed": pool.failed,
            "queue_to_commit": pool.latency.snapshot(),
        },
        "diagnosis": {
            "p50_s": _num(dia[0]),
            "p95_s": _num(dia[1]),
            "diagnosed_24h": diagnosed,
            "inconclusive_rate": (inconclusive / diagnosed) if diagnosed else None,
        },
        "incidents": {"open": int(inc[0]), "diagnosing": int(inc[1]), "deferred": int(inc[2])},
        "llm": {"tokens_used_this_hour": tokens, "hourly_cap": settings.llm_hourly_token_cap},
    }
