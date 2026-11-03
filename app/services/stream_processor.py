"""Per-stream pipeline: bytes -> text -> redaction -> records -> chunks -> fingerprints.

One StreamProcessor per log stream (not thread-safe). State kept between batches:
the unfinished record (so a stack trace split across two batches is reassembled),
the IP -> placeholder map (so <IP_1> stays stable) and the stats.

Contract with the caller (shipper and worker):
- Every feed_*() call carries WHOLE lines. A last line without a trailing newline
  counts as a whole line.
- The newest record stays pending until another record starts or flush() is called.
  Call flush() on an idle timeout and at shutdown, or the last record is never stored.
- Redaction runs on the raw text BEFORE it is split, parsed, hashed or stored, so no
  later stage ever sees a secret. It runs again on each finished record; that second
  pass is idempotent and also covers a secret split across two batches inside one
  stack trace.
- Chunk boundaries depend on batch boundaries, so a replayed batch only dedupes
  (UNIQUE chunk_hash) if it is replayed with the same boundaries. ERROR/FATAL and
  stack-trace chunks hold exactly one record, so their hashes never depend on batching.
- Known limit: <IP_n> numbers depend on the order IPs were first seen, so after a
  processor restart the same raw line can hash differently.
- Fingerprints are only set for ERROR/FATAL and stack-trace chunks (the ones the
  detector counts). Packed INFO/WARN chunks get None.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from typing import Any

from app.services.chunking import MAX_CHUNK_CHARS, SEVERITY, Chunk, build_chunks
from app.services.fingerprint import extract_error_codes, make_fingerprint
from app.services.parsing import LogRecord, StreamParser, decode_bytes, split_lines
from app.services.redaction import Redactor


@dataclass(frozen=True)
class ProcessedChunk:
    chunk: Chunk
    fingerprint: str | None
    error_codes: tuple[str, ...]


@dataclass
class StreamStats:
    bytes_in: int = 0
    lines_in: int = 0
    records_out: int = 0
    chunks_out: int = 0
    decode_replacements: int = 0  # batches that contained invalid UTF-8


def _isolated(chunk: Chunk) -> bool:
    return SEVERITY.get(chunk.level, 0) >= SEVERITY["ERROR"] or "stack_trace" in chunk.flags


class StreamProcessor:
    def __init__(
        self,
        source_id: str,
        default_tz: tzinfo = UTC,
        clock: Callable[[], datetime] | None = None,
        max_chunk_chars: int = MAX_CHUNK_CHARS,
    ) -> None:
        self._source_id = source_id
        self._max_chars = max_chunk_chars
        self._parser = StreamParser(default_tz=default_tz, clock=clock)
        self._redactor = Redactor()
        self.stats = StreamStats()

    def export_state(self) -> dict[str, Any]:
        """JSON-safe state to carry to the next batch, possibly in another process."""
        return {
            "v": 1,
            "parser": self._parser.export_state(),
            "ips": self._redactor.export_state(),
        }

    def import_state(self, state: dict[str, Any]) -> None:
        """Restore exported state. {} (a brand-new stream) leaves a fresh processor."""
        if not state:
            return
        if state.get("v") != 1:
            raise ValueError("unknown stream state version")
        self._parser.import_state(state["parser"])
        self._redactor.import_state(state["ips"])

    def feed_bytes(self, data: bytes) -> list[ProcessedChunk]:
        """Raises BinaryInputError for binary-looking input; the caller decides what to do."""
        self.stats.bytes_in += len(data)
        decoded = decode_bytes(data)
        if decoded.had_replacements:
            self.stats.decode_replacements += 1
        return self.feed_text(decoded.text)

    def feed_text(self, text: str) -> list[ProcessedChunk]:
        if not text or text.isspace():
            return []
        lines = split_lines(self._redactor.redact(text))
        self.stats.lines_in += len(lines)
        return self._to_chunks(self._parser.feed(lines))

    def flush(self) -> list[ProcessedChunk]:
        return self._to_chunks(self._parser.flush())

    def _to_chunks(self, records: list[LogRecord]) -> list[ProcessedChunk]:
        if not records:
            return []
        safe = [replace(r, text=self._redactor.redact(r.text)) for r in records]
        self.stats.records_out += len(safe)
        out = [self._enrich(c) for c in build_chunks(safe, self._source_id, self._max_chars)]
        self.stats.chunks_out += len(out)
        return out

    @staticmethod
    def _enrich(chunk: Chunk) -> ProcessedChunk:
        fingerprint = make_fingerprint(chunk.text) if _isolated(chunk) else None
        return ProcessedChunk(chunk, fingerprint, tuple(extract_error_codes(chunk.text)))