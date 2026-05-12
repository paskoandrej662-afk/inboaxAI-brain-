"""Orchestrator pre Universal Ingestion Engine v2 (Phase 2A) — main entry point.

`ingest_company_v2`: render + raw extrakcia + block detekcia + persistencia,
vsetko v ramci budgetu. Zero LLM. Pipeline je defensive — chyby per-page sa
zaznamenaju do `errors`/`warnings`, ale cely job nikdy nespadne kvoli jednej
stranke.

Volane bud z FastAPI BackgroundTasks (2A-3 testovanie) alebo neskor z Arq workera.
Job_id musi uz existovat (vytvori ho API cez `persistence.create_job`).
"""
from __future__ import annotations

import logging
import time
import uuid

from app.core.ingest_v2 import persistence as p
from app.core.ingest_v2 import raw_extraction as rx
from app.core.ingest_v2.block_detection import detect_blocks
from app.core.ingest_v2.budget import BudgetManager
from app.core.ingest_v2.crawler import CrawlerV2, DiscoveredPage
from app.core.ingest_v2.renderer import RendererV2
from app.core.ingest_v2.types import BudgetLimits, RawPageData
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _build_limits(mode: str, budget_eur: float) -> BudgetLimits:
    limits = BudgetLimits(
        hard_limit_eur=budget_eur,
        soft_limit_eur=max(0.5, budget_eur * 0.85),
        target_eur=max(0.3, budget_eur * 0.6),
    )
    if mode == "quick":
        limits.max_pages = 5
        limits.max_runtime_seconds = 90
    elif mode == "deep":
        limits.max_pages = 20
        limits.max_runtime_seconds = 300
    return limits


def _seed_pages(source_url: str) -> list[DiscoveredPage]:
    return [DiscoveredPage(
        url=source_url, discovery_method="seed", depth=0, parent_url=None, priority_score=0.85,
    )]


async def ingest_company_v2(
    job_id: uuid.UUID,
    company_id: uuid.UUID,
    source_url: str,
    mode: str = "standard",
    budget_eur: float = 1.20,
) -> dict:
    """Main entry point. Job_id musi uz existovat (vytvoreny cez API)."""
    start_ts = time.monotonic()
    limits = _build_limits(mode, budget_eur)
    budget = BudgetManager(limits)

    errors: list[dict] = []
    warnings: list[dict] = []
    pages_succeeded = 0
    pages_failed = 0
    total_blocks = 0

    # 1. status -> running
    async with AsyncSessionLocal() as session:
        await p.update_job_status(session, job_id, "running", progress=5)
        await session.commit()

    renderer = RendererV2()
    crawler = CrawlerV2(renderer=renderer, max_pages=limits.max_pages, max_depth=limits.max_depth)

    # 2. start renderer — ak zlyha, job je failed
    try:
        await renderer.start()
    except Exception as e:
        logger.exception("renderer.start failed: %s", e)
        async with AsyncSessionLocal() as session:
            await p.finalize_job(
                session, job_id, "failed",
                pages_visited=0, pages_succeeded=0, pages_failed=0,
                blocks_found=0, cost_total_eur=0.0,
                errors=[{"stage": "renderer_start", "error": str(e)[:500]}],
            )
            await session.commit()
        return {"status": "failed", "reason": "renderer_start_failed"}

    pages: list[DiscoveredPage] = []
    try:
        # 3. discovery
        try:
            pages = await crawler.discover_pages(source_url)
        except Exception as e:
            logger.warning("crawler.discover_pages failed: %s — fallback na seed", e)
            warnings.append({"stage": "discovery", "error": str(e)[:500]})
            pages = _seed_pages(source_url)
        if not pages:
            pages = _seed_pages(source_url)

        logger.info("ingest_v2: discovered %d pages for %s (mode=%s)", len(pages), source_url, mode)
        per_page_block_cap = max(10, limits.max_blocks // max(1, len(pages)))

        # 4. render + persist kazdej stranky
        for i, page in enumerate(pages):
            can, reason = budget.can_render_page()
            if not can:
                warnings.append({"stage": "budget", "reason": reason, "skipped_from_index": i})
                logger.info("ingest_v2: budget halt at page %d/%d (%s)", i, len(pages), reason)
                break

            page_start = time.monotonic()
            try:
                result = await renderer.render_page(page.url, take_screenshot=False)
            except Exception as e:
                pages_failed += 1
                errors.append({"stage": "render", "url": page.url, "error": str(e)[:500]})
                continue

            render_ms = int((time.monotonic() - page_start) * 1000)
            budget.record_render(page_count=1, render_ms=render_ms, html_bytes=result.dom_size)

            if result.render_status != "success":
                pages_failed += 1
                errors.append({
                    "stage": "render", "url": page.url,
                    "status": result.render_status, "error": result.error_message,
                })
                # ulozime aj neuspesny render (audit trail)
                async with AsyncSessionLocal() as session:
                    try:
                        await p.save_page(session, job_id, company_id, page, result, RawPageData())
                        await session.commit()
                    except Exception as e2:
                        logger.warning("save_page (failed render) error: %s", e2)
                        await session.rollback()
                continue

            # 4a. raw extrakcia (Layer A)
            try:
                raw_data = RawPageData(
                    headings=rx.extract_headings(result.html),
                    links=rx.extract_links(result.html, result.final_url),
                    images=rx.extract_images(result.html, result.final_url),
                    tables=rx.extract_tables(result.html),
                    lists=rx.extract_lists(result.html),
                    forms=rx.extract_forms(result.html),
                    json_ld=rx.extract_json_ld(result.html),
                    microdata=rx.extract_microdata(result.html),
                    meta=rx.extract_meta(result.html),
                    open_graph=rx.extract_open_graph(result.html),
                    pdfs=rx.extract_pdfs(result.html, result.final_url),
                    social_links=rx.extract_social_links(result.html, result.final_url),
                    contact_patterns=rx.extract_contact_patterns(result.visible_text),
                )
            except Exception as e:
                logger.warning("raw_extraction failed for %s: %s", page.url, e)
                raw_data = RawPageData()

            # 4b. block detekcia (Layer B)
            try:
                blocks = detect_blocks(result.html, max_blocks=per_page_block_cap)
            except Exception as e:
                logger.warning("detect_blocks failed for %s: %s", page.url, e)
                blocks = []

            budget.record_images(len(raw_data.images))

            # 4c. persist page + blocks
            async with AsyncSessionLocal() as session:
                try:
                    saved_page = await p.save_page(session, job_id, company_id, page, result, raw_data)
                    can_blocks, _ = budget.can_store_block()
                    if can_blocks and blocks:
                        inserted = await p.save_blocks(
                            session, job_id, company_id, saved_page.id, page.url, blocks
                        )
                        budget.record_blocks(inserted)
                        total_blocks += inserted
                    await session.commit()
                    pages_succeeded += 1
                except Exception as e:
                    await session.rollback()
                    pages_failed += 1
                    errors.append({"stage": "persist", "url": page.url, "error": str(e)[:500]})

            # progress update po kazdej stranke
            progress = min(95, 5 + int(85 * (i + 1) / max(1, len(pages))))
            async with AsyncSessionLocal() as session:
                await p.update_job_status(session, job_id, "running", progress=progress)
                await session.commit()

    finally:
        try:
            await renderer.close()
        except Exception as e:
            logger.warning("renderer.close error: %s", e)

    # 5. finalize
    total_pages_visited = pages_succeeded + pages_failed
    if pages_succeeded == 0:
        final_status = "failed"
    elif pages_failed == 0:
        final_status = "completed"
    else:
        final_status = "partial"

    try:
        budget_status = budget.status().model_dump()
    except Exception:
        budget_status = {}

    summary = {
        "pages_discovered": len(pages),
        "pages_visited": total_pages_visited,
        "pages_succeeded": pages_succeeded,
        "pages_failed": pages_failed,
        "blocks_found": total_blocks,
        "budget_status": budget_status,
        "runtime_seconds": round(time.monotonic() - start_ts, 2),
    }

    async with AsyncSessionLocal() as session:
        await p.finalize_job(
            session, job_id, final_status,
            pages_visited=total_pages_visited,
            pages_succeeded=pages_succeeded,
            pages_failed=pages_failed,
            blocks_found=total_blocks,
            cost_total_eur=budget.spent_eur,
            result_summary=summary,
            errors=errors,
            warnings=warnings,
        )
        await session.commit()

    logger.info(
        "ingest_v2 finished job=%s status=%s pages=%d blocks=%d runtime=%.1fs",
        job_id, final_status, total_pages_visited, total_blocks, summary["runtime_seconds"],
    )
    return {"status": final_status, "summary": summary}
