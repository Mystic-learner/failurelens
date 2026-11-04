"""Embedding providers. Text passed in is already redacted (chunks are redacted before storage).

- fake:   deterministic hashed bag-of-words, 384 dims. TEST ONLY. It reuses the 384-dim
          chunk_embeddings_local table, so never run it against a database you care about.
- openai: text-embedding-3-small, 1536 dims, own table. Timeout on every call, one retry on
          transient errors (network, 408, 429, 5xx). Neither the key nor the text is logged.
Providers are never mixed: each has its own table and HNSW index.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings

TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_TOKEN = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingError(Exception):
    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class EmbeddingProvider(Protocol):
    name: str
    table: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbeddingProvider:
    name = "fake"
    table = "chunk_embeddings_local"
    dim = 384

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vec[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class OpenAIEmbeddingProvider:
    name = "openai"
    table = "chunk_embeddings_openai"
    dim = 1536

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 20.0,
        retries: int = 1,
        retry_delay_s: float = 0.5,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._retries = retries
        self._retry_delay_s = retry_delay_s
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = {"model": self._model, "input": texts, "encoding_format": "float"}
        for attempt in range(self._retries + 1):
            try:
                resp = await self._http.post("/embeddings", json=body, headers=self._headers)
            except httpx.HTTPError as exc:
                error = EmbeddingError(f"network error: {type(exc).__name__}", transient=True)
            else:
                if 200 <= resp.status_code < 300:
                    return self._parse(resp, len(texts))
                error = EmbeddingError(
                    f"HTTP {resp.status_code}", transient=resp.status_code in TRANSIENT_STATUSES
                )
            if not error.transient or attempt == self._retries:
                raise error
            await asyncio.sleep(self._retry_delay_s * 2**attempt)
        raise EmbeddingError("unreachable")

    def _parse(self, resp: httpx.Response, expected: int) -> list[list[float]]:
        try:
            items = sorted(resp.json()["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in items]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError(f"malformed response: {type(exc).__name__}") from None
        if len(vectors) != expected or any(len(v) != self.dim for v in vectors):
            raise EmbeddingError("response has the wrong number of vectors or dimensions")
        return vectors


def make_provider(settings: Settings) -> EmbeddingProvider | None:
    """The configured provider, or None when it cannot run (no key; local arrives in Phase 8)."""
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_provider == "openai" and settings.openai_api_key:
        return OpenAIEmbeddingProvider(settings.openai_api_key)
    return None