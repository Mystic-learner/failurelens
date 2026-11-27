"""Gemini embeddings (gemini-embedding-001, truncated to 384 dims) over the REST API.

For the hosted demo only. It reuses the 384-dim chunk_embeddings_local table, so a database
must hold vectors from ONE provider: delete and re-upload documents after switching.

Free-tier quota is small, so a 429/5xx is retried briefly (honouring Retry-After) before
failing, and single-text calls (search queries) are cached so a repeated query costs nothing.
"""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict

import httpx

from app.services.embeddings import TRANSIENT_STATUSES, EmbeddingError

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
BATCH_SIZE = 32
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 2
MAX_WAIT_S = 5.0
CACHE_SIZE = 256
CACHE_MAX_CHARS = 2000


def _retry_wait(resp: httpx.Response, attempt: int) -> float:
    header = resp.headers.get("retry-after", "")
    try:
        return max(0.0, float(header))
    except ValueError:
        return 1.5 * 2**attempt


class GeminiEmbeddingProvider:
    name = "gemini"
    table = "chunk_embeddings_local"
    dim = 384

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        *,
        timeout: float = 30.0,
        max_retries: int = MAX_RETRIES,
        max_wait_s: float = MAX_WAIT_S,
    ) -> None:
        if not api_key:
            raise EmbeddingError("gemini api key is empty")
        self._key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_wait_s = max_wait_s
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        single = len(texts) == 1 and len(texts[0]) <= CACHE_MAX_CHARS
        if single:
            hit = self._cache.get(texts[0])
            if hit is not None:
                self._cache.move_to_end(texts[0])
                return [list(hit)]
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start in range(0, len(texts), BATCH_SIZE):
                out.extend(await self._batch(client, texts[start : start + BATCH_SIZE]))
        if single and out:
            self._cache[texts[0]] = list(out[0])
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return out

    async def _post(self, client: httpx.AsyncClient, body: dict) -> httpx.Response:
        attempt = 0
        while True:
            try:
                resp = await client.post(
                    URL.format(model=self._model), json=body, headers={"x-goog-api-key": self._key}
                )
            except httpx.TransportError as exc:
                raise EmbeddingError(
                    f"gemini request failed: {type(exc).__name__}", transient=True
                ) from None
            if resp.status_code not in RETRY_STATUSES or attempt >= self._max_retries:
                return resp
            wait = _retry_wait(resp, attempt)
            if wait > self._max_wait_s:
                return resp
            await asyncio.sleep(wait)
            attempt += 1

    async def _batch(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self._model}",
                    "content": {"parts": [{"text": t or " "}]},
                    "outputDimensionality": self.dim,
                }
                for t in texts
            ]
        }
        resp = await self._post(client, body)
        if resp.status_code != 200:
            try:
                detail = str(resp.json().get("error", {}).get("message", ""))[:200]
            except ValueError:
                detail = ""
            raise EmbeddingError(
                f"gemini embeddings http {resp.status_code}: {detail}",
                transient=resp.status_code in TRANSIENT_STATUSES,
            )
        items = resp.json().get("embeddings", [])
        if len(items) != len(texts):
            raise EmbeddingError(f"gemini returned {len(items)} vectors for {len(texts)} texts")
        vectors: list[list[float]] = []
        for item in items:
            vec = [float(v) for v in item.get("values", [])]
            if len(vec) != self.dim:
                raise EmbeddingError(f"unexpected embedding size {len(vec)}")
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])  # truncated vectors are not pre-normalised
        return vectors
