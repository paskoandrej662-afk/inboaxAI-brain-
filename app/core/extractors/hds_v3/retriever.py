"""HDS-v3 Retriever: pgvector cosine search over brain_chunks."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.embedder import Embedder

logger = logging.getLogger(__name__)

SOURCE_TYPE = "hds_v3_chunk"
MIN_SIMILARITY = 0.3


@dataclass
class RetrievedChunk:
    text: str
    source_url: str | None
    section: str | None
    similarity: float
    meta: dict[str, Any] = field(default_factory=dict)


class HDSv3Retriever:
    """Embed the query, run pgvector cosine search, filter weak matches."""

    def __init__(self, embedder: Embedder | None = None):
        self._embedder = embedder

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder()
        return self._embedder

    async def retrieve(
        self,
        session: AsyncSession,
        company_id: UUID,
        query: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        if not query or not query.strip():
            return []
        query_emb = await self.embedder.embed_text(query)
        emb_literal = _vector_literal(query_emb)

        res = await session.execute(
            sa_text(
                """
                SELECT text, source_url, section, meta,
                       1 - (embedding <=> CAST(:emb AS vector)) AS similarity
                FROM brain_chunks
                WHERE company_id = :cid
                  AND source_type = :stype
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:emb AS vector)
                LIMIT :k
                """
            ),
            {
                "cid": str(company_id),
                "stype": SOURCE_TYPE,
                "emb": emb_literal,
                "k": top_k,
            },
        )

        chunks: list[RetrievedChunk] = []
        for row in res.fetchall():
            similarity = float(row[4]) if row[4] is not None else 0.0
            if similarity < MIN_SIMILARITY:
                continue
            meta = row[3] if isinstance(row[3], dict) else {}
            chunks.append(
                RetrievedChunk(
                    text=row[0],
                    source_url=row[1],
                    section=row[2],
                    similarity=similarity,
                    meta=meta,
                )
            )
        return chunks


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


__all__ = ["HDSv3Retriever", "RetrievedChunk"]
