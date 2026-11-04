"""Hourly LLM token budget with an atomic reserve."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_OUTPUT_TOKENS_ESTIMATE = 800


def hour_start(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return current.replace(minute=0, second=0, microsecond=0)


def estimate_tokens(prompt_chars: int) -> int:
    return prompt_chars // 3 + MAX_OUTPUT_TOKENS_ESTIMATE


async def reserve(
    session: AsyncSession, tokens: int, cap: int, now: datetime | None = None
) -> bool:
    """Reserve tokens for the current hour. Returns False when it would exceed the cap.

    Commits immediately so the row lock is held as briefly as possible.
    """
    if tokens > cap:
        return False
    result = await session.execute(
        text(
            """
            INSERT INTO llm_usage (hour, tokens, calls) VALUES (:hour, :n, 1)
            ON CONFLICT (hour) DO UPDATE
               SET tokens = llm_usage.tokens + EXCLUDED.tokens,
                   calls = llm_usage.calls + 1
             WHERE llm_usage.tokens + EXCLUDED.tokens <= :cap
            RETURNING tokens
            """
        ),
        {"hour": hour_start(now), "n": tokens, "cap": cap},
    )
    granted = result.first() is not None
    await session.commit()
    return granted


async def settle(
    session: AsyncSession, reserved: int, actual: int, now: datetime | None = None
) -> None:
    """Replace the estimate with real usage (may exceed the cap slightly; never below zero)."""
    await session.execute(
        text("UPDATE llm_usage SET tokens = GREATEST(0, tokens + :delta) WHERE hour = :hour"),
        {"hour": hour_start(now), "delta": actual - reserved},
    )
    await session.commit()


async def used_this_hour(session: AsyncSession, now: datetime | None = None) -> int:
    result = await session.execute(
        text("SELECT tokens FROM llm_usage WHERE hour = :hour"), {"hour": hour_start(now)}
    )
    row = result.first()
    return int(row[0]) if row else 0