"""Diagnosis runner: claims incidents, retrieves evidence, diagnoses under a token budget.

Claim is one UPDATE ... FOR UPDATE SKIP LOCKED, so concurrent runners never take the same
incident. A crashed runner leaves status='diagnosing'; such rows are re-claimed once
updated_at is older than stale_s (this is also the cold-start recovery path).
When the hourly budget is exhausted the incident goes back to 'open' with
diagnosis_deferred=true and is not retried until the hour rolls over.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.services.diagnosis import DiagnosisOutcome, EvidenceChunk, LLMClient, diagnose
from app.services.evidence_context import (
    fetch_before,
    fetch_other_services,
    merge_context,
)
from app.services.llm_budget import estimate_tokens, hour_start, reserve, settle
from app.services.redact import redact
from app.services.retrieval import Hit, SearchFilters, SearchResult

logger = logging.getLogger(__name__)

Searcher = Callable[[str, SearchFilters | None], Awaitable[SearchResult]]
# Evidence comes from the incident's own service, this far around the exemplar line.
EVIDENCE_WINDOW = timedelta(minutes=15)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _evidence_filters(service: object, ts: object) -> SearchFilters | None:
    """Scope evidence to one service and a window around the exemplar line."""
    if service is None or not isinstance(ts, datetime):
        return None
    return SearchFilters(
        service=str(service), since=ts - EVIDENCE_WINDOW, until=ts + EVIDENCE_WINDOW
    )


@dataclass(frozen=True)
class RunnerConfig:
    hourly_token_cap: int = 200_000
    debounce_s: int = 60  # min gap between attempts on one incident
    settle_s: int = 5  # wait after an incident opens so more logs can land
    stale_s: int = 300  # 'diagnosing' older than this is considered abandoned
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.hourly_token_cap <= 0 or self.max_attempts < 1:
            raise ValueError("hourly_token_cap and max_attempts must be positive")
        if self.debounce_s < 0 or self.settle_s < 0 or self.stale_s < 0:
            raise ValueError("durations must be >= 0")

    @classmethod
    def for_ingest(
        cls, *, hourly_token_cap: int, idle_s: float, flush_interval_s: float
    ) -> RunnerConfig:
        """Settle long enough for a quiet stream's last line to be flushed and stored."""
        return cls(
            hourly_token_cap=hourly_token_cap,
            settle_s=math.ceil(idle_s + flush_interval_s) + 1,
        )


@dataclass
class CoreResult:
    outcome: DiagnosisOutcome | None  # None means deferred (budget exhausted)
    deferred: bool
    degraded: bool
    retrieval_reason: str
    hits: list[Hit] = field(default_factory=list)


_SQL_CLAIM = text(
    """
    UPDATE incidents
       SET status = 'diagnosing',
           last_diagnosis_attempt_at = CAST(:now AS timestamptz),
           updated_at = CAST(:now AS timestamptz)
     WHERE id = (
        SELECT id FROM incidents
         WHERE (status = 'open'
                OR (status = 'diagnosing' AND updated_at < CAST(:stale AS timestamptz)))
           AND created_at <= CAST(:settle AS timestamptz)
           AND (CAST(:inc AS boolean) OR NOT diagnosis_deferred)
           AND (diagnosis_deferred
                OR last_diagnosis_attempt_at IS NULL
                OR last_diagnosis_attempt_at < CAST(:debounce AS timestamptz))
         ORDER BY id
         FOR UPDATE SKIP LOCKED
         LIMIT 1)
    RETURNING id, signature_id, CAST(diagnosis AS text)
    """
)
_SQL_EXEMPLAR = text(
    "SELECT c.text, c.service, c.ts, s.exemplar_chunk_id, s.fingerprint "
    "FROM signatures s "
    "LEFT JOIN chunks c ON c.id = s.exemplar_chunk_id "
    "WHERE s.id = CAST(:sid AS bigint)"
)
_SQL_KNOWN = text(
    "SELECT id FROM incidents WHERE signature_id = CAST(:sid AS bigint) "
    "AND id <> CAST(:iid AS bigint) AND status = 'diagnosed' ORDER BY id DESC LIMIT 5"
)
_SQL_DEFER = text(
    "UPDATE incidents SET status = 'open', diagnosis_deferred = true, "
    "updated_at = CAST(:now AS timestamptz) "
    "WHERE id = CAST(:id AS bigint) AND status = 'diagnosing'"
)
_SQL_RETRY = text(
    "UPDATE incidents SET status = 'open', "
    "diagnosis = CAST(CAST(:doc AS text) AS jsonb), updated_at = CAST(:now AS timestamptz) "
    "WHERE id = CAST(:id AS bigint) AND status = 'diagnosing'"
)
_SQL_FINAL = text(
    """
    UPDATE incidents
       SET status = 'diagnosed',
           diagnosis = CAST(CAST(:doc AS text) AS jsonb),
           degraded = CAST(:degraded AS boolean),
           diagnosis_deferred = false,
           diagnosed_at = CAST(:now AS timestamptz),
           updated_at = CAST(:now AS timestamptz)
     WHERE id = CAST(:id AS bigint) AND status = 'diagnosing'
    RETURNING id
    """
)
_SQL_EVENT = text(
    "INSERT INTO events (type, payload) "
    "VALUES (CAST(:type AS text), CAST(CAST(:payload AS text) AS jsonb))"
)


def _doc(core: CoreResult | None, error: str | None = None) -> dict[str, Any]:
    out = core.outcome if core is not None else None
    return {
        "status": out.status if out is not None else "failed",
        "result": out.result.model_dump() if out is not None and out.result else None,
        "dropped_evidence": out.dropped_evidence if out is not None else 0,
        "tokens": {
            "prompt": out.prompt_tokens if out is not None else 0,
            "completion": out.completion_tokens if out is not None else 0,
        },
        "degraded": core.degraded if core is not None else False,
        "retrieval_reason": core.retrieval_reason if core is not None else "",
        "error": error if error is not None else (out.error if out is not None else None),
    }


class DiagnosisRunner:
    def __init__(
        self,
        engine: AsyncEngine,
        llm: LLMClient,
        searcher: Searcher,
        config: RunnerConfig | None = None,
        clock: Clock = _utcnow,
    ) -> None:
        self._engine = engine
        self._llm = llm
        self._searcher = searcher
        self._cfg = config or RunnerConfig()
        self._clock = clock
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._blocked_hour: datetime | None = None

    async def diagnose_text(self, query: str, known_ids: list[str] | None = None) -> CoreResult:
        """Retrieval + budget + diagnosis for pasted text (used by POST /diagnose)."""
        return await self._core(query, known_ids or [])

    async def _core(
        self,
        query: str,
        known_ids: list[str],
        filters: SearchFilters | None = None,
        context: tuple[str, datetime, int | None, str | None] | None = None,
    ) -> CoreResult:
        found = await self._searcher(query, filters)
        chunks = [EvidenceChunk(str(h.chunk_id), h.text) for h in found.hits]
        if found.inconclusive or not chunks:
            out = await diagnose(
                self._llm, found.query, chunks, known_ids, retrieval_inconclusive=True
            )
            return CoreResult(out, False, found.degraded, found.reason, found.hits)
        if context is not None:
            chunks = merge_context(chunks, await fetch_before(self._engine, *context))
            other = await fetch_other_services(self._engine, context[0], context[1], query)
            chunks = merge_context(chunks, other)
        # Nothing leaves for the LLM unredacted; evidence is verified against these copies.
        chunks = [EvidenceChunk(c.chunk_id, redact(c.text)) for c in chunks]
        llm_query = redact(found.query)
        now = self._clock()
        hour = hour_start(now)
        est = estimate_tokens(len(found.query) + sum(len(c.text) for c in chunks))
        async with self._sessions() as session:
            granted = await reserve(session, est, self._cfg.hourly_token_cap, now)
        if not granted:
            self._blocked_hour = hour
            return CoreResult(None, True, found.degraded, found.reason, found.hits)
        try:
            out = await diagnose(self._llm, llm_query, chunks, known_ids)
        except BaseException:
            async with self._sessions() as session:
                await settle(session, est, 0, hour=hour)
            raise
        async with self._sessions() as session:
            await settle(session, est, out.prompt_tokens + out.completion_tokens, hour=hour)
        return CoreResult(out, False, found.degraded, found.reason, found.hits)

    async def _claim(
        self, now: datetime, include_deferred: bool
    ) -> tuple[int, int, dict[str, Any] | None] | None:
        params = {
            "now": now,
            "stale": now - timedelta(seconds=self._cfg.stale_s),
            "settle": now - timedelta(seconds=self._cfg.settle_s),
            "debounce": now - timedelta(seconds=self._cfg.debounce_s),
            "inc": include_deferred,
        }
        async with self._engine.begin() as conn:
            row = (await conn.execute(_SQL_CLAIM, params)).first()
        if row is None:
            return None
        prior = json.loads(row[2]) if row[2] else None
        return int(row[0]), int(row[1]), prior if isinstance(prior, dict) else None

    async def run_once(self) -> str | None:
        """Handle one incident. Returns its outcome label, or None if nothing was claimable."""
        now = self._clock()
        claimed = await self._claim(now, self._blocked_hour != hour_start(now))
        if claimed is None:
            return None
        incident_id, signature_id, prior = claimed
        attempts = int(prior.get("attempts", 0)) if prior else 0
        try:
            return await self._process(incident_id, signature_id, attempts, now)
        except Exception as exc:
            logger.exception("diagnosis of incident %s crashed", incident_id)
            return await self._retry_or_fail(
                incident_id, signature_id, attempts, type(exc).__name__, now, None
            )

    async def run_pending(self) -> dict[str, int]:
        """Drain everything currently claimable. Also the cold-start entry point."""
        counts: dict[str, int] = {}
        while True:
            label = await self.run_once()
            if label is None:
                return counts
            counts[label] = counts.get(label, 0) + 1

    async def run_forever(self, stop: asyncio.Event, poll_s: float = 2.0) -> None:
        while not stop.is_set():
            try:
                await self.run_pending()
            except Exception:
                logger.exception("diagnosis runner loop error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            except TimeoutError:
                pass

    async def _process(
        self, incident_id: int, signature_id: int, attempts: int, now: datetime
    ) -> str:
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SQL_EXEMPLAR, {"sid": signature_id})).first()
            known = [
                str(r[0])
                for r in (
                    await conn.execute(_SQL_KNOWN, {"sid": signature_id, "iid": incident_id})
                ).all()
            ]
        query = str(row[0]) if row is not None and row[0] is not None else ""
        filters = _evidence_filters(row[1], row[2]) if row is not None else None
        context = (
            (str(row[1]), row[2], row[3], row[4])
            if row is not None and row[1] is not None and isinstance(row[2], datetime)
            else None
        )
        if not query.strip():
            core = CoreResult(
                DiagnosisOutcome(status="inconclusive", result=None),
                False,
                False,
                "no exemplar chunk for signature",
            )
        else:
            core = await self._core(query, known, filters, context)
        if core.outcome is None:
            async with self._engine.begin() as conn:
                await conn.execute(_SQL_DEFER, {"id": incident_id, "now": now})
            return "deferred"
        if core.outcome.status == "failed":
            return await self._retry_or_fail(
                incident_id, signature_id, attempts, core.outcome.error or "failed", now, core
            )
        await self._finalize(incident_id, signature_id, core, now)
        return core.outcome.status

    async def _retry_or_fail(
        self,
        incident_id: int,
        signature_id: int,
        attempts: int,
        error: str,
        now: datetime,
        core: CoreResult | None,
    ) -> str:
        n = attempts + 1
        if n >= self._cfg.max_attempts:
            doc = _doc(core, error)
            doc["status"] = "failed"
            doc["attempts"] = n
            await self._finalize_doc(incident_id, signature_id, doc, False, "failed", None, now)
            return "failed"
        doc = {"attempts": n, "last_error": error}
        async with self._engine.begin() as conn:
            await conn.execute(_SQL_RETRY, {"id": incident_id, "doc": json.dumps(doc), "now": now})
        return "retry"

    async def _finalize(
        self, incident_id: int, signature_id: int, core: CoreResult, now: datetime
    ) -> None:
        out = core.outcome
        assert out is not None
        confidence = out.result.confidence if out.result is not None else "inconclusive"
        await self._finalize_doc(
            incident_id, signature_id, _doc(core), core.degraded, out.status, confidence, now
        )

    async def _finalize_doc(
        self,
        incident_id: int,
        signature_id: int,
        doc: dict[str, Any],
        degraded: bool,
        status: str,
        confidence: str | None,
        now: datetime,
    ) -> None:
        async with self._engine.begin() as conn:
            done = (
                await conn.execute(
                    _SQL_FINAL,
                    {
                        "id": incident_id,
                        "doc": json.dumps(doc),
                        "degraded": degraded,
                        "now": now,
                    },
                )
            ).first()
            if done is None:
                return  # another runner finished it; do not emit a duplicate event
            payload = {
                "incident_id": incident_id,
                "signature_id": signature_id,
                "status": status,
                "confidence": confidence,
            }
            await conn.execute(
                _SQL_EVENT, {"type": "incident.diagnosed", "payload": json.dumps(payload)}
            )
