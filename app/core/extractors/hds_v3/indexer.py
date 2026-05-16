"""HDS-v3 Indexer: brain_facts + brain_faqs -> brain_chunks (embedded).

Run after `HDSv3Persistence.persist()` (called from engine.ingest_and_persist).
Deletes the company's existing `source_type='hds_v3_chunk'` rows, builds one
chunk per fact/faq, embeds the batch, and inserts the new rows.

Designed to be idempotent: same input data -> same chunks (modulo embed
non-determinism, which is negligible).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.embedder import Embedder

logger = logging.getLogger(__name__)

SOURCE_TYPE = "hds_v3_chunk"


def _fmt_price(prod_value: dict) -> str:
    price = prod_value.get("price_eur")
    unit = prod_value.get("price_unit") or prod_value.get("price_text")
    if price is None and not unit:
        return ""
    parts: list[str] = []
    if price is not None:
        try:
            parts.append(f"{float(price):.2f}€")
        except (TypeError, ValueError):
            parts.append(str(price))
    if unit:
        parts.append(f"/ {unit}")
    return " ".join(parts).strip()


def _fmt_attrs(attrs: dict | None) -> str:
    if not attrs:
        return ""
    keep = {k: v for k, v in attrs.items() if k not in {"primary_image_url", "image_urls"}}
    if not keep:
        return ""
    bits = []
    for k, v in keep.items():
        if v is None or v == "" or v == []:
            continue
        bits.append(f"{k}={v}")
    return ", ".join(bits)


class HDSv3Indexer:
    """Build embedded brain_chunks from brain_facts + brain_faqs."""

    def __init__(self, embedder: Embedder | None = None):
        self._embedder = embedder

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder()
        return self._embedder

    async def reindex(self, session: AsyncSession, company_id: UUID) -> dict:
        result: dict[str, Any] = {
            "success": False,
            "deleted": 0,
            "inserted": 0,
            "facts_seen": 0,
            "faqs_seen": 0,
            "error": None,
        }
        try:
            facts = await self._load_facts(session, company_id)
            faqs = await self._load_faqs(session, company_id)
            result["facts_seen"] = len(facts)
            result["faqs_seen"] = len(faqs)

            chunks = list(self._build_chunks(facts, faqs))
            if not chunks:
                result["error"] = "no_facts_or_faqs"
                return result

            deleted = await self._delete_existing(session, company_id)
            result["deleted"] = deleted

            texts = [c["text"] for c in chunks]
            embeddings = await self.embedder.embed_batch(texts)
            if len(embeddings) != len(chunks):
                raise RuntimeError(
                    f"embedding/chunk mismatch: {len(embeddings)} vs {len(chunks)}"
                )

            for chunk, emb in zip(chunks, embeddings):
                await session.execute(
                    sa_text(
                        """
                        INSERT INTO brain_chunks
                            (company_id, text, source_url, source_type, section,
                             embedding, meta)
                        VALUES
                            (:cid, :text, :source_url, :source_type, :section,
                             CAST(:emb AS vector), CAST(:meta AS jsonb))
                        """
                    ),
                    {
                        "cid": str(company_id),
                        "text": chunk["text"],
                        "source_url": chunk["source_url"],
                        "source_type": SOURCE_TYPE,
                        "section": chunk["section"],
                        "emb": _vector_literal(emb),
                        "meta": json.dumps(chunk["meta"], ensure_ascii=False),
                    },
                )
                result["inserted"] += 1

            await session.commit()
            result["success"] = True
            logger.info(
                "HDS-v3 reindex OK company=%s deleted=%d inserted=%d",
                company_id,
                result["deleted"],
                result["inserted"],
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("HDS-v3 reindex failed")
            await session.rollback()
            result["error"] = str(e)[:300]
            return result

    async def _load_facts(
        self, session: AsyncSession, company_id: UUID
    ) -> list[dict]:
        res = await session.execute(
            sa_text(
                "SELECT key, subject, value, source_url FROM brain_facts "
                "WHERE company_id = :cid"
            ),
            {"cid": str(company_id)},
        )
        rows = []
        for r in res.fetchall():
            value = r[2]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:  # noqa: BLE001
                    value = {"value": value}
            rows.append(
                {
                    "key": r[0],
                    "subject": r[1],
                    "value": value or {},
                    "source_url": r[3],
                }
            )
        return rows

    async def _load_faqs(
        self, session: AsyncSession, company_id: UUID
    ) -> list[dict]:
        res = await session.execute(
            sa_text(
                "SELECT question, answer, source_url FROM brain_faqs "
                "WHERE company_id = :cid"
            ),
            {"cid": str(company_id)},
        )
        return [
            {"question": r[0], "answer": r[1], "source_url": r[2]}
            for r in res.fetchall()
        ]

    async def _delete_existing(
        self, session: AsyncSession, company_id: UUID
    ) -> int:
        res = await session.execute(
            sa_text(
                "DELETE FROM brain_chunks WHERE company_id = :cid "
                "AND source_type = :stype"
            ),
            {"cid": str(company_id), "stype": SOURCE_TYPE},
        )
        return getattr(res, "rowcount", 0) or 0

    def _build_chunks(self, facts: list[dict], faqs: list[dict]):
        for f in facts:
            key = f["key"] or "info"
            value = f["value"] if isinstance(f["value"], dict) else {}
            text = self._fact_text(key, value)
            if not text:
                continue
            yield {
                "text": text,
                "source_url": f.get("source_url"),
                "section": key,
                "meta": {
                    "key": key,
                    "subject": f.get("subject"),
                    "value_keys": sorted(value.keys()),
                },
            }
        for q in faqs:
            question = (q.get("question") or "").strip()
            answer = (q.get("answer") or "").strip()
            if not question or not answer:
                continue
            yield {
                "text": f"Otázka: {question}\nOdpoveď: {answer}",
                "source_url": q.get("source_url"),
                "section": "faq",
                "meta": {"kind": "faq"},
            }

    def _fact_text(self, key: str, value: dict) -> str:
        text_value = value.get("value") or ""
        if key == "product":
            name = value.get("name") or text_value
            if not name:
                return ""
            price = _fmt_price(value)
            desc = (value.get("description") or "").strip()
            attrs = _fmt_attrs(value.get("attributes"))
            parts = [f"Produkt: {name}"]
            if price:
                parts.append(f"Cena: {price}")
            if desc:
                parts.append(f"Popis: {desc}")
            if attrs:
                parts.append(f"Atribúty: {attrs}")
            return ". ".join(parts) + "."
        if key.startswith("contact_"):
            label = key.replace("contact_", "")
            return f"Kontakt ({label}): {text_value}"
        if key.startswith("info_"):
            return f"{text_value}"
        if key == "company_name":
            return f"Názov firmy: {text_value}"
        if key in {"ico", "dic"}:
            return f"{key.upper()}: {text_value}"
        return str(text_value or "").strip()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


__all__ = ["HDSv3Indexer"]
