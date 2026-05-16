"""HDS-v3 Responder: persona + memory + RAG -> Gemini Flash reply.

Wires together the loaded persona document, conversation memory (summary
+ recent), and top-K retrieved chunks into one Gemini Flash call. The
reply is plain text in Slovak. Memory is appended in a fire-and-forget
asyncio task so the response is not blocked on summarization.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from google import genai
from google.genai import types as genai_types
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.conversation_memory import (
    ConversationMemory,
    ConversationState,
)
from app.core.extractors.hds_v3.retriever import HDSv3Retriever, RetrievedChunk
from app.models.brain_personas import BrainPersonaDocument

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """Si AI asistent firmy. Odpovedáš ako jej zamestnanec — nie ako bot.

PROFIL FIRMY:
{persona}

PRAVIDLÁ:
- Odpovedaj IBA na základe informácií z PROFILU FIRMY, RELEVANTNÝCH DÁT a PAMÄTE.
- Nikdy si nevymýšľaj fakty (ceny, produkty, dostupnosť).
- Pri neistote alebo ak údaj chýba: úprimne povedz, že to overíš a ozveš sa.
- Buď stručný a vecný, v slovenčine, používaj tykanie ak persona neurčí inak.
- Žiadne uvádzacie frázy typu "Ako AI…". Konaj ako človek z firmy.

PAMÄŤ KONVERZÁCIE (súhrn):
{summary}

POSLEDNÉ SPRÁVY:
{recent}

RELEVANTNÉ DÁTA Z DATABÁZY:
{chunks}
"""


@dataclass
class ResponseResult:
    success: bool = False
    reply_text: str | None = None
    persona_version: int | None = None
    chunks_used: int = 0
    memory_summary_len: int = 0
    memory_recent_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    error: str | None = None
    chunks: list[RetrievedChunk] = field(default_factory=list)


class HDSv3Responder:
    MODEL = "gemini-2.5-flash"
    TIMEOUT_SEC = 30
    TOP_K = 5
    MAX_OUTPUT_TOKENS = 512
    TEMPERATURE = 0.7

    INPUT_TOKEN_PRICE_PER_1M = 0.30
    OUTPUT_TOKEN_PRICE_PER_1M = 2.50

    def __init__(
        self,
        api_key: str | None = None,
        retriever: HDSv3Retriever | None = None,
        memory: ConversationMemory | None = None,
        session_factory=None,
    ):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client: genai.Client | None = None
        self._retriever = retriever
        self._memory = memory
        self._session_factory = session_factory  # used for fire-and-forget memory updates

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self._api_key:
                raise ValueError("GEMINI_API_KEY not set")
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    @property
    def retriever(self) -> HDSv3Retriever:
        if self._retriever is None:
            self._retriever = HDSv3Retriever()
        return self._retriever

    @property
    def memory(self) -> ConversationMemory:
        if self._memory is None:
            self._memory = ConversationMemory(api_key=self._api_key)
        return self._memory

    async def respond(
        self,
        session: AsyncSession,
        company_id: UUID,
        message: str,
        customer_id: str | None = None,
    ) -> ResponseResult:
        start = time.time()
        result = ResponseResult()

        if not message or not message.strip():
            result.error = "empty_message"
            result.duration_sec = time.time() - start
            return result

        # 1. Load persona (latest version)
        persona_doc = await self._load_persona(session, company_id)
        if persona_doc is None:
            result.error = "no_persona_for_company"
            result.duration_sec = time.time() - start
            return result
        result.persona_version = persona_doc.version

        # 2. Conversation state
        state = ConversationState()
        if customer_id:
            try:
                state = await self.memory.load(session, company_id, customer_id)
            except Exception:  # noqa: BLE001
                logger.exception("Memory load failed — proceeding stateless")
        result.memory_summary_len = len(state.summary_text or "")
        result.memory_recent_count = len(state.recent_messages)

        # 3. Retrieve top-K chunks
        try:
            chunks = await self.retriever.retrieve(
                session, company_id, message, top_k=self.TOP_K
            )
        except Exception:  # noqa: BLE001
            logger.exception("Retrieval failed — proceeding without chunks")
            chunks = []
        result.chunks_used = len(chunks)
        result.chunks = chunks

        # 4. Build prompt + call Gemini
        prompt = self._build_user_prompt(message)
        system = self._build_system_prompt(persona_doc.persona_text, state, chunks)

        try:
            response = await asyncio.wait_for(
                self._gemini_call(system, prompt),
                timeout=self.TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            result.error = "gemini_timeout"
            result.duration_sec = time.time() - start
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("Gemini call failed")
            result.error = f"gemini_error: {str(e)[:200]}"
            result.duration_sec = time.time() - start
            return result

        reply_text = self._extract_text(response)
        if not reply_text:
            result.error = "empty_response"
            result.duration_sec = time.time() - start
            return result

        result.reply_text = reply_text.strip()
        result.success = True

        um = getattr(response, "usage_metadata", None)
        if um is not None:
            result.input_tokens = getattr(um, "prompt_token_count", 0) or 0
            result.output_tokens = getattr(um, "candidates_token_count", 0) or 0
            result.cost_usd = (
                result.input_tokens
                * self.INPUT_TOKEN_PRICE_PER_1M
                / 1_000_000
                + result.output_tokens
                * self.OUTPUT_TOKEN_PRICE_PER_1M
                / 1_000_000
            )

        result.duration_sec = time.time() - start

        # 5. Fire-and-forget memory update
        if customer_id:
            new_messages = [
                {"role": "user", "text": message},
                {"role": "assistant", "text": result.reply_text},
            ]
            self._schedule_memory_update(company_id, customer_id, new_messages)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _load_persona(
        self, session: AsyncSession, company_id: UUID
    ) -> BrainPersonaDocument | None:
        stmt = (
            select(BrainPersonaDocument)
            .where(BrainPersonaDocument.company_id == company_id)
            .order_by(desc(BrainPersonaDocument.version))
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalars().first()

    def _build_system_prompt(
        self,
        persona_text: str,
        state: ConversationState,
        chunks: list[RetrievedChunk],
    ) -> str:
        recent_lines = "\n".join(
            f"[{m.get('role') or 'msg'}] {m.get('text') or ''}"
            for m in state.recent_messages
        ) or "(žiadne predchádzajúce správy)"
        chunk_lines = "\n".join(
            f"- ({c.section or 'info'}, sim={c.similarity:.2f}) {c.text}"
            for c in chunks
        ) or "(žiadne relevantné záznamy)"
        summary = state.summary_text or "(žiadny súhrn)"
        return SYSTEM_PROMPT_TEMPLATE.format(
            persona=persona_text.strip(),
            summary=summary,
            recent=recent_lines,
            chunks=chunk_lines,
        )

    def _build_user_prompt(self, message: str) -> str:
        return f"Správa od zákazníka: {message.strip()}\n\nOdpovedz prirodzene a krátko."

    async def _gemini_call(self, system: str, prompt: str):
        return await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.TEMPERATURE,
                max_output_tokens=self.MAX_OUTPUT_TOKENS,
            ),
        )

    def _extract_text(self, response: Any) -> str:
        text = getattr(response, "text", None) or ""
        if text.strip():
            return text
        # Fallback: candidates[0].content.parts[*].text (2B-10 lesson)
        try:
            candidates = getattr(response, "candidates", None) or []
            if candidates:
                cand = candidates[0]
                content = getattr(cand, "content", None)
                parts = getattr(content, "parts", None) if content else None
                if parts:
                    return "".join(
                        (getattr(p, "text", None) or "") for p in parts
                    )
        except Exception:  # noqa: BLE001
            logger.debug("Fallback text extraction failed", exc_info=True)
        return ""

    def _schedule_memory_update(
        self,
        company_id: UUID,
        customer_id: str,
        new_messages: list[dict[str, Any]],
    ) -> None:
        async def _do_update():
            try:
                if self._session_factory is None:
                    # Lazy import to avoid hard coupling at module import.
                    from app.db import AsyncSessionLocal
                    factory = AsyncSessionLocal
                else:
                    factory = self._session_factory
                async with factory() as new_session:
                    await self.memory.append_and_maybe_summarize(
                        new_session, company_id, customer_id, new_messages
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Background memory update failed")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_update())
        except RuntimeError:
            # No running loop — caller will await separately.
            logger.debug("No running loop for memory update; skipping background dispatch")


__all__ = ["HDSv3Responder", "ResponseResult"]
