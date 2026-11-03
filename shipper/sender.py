"""HTTP client for the FailureLens ingest API (the shipper's only network code).

send() never raises for network or HTTP problems; it returns a SendResult:
  accepted  2xx: the server queued the batch (NOT yet committed to the database)
  retry     timeout, connection error, 408/425/429/5xx: try again later, same batch
  rejected  any other status (400, 401, 413, 422 ...): resending cannot help
committed_seq() asks the server how far the stream is durably committed, or None if it
cannot be reached or answers nonsense.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from shipper.backoff import is_retryable_status, parse_retry_after
from shipper.reader import Batch


@dataclass(frozen=True)
class SendResult:
    kind: Literal["accepted", "retry", "rejected"]
    status: int | None = None
    retry_after: float | None = None
    detail: str = ""


class IngestClient:
    def __init__(
        self,
        base_url: str,
        service: str,
        source_id: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._service = service
        self._source_id = source_id
        self._http = http or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, batch: Batch) -> SendResult:
        headers = {
            "X-Service": self._service,
            "X-Source-Id": self._source_id,
            "Idempotency-Key": batch.key,
            "X-Sequence": str(batch.seq),
            "Content-Type": "application/octet-stream",
        }
        try:
            resp = await self._http.post("/ingest/stream", content=batch.data, headers=headers)
        except httpx.HTTPError as exc:
            return SendResult("retry", detail=type(exc).__name__)
        status = resp.status_code
        if 200 <= status < 300:
            return SendResult("accepted", status)
        if is_retryable_status(status):
            return SendResult(
                "retry",
                status,
                retry_after=parse_retry_after(resp.headers.get("Retry-After")),
                detail=f"HTTP {status}",
            )
        return SendResult("rejected", status, detail=f"HTTP {status}")

    async def committed_seq(self) -> int | None:
        params = {"service": self._service, "source_id": self._source_id}
        try:
            resp = await self._http.get("/ingest/status", params=params)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            value = resp.json()["committed_seq"]
        except (ValueError, KeyError, TypeError):
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value