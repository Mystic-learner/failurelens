"""POST /ingest/committed: which of these batch idempotency keys are already committed?

The shipper keeps every batch in its disk spool until the server confirms that batch's OWN
key. A sequence watermark is not safe: batches of one stream can commit out of order, and a
batch that keeps failing is dropped by the worker pool, so "seq N committed" says nothing
about seq N-1. A key is reported only once its ingest_batches row exists, and that row is
written in the same transaction as the chunks, so "committed" means durably stored.
Only keys are exchanged; no log content is ever read or returned.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter()

Key = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]

_SQL = text(
    """
    SELECT b.idempotency_key
    FROM ingest_batches b
    JOIN streams s ON s.id = b.stream_id
    WHERE s.service = CAST(:service AS text)
      AND s.source_id = CAST(:source_id AS text)
      AND b.idempotency_key = ANY(CAST(:keys AS text[]))
    """
)


class CommittedRequest(BaseModel):
    service: Name
    source_id: Name
    keys: list[Key] = Field(min_length=1, max_length=1000)


class CommittedResponse(BaseModel):
    committed: list[str]


@router.post("/ingest/committed", response_model=CommittedResponse)
async def ingest_committed(body: CommittedRequest, request: Request) -> CommittedResponse:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        rows = await conn.execute(
            _SQL,
            {"service": body.service, "source_id": body.source_id, "keys": list(set(body.keys))},
        )
        found = sorted(str(r[0]) for r in rows.all())
    return CommittedResponse(committed=found)