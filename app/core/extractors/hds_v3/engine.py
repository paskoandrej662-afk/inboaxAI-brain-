"""HDS-v3 Engine: full pipeline crawler -> gemini -> parse -> validate -> dedup -> persona.

DB persistence is deferred to a thin wrapper in knowledge_hub.py (Commit 3.1).
This engine exposes parsed + persona results via attributes for inspection.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.extractors.hds_v3.crawler import HDSCrawler
from app.core.extractors.hds_v3.dedup import Deduplicator
from app.core.extractors.hds_v3.gemini_client import GeminiClient
from app.core.extractors.hds_v3.parser import MarkdownParser, ParseResult
from app.core.extractors.hds_v3.persona_generator import PersonaGenerator
from app.core.extractors.hds_v3.validator import Validator

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

            gemini = GeminiClient()
            extract = await gemini.extract_pages(base_url, crawl.pages)
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
