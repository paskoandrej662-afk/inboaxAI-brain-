"""HDS-v3 ConversationMemory: per-customer summary + recent messages.

Stored in `brain_customer_memory` (HDS-v3 fields: summary_text,
last_messages, message_count). Logical key is (company_id, external_id).

Memory shape:
    - last_messages: JSON array of {role: 'user'|'assistant', text: str}
    - summary_text: rolling SK summary (≤150 words) of everything older
      than the last KEEP_RECENT messages
    - message_count: total messages ever appended for this customer

Fire-and-forget: any failure (DB, LLM) is logged and swallowed. Conversation
flow must never break because memory summarization had a hiccup.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from google import genai
from google.genai import types as genai_types
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


SUMMARIZATION_MODEL = "gemini-2.5-flash"
SUMMARIZATION_TIMEOUT_SEC = 30


SUMMARIZATION_PROMPT = """Si conversation summarizer. Vytvor STRUČNÝ súhrn konverzácie.

EXISTUJÚCI SÚHRN:
{existing}

NOVÉ SPRÁVY (chronologicky):
{new}

ÚLOHA: Zachyť kto je zákazník, čo plánuje, aké produkty ho zaujímajú,
status objednávky (rezervácia / nákup / dotaz), spomínaný termín či miesto,
preferencie. Vynechaj zdvorilostné frázy.

Max 150 slov. Slovenčina. Faktické, žiadne hypotézy. Súhrn:"""


@dataclass
class ConversationState:
    summary_text: str | None = None
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    message_count: int = 0


class ConversationMemory:
    """Per-customer summary + rolling tail of raw messages."""

    KEEP_RECENT = 5
    SUMMARIZE_THRESHOLD = 7

    def __init__(self, api_key: str | None = None):
        # Lazy-construct the Gemini client; tests can override _summarize.
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self._api_key:
                raise ValueError("GEMINI_API_KEY not set")
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def load(
        self,
        session: AsyncSession,
        company_id: UUID,
        customer_id: str,
    ) -> ConversationState:
        row = await self._fetch_row(session, company_id, customer_id)
        if not row:
            return ConversationState()
        return ConversationState(
            summary_text=row.get("summary_text"),
            recent_messages=list(row.get("last_messages") or []),
            message_count=int(row.get("message_count") or 0),
        )

    async def append_and_maybe_summarize(
        self,
        session: AsyncSession,
        company_id: UUID,
        customer_id: str,
        new_messages: list[dict[str, Any]],
    ) -> None:
        """Append messages, summarize if combined len > threshold.

        Fire-and-forget: errors are logged, never raised. Caller may
        schedule this with `asyncio.create_task(...)` and continue.
        """
        if not new_messages:
            return
        try:
            row = await self._fetch_row(session, company_id, customer_id)
            existing_summary = (row or {}).get("summary_text") if row else None
            recent: list[dict[str, Any]] = list(
                (row or {}).get("last_messages") or []
            )
            count = int((row or {}).get("message_count") or 0)

            combined = recent + list(new_messages)
            count += len(new_messages)

            new_summary = existing_summary
            new_recent = combined

            if len(combined) > self.SUMMARIZE_THRESHOLD:
                to_summarize = combined[: -self.KEEP_RECENT]
                new_recent = combined[-self.KEEP_RECENT :]
                try:
                    new_summary = await self._summarize(
                        existing_summary, to_summarize
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Conversation summary LLM failed — keeping previous summary"
                    )
                    new_summary = existing_summary
                    # We still trim recent_messages to KEEP_RECENT so the
                    # tail doesn't grow unbounded. The dropped messages
                    # are lost (no summary), but the next round will
                    # re-attempt summarization with the new arrivals.

            await self._upsert(
                session=session,
                company_id=company_id,
                customer_id=customer_id,
                summary_text=new_summary,
                last_messages=new_recent,
                message_count=count,
            )
            await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("ConversationMemory append failed (non-fatal)")
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------
    async def _fetch_row(
        self,
        session: AsyncSession,
        company_id: UUID,
        customer_id: str,
    ) -> dict | None:
        res = await session.execute(
            sa_text(
                """
                SELECT summary_text, last_messages, message_count
                FROM brain_customer_memory
                WHERE company_id = :cid AND external_id = :ext
                LIMIT 1
                """
            ),
            {"cid": str(company_id), "ext": customer_id},
        )
        row = res.fetchone()
        if not row:
            return None
        return {
            "summary_text": row[0],
            "last_messages": row[1],
            "message_count": row[2],
        }

    async def _upsert(
        self,
        session: AsyncSession,
        company_id: UUID,
        customer_id: str,
        summary_text: str | None,
        last_messages: list,
        message_count: int,
    ) -> None:
        import json

        await session.execute(
            sa_text(
                """
                INSERT INTO brain_customer_memory
                    (company_id, external_id, summary_text, last_messages,
                     message_count, updated_at)
                VALUES
                    (:cid, :ext, :summary, CAST(:msgs AS jsonb), :count, now())
                ON CONFLICT (company_id, external_id) WHERE external_id IS NOT NULL
                DO UPDATE SET
                    summary_text = EXCLUDED.summary_text,
                    last_messages = EXCLUDED.last_messages,
                    message_count = EXCLUDED.message_count,
                    updated_at = now()
                """
            ),
            {
                "cid": str(company_id),
                "ext": customer_id,
                "summary": summary_text,
                "msgs": json.dumps(last_messages, ensure_ascii=False),
                "count": message_count,
            },
        )

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------
    async def _summarize(
        self,
        existing_summary: str | None,
        to_summarize: list[dict[str, Any]],
    ) -> str:
        formatted = "\n".join(
            f"[{m.get('role') or 'msg'}] {m.get('text') or ''}"
            for m in to_summarize
        )
        prompt = SUMMARIZATION_PROMPT.format(
            existing=existing_summary or "(žiadny)",
            new=formatted or "(nič)",
        )
        response = await asyncio.wait_for(
            asyncio.to_thread(
                self.client.models.generate_content,
                model=SUMMARIZATION_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=400,
                ),
            ),
            timeout=SUMMARIZATION_TIMEOUT_SEC,
        )
        text = getattr(response, "text", None) or ""
        if not text.strip() and getattr(response, "candidates", None):
            try:
                cand = response.candidates[0]
                if cand.content and cand.content.parts:
                    text = "".join(
                        (getattr(p, "text", None) or "") for p in cand.content.parts
                    )
            except Exception:  # noqa: BLE001
                pass
        text = text.strip()
        if not text:
            raise RuntimeError("empty summarization response")
        return text


__all__ = ["ConversationMemory", "ConversationState"]
