"""HDS-v3 persistence: ParseResult + persona -> DB.

Adaptation note (vs. original Commit 3.1 brief)
-----------------------------------------------
The brief assumed `brain_facts` / `brain_faqs` carried a `superseded_at`
column. The real schema (see `app/models/brain_facts.py`,
`app/models/brain_faqs.py`) has no supersede column. The matching dedup
patterns already used by `app/core/knowledge_hub.py` are:

* `brain_facts`: UPSERT on UniqueConstraint(company_id, key, subject)
  — `value` is JSONB shaped as `{"value": <text>, "source_type": <str>}`.
* `brain_faqs`: skip-if-question-already-exists (case-insensitive).
* `brain_personas`: append-only with monotonically increasing `version`.

This module reuses those patterns to keep HDS-v3 writes compatible with
the rest of the system (responder/RAG reads).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.parser import ParseResult
from app.models.brain_faqs import BrainFaq
from app.models.brain_personas import BrainPersonaDocument

logger = logging.getLogger(__name__)


_SUBJECT_TRUNC = 200


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_subject(value: str) -> str:
    """Subject is the dedup discriminator for brain_facts unique constraint."""
    norm = _strip_diacritics(value).lower().strip()
    norm = re.sub(r"\s+", " ", norm)
    return norm[:_SUBJECT_TRUNC]


def _normalize_question(q: str) -> str:
    return q.strip().lower()


class HDSv3Persistence:
    """Persist ParseResult + persona dict to DB.

    Strategy:
      * facts (company_meta, contacts, products as facts, info): UPSERT
        via ON CONFLICT (company_id, key, subject).
      * faqs: insert only when normalized question is new for company.
      * persona: append row with `version = max(version)+1`.

    Single commit per `persist()` call; rollback on any error.
    """

    async def persist(
        self,
        session: AsyncSession,
        company_id: UUID,
        parse: ParseResult,
        persona: dict,
        source_url: str,
    ) -> dict:
        result: dict[str, Any] = {
            "facts_inserted": 0,
            "facts_updated": 0,
            "faqs_inserted": 0,
            "faqs_skipped_duplicates": 0,
            "persona_inserted": False,
            "persona_version": None,
            "error": None,
        }
        try:
            ins, upd = await self._upsert_facts(session, company_id, parse, source_url)
            result["facts_inserted"] = ins
            result["facts_updated"] = upd

            f_ins, f_dup = await self._insert_faqs(session, company_id, parse.faqs, source_url)
            result["faqs_inserted"] = f_ins
            result["faqs_skipped_duplicates"] = f_dup

            if persona and persona.get("success") and persona.get("persona_text"):
                version = await self._next_persona_version(session, company_id)
                doc = BrainPersonaDocument(
                    company_id=company_id,
                    version=version,
                    persona_text=persona["persona_text"],
                    word_count=int(persona.get("word_count") or 0) or None,
                    source_urls=persona.get("source_urls") or [],
                    gemini_cost_usd=persona.get("cost_usd") or 0,
                    tokens_in=int(persona.get("tokens_in") or 0),
                    tokens_out=int(persona.get("tokens_out") or 0),
                    meta={"source": "hds_v3", "base_url": source_url},
                )
                session.add(doc)
                result["persona_inserted"] = True
                result["persona_version"] = version

            await session.commit()
            logger.info(
                "HDS-v3 persist OK company=%s facts(ins=%d upd=%d) faqs(ins=%d dup=%d) persona_v=%s",
                company_id,
                result["facts_inserted"],
                result["facts_updated"],
                result["faqs_inserted"],
                result["faqs_skipped_duplicates"],
                result["persona_version"],
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("HDS-v3 persistence failed")
            await session.rollback()
            result["error"] = str(e)[:300]
            return result

    # ------------------------------------------------------------------
    # Facts: ParseResult -> brain_facts rows via UPSERT
    # ------------------------------------------------------------------
    async def _upsert_facts(
        self,
        session: AsyncSession,
        company_id: UUID,
        parse: ParseResult,
        source_url: str,
    ) -> tuple[int, int]:
        rows = list(self._build_fact_rows(parse, source_url))
        if not rows:
            return 0, 0

        # We don't get per-row INSERTED-vs-UPDATED from a multi-row upsert
        # easily, so issue one statement per row and inspect xmax — postgres
        # exposes xmax != 0 for updated rows. To stay portable & cheap, we
        # instead pre-fetch the existing (key, subject) keys.
        existing_keys = await self._existing_fact_keys(session, company_id)

        inserted = 0
        updated = 0
        for row in rows:
            kkey = (row["key"], row["subject"])
            if kkey in existing_keys:
                updated += 1
            else:
                inserted += 1
                existing_keys.add(kkey)

            await session.execute(
                sa_text(
                    """
                    INSERT INTO brain_facts
                        (company_id, key, subject, value, evidence, source_url, confidence)
                    VALUES
                        (:cid, :key, :subject, CAST(:value AS jsonb), :evidence, :source_url, :confidence)
                    ON CONFLICT (company_id, key, subject) DO UPDATE
                    SET value = EXCLUDED.value,
                        evidence = EXCLUDED.evidence,
                        source_url = EXCLUDED.source_url,
                        confidence = GREATEST(brain_facts.confidence, EXCLUDED.confidence),
                        updated_at = now()
                    """
                ),
                {
                    "cid": str(company_id),
                    "key": row["key"],
                    "subject": row["subject"],
                    "value": json.dumps(row["value"], ensure_ascii=False),
                    "evidence": row["evidence"],
                    "source_url": row["source_url"],
                    "confidence": row["confidence"],
                },
            )
        return inserted, updated

    async def _existing_fact_keys(
        self, session: AsyncSession, company_id: UUID
    ) -> set[tuple[str, str | None]]:
        res = await session.execute(
            sa_text(
                "SELECT key, subject FROM brain_facts WHERE company_id = :cid"
            ),
            {"cid": str(company_id)},
        )
        return {(r[0], r[1]) for r in res.fetchall()}

    def _build_fact_rows(self, parse: ParseResult, source_url: str):
        """Yield dicts shaped for the INSERT statement above."""

        def row(
            key: str,
            subject: str,
            value_text: str,
            *,
            source_type: str,
            extra: dict | None = None,
            evidence: str | None = None,
            row_source_url: str | None = None,
            confidence: float = 0.9,
        ):
            value_payload: dict[str, Any] = {
                "value": value_text,
                "source_type": source_type,
            }
            if extra:
                value_payload.update(extra)
            return {
                "key": key,
                "subject": _normalize_subject(subject) or _normalize_subject(value_text),
                "value": value_payload,
                "evidence": (evidence or value_text)[:500],
                "source_url": row_source_url or source_url,
                "confidence": confidence,
            }

        # Company metadata
        if parse.company_name:
            yield row(
                "company_name",
                parse.company_name,
                parse.company_name,
                source_type="hds_v3",
            )
        if parse.company_ico:
            yield row(
                "ico",
                parse.company_ico,
                parse.company_ico,
                source_type="hds_v3",
            )
        if parse.company_dic:
            yield row(
                "dic",
                parse.company_dic,
                parse.company_dic,
                source_type="hds_v3",
            )

        # Products
        for prod in parse.products:
            name = (prod.name or "").strip()
            if not name:
                continue
            # Image matching may set either direct fields on the product
            # (engine._match_images writes prod.image_url / prod.image_urls)
            # or attributes-dict entries (older designs). Read attributes
            # first so this is robust to either upstream.
            prod_attrs = prod.attributes or {}
            primary_image = (
                prod_attrs.get("primary_image_url")
                or getattr(prod, "primary_image_url", None)
                or getattr(prod, "image_url", None)
            )
            image_urls = (
                list(prod_attrs.get("image_urls") or [])
                or list(getattr(prod, "image_urls", []) or [])
                or []
            )
            yield row(
                "product",
                name,
                name,
                source_type="hds_v3_product",
                extra={
                    "name": name,
                    "price_eur": (
                        float(prod.price_eur) if prod.price_eur is not None else None
                    ),
                    "price_text": prod.price_text,
                    "price_unit": getattr(prod, "price_unit", None),
                    "description": prod.description,
                    "attributes": prod.attributes,
                    "primary_image_url": primary_image,
                    "image_urls": image_urls,
                },
                evidence=prod.description or name,
                row_source_url=prod.source_url or source_url,
            )

        # Contacts (phone/email/address/social)
        for contact in parse.contacts:
            ctype = contact.type or "contact"
            content = (contact.content or "").strip()
            if not content:
                continue
            subj_seed = (
                (contact.meta or {}).get("phone")
                or (contact.meta or {}).get("email")
                or (contact.meta or {}).get("url")
                or content
            )
            yield row(
                f"contact_{ctype}",
                subj_seed,
                content[:500],
                source_type="hds_v3_contact",
                extra={**(contact.meta or {}), "type": ctype},
                row_source_url=contact.source_url or source_url,
            )

        # Info facts (geographic, process, terms, etc.)
        for fact in parse.facts:
            ftype = fact.type or "info"
            content = (fact.content or "").strip()
            if not content:
                continue
            section = (fact.meta or {}).get("section") or ftype
            yield row(
                f"info_{section}".lower(),
                content[:200],
                content[:2000],
                source_type="hds_v3_info",
                extra={**(fact.meta or {}), "type": ftype},
                row_source_url=fact.source_url or source_url,
                confidence=0.75,
            )

    # ------------------------------------------------------------------
    # FAQs: dedup by normalized question per company
    # ------------------------------------------------------------------
    async def _insert_faqs(
        self,
        session: AsyncSession,
        company_id: UUID,
        faqs: list,
        source_url: str,
    ) -> tuple[int, int]:
        if not faqs:
            return 0, 0
        existing = await session.execute(
            sa_text("SELECT question FROM brain_faqs WHERE company_id = :cid"),
            {"cid": str(company_id)},
        )
        existing_set = {_normalize_question(q) for q in existing.scalars().all()}

        inserted = 0
        skipped = 0
        seen: set[str] = set()
        for faq in faqs:
            q = (faq.question or "").strip()
            a = (faq.answer or "").strip()
            if not q or not a:
                skipped += 1
                continue
            norm = _normalize_question(q)
            if norm in seen or norm in existing_set:
                skipped += 1
                continue
            seen.add(norm)
            session.add(
                BrainFaq(
                    company_id=company_id,
                    question=q[:500],
                    answer=a[:2000],
                    source_url=faq.source_url or source_url,
                )
            )
            inserted += 1
        return inserted, skipped

    # ------------------------------------------------------------------
    # Persona: monotonic version per company
    # ------------------------------------------------------------------
    async def _next_persona_version(
        self, session: AsyncSession, company_id: UUID
    ) -> int:
        stmt = (
            select(func.coalesce(func.max(BrainPersonaDocument.version), 0))
            .where(BrainPersonaDocument.company_id == company_id)
        )
        res = await session.execute(stmt)
        return int(res.scalar() or 0) + 1


__all__ = ["HDSv3Persistence"]
