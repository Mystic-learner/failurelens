"""POST /ingest/stream: accept one batch of raw log bytes and enqueue it.

Wire format: the body is the raw bytes of the batch (any encoding, any line endings).
Metadata travels in headers: X-Service, X-Source-Id, Idempotency-Key, X-Sequence.
The endpoint only validates and enqueues; decoding, parsing and storing happen in the
workers. It answers 202 as soon as the batch is in the bounded queue, 429 (with
Retry-After) when the queue is full, and 503 while the server is shutting down.
Raw content is never logged and never echoed back.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.services.ingest_queue import IngestItem, IngestQueue, QueueClosed, QueueFull

router = APIRouter()

MAX_SEQ = 2**62
SHUTDOWN_RETRY_AFTER_S = 5


class IngestAccepted(BaseModel):
    status: Literal["accepted"]
    queue_depth: int


def _not_blank(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{name} must not be blank")
    return cleaned


@router.post(
    "/ingest/stream",
    status_code=202,
    response_model=IngestAccepted,
    responses={400: {}, 413: {}, 422: {}, 429: {}, 503: {}},
)
async def ingest_stream(
    request: Request,
    x_service: Annotated[str, Header(min_length=1, max_length=200)],
    x_source_id: Annotated[str, Header(min_length=1, max_length=200)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    x_sequence: Annotated[int, Header(ge=0, le=MAX_SEQ)],
) -> IngestAccepted:
    service = _not_blank("X-Service", x_service)
    source_id = _not_blank("X-Source-Id", x_source_id)
    key = _not_blank("Idempotency-Key", idempotency_key)

    data = await request.body()  # the body-limit middleware enforces the size cap (413)
    if not data:
        raise HTTPException(status_code=400, detail="Request body is empty")

    queue: IngestQueue = request.app.state.queue
    try:
        queue.put(
            IngestItem(
                service=service,
                source_id=source_id,
                idempotency_key=key,
                seq=x_sequence,
                data=data,
            )
        )
    except QueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail="Ingest queue is full, retry later",
            headers={"Retry-After": str(exc.retry_after_s)},
        ) from None
    except QueueClosed:
        raise HTTPException(
            status_code=503,
            detail="Server is shutting down, retry later",
            headers={"Retry-After": str(SHUTDOWN_RETRY_AFTER_S)},
        ) from None
    return IngestAccepted(status="accepted", queue_depth=queue.depth)