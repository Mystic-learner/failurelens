"""Uploaded documents: decode, redact, chunk, fingerprint and store under a documents row.

Uses the same StreamProcessor as live ingestion, so redaction happens before storage.
source_id = sha256(filename + content): uploading the same file again finds the existing
document and writes nothing. Documents store chunks only (no signatures or incidents).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.stream_processor import ProcessedChunk, StreamProcessor

_SQL_LOCK = text("SELECT pg_advisory_xact_lock(hashtext(CAST(:k AS text)))")
_SQL_EXISTING = text(
    "SELECT d.id, d.filename, (SELECT count(*) FROM chunks c WHERE c.document_id = d.id) "
    "FROM documents d WHERE d.source_id = CAST(:sid AS text) ORDER BY d.id LIMIT 1"
)
_SQL_DOC = text(
    "INSERT INTO documents (source_id, filename) "
    "VALUES (CAST(:sid AS text), CAST(:fn AS text)) RETURNING id"
)
_SQL_CHUNKS = text(
    """
    INSERT INTO chunks (document_id, text, chunk_hash, fingerprint, level, ts, error_codes)
    SELECT CAST(:doc AS bigint), r.body, r.chunk_hash, r.fingerprint, r.level, r.ts,
           ARRAY(SELECT jsonb_array_elements_text(r.error_codes))
    FROM jsonb_to_recordset(CAST(CAST(:rows AS text) AS jsonb))
         AS r(body text, chunk_hash text, fingerprint text, level text, ts timestamptz,
              error_codes jsonb)
    ORDER BY r.chunk_hash
    ON CONFLICT (chunk_hash) DO NOTHING
    """
)
_SQL_COUNT = text("SELECT count(*) FROM chunks WHERE document_id = CAST(:doc AS bigint)")


class EmptyDocumentError(ValueError):
    """The file has no text content."""


@dataclass(frozen=True)
class StoredDocument:
    id: int
    source_id: str
    filename: str
    chunks: int
    duplicate: bool


def clean_filename(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()[:255]
    return base or "upload"


def document_source_id(filename: str, data: bytes) -> str:
    h = hashlib.sha256()
    h.update(filename.encode("utf-8"))
    h.update(b"\x00")
    h.update(data)
    return h.hexdigest()


def _process(source_id: str, data: bytes) -> list[ProcessedChunk]:
    """Blocking and CPU-bound. Raises BinaryInputError for binary-looking input."""
    proc = StreamProcessor(source_id)
    chunks = proc.feed_bytes(data)
    chunks.extend(proc.flush())
    return chunks


def _rows(chunks: list[ProcessedChunk]) -> list[dict[str, object]]:
    unique: dict[str, ProcessedChunk] = {}
    for pc in chunks:
        unique.setdefault(pc.chunk.chunk_hash, pc)
    return [
        {
            "body": pc.chunk.text.replace("\x00", ""),
            "chunk_hash": chunk_hash,
            "fingerprint": pc.fingerprint,
            "level": pc.chunk.level,
            "ts": pc.chunk.first_ts.astimezone(UTC).isoformat() if pc.chunk.first_ts else None,
            "error_codes": list(pc.error_codes),
        }
        for chunk_hash, pc in sorted(unique.items())
    ]


async def store_document(engine: AsyncEngine, filename: str, data: bytes) -> StoredDocument:
    if not data or not data.strip():
        raise EmptyDocumentError("file is empty")
    source_id = document_source_id(filename, data)
    chunks = await asyncio.to_thread(_process, source_id, data)
    if not chunks:
        raise EmptyDocumentError("file has no text content")
    rows = _rows(chunks)

    async with engine.begin() as conn:
        await conn.execute(_SQL_LOCK, {"k": source_id})
        existing = (await conn.execute(_SQL_EXISTING, {"sid": source_id})).first()
        if existing is not None:
            return StoredDocument(int(existing[0]), source_id, existing[1], int(existing[2]), True)
        doc_id = int(
            (await conn.execute(_SQL_DOC, {"sid": source_id, "fn": filename})).scalar_one()
        )
        await conn.execute(_SQL_CHUNKS, {"doc": doc_id, "rows": json.dumps(rows)})
        count = int((await conn.execute(_SQL_COUNT, {"doc": doc_id})).scalar_one())
    return StoredDocument(doc_id, source_id, filename, count, False)
