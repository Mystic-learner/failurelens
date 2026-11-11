"""Local embeddings: bge-small-en-v1.5 on CPU through ONNX Runtime (384 dims).

Needs the optional packages onnxruntime, tokenizers and numpy, and two model files
(model.onnx, tokenizer.json) in one directory. Importing this module without the packages
raises ImportError; callers treat that as "local provider unavailable".

- Pooling: hidden state of the [CLS] token, L2-normalised (how BGE models are trained).
- No query instruction prefix: chunks and queries go through the same embed() call.
- split() cuts a chunk into line-aligned windows of about WINDOW_TOKENS tokens; the
  worker stores one vector per window. A single line over MAX_TOKENS is still truncated.
- Inference is CPU-bound: it runs in a worker thread, one call at a time.
Own table (chunk_embeddings_local), never mixed with openai. The fake test provider uses
the same table, so never run both against one database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from app.services.embeddings import EmbeddingError

MODEL_FILE = "model.onnx"
TOKENIZER_FILE = "tokenizer.json"
MAX_TOKENS = 512
BATCH_SIZE = 16
WINDOW_TOKENS = 256
_REQUIRED_INPUTS = frozenset({"input_ids", "attention_mask"})
_KNOWN_INPUTS = _REQUIRED_INPUTS | {"token_type_ids"}
_OUTPUT = "last_hidden_state"


def model_files_present(model_dir: str | Path) -> bool:
    base = Path(model_dir)
    return (base / MODEL_FILE).is_file() and (base / TOKENIZER_FILE).is_file()


class LocalEmbeddingProvider:
    name = "local"
    table = "chunk_embeddings_local"
    dim = 384

    def __init__(self, model_dir: str | Path, *, batch_size: int = BATCH_SIZE) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        base = Path(model_dir)
        if not model_files_present(base):
            raise EmbeddingError(f"model files not found in {base}")
        tokenizer = Tokenizer.from_file(str(base / TOKENIZER_FILE))
        pad_id = tokenizer.token_to_id("[PAD]")
        if pad_id is None:
            raise EmbeddingError("tokenizer has no [PAD] token")
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        tokenizer.enable_padding(pad_id=pad_id, pad_token="[PAD]")
        session = ort.InferenceSession(str(base / MODEL_FILE), providers=["CPUExecutionProvider"])
        inputs = {i.name for i in session.get_inputs()}
        if not _REQUIRED_INPUTS <= inputs or not inputs <= _KNOWN_INPUTS:
            raise EmbeddingError(f"unexpected model inputs: {sorted(inputs)}")
        outputs = [o.name for o in session.get_outputs()]
        if _OUTPUT not in outputs:
            raise EmbeddingError(f"model has no {_OUTPUT} output: {outputs}")
        counter = Tokenizer.from_file(str(base / TOKENIZER_FILE))
        counter.no_truncation()
        counter.no_padding()
        self._counter = counter
        self._tokenizer = tokenizer
        self._session = session
        self._token_types = "token_type_ids" in inputs
        self._batch_size = batch_size
        self._lock = asyncio.Lock()

    def split(self, text: str) -> list[str]:
        """Cut text into line-aligned windows of about WINDOW_TOKENS tokens."""
        lines = text.splitlines()
        if not lines:
            return [text]
        counts = [len(e.ids) for e in self._counter.encode_batch(lines)]
        windows: list[str] = []
        current: list[str] = []
        used = 0
        for line, n in zip(lines, counts, strict=True):
            if current and used + n > WINDOW_TOKENS:
                windows.append("\n".join(current))
                current, used = [], 0
            current.append(line)
            used += n
        if current:
            windows.append("\n".join(current))
        return windows

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with self._lock:
            try:
                return await asyncio.to_thread(self._embed_sync, texts)
            except EmbeddingError:
                raise
            except Exception as exc:  # onnxruntime raises many unrelated error types
                raise EmbeddingError(f"local inference failed: {type(exc).__name__}") from None

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            encodings = self._tokenizer.encode_batch(texts[start : start + self._batch_size])
            feed: dict[str, Any] = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            }
            if self._token_types:
                feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
            hidden = np.asarray(self._session.run([_OUTPUT], feed)[0], dtype=np.float32)
            if hidden.ndim != 3 or hidden.shape[2] != self.dim:
                raise EmbeddingError(f"unexpected output shape {hidden.shape}")
            cls = hidden[:, 0, :]
            if not np.isfinite(cls).all():
                raise EmbeddingError("model produced non-finite values")
            norms = np.linalg.norm(cls, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            vectors.extend((cls / norms).tolist())
        return vectors
