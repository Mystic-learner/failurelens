"""Group parsed records into chunks and give each chunk a dedupe hash.

Rules:
- A record is never split, so a stack trace stays whole in one chunk.
- ERROR/FATAL records and stack traces get a chunk of their own, so two unrelated
  errors are never mixed in one chunk.
- INFO/DEBUG/WARN/UNKNOWN records are packed together up to max_chars.
- chunk_hash = sha256(normalized_text + source_id), so a replayed batch produces
  the same hashes and INSERT ... ON CONFLICT DO NOTHING drops the duplicates.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from app.services.parsing import LogRecord

MAX_CHUNK_CHARS = 2000
SEVERITY = {"UNKNOWN": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "ERROR": 4, "FATAL": 5}


@dataclass(frozen=True)
class Chunk:
    text: str
    chunk_hash: str
    level: str
    first_ts: datetime | None
    last_ts: datetime | None
    record_count: int
    line_count: int
    flags: tuple[str, ...]


def normalize_for_hash(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def make_chunk_hash(text: str, source_id: str) -> str:
    payload = normalize_for_hash(text) + "\x1f" + source_id
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_isolated(record: LogRecord) -> bool:
    return SEVERITY.get(record.level, 0) >= SEVERITY["ERROR"] or "stack_trace" in record.flags


def _make_chunk(records: list[LogRecord], source_id: str) -> Chunk:
    text = "\n".join(r.text for r in records)
    level = max((r.level for r in records), key=lambda lv: SEVERITY.get(lv, 0))
    stamps = [r.timestamp for r in records if r.timestamp is not None]
    return Chunk(
        text=text,
        chunk_hash=make_chunk_hash(text, source_id),
        level=level,
        first_ts=min(stamps) if stamps else None,
        last_ts=max(stamps) if stamps else None,
        record_count=len(records),
        line_count=sum(r.line_count for r in records),
        flags=tuple(sorted({f for r in records for f in r.flags})),
    )


def build_chunks(
    records: list[LogRecord], source_id: str, max_chars: int = MAX_CHUNK_CHARS
) -> list[Chunk]:
    chunks: list[Chunk] = []
    group: list[LogRecord] = []
    size = 0
    for record in records:
        if _is_isolated(record):
            if group:
                chunks.append(_make_chunk(group, source_id))
                group, size = [], 0
            chunks.append(_make_chunk([record], source_id))
            continue
        added = len(record.text) + (1 if group else 0)
        if group and size + added > max_chars:
            chunks.append(_make_chunk(group, source_id))
            group, size = [], 0
            added = len(record.text)
        group.append(record)
        size += added
    if group:
        chunks.append(_make_chunk(group, source_id))
    return chunks