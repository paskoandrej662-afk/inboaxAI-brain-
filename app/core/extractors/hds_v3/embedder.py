"""Gemini gemini-embedding-001 wrapper for HDS-v3 RAG.

gemini-embedding-001 supports a configurable `output_dimensionality`. We
request 1536 directly to match the existing `brain_chunks.embedding`
column (`vector(1536)`, OpenAI legacy schema), so no padding is needed.

(The previous model `text-embedding-004` was retired from the v1beta
API — it now 404s on embedContent. `gemini-embedding-001` is the
current replacement.)

Note: for output dimensions < 3072 the API returns un-normalized
vectors, but pgvector cosine distance (`<=>`) is scale-invariant, so
retrieval ranking is unaffected.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


class Embedder:
    """Thin async wrapper around Gemini's text embedding endpoint."""

    MODEL_NAME = "gemini-embedding-001"
    TARGET_DIM = 1536  # match brain_chunks.embedding column
    MAX_BATCH = 100

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not set (in env or constructor)")
        self.client = genai.Client(api_key=key)

    async def embed_text(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            chunk = texts[start : start + self.MAX_BATCH]
            cleaned = [t if t and t.strip() else "(empty)" for t in chunk]
            vectors = await self._call_embed(cleaned)
            if len(vectors) != len(cleaned):
                raise RuntimeError(
                    f"embedding count mismatch: got {len(vectors)} for {len(cleaned)} inputs"
                )
            out.extend(self._pad(v) for v in vectors)
        return out

    async def _call_embed(self, texts: list[str]) -> list[list[float]]:
        response = await asyncio.to_thread(
            self.client.models.embed_content,
            model=self.MODEL_NAME,
            contents=texts,
            config=genai_types.EmbedContentConfig(
                output_dimensionality=self.TARGET_DIM
            ),
        )
        embeddings = getattr(response, "embeddings", None) or []
        out: list[list[float]] = []
        for emb in embeddings:
            values = getattr(emb, "values", None)
            if values is None and isinstance(emb, (list, tuple)):
                values = list(emb)
            if values is None:
                raise RuntimeError("Gemini embed response missing .values")
            out.append(list(values))
        return out

    def _pad(self, vector: list[float]) -> list[float]:
        if len(vector) >= self.TARGET_DIM:
            return list(vector[: self.TARGET_DIM])
        return list(vector) + [0.0] * (self.TARGET_DIM - len(vector))


__all__ = ["Embedder"]
