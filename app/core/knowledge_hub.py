from __future__ import annotations

import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import bindparam, select, text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.chunker import Chunk, chunk_text, normalize_text
from app.core.embeddings import embed_batch
from app.core.extractor import (
    ExtractedFact,
    ExtractedFaq,
    extract_facts,
    extract_faqs,
    extract_text,
)
from app.core.scraper import ScrapedPage, scrape_site
from app.db import AsyncSessionLocal
from app.models.brain_chunks import BrainChunk
from app.models.brain_facts import BrainFact
from app.models.brain_faqs import BrainFaq
from app.models.brain_jobs import BrainJob

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    chunks_inserted: int = 0
    chunks_skipped: int = 0
    chunks_superseded: int = 0
    facts_inserted: int = 0
    faqs_inserted: int = 0
    pages_scraped: int = 0
    pages_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunks": self.chunks_inserted,
            "chunks_skipped": self.chunks_skipped,
            "chunks_superseded": self.chunks_superseded,
            "facts": self.facts_inserted,
            "faqs": self.faqs_inserted,
            "pages_scraped": self.pages_scraped,
            "pages_failed": self.pages_failed,
            "errors": self.errors[:5],
        }


async def _set_job(
    session: AsyncSession,
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    sets: list[str] = ["updated_at = now()"]
    params: dict[str, Any] = {"id": job_id}
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if progress is not None:
        sets.append("progress = :progress")
        params["progress"] = progress
    if result is not None:
        sets.append("result = CAST(:result AS jsonb)")
        import json as _json

        params["result"] = _json.dumps(result, ensure_ascii=False)
    if error is not None:
        sets.append("error = :error")
        params["error"] = error
    sql = f"UPDATE brain_jobs SET {', '.join(sets)} WHERE id = :id"
    await session.execute(sa_text(sql), params)
    await session.commit()


async def _update_progress(job_id: str, progress: int, status: str = "running") -> None:
    """Independent session so progress updates are visible to pollers immediately."""
    async with AsyncSessionLocal() as session:
        await _set_job(session, job_id, status=status, progress=progress)


async def ingest_url(company_id: uuid.UUID, url: str, job_id: str, max_pages: int = 30) -> IngestResult:
    """Run the full ingestion pipeline for one start URL. Updates brain_jobs in real time."""
    result = IngestResult()
    try:
        await _update_progress(job_id, 0, "running")

        # 1. Discover + scrape
        await _update_progress(job_id, 10)
        pages = await scrape_site(url, max_pages=max_pages, time_budget_s=90.0)
        result.pages_scraped = sum(1 for p in pages if p.html and not p.error)
        result.pages_failed = sum(1 for p in pages if p.error)
        await _update_progress(job_id, 30)

        # 2. Extract per page
        all_chunks: list[tuple[Chunk, ScrapedPage, str]] = []  # (chunk, page, section)
        all_facts: list[ExtractedFact] = []
        all_faqs: list[ExtractedFaq] = []

        for page in pages:
            if page.error or not page.html:
                continue
            try:
                content = extract_text(page.html, page.url)
            except Exception as exc:
                logger.warning("extract_text failed for %s: %s", page.url, exc)
                result.errors.append(f"extract:{page.url}:{exc}")
                continue

            if not content.text or len(content.text) < 50:
                continue

            page_chunks = chunk_text(
                content.text,
                target_size=800,
                overlap=100,
                min_size=100,
                max_size=1500,
            )
            for ch in page_chunks:
                all_chunks.append((ch, page, content.section))

            try:
                all_facts.extend(extract_facts(content.text, page.url))
            except Exception as exc:
                logger.warning("extract_facts failed for %s: %s", page.url, exc)

            try:
                all_faqs.extend(extract_faqs(content.text, page.html, page.url))
            except Exception as exc:
                logger.warning("extract_faqs failed for %s: %s", page.url, exc)

        await _update_progress(job_id, 50)

        # 3. Dedupe chunks against DB by (company_id, content_hash)
        async with AsyncSessionLocal() as session:
            existing_hashes: set[str] = set()
            superseded_old: list[uuid.UUID] = []
            if all_chunks:
                hashes = list({c.content_hash for c, _, _ in all_chunks})
                stmt = select(BrainChunk.content_hash).where(
                    BrainChunk.company_id == company_id,
                    BrainChunk.content_hash.in_(hashes),
                    BrainChunk.superseded_at.is_(None),
                )
                rows = (await session.execute(stmt)).scalars().all()
                existing_hashes = set(rows)

            # Per-source-url supersession: if a URL was previously ingested with chunks
            # whose content_hash is no longer present, mark them superseded.
            url_to_new_hashes: dict[str, set[str]] = {}
            for ch, page, _ in all_chunks:
                url_to_new_hashes.setdefault(page.url, set()).add(ch.content_hash)

            if url_to_new_hashes:
                stmt = sa_text(
                    """
                    SELECT id, source_url, content_hash
                    FROM brain_chunks
                    WHERE company_id = :cid
                      AND superseded_at IS NULL
                      AND source_url = ANY(:urls)
                    """
                ).bindparams(bindparam("urls", expanding=False))
                urls_list = list(url_to_new_hashes.keys())
                rows = (
                    await session.execute(
                        sa_text(
                            "SELECT id, source_url, content_hash FROM brain_chunks "
                            "WHERE company_id = :cid AND superseded_at IS NULL "
                            "AND source_url = ANY(:urls)"
                        ),
                        {"cid": str(company_id), "urls": urls_list},
                    )
                ).all()
                for r in rows:
                    rid, src_url, old_hash = r[0], r[1], r[2]
                    if (
                        src_url in url_to_new_hashes
                        and old_hash not in url_to_new_hashes[src_url]
                    ):
                        superseded_old.append(rid)

            new_chunks = [
                (ch, page, section)
                for ch, page, section in all_chunks
                if ch.content_hash not in existing_hashes
            ]
            result.chunks_skipped = len(all_chunks) - len(new_chunks)

        # 4. Embed
        if new_chunks:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            try:
                embeddings = await embed_batch([c.text for c, _, _ in new_chunks], client)
            finally:
                await client.close()
            if len(embeddings) != len(new_chunks):
                raise RuntimeError(
                    f"embedding count mismatch: {len(embeddings)} vs {len(new_chunks)} chunks"
                )
        else:
            embeddings = []
        await _update_progress(job_id, 80)

        # 5. Insert in transaction
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Mark superseded
                if superseded_old:
                    await session.execute(
                        sa_text(
                            "UPDATE brain_chunks SET superseded_at = now() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": [str(i) for i in superseded_old]},
                    )
                    result.chunks_superseded = len(superseded_old)

                # Insert chunks
                for (ch, page, section), emb in zip(new_chunks, embeddings):
                    obj = BrainChunk(
                        company_id=company_id,
                        text=ch.text,
                        source_url=page.url,
                        source_type="web",
                        section=section,
                        content_hash=ch.content_hash,
                        embedding=emb,
                        meta={"char_count": ch.char_count, "paragraph_idx": ch.paragraph_idx},
                    )
                    session.add(obj)
                    result.chunks_inserted += 1

                # Insert facts (upsert on (company_id, key, subject))
                for f in all_facts:
                    await session.execute(
                        sa_text(
                            """
                            INSERT INTO brain_facts (company_id, key, subject, value, evidence, source_url, confidence)
                            VALUES (:cid, :key, :subject, CAST(:value AS jsonb), :evidence, :source_url, :confidence)
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
                            "key": f.key,
                            "subject": f.subject,
                            "value": __import__("json").dumps(f.value, ensure_ascii=False),
                            "evidence": f.evidence,
                            "source_url": f.source_url,
                            "confidence": f.confidence,
                        },
                    )
                    result.facts_inserted += 1

                # Insert FAQs (no unique constraint — dedupe by (company_id, question))
                if all_faqs:
                    seen_q: set[tuple[str, str]] = set()
                    # Pre-load existing question text for this company to skip dupes
                    existing_q = (
                        await session.execute(
                            sa_text(
                                "SELECT question FROM brain_faqs WHERE company_id = :cid"
                            ),
                            {"cid": str(company_id)},
                        )
                    ).scalars().all()
                    existing_set = {q.strip().lower() for q in existing_q}

                    # Embed FAQ questions in one batch
                    faq_texts: list[str] = []
                    fresh_faqs: list[ExtractedFaq] = []
                    for faq in all_faqs:
                        sig = (faq.question.strip().lower(), faq.answer[:60])
                        if sig in seen_q:
                            continue
                        if faq.question.strip().lower() in existing_set:
                            continue
                        seen_q.add(sig)
                        faq_texts.append(f"{faq.question}\n{faq.answer}")
                        fresh_faqs.append(faq)

                    if fresh_faqs:
                        client2 = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                        try:
                            faq_embeddings = await embed_batch(faq_texts, client2)
                        finally:
                            await client2.close()
                        for faq, fe in zip(fresh_faqs, faq_embeddings):
                            session.add(
                                BrainFaq(
                                    company_id=company_id,
                                    question=faq.question,
                                    answer=faq.answer,
                                    source_url=faq.source_url,
                                    embedding=fe,
                                )
                            )
                            result.faqs_inserted += 1
            # commit happens on session.begin() exit

        await _update_progress(job_id, 95)

        # 6. Done
        async with AsyncSessionLocal() as session:
            await _set_job(
                session,
                job_id,
                status="done",
                progress=100,
                result=result.to_dict(),
            )
        return result

    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("ingest_url failed for %s", url)
        try:
            async with AsyncSessionLocal() as session:
                await _set_job(
                    session,
                    job_id,
                    status="failed",
                    error=f"{exc}\n{tb}"[:4000],
                    result=result.to_dict(),
                )
        except Exception:
            logger.exception("failed to update brain_jobs on error")
        raise
