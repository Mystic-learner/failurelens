"""Embedding providers. Text passed in is already redacted (chunks are redacted before storage).

- fake:   deterministic hashed bag-of-words, 384 dims. TEST ONLY. It reuses the 384-dim
          chunk_embeddings_local table, so never run it against a database you care about.
Providers are never mixed: each has its own table and HNSW index.
"""

from __future__ import annotations

import hashlib
import math
import re
from importlib.util import find_spec
from pathlib import Path
from typing import Protocol

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


_LOCAL_PACKAGES = ("onnxruntime", "tokenizers", "numpy")
_LOCAL_FILES = ("model.onnx", "tokenizer.json")  # keep in step with embeddings_local


def local_provider_available(settings: Settings) -> bool:
    """Whether the local provider can run: optional packages installed, model files present.

    Nothing is imported or loaded here, so the health check can call it cheaply.
    """
    if any(find_spec(pkg) is None for pkg in _LOCAL_PACKAGES):
        return False
    base = Path(settings.local_model_dir)
    return all((base / name).is_file() for name in _LOCAL_FILES)


def make_provider(settings: Settings) -> EmbeddingProvider | None:
    """The configured provider, or None when it cannot run (no key, or local without its
    packages or model files)."""
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_provider == "local" and local_provider_available(settings):
        # Imported here: embeddings_local imports EmbeddingError from this module.
        from app.services.embeddings_local import LocalEmbeddingProvider

        return LocalEmbeddingProvider(settings.local_model_dir)
    return None
