"""POST /search: hybrid retrieval over stored chunks (Phase 5 builds /diagnose on top)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.embeddings import EmbeddingProvider
from app.services.retrieval import SearchFilters, search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20000)
    top_k: int = Field(default=8, ge=1, le=50)
    min_similarity: float = Field(default=0.5, ge=0.0, le=1.0)
    service: str | None = Field(default=None, max_length=200)
    since: datetime | None = None
    until: datetime | None = None


class HitOut(BaseModel):
    chunk_id: int
    text: str
    service: str | None
    level: str | None
    ts: datetime | None
    score: float
    sources: list[str]
    similarity: float | None
    fingerprint_match: bool


class SearchResponse(BaseModel):
    hits: list[HitOut]
    degraded: bool
    inconclusive: bool
    reason: str


@router.post("/search", response_model=SearchResponse)
async def search_endpoint(body: SearchRequest, request: Request) -> SearchResponse:
    engine: AsyncEngine = request.app.state.engine
    provider: EmbeddingProvider | None = request.app.state.embedding_provider
    result = await search(
        engine,
        provider,
        body.query,
        top_k=body.top_k,
        min_similarity=body.min_similarity,
        filters=SearchFilters(body.service, body.since, body.until),
    )
    return SearchResponse(
        hits=[
            HitOut(
                chunk_id=h.chunk_id,
                text=h.text,
                service=h.service,
                level=h.level,
                ts=h.ts,
                score=h.score,
                sources=h.sources,
                similarity=h.similarity,
                fingerprint_match=h.fingerprint_match,
            )
            for h in result.hits
        ],
        degraded=result.degraded,
        inconclusive=result.inconclusive,
        reason=result.reason,
    )