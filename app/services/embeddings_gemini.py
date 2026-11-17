"""Gemini embeddings (gemini-embedding-001, truncated to 384 dims) over the REST API.

For the hosted demo only. It reuses the 384-dim chunk_embeddings_local table, so a database
must hold vectors from ONE provider: delete and re-upload documents after switching.
"""

from __future__ import annotations

import math

import httpx

from app.services.embeddings import TRANSIENT_STATUSES, EmbeddingError

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
BATCH_SIZE = 32


class GeminiEmbeddingProvider:
    name = "gemini"
    table = "chunk_embeddings_local"
    dim = 384

    def __init__(
        self, api_key: str, model: str = "gemini-embedding-001", *, timeout: float = 30.0
    ) -> None:
        if not api_key:
            raise EmbeddingError("gemini api key is empty")
        self._key = api_key
        self._model = model
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start in range(0, len(texts), BATCH_SIZE):
                out.extend(await self._batch(client, texts[start : start + BATCH_SIZE]))
        return out

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
        try:
            resp = await client.post(
                URL.format(model=self._model), json=body, headers={"x-goog-api-key": self._key}
            )
        except httpx.TransportError as exc:
            raise EmbeddingError(
                f"gemini request failed: {type(exc).__name__}", transient=True
            ) from None
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
