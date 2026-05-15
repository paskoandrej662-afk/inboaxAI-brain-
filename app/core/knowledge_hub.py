from __future__ import annotations

import json as _json
import logging
import re
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from openai import AsyncOpenAI
from sqlalchemy import bindparam, select, text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.chunker import Chunk, chunk_text, compute_hash, normalize_text
from app.core.embeddings import embed_batch
from app.core.extractor import (
    ExtractedFact,
    ExtractedFaq,
    extract_facts,
    extract_faqs,
    extract_text,
)
from app.core.extractors.classifier import heuristic_classify
from app.core.scraper import (
    ScrapedPage,
    discover_pages as legacy_discover_pages,
    scrape_site,
)

if TYPE_CHECKING:
    from app.core.browser import BrowserPool
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
    # Phase 1C additions (optional, backward compat)
    pages_visited: int = 0
    pages_succeeded: int = 0
    products_inserted: int = 0
    images_inserted: int = 0
    total_llm_cost_usd: float = 0.0
    extraction_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chunks": self.chunks_inserted,
            "chunks_skipped": self.chunks_skipped,
            "chunks_superseded": self.chunks_superseded,
            "facts": self.facts_inserted,
            "faqs": self.faqs_inserted,
            "pages_scraped": self.pages_scraped,
            "pages_failed": self.pages_failed,
            "errors": self.errors[:5],
        }
        if self.pages_visited:
            out["pages_visited"] = self.pages_visited
        if self.pages_succeeded:
            out["pages_succeeded"] = self.pages_succeeded
        if self.products_inserted:
            out["products"] = self.products_inserted
        if self.images_inserted:
            out["images"] = self.images_inserted
        if self.total_llm_cost_usd:
            out["llm_cost_usd"] = round(self.total_llm_cost_usd, 4)
        if self.extraction_run_id:
            out["extraction_run_id"] = self.extraction_run_id
        return out


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


# ───────────────────────────────────────────────────
# Public entry point — dispatcher
# ───────────────────────────────────────────────────


async def ingest_url(
    company_id: uuid.UUID, url: str, job_id: str, max_pages: int = 30
) -> IngestResult:
    """Run the full ingestion pipeline for one start URL. Updates brain_jobs in real time.

    Dispatches to vision-based pipeline if VISION_INGEST_ENABLED, else legacy text-only path.
    """
    if not settings.VISION_INGEST_ENABLED:
        return await _ingest_url_legacy(company_id, url, job_id, max_pages)

    return await _ingest_url_vision(company_id, url, job_id, max_pages)


# ───────────────────────────────────────────────────
# Hybrid page discovery (legacy + JS-rendered)
# ───────────────────────────────────────────────────


def _prioritize_urls(
    urls: list[str], start_domain: str, max_pages: int
) -> list[str]:
    """Same-domain filter + priority sort by URL heuristics + max_pages cap.

    - score = heuristic_classify(url):
        - other_low_value (legal/cookies/gdpr) → 0.0
        - known type → base score (home=0.85, faq=0.95, ...)
        - None (unknown) → 0.5
    - Same-domain only (with www. normalization).
    - Dedupe case-insensitive po normalizácii (lower netloc, bez fragmentu, bez trailing /).
    - Sort desc by score, tie-break alphabetically; vrátime prvých max_pages.
    """
    if not urls:
        return []

    seen: set[str] = set()
    candidates: list[tuple[float, str]] = []

    for u in urls:
        if not u or not isinstance(u, str):
            continue

        try:
            parsed = urlparse(u)
        except Exception:
            continue

        if parsed.netloc and parsed.netloc != start_domain:
            if parsed.netloc.lstrip("www.") != start_domain.lstrip("www."):
                continue

        if parsed.scheme and parsed.scheme not in ("http", "https"):
            continue

        normalized = (
            (parsed.scheme or "https")
            + "://"
            + (parsed.netloc or start_domain).lower()
            + (parsed.path.rstrip("/") or "/")
        )
        if parsed.query:
            normalized += "?" + parsed.query

        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)

        h = heuristic_classify(normalized)
        if h is None:
            score = 0.5
        else:
            page_type, base_score = h
            score = 0.0 if page_type == "other_low_value" else base_score

        candidates.append((score, normalized))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    return [url for _, url in candidates[:max_pages]]


async def _discover_pages_hybrid(
    start_url: str,
    browser: "BrowserPool",
    max_pages: int,
) -> list[str]:
    """Hybrid page discovery: legacy (requests+BS4) + JS-rendered (Playwright).

    1. legacy_discover_pages — lacný baseline, JS-rendered linky nevidí.
    2. browser.discover_links(start_url) — JS DOM, doplní dynamické linky.
    3. Pre top-3 listing/pricing stránky urobí 1-hop browser.discover_links() pre detail linky.
    4. Union → priorita → cap na max_pages.

    Defenzívne: žiadny error neraisuje, vždy vráti aspoň [start_url].
    """
    try:
        start_domain = urlparse(start_url).netloc.lower()
    except Exception:
        start_domain = ""

    all_urls: list[str] = [start_url]

    # Step 1: Legacy discovery (cheap baseline; over-fetch — prioritize later)
    try:
        legacy = await legacy_discover_pages(start_url, max_pages=max_pages * 3)
        if legacy:
            all_urls.extend(legacy)
            logger.info("hybrid discovery legacy: +%d urls", len(legacy))
    except Exception as e:
        logger.warning("legacy discovery failed: %s", e)

    # Step 2: Browser-rendered discovery on start URL
    try:
        js_links = await browser.discover_links(start_url)
        if js_links:
            all_urls.extend(js_links)
            logger.info("hybrid discovery browser(start): +%d urls", len(js_links))
    except Exception as e:
        logger.warning("browser discover_links(start) failed: %s", e)

    # Step 3: 1-hop discovery from top listing/pricing pages
    prioritized_so_far = _prioritize_urls(all_urls, start_domain, max_pages * 3)
    listing_urls: list[str] = []
    for u in prioritized_so_far[:10]:
        h = heuristic_classify(u)
        if h is not None and h[0] in {"product_listing", "service_pricing"}:
            listing_urls.append(u)
        if len(listing_urls) >= 3:
            break

    for listing_url in listing_urls:
        try:
            sub_links = await browser.discover_links(listing_url)
            if sub_links:
                all_urls.extend(sub_links)
                logger.info(
                    "hybrid discovery browser(%s): +%d urls",
                    listing_url,
                    len(sub_links),
                )
        except Exception as e:
            logger.warning("browser discover_links(%s) failed: %s", listing_url, e)

    final = _prioritize_urls(all_urls, start_domain, max_pages)
    logger.info(
        "hybrid discovery final: %d urls (start_domain=%s)", len(final), start_domain
    )
    return final


# ───────────────────────────────────────────────────
# New vision-based pipeline (Phase 1C)
# ───────────────────────────────────────────────────


async def _ingest_url_vision(
    company_id: uuid.UUID, url: str, job_id: str, max_pages: int = 30
) -> IngestResult:
    """Vision-based ingest using BrowserPool + html_structured + classifier + vision + verification + merger."""
    # Lazy imports — keep the legacy path lean and avoid Playwright import on cold legacy fallback.
    from app.core.browser import BrowserPool
    from app.core.extractors.classifier import classify_page
    from app.core.extractors.html_structured import (
        extract_contact_patterns,
        extract_image_refs,
        extract_jsonld_business,
        extract_jsonld_faq,
        extract_jsonld_products,
    )
    from app.core.extractors.merger import (
        merge_facts,
        merge_faqs,
        merge_images,
        merge_products,
    )
    from app.core.extractors.verification import verify_fact, verify_product
    from app.core.extractors.vision import (
        extract_page_with_tiled_vision,
        extract_page_with_vision,
    )

    result = IngestResult()

    try:
        await _update_progress(job_id, 0, "running")

        # 1. Init browser FIRST (discovery teraz potrebuje browser pre JS-rendered linky)
        browser = BrowserPool()
        try:
            await browser.start()
        except Exception as e:
            logger.warning(
                "BrowserPool start failed: %s — falling back to legacy ingest", e
            )
            return await _ingest_url_legacy(company_id, url, job_id, max_pages)

        # 2. Hybrid discovery (legacy + JS-rendered)
        try:
            pages = await _discover_pages_hybrid(url, browser, max_pages)
        except Exception as e:
            logger.warning(
                "hybrid discover_pages failed: %s — using only start URL", e
            )
            pages = [url]

        if not pages:
            pages = [url]

        total = len(pages)
        logger.info("ingest_url: discovered %d pages for %s (hybrid)", total, url)

        all_products: list = []
        all_facts: list = []
        all_faqs: list = []
        all_images: list = []
        all_chunks_text_tuples: list[tuple[str, str, str]] = []
        total_cost_usd = 0.0

        try:
            for i, page_url in enumerate(pages):
                try:
                    await _update_progress(job_id, int((i / total) * 90))

                    # 1. Render page (lightweight najprv — precitame HTML + rozhodneme tile vs single)
                    rendered_basic = await browser.render_page(
                        page_url, take_screenshot=False
                    )
                    if rendered_basic.error:
                        result.errors.append(
                            f"render {page_url}: {rendered_basic.error}"
                        )
                        result.pages_failed += 1
                        continue
                    result.pages_visited += 1

                    # 2. Raw text + klasifikacia
                    try:
                        content = extract_text(
                            rendered_basic.html, rendered_basic.final_url
                        )
                        raw_text = content.text or ""
                        title = content.title or ""
                        section_hint = content.section
                    except Exception:
                        raw_text = ""
                        title = ""
                        section_hint = "general"

                    try:
                        page_type, value_score = await classify_page(
                            page_url, title, raw_text[:600]
                        )
                    except Exception as e:
                        logger.warning(
                            "classify_page failed for %s: %s", page_url, e
                        )
                        page_type, value_score = "other_low_value", 0.3

                    if page_type == "other_low_value" and value_score < 0.2:
                        logger.info("skipping low-value page: %s", page_url)
                        continue

                    # 3. HTML structured extraction (lacne, deterministicke)
                    html_products = extract_jsonld_products(
                        rendered_basic.html, rendered_basic.final_url
                    )
                    html_faqs = extract_jsonld_faq(
                        rendered_basic.html, rendered_basic.final_url
                    )
                    html_business = extract_jsonld_business(
                        rendered_basic.html, rendered_basic.final_url
                    )
                    html_business += extract_contact_patterns(
                        raw_text, rendered_basic.final_url
                    )
                    html_images = extract_image_refs(
                        rendered_basic.html, rendered_basic.final_url
                    )

                    # 4. Vision extraction — TILED pre vision-worthy stranky
                    use_vision = (
                        page_type
                        in {
                            "product_listing",
                            "product_detail",
                            "service_pricing",
                            "faq",
                            "home",
                            "about",
                            "contact",
                        }
                        and total_cost_usd < settings.INGEST_LLM_COST_CAP_USD
                    )

                    vp: list = []
                    vf: list = []
                    vfq: list = []
                    vi: list = []
                    summary = ""
                    cost = 0.0
                    if use_vision:
                        try:
                            # NEW: tiled rendering + tiled vision
                            tiled = await browser.render_page_tiled(page_url)

                            # === HDS-Lite primary extraction (2B-8) ===
                            # Skus deterministicku DOM-segmentation engine ako prvy.
                            # Ak uspeje, zoberieme HDS produkty a vision pipeline preskocime
                            # pre products (vision sa stale moze pouzit pre facts/faqs/images).
                            hds_products: list = []
                            hds_success = False
                            try:
                                if tiled.segments and tiled.html:
                                    from app.core.extractors.hds.engine import (
                                        run_hds_extraction,
                                    )
                                    from app.core.extractors.types import (
                                        ExtractedProduct,
                                    )

                                    middle_idx = len(tiled.segments) // 2
                                    middle_screenshot = tiled.segments[middle_idx]
                                    hds_result = await run_hds_extraction(
                                        html=tiled.html,
                                        screenshot_bytes=middle_screenshot,
                                        page=None,  # Playwright Page nie je v scope; visibility best-effort
                                        page_url=page_url,
                                    )
                                    logger.info(
                                        "hds_extraction %s: success=%s cards=%d seeds=%d "
                                        "lcas=%d candidates=%d cost=$%.4f reason=%s",
                                        page_url,
                                        hds_result.success,
                                        len(hds_result.cards),
                                        hds_result.seeds_found,
                                        hds_result.lcas_found,
                                        hds_result.candidate_count,
                                        hds_result.sonnet_cost_usd,
                                        hds_result.fallback_reason,
                                    )
                                    total_cost_usd += hds_result.sonnet_cost_usd
                                    if (
                                        hds_result.success
                                        and len(hds_result.cards) >= 3
                                    ):
                                        hds_products = [
                                            ExtractedProduct(
                                                name=c.name,
                                                price_eur=c.price_eur,
                                                price_text=c.price_text,
                                                price_unit=None,
                                                attributes=c.attributes or {},
                                                source_url=tiled.final_url or page_url,
                                                source_block_text="",
                                                source_type="hds",
                                                confidence=c.confidence,
                                                verified=False,
                                            )
                                            for c in hds_result.cards
                                        ]
                                        hds_success = True
                                        vp = hds_products
                            except Exception as e:
                                logger.warning(
                                    "HDS extraction failed for %s: %s — falling back to vision",
                                    page_url,
                                    e,
                                )

                            if not hds_success and tiled.segments:
                                logger.info(
                                    "using tiled vision for %s (scrollHeight=%d, %d segments)",
                                    page_url,
                                    tiled.scroll_height,
                                    len(tiled.segments),
                                )
                                (
                                    vp,
                                    vf,
                                    vfq,
                                    vi,
                                    summary,
                                    cost,
                                ) = await extract_page_with_tiled_vision(
                                    tiled, page_type, raw_text
                                )
                            elif not hds_success and not tiled.segments:
                                # Fallback na legacy single screenshot
                                logger.warning(
                                    "tiled returned 0 segments for %s, fallback to single",
                                    page_url,
                                )
                                rendered_single = await browser.render_page(
                                    page_url, take_screenshot=True
                                )
                                if rendered_single.screenshot_png:
                                    (
                                        vp,
                                        vf,
                                        vfq,
                                        vi,
                                        summary,
                                        cost,
                                    ) = await extract_page_with_vision(
                                        rendered_single, page_type, raw_text
                                    )
                        except Exception as e:
                            logger.warning(
                                "vision extraction failed for %s: %s", page_url, e
                            )

                        total_cost_usd += cost

                        # Verify vision-extracted fakty (anti-halucinacia, reuse existing)
                        vp = [verify_product(p, raw_text) for p in vp]
                        verified_facts = []
                        for f in vf:
                            vf2 = verify_fact(f, raw_text)
                            if vf2 is not None:
                                verified_facts.append(vf2)
                        vf = verified_facts

                    # 5. Merge per-page (JSON-LD + vision)
                    page_products = merge_products(html_products, vp)
                    page_facts = merge_facts(html_business, vf)
                    page_faqs = merge_faqs(html_faqs, vfq)

                    all_products.extend(page_products)
                    all_facts.extend(page_facts)
                    all_faqs.extend(page_faqs)
                    all_images.extend(html_images + vi)

                    if raw_text:
                        all_chunks_text_tuples.append(
                            (raw_text, rendered_basic.final_url, section_hint)
                        )

                    result.pages_succeeded += 1

                except Exception as e:
                    logger.exception(
                        "page processing error for %s: %s", page_url, e
                    )
                    result.errors.append(f"page {page_url}: {str(e)[:200]}")
                    result.pages_failed += 1

        finally:
            await browser.close()

        # Cross-page dedupe
        all_products = merge_products(all_products)
        all_facts = merge_facts(all_facts)
        all_faqs = merge_faqs(all_faqs)
        all_images = merge_images(all_images)

        # Backward-compat: legacy field used by callers / dashboards
        result.pages_scraped = result.pages_succeeded
        result.total_llm_cost_usd = total_cost_usd

        logger.info(
            "ingest_url done: %d products, %d facts, %d faqs, %d images, cost=$%.4f",
            len(all_products),
            len(all_facts),
            len(all_faqs),
            len(all_images),
            total_cost_usd,
        )

        await _update_progress(job_id, 92)

        # Persistence
        if settings.KNOWLEDGE_REVIEW_ENABLED:
            run_id = await _persist_to_extraction_runs(
                company_id,
                job_id,
                url,
                all_products,
                all_facts,
                all_faqs,
                all_images,
                total_cost_usd,
                result,
            )
            result.extraction_run_id = run_id
        else:
            await _persist_to_brain_tables(
                company_id,
                all_products,
                all_facts,
                all_faqs,
                all_chunks_text_tuples,
                result,
            )

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
        logger.exception("ingest_url (vision) failed for %s", url)
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


# ───────────────────────────────────────────────────
# Persistence helpers
# ───────────────────────────────────────────────────


def _normalize_subject(key: str, value: str) -> str:
    """Normalize fact subject for upsert key. Mirrors legacy extractor.extract_facts behaviour."""
    v = (value or "").strip()
    if not v:
        return ""
    if key == "phone":
        return re.sub(r"\D", "", v)
    if key == "email":
        return v.lower()
    if key == "ico":
        return re.sub(r"\D", "", v)
    if key == "iban":
        return re.sub(r"\s+", "", v).upper()
    return v[:120].lower()


def _serialize_product_text(p: Any) -> str:
    """Render a product into a single chunk-friendly text body."""
    parts: list[str] = [str(p.name).strip()]
    if p.description:
        parts.append(str(p.description).strip())
    if p.price_text:
        parts.append(f"Cena: {p.price_text}")
    if p.price_unit and p.price_unit != "neuvedene":
        parts.append(f"Jednotka: {p.price_unit}")
    if p.attributes:
        for k, v in p.attributes.items():
            parts.append(f"{k}: {v}")
    if p.image_url:
        parts.append(f"Obrazok: {p.image_url}")
    return "\n".join(parts).strip()


async def _persist_to_brain_tables(
    company_id: uuid.UUID,
    products: list,
    facts: list,
    faqs: list,
    chunks_text_tuples: list[tuple[str, str, str]],
    result: IngestResult,
) -> None:
    """Direct save to brain_chunks/brain_facts/brain_faqs (legacy production path)."""
    # 1) Build product chunks
    product_chunk_specs: list[tuple[str, str, str, str, dict]] = []
    # (text, content_hash, source_url, section, meta)
    for p in products:
        body = _serialize_product_text(p)
        if not body or len(body) < 5:
            continue
        text_norm = normalize_text(body)
        h = compute_hash(text_norm)
        meta = {
            "kind": "product",
            "name": p.name,
            "source_type": getattr(p, "source_type", None),
            "verified": bool(getattr(p, "verified", False)),
            "confidence": float(getattr(p, "confidence", 0.0) or 0.0),
        }
        if p.price_eur is not None:
            meta["price_eur"] = float(p.price_eur)
        if p.price_unit:
            meta["price_unit"] = p.price_unit
        if p.attributes:
            meta["attributes"] = p.attributes
        if p.image_url:
            meta["image_url"] = p.image_url
        product_chunk_specs.append(
            (text_norm, h, p.source_url or "", "products", meta)
        )

    # 2) Build prose chunks from raw page text
    prose_chunk_specs: list[tuple[str, str, str, str, dict]] = []
    for raw_text, src_url, section_hint in chunks_text_tuples:
        if not raw_text or len(raw_text) < 50:
            continue
        try:
            chunks = chunk_text(
                raw_text,
                target_size=800,
                overlap=100,
                min_size=100,
                max_size=1500,
            )
        except Exception as e:
            logger.warning("chunk_text failed for %s: %s", src_url, e)
            continue
        for ch in chunks:
            prose_chunk_specs.append(
                (
                    ch.text,
                    ch.content_hash,
                    src_url,
                    section_hint or "general",
                    {"char_count": ch.char_count, "paragraph_idx": ch.paragraph_idx},
                )
            )

    all_specs = product_chunk_specs + prose_chunk_specs

    # 3) Dedupe vs DB by content_hash
    new_specs: list[tuple[str, str, str, str, dict]] = []
    superseded_old: list[uuid.UUID] = []
    if all_specs:
        async with AsyncSessionLocal() as session:
            hashes = list({h for _, h, _, _, _ in all_specs})
            stmt = select(BrainChunk.content_hash).where(
                BrainChunk.company_id == company_id,
                BrainChunk.content_hash.in_(hashes),
                BrainChunk.superseded_at.is_(None),
            )
            existing = set((await session.execute(stmt)).scalars().all())

            url_to_new_hashes: dict[str, set[str]] = {}
            for _, h, src_url, _, _ in all_specs:
                if src_url:
                    url_to_new_hashes.setdefault(src_url, set()).add(h)

            if url_to_new_hashes:
                rows = (
                    await session.execute(
                        sa_text(
                            "SELECT id, source_url, content_hash FROM brain_chunks "
                            "WHERE company_id = :cid AND superseded_at IS NULL "
                            "AND source_url = ANY(:urls)"
                        ),
                        {
                            "cid": str(company_id),
                            "urls": list(url_to_new_hashes.keys()),
                        },
                    )
                ).all()
                for r in rows:
                    rid, src_url, old_hash = r[0], r[1], r[2]
                    if (
                        src_url in url_to_new_hashes
                        and old_hash not in url_to_new_hashes[src_url]
                    ):
                        superseded_old.append(rid)

        for spec in all_specs:
            if spec[1] not in existing:
                new_specs.append(spec)
        result.chunks_skipped = len(all_specs) - len(new_specs)

    # 4) Embed
    if new_specs:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        try:
            embeddings = await embed_batch([t for t, _, _, _, _ in new_specs], client)
        finally:
            await client.close()
        if len(embeddings) != len(new_specs):
            raise RuntimeError(
                f"embedding count mismatch: {len(embeddings)} vs {len(new_specs)} chunks"
            )
    else:
        embeddings = []

    # 5) Insert in transaction
    async with AsyncSessionLocal() as session:
        async with session.begin():
            if superseded_old:
                await session.execute(
                    sa_text(
                        "UPDATE brain_chunks SET superseded_at = now() "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": [str(i) for i in superseded_old]},
                )
                result.chunks_superseded = len(superseded_old)

            for (text, h, src_url, section, meta), emb in zip(new_specs, embeddings):
                obj = BrainChunk(
                    company_id=company_id,
                    text=text,
                    source_url=src_url or None,
                    source_type="web",
                    section=section,
                    content_hash=h,
                    embedding=emb,
                    meta=meta,
                )
                session.add(obj)
                result.chunks_inserted += 1
                if meta.get("kind") == "product":
                    result.products_inserted += 1

            # Facts: upsert
            for f in facts:
                value_str = (f.value or "").strip()
                if not value_str:
                    continue
                subject = _normalize_subject(f.key, value_str)
                evidence_value = value_str[:500]
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
                        "subject": subject,
                        "value": _json.dumps(
                            {"value": value_str, "source_type": f.source_type},
                            ensure_ascii=False,
                        ),
                        "evidence": evidence_value,
                        "source_url": f.source_url or None,
                        "confidence": float(f.confidence),
                    },
                )
                result.facts_inserted += 1

            # FAQs: dedupe by question
            if faqs:
                existing_q = (
                    await session.execute(
                        sa_text(
                            "SELECT question FROM brain_faqs WHERE company_id = :cid"
                        ),
                        {"cid": str(company_id)},
                    )
                ).scalars().all()
                existing_set = {q.strip().lower() for q in existing_q}

                seen_q: set[str] = set()
                fresh_faqs: list = []
                faq_texts: list[str] = []
                for faq in faqs:
                    norm_q = faq.question.strip().lower()
                    if not norm_q or norm_q in seen_q or norm_q in existing_set:
                        continue
                    seen_q.add(norm_q)
                    fresh_faqs.append(faq)
                    faq_texts.append(f"{faq.question}\n{faq.answer}")

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
                                source_url=faq.source_url or None,
                                embedding=fe,
                            )
                        )
                        result.faqs_inserted += 1


async def _persist_to_extraction_runs(
    company_id: uuid.UUID,
    job_id: str,
    source_url: str,
    products: list,
    facts: list,
    faqs: list,
    images: list,
    total_cost_usd: float,
    result: IngestResult,
) -> str | None:
    """Save to staging extraction_runs + per-entity tables (Phase 2/3 review UI).

    Best-effort: schema may not exist in all environments yet, so individual
    INSERTs are wrapped to avoid blowing up the entire pipeline.
    """
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                summary = {
                    "products": len(products),
                    "facts": len(facts),
                    "faqs": len(faqs),
                    "images": len(images),
                }
                row = (
                    await session.execute(
                        sa_text(
                            """
                            INSERT INTO extraction_runs (
                                company_id, job_id, source_type, source_url, status,
                                extracted_data, pages_visited, pages_succeeded,
                                pages_failed, total_llm_cost_usd, errors
                            )
                            VALUES (
                                :cid, :jid, 'web', :src, 'pending_review',
                                CAST(:summary AS jsonb), :pv, :ps,
                                :pf, :cost, CAST(:errors AS jsonb)
                            )
                            RETURNING id
                            """
                        ),
                        {
                            "cid": str(company_id),
                            "jid": job_id,
                            "src": source_url,
                            "summary": _json.dumps(summary, ensure_ascii=False),
                            "pv": result.pages_visited,
                            "ps": result.pages_succeeded,
                            "pf": result.pages_failed,
                            "cost": total_cost_usd,
                            "errors": _json.dumps(result.errors[:20], ensure_ascii=False),
                        },
                    )
                ).first()
                run_id = str(row[0]) if row else None

                if run_id is None:
                    return None

                for p in products:
                    await session.execute(
                        sa_text(
                            """
                            INSERT INTO extraction_products (
                                run_id, company_id, name, description, price_text,
                                price_eur, price_unit, attributes, image_url,
                                source_url, source_block_text, source_type,
                                confidence, verified
                            )
                            VALUES (
                                :rid, :cid, :name, :desc, :ptext,
                                :peur, :punit, CAST(:attrs AS jsonb), :img,
                                :src, :block, :stype,
                                :conf, :ver
                            )
                            """
                        ),
                        {
                            "rid": run_id,
                            "cid": str(company_id),
                            "name": p.name,
                            "desc": p.description,
                            "ptext": p.price_text,
                            "peur": p.price_eur,
                            "punit": p.price_unit,
                            "attrs": _json.dumps(p.attributes or {}, ensure_ascii=False),
                            "img": p.image_url,
                            "src": p.source_url,
                            "block": p.source_block_text,
                            "stype": p.source_type,
                            "conf": float(p.confidence),
                            "ver": bool(p.verified),
                        },
                    )

                for f in facts:
                    await session.execute(
                        sa_text(
                            """
                            INSERT INTO extraction_facts (
                                run_id, company_id, key, value,
                                source_url, source_type, confidence
                            )
                            VALUES (
                                :rid, :cid, :key, :value,
                                :src, :stype, :conf
                            )
                            """
                        ),
                        {
                            "rid": run_id,
                            "cid": str(company_id),
                            "key": f.key,
                            "value": f.value,
                            "src": f.source_url,
                            "stype": f.source_type,
                            "conf": float(f.confidence),
                        },
                    )

                for fq in faqs:
                    await session.execute(
                        sa_text(
                            """
                            INSERT INTO extraction_faqs (
                                run_id, company_id, question, answer,
                                source_url, source_type, confidence
                            )
                            VALUES (
                                :rid, :cid, :q, :a,
                                :src, :stype, :conf
                            )
                            """
                        ),
                        {
                            "rid": run_id,
                            "cid": str(company_id),
                            "q": fq.question,
                            "a": fq.answer,
                            "src": fq.source_url,
                            "stype": fq.source_type,
                            "conf": float(fq.confidence),
                        },
                    )

                for img in images:
                    await session.execute(
                        sa_text(
                            """
                            INSERT INTO extraction_images (
                                run_id, company_id, image_url, alt_text,
                                description, near_product_name, source_url
                            )
                            VALUES (
                                :rid, :cid, :url, :alt,
                                :desc, :near, :src
                            )
                            """
                        ),
                        {
                            "rid": run_id,
                            "cid": str(company_id),
                            "url": img.url,
                            "alt": img.alt_text,
                            "desc": img.description,
                            "near": img.near_product_name,
                            "src": img.source_url,
                        },
                    )

                result.products_inserted = len(products)
                result.images_inserted = len(images)

                return run_id
    except Exception as e:
        logger.exception("_persist_to_extraction_runs failed: %s", e)
        return None


# ───────────────────────────────────────────────────
# Legacy text-only pipeline (production safety net)
# ───────────────────────────────────────────────────


async def _ingest_url_legacy(
    company_id: uuid.UUID, url: str, job_id: str, max_pages: int = 30
) -> IngestResult:
    """Original text-only ingestion pipeline. Used when VISION_INGEST_ENABLED=False
    or as a fallback when BrowserPool fails to start."""
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
                if superseded_old:
                    await session.execute(
                        sa_text(
                            "UPDATE brain_chunks SET superseded_at = now() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": [str(i) for i in superseded_old]},
                    )
                    result.chunks_superseded = len(superseded_old)

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
                            "value": _json.dumps(f.value, ensure_ascii=False),
                            "evidence": f.evidence,
                            "source_url": f.source_url,
                            "confidence": f.confidence,
                        },
                    )
                    result.facts_inserted += 1

                if all_faqs:
                    seen_q: set[tuple[str, str]] = set()
                    existing_q = (
                        await session.execute(
                            sa_text(
                                "SELECT question FROM brain_faqs WHERE company_id = :cid"
                            ),
                            {"cid": str(company_id)},
                        )
                    ).scalars().all()
                    existing_set = {q.strip().lower() for q in existing_q}

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
