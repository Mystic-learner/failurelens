"""POST /diagnose, GET /incidents, GET /incidents/{id}, GET /signatures."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.diagnosis_runner import DiagnosisRunner

router = APIRouter()

MAX_BIGINT = 9223372036854775807

IncidentStatus = Literal["open", "diagnosing", "diagnosed", "resolved"]

_INCIDENT_COLS = (
    "i.id, i.signature_id, i.trigger, i.status, i.degraded, i.diagnosis_deferred, "
    "i.created_at, i.updated_at, i.diagnosed_at, i.resolved_at, CAST(i.diagnosis AS text), "
    "s.service, s.level, s.count"
)


class DiagnoseRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20000)


def _loads(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = json.loads(str(raw))
    return value if isinstance(value, dict) else None


def _incident(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "signature_id": int(row[1]),
        "trigger": row[2],
        "status": row[3],
        "degraded": row[4],
        "diagnosis_deferred": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "diagnosed_at": row[8],
        "resolved_at": row[9],
        "diagnosis": _loads(row[10]),
        "service": row[11],
        "level": row[12],
        "signature_count": int(row[13]),
    }


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


@router.post("/diagnose")
async def post_diagnose(body: DiagnoseRequest, request: Request) -> dict[str, Any]:
    runner: DiagnosisRunner | None = getattr(request.app.state, "diagnosis_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="diagnosis is not configured")
    core = await runner.diagnose_text(body.query)
    if core.outcome is None:
        now = datetime.now(UTC)
        nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        raise HTTPException(
            status_code=429,
            detail="LLM hourly budget exhausted; retry after the hour rolls over",
            headers={"Retry-After": str(max(1, int((nxt - now).total_seconds())))},
        )
    out = core.outcome
    if out.status == "failed":
        raise HTTPException(status_code=502, detail="diagnosis failed; try again shortly")
    return {
        "status": out.status,
        "degraded": core.degraded,
        "retrieval_reason": core.retrieval_reason,
        "result": out.result.model_dump() if out.result is not None else None,
        "dropped_evidence": out.dropped_evidence,
        "tokens": {"prompt": out.prompt_tokens, "completion": out.completion_tokens},
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "service": h.service,
                "level": h.level,
                "score": h.score,
                "fingerprint_match": h.fingerprint_match,
                "text": h.text,
            }
            for h in core.hits
        ],
    }


@router.get("/incidents")
async def list_incidents(
    request: Request,
    status: IncidentStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    sql = text(
        f"SELECT {_INCIDENT_COLS} FROM incidents i JOIN signatures s ON s.id = i.signature_id "
        "WHERE (CAST(:status AS text) IS NULL OR i.status = CAST(:status AS text)) "
        "AND (CAST(:before AS bigint) IS NULL OR i.id < CAST(:before AS bigint)) "
        "ORDER BY i.id DESC LIMIT CAST(:n AS integer)"
    )
    async with _engine(request).connect() as conn:
        rows = (await conn.execute(sql, {"status": status, "before": before_id, "n": limit})).all()
    items = [_incident(r) for r in rows]
    return {"items": items, "next_before_id": items[-1]["id"] if len(items) == limit else None}


@router.get("/incidents/{incident_id}")
async def get_incident(
    request: Request, incident_id: int = Path(ge=1, le=MAX_BIGINT)
) -> dict[str, Any]:
    sql = text(
        f"SELECT {_INCIDENT_COLS} FROM incidents i JOIN signatures s ON s.id = i.signature_id "
        "WHERE i.id = CAST(:id AS bigint)"
    )
    async with _engine(request).connect() as conn:
        row = (await conn.execute(sql, {"id": incident_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return _incident(row)


_SORTS = {"count": "s.count DESC, s.id", "last_seen": "s.last_seen DESC, s.id"}


@router.get("/signatures")
async def list_signatures(
    request: Request,
    service: str | None = None,
    sort: Literal["count", "last_seen"] = "count",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    sql = text(
        "SELECT s.id, s.service, s.fingerprint, s.level, s.count, s.first_seen, s.last_seen, "
        "s.exemplar_chunk_id, (SELECT max(id) FROM incidents WHERE signature_id = s.id) "
        "FROM signatures s "
        "WHERE (CAST(:service AS text) IS NULL OR s.service = CAST(:service AS text)) "
        f"ORDER BY {_SORTS[sort]} LIMIT CAST(:n AS integer)"
    )
    async with _engine(request).connect() as conn:
        rows = (await conn.execute(sql, {"service": service, "n": limit})).all()
    return {
        "items": [
            {
                "id": int(r[0]),
                "service": r[1],
                "fingerprint": r[2],
                "level": r[3],
                "count": int(r[4]),
                "first_seen": r[5],
                "last_seen": r[6],
                "exemplar_chunk_id": r[7],
                "latest_incident_id": r[8],
            }
            for r in rows
        ]
    }


@router.get("/chunks/{chunk_id}")
async def get_chunk(request: Request, chunk_id: int = Path(ge=1, le=MAX_BIGINT)) -> dict[str, Any]:
    sql = text("SELECT id, text, service, level, ts FROM chunks WHERE id = CAST(:id AS bigint)")
    async with _engine(request).connect() as conn:
        row = (await conn.execute(sql, {"id": chunk_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="chunk not found")
    return {"id": int(row[0]), "text": row[1], "service": row[2], "level": row[3], "ts": row[4]}
