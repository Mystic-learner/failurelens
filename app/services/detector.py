"""Incident detector. Runs inside the batch transaction (and the idle-flush transaction).

Opens an incident when, for an ERROR-or-higher signature touched by the batch:
  (a) the signature is new (never seen before), or
  (b) its rate in the last window_min minutes exceeds spike_factor x its baseline rate over the
      baseline_min minutes before that, with at least min_count events in the window.
Duplicates are prevented two ways: a partial UNIQUE index allows only one non-resolved incident
per signature, and no incident opens while a previous one of the same signature has
cooldown_until in the future. Both are enforced by one INSERT ... ON CONFLICT DO NOTHING, so
concurrent writers cannot create two. Each opened incident writes an 'incident.opened' event
in the same transaction.

Known limit: spike windows use event time, so replaying old logs never triggers (b).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.chunking import SEVERITY
from app.services.storage import SignatureHit


@dataclass(frozen=True)
class DetectorConfig:
    window_min: int = 5
    baseline_min: int = 60
    spike_factor: float = 5.0
    min_count: int = 20
    cooldown_min: int = 10

    def __post_init__(self) -> None:
        if self.window_min <= 0 or self.baseline_min <= 0 or self.cooldown_min < 0:
            raise ValueError("window, baseline and cooldown must be positive")
        if self.spike_factor < 0 or self.min_count < 1:
            raise ValueError("spike_factor must be >= 0 and min_count >= 1")


DEFAULT_DETECTOR = DetectorConfig()

_SQL_SIGS = text(
    "SELECT id, service, fingerprint, level FROM signatures "
    "WHERE id = ANY(CAST(:ids AS bigint[])) ORDER BY id"
)
_SQL_RATES = text(
    """
    SELECT
      COALESCE(sum(count) FILTER (WHERE minute > CAST(:rs AS timestamptz)), 0),
      COALESCE(sum(count) FILTER (WHERE minute <= CAST(:rs AS timestamptz)), 0)
    FROM signature_buckets
    WHERE signature_id = CAST(:sid AS bigint) AND minute > CAST(:bs AS timestamptz)
    """
)
_SQL_OPEN = text(
    """
    INSERT INTO incidents (signature_id, trigger, status, cooldown_until)
    SELECT CAST(:sid AS bigint), CAST(:trigger AS text), 'open', CAST(:until AS timestamptz)
    WHERE NOT EXISTS (
        SELECT 1 FROM incidents
        WHERE signature_id = CAST(:sid AS bigint) AND cooldown_until > CAST(:now AS timestamptz)
    )
    ON CONFLICT DO NOTHING
    RETURNING id
    """
)
_SQL_EVENT = text(
    "INSERT INTO events (type, payload) "
    "VALUES (CAST(:type AS text), CAST(CAST(:payload AS text) AS jsonb))"
)


async def _is_spike(
    conn: AsyncConnection, signature_id: int, now: datetime, cfg: DetectorConfig
) -> bool:
    recent_start = now - timedelta(minutes=cfg.window_min)
    base_start = recent_start - timedelta(minutes=cfg.baseline_min)
    row = (
        await conn.execute(_SQL_RATES, {"sid": signature_id, "rs": recent_start, "bs": base_start})
    ).one()
    recent, base = int(row[0]), int(row[1])
    if recent < cfg.min_count:
        return False
    recent_rate = recent / cfg.window_min
    base_rate = max(base / cfg.baseline_min, 1.0 / cfg.baseline_min)
    return recent_rate > cfg.spike_factor * base_rate


async def detect(
    conn: AsyncConnection,
    hits: tuple[SignatureHit, ...],
    now: datetime,
    cfg: DetectorConfig = DEFAULT_DETECTOR,
) -> list[int]:
    """Open incidents for the signatures in `hits`. Returns the ids of the incidents opened."""
    if not hits:
        return []
    when = now.astimezone(UTC)
    ordered = sorted(hits, key=lambda h: h.signature_id)
    rows = (await conn.execute(_SQL_SIGS, {"ids": [h.signature_id for h in ordered]})).all()
    sigs = {int(r[0]): r for r in rows}

    opened: list[int] = []
    for hit in ordered:
        sig = sigs.get(hit.signature_id)
        if sig is None or SEVERITY.get(str(sig[3]), 0) < SEVERITY["ERROR"]:
            continue
        trigger: str | None = None
        if hit.is_new:
            trigger = "new_signature"
        elif await _is_spike(conn, hit.signature_id, when, cfg):
            trigger = "rate_spike"
        if trigger is None:
            continue
        result = await conn.execute(
            _SQL_OPEN,
            {
                "sid": hit.signature_id,
                "trigger": trigger,
                "until": when + timedelta(minutes=cfg.cooldown_min),
                "now": when,
            },
        )
        row = result.first()
        if row is None:
            continue  # an active incident or the cooldown already covers this signature
        incident_id = int(row[0])
        payload = {
            "incident_id": incident_id,
            "signature_id": hit.signature_id,
            "service": sig[1],
            "fingerprint": sig[2],
            "trigger": trigger,
        }
        await conn.execute(_SQL_EVENT, {"type": "incident.opened", "payload": json.dumps(payload)})
        opened.append(incident_id)
    return opened
