"""HDS-v3 Engine: full pipeline crawler -> gemini -> parse -> validate -> dedup -> persona.

DB persistence is deferred to a thin wrapper in knowledge_hub.py (Commit 3.1).
This engine exposes parsed + persona results via attributes for inspection.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.extractors.hds_v3.crawler import HDSCrawler
from app.core.extractors.hds_v3.dedup import Deduplicator
from app.core.extractors.hds_v3.gemini_client import GeminiClient
from app.core.extractors.hds_v3.parser import MarkdownParser, ParseResult
from app.core.extractors.hds_v3.persona_generator import PersonaGenerator
from app.core.extractors.hds_v3.validator import Validator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class HDSv3Engine:
    """Full HDS-v3 pipeline orchestrator.

    Steps:
        1. Crawl pages
        2. Gemini extract markdown
        3. Parse markdown -> ParseResult
        4. Validate (anti-halucinacia)
        5. Dedup
        6. Generate persona (parallel with parse/validate/dedup)

    Persistence is a separate concern (see knowledge_hub.persist_hds_v3_result).
    """

    def __init__(self):
        self._last_parsed: ParseResult | None = None
        self._last_persona: dict | None = None
        self._last_media_pages: list | None = None

    async def ingest(self, base_url: str, company_id: str) -> dict:
        """Run full ingest pipeline. Returns summary dict."""
        result = {
            "success": False,
            "base_url": base_url,
            "company_id": company_id,
            "pages_discovered": 0,
            "batches_total": 0,
            "batches_successful": 0,
            "products": 0,
            "contacts": 0,
            "facts": 0,
            "faqs": 0,
            "persona_generated": False,
            "persona_words": 0,
            "total_cost_usd": 0.0,
            "extraction_cost": 0.0,
            "persona_cost": 0.0,
            "images_matched": 0,
            "images_total_candidates": 0,
            "error": None,
        }

        try:
            logger.info("Starting HDS-v3 ingest for %s", base_url)
            crawler = HDSCrawler()
            crawl = await crawler.discover(base_url)
            result["pages_discovered"] = crawl.total_discovered
            if not crawl.pages:
                result["error"] = "no_pages_discovered"
                return result

            # Gemini extraction and local rendering for media streams run
            # in parallel — Gemini hits the URLs via url_context while
            # BrowserPool renders them locally for image discovery.
            gemini = GeminiClient()
            extract, media_pages = await asyncio.gather(
                gemini.extract_pages(base_url, crawl.pages),
                crawler.crawl_media_streams(crawl.pages),
            )
            self._last_media_pages = media_pages
            result["batches_total"] = extract.total_batches
            result["batches_successful"] = extract.successful_batches
            result["extraction_cost"] = extract.total_cost_usd
            if not extract.success:
                result["error"] = "extraction_failed"
                return result

            parser = MarkdownParser()
            validator = Validator()
            dedup = Deduplicator()
            persona_gen = PersonaGenerator()

            async def parse_pipeline() -> ParseResult:
                parsed = parser.parse_batches(extract.batches)
                parsed = validator.validate(parsed)
                parsed = dedup.deduplicate(parsed)
                return parsed

            parsed, persona_result = await asyncio.gather(
                parse_pipeline(),
                persona_gen.generate(extract.batches),
            )

            # Image matching — universal, DOM-agnostic.
            if parsed.products and media_pages:
                images_matched, total_candidates = await self._match_images(
                    parsed, media_pages
                )
                result["images_matched"] = images_matched
                result["images_total_candidates"] = total_candidates

            result["products"] = len(parsed.products)
            result["contacts"] = len(parsed.contacts)
            result["facts"] = len(parsed.facts)
            result["faqs"] = len(parsed.faqs)
            result["persona_generated"] = persona_result["success"]
            result["persona_words"] = persona_result["word_count"]
            result["persona_cost"] = persona_result["cost_usd"]
            result["total_cost_usd"] = (
                result["extraction_cost"] + result["persona_cost"]
            )

            self._last_parsed = parsed
            self._last_persona = persona_result

            result["success"] = True
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("HDS-v3 ingest failed")
            result["error"] = str(e)[:300]
            return result

    async def _match_images(
        self, parsed: ParseResult, media_pages: list
    ) -> tuple[int, int]:
        """Run filter + match + HEAD-validate, attach URLs to products.

        Returns (products_with_primary_image, total_candidate_images).
        """
        from app.core.extractors.hds_v3.image_matcher import ImageMatcher
        from app.core.extractors.hds_v3.image_validator import ImageValidator

        matcher = ImageMatcher()
        validator = ImageValidator()

        # 1) Pre-filter junk per page, collect surviving streams.
        filtered_streams: list[list] = []
        total_candidates = 0
        for mp in media_pages:
            stream = getattr(mp, "media_stream", None) or []
            kept: list = []
            for item in stream:
                if item.item_type != "image":
                    kept.append(item)
                    continue
                total_candidates += 1
                if validator.pre_filter(item):
                    kept.append(item)
            if kept:
                filtered_streams.append(kept)

        if not filtered_streams:
            return 0, total_candidates

        # 2) Match images per page, accumulate.
        product_names = [p.name for p in parsed.products if p.name]
        all_matches = []
        for stream in filtered_streams:
            all_matches.extend(matcher.match_images(stream, product_names))

        # 3) HEAD-validate deterministic matches in parallel.
        if not all_matches:
            return 0, total_candidates

        import httpx

        urls_to_validate = list({m.image_url for m in all_matches})
        async with httpx.AsyncClient(
            timeout=validator.HEAD_TIMEOUT_SEC, follow_redirects=True
        ) as client:
            head_results = await asyncio.gather(
                *(validator.head_validate(u, client=client) for u in urls_to_validate),
                return_exceptions=True,
            )
        valid_urls = {
            u for u, ok in zip(urls_to_validate, head_results) if ok is True
        }

        valid_matches = [m for m in all_matches if m.image_url in valid_urls]
        if not valid_matches:
            return 0, total_candidates

        # 4) Group + attach to products.
        grouped = matcher.group_by_product(valid_matches)
        attached = 0
        for prod in parsed.products:
            g = grouped.get(prod.name)
            if not g:
                continue
            prod.image_url = g["primary"]
            prod.image_urls = list(g.get("secondary") or [])
            attached += 1
        return attached, total_candidates

    async def ingest_and_persist(
        self,
        base_url: str,
        company_id: UUID,
        session: "AsyncSession",
    ) -> dict:
        """Run `ingest()` then persist parsed result + persona to DB.

        Returns the ingest summary with a `persist` sub-dict (see
        `HDSv3Persistence.persist`). If ingest fails, persistence is
        skipped and `persist` key is absent.
        """
        ingest_result = await self.ingest(base_url, str(company_id))
        if not ingest_result.get("success"):
            return ingest_result
        if self._last_parsed is None:
            ingest_result["persist"] = {"error": "no_parsed_result"}
            return ingest_result

        from app.core.extractors.hds_v3.persistence import HDSv3Persistence

        persistence = HDSv3Persistence()
        persist_result = await persistence.persist(
            session=session,
            company_id=company_id,
            parse=self._last_parsed,
            persona=self._last_persona or {},
            source_url=base_url,
        )
        ingest_result["persist"] = persist_result

        # Reindex brain_chunks (Gemini embeddings) so the responder has fresh
        # RAG context. Non-fatal: a reindex failure must not break ingest.
        try:
            from app.core.extractors.hds_v3.indexer import HDSv3Indexer

            indexer = HDSv3Indexer()
            reindex_result = await indexer.reindex(session, company_id)
            ingest_result["reindex"] = reindex_result
        except Exception as e:  # noqa: BLE001
            logger.exception("Reindex failed (non-fatal)")
            ingest_result["reindex"] = {"error": str(e)[:200]}

        return ingest_result
