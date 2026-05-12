"""Heuristicky block detektor pre Universal Ingestion Engine v2 (Phase 2A, Layer B).

Cisto heuristika nad DOM-om (BeautifulSoup) — ziaden LLM, ziadna siet. Hlada
"bloky-kandidatov" (sekcie, opakovane karty, FAQ akordeony, footer/nav, ...) a
ku kazdemu vypocita lacne signaly (cena, CTA, kontakt, obrazky, ...), ktore
neskor Phase 2B pouzije na klasifikaciu.

Defensive: `detect_blocks` nikdy neraisne — pri chybe vracia (ciastocny) zoznam.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional  # noqa: F401  (vyhradene pre buduce typy)

from bs4 import BeautifulSoup, Tag

from app.core.ingest_v2.types import BlockSignals, BlockTypeHint

logger = logging.getLogger(__name__)

# Cena: "160 €", "1 200,50 EUR", "299 Kč", "1500 CZK" ...
PRICE_RE = re.compile(r"(\d+[\s.,]?\d*)\s?(€|EUR|eur|Kč|CZK)", re.IGNORECASE)
CTA_KEYWORDS = (
    "objednať", "rezervovať", "kúpiť", "viac info", "detail", "kontakt",
    "zobraziť", "order", "book", "buy", "add to cart",
)
QUESTION_RE = re.compile(r"\?\s*$", re.MULTILINE)
DATE_RE = re.compile(r"\d{1,2}\.\s?\d{1,2}\.\s?\d{4}")
CONTACT_RE = re.compile(r"\b(@|tel:|kontakt|email|telef[óo]n)", re.IGNORECASE)

# Minimalna dlzka textu, aby sme blok vobec brali do uvahy (filtruje prazdne/sumarne uzly).
_MIN_TEXT_SEMANTIC = 15  # section/article/footer/...
_MIN_TEXT_REPEATED = 5   # opakovane karty byvaju kratke (nazov + cena)


@dataclass
class DetectedBlock:
    block_type_hint: str               # jedna z hodnot BlockTypeHint
    selector: str                      # priblizny CSS path
    section_heading: str | None
    text: str
    html_snippet: str                  # prvych ~2000 znakov HTML bloku (evidence)
    headings: list[dict]               # [{level, text}]
    images: list[dict]                 # [{src, alt}]
    links: list[dict]                  # [{href, text}]
    signals: BlockSignals
    position_index: int
    depth: int
    parent_selector: str | None
    confidence: float


# ---------------------------------------------------------------------------
# Pomocne funkcie
# ---------------------------------------------------------------------------
def _build_selector(el: Tag) -> str:
    """Postavi priblizny CSS path, napr. 'section.products > div.card:nth-of-type(3)'."""
    parts: list[str] = []
    current: Optional[Tag] = el
    while current is not None and getattr(current, "name", None) and current.name != "[document]" and len(parts) < 6:
        sel = current.name
        cls = current.get("class") if isinstance(current, Tag) else None
        if cls:
            cls_token = ".".join(c for c in list(cls)[:3] if c)
            if cls_token:
                sel += f".{cls_token}"
        # nth-of-type ak ma sfor. súrodencov rovnaky tag + classy
        parent = current.parent if isinstance(current, Tag) else None
        if isinstance(parent, Tag):
            try:
                same = [
                    s for s in parent.find_all(current.name, recursive=False)
                    if isinstance(s, Tag) and s.get("class") == current.get("class")
                ]
                if len(same) > 1 and current in same:
                    idx = same.index(current) + 1
                    sel += f":nth-of-type({idx})"
            except Exception:
                pass
        parts.append(sel)
        current = parent
        if current is None or not isinstance(current, Tag):
            break
    return " > ".join(reversed(parts[:6]))


def _section_heading_for(el: Tag) -> str | None:
    """Najde najblizsiu obalovu section/article/main a jej prvy nadpis."""
    for parent in el.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in ("section", "article", "main"):
            h = parent.find(["h1", "h2", "h3"])
            if h:
                return h.get_text(strip=True)[:200]
            break
    return None


def _class_tokens(el: Tag) -> list[str]:
    cls = el.get("class") or []
    return [c.lower() for c in list(cls) if c][:10]


def _build_signals(el: Tag, full_text: str) -> BlockSignals:
    """Vypocita lacne heuristicke signaly pre blok."""
    price_matches = PRICE_RE.findall(full_text)

    images = el.find_all("img")
    image_count = len(images)

    cta_texts: list[str] = []
    for btn in el.find_all(["button", "a"]):
        bt = btn.get_text(strip=True).lower()
        for kw in CTA_KEYWORDS:
            if kw in bt:
                cta_texts.append(btn.get_text(strip=True)[:80])
                break

    links = el.find_all("a", href=True)
    link_count = len(links)

    return BlockSignals(
        has_price=len(price_matches) > 0,
        price_count=len(price_matches),
        price_patterns=[" ".join(m).strip() for m in price_matches[:10]],
        has_image=image_count > 0,
        image_count=image_count,
        has_cta=len(cta_texts) > 0,
        cta_texts=cta_texts[:10],
        has_contact=bool(CONTACT_RE.search(full_text)),
        has_date=bool(DATE_RE.search(full_text)),
        has_question=bool(QUESTION_RE.search(full_text)),
        repeated_structure_count=0,  # doplni sa neskor
        section_heading=None,
        class_tokens=_class_tokens(el),
        link_count=link_count,
        text_length=len(full_text),
    )


def _classify_block_hint(el: Tag, signals: BlockSignals, repeated_count: int) -> str:
    """Klasifikuje typ bloku podla tagu, classy a signalov."""
    class_str = " ".join(_class_tokens(el))
    tag = el.name

    # Footer / nav / header
    if tag == "footer":
        return BlockTypeHint.FOOTER_CANDIDATE.value
    if tag in ("nav", "header"):
        return BlockTypeHint.HEADER_NAV_CANDIDATE.value

    # Hero / banner
    if "hero" in class_str or "banner" in class_str:
        return BlockTypeHint.HERO_CANDIDATE.value

    # FAQ
    if "faq" in class_str or "q&a" in class_str or signals.has_question:
        return BlockTypeHint.FAQ_CANDIDATE.value

    # Kontakt
    if "contact" in class_str or signals.has_contact:
        if signals.text_length < 2000:
            return BlockTypeHint.CONTACT_CANDIDATE.value

    # Cennik
    if "price" in class_str or "cennik" in class_str or "pricing" in class_str:
        return BlockTypeHint.PRICING_CANDIDATE.value

    # Galeria
    if "gallery" in class_str or "galeria" in class_str:
        return BlockTypeHint.GALLERY_CANDIDATE.value

    # Article / blog post
    if tag == "article" or "article" in class_str or "post" in class_str or "blog" in class_str:
        return BlockTypeHint.ARTICLE_CANDIDATE.value

    # O nas
    if "about" in class_str or "o-nas" in class_str or "about-us" in class_str:
        return BlockTypeHint.ABOUT_CANDIDATE.value

    # Tabulka
    if el.find("table"):
        return BlockTypeHint.TABLE_CANDIDATE.value

    # Opakovana karta
    if repeated_count >= 3 and (
        "card" in class_str or "item" in class_str or "product" in class_str or signals.has_price
    ):
        return BlockTypeHint.REPEATED_CARD_CANDIDATE.value

    # Sekcia (default pre <section>/<main>/<article>)
    if tag in ("section", "main", "article"):
        return BlockTypeHint.SECTION_CANDIDATE.value

    if signals.has_price or signals.image_count > 0:
        return BlockTypeHint.CANDIDATE_CARD.value

    return BlockTypeHint.UNKNOWN.value


def _headings_of(el: Tag, limit: int) -> list[dict]:
    out: list[dict] = []
    for h in el.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])[:limit]:
        try:
            out.append({"level": int(h.name[1]), "text": h.get_text(strip=True)[:200]})
        except Exception:
            continue
    return out


def _images_of(el: Tag, limit: int) -> list[dict]:
    return [
        {"src": img.get("src") or img.get("data-src"), "alt": img.get("alt")}
        for img in el.find_all("img")[:limit]
    ]


def _links_of(el: Tag, limit: int) -> list[dict]:
    return [
        {"href": a.get("href"), "text": a.get_text(strip=True)[:100]}
        for a in el.find_all("a", href=True)[:limit]
    ]


# ---------------------------------------------------------------------------
# Hlavny vstup
# ---------------------------------------------------------------------------
def detect_blocks(html: str, max_blocks: int = 200) -> list[DetectedBlock]:
    """Najde bloky-kandidatov v HTML. Cisto heuristika (ziaden LLM).

    Vrati max `max_blocks` DetectedBlock objektov. Defensive — pri chybe vracia
    (ciastocny) zoznam.
    """
    out: list[DetectedBlock] = []
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        position_index = 0

        # 1. Semanticke bloky — section / article / main / footer / nav / header / aside
        for tag_name in ("section", "article", "main", "footer", "nav", "header", "aside"):
            for el in soup.find_all(tag_name)[:30]:
                if len(out) >= max_blocks:
                    break
                text = el.get_text(separator=" ", strip=True)
                if len(text) < _MIN_TEXT_SEMANTIC:
                    continue
                signals = _build_signals(el, text)
                signals.section_heading = _section_heading_for(el)
                hint = _classify_block_hint(el, signals, 0)

                parent = el.parent
                out.append(DetectedBlock(
                    block_type_hint=hint,
                    selector=_build_selector(el),
                    section_heading=signals.section_heading,
                    text=text[:5000],
                    html_snippet=str(el)[:2000],
                    headings=_headings_of(el, 10),
                    images=_images_of(el, 20),
                    links=_links_of(el, 30),
                    signals=signals,
                    position_index=position_index,
                    depth=len(list(el.parents)),
                    parent_selector=(
                        _build_selector(parent)
                        if isinstance(parent, Tag) and parent.name != "[document]"
                        else None
                    ),
                    confidence=0.60,
                ))
                position_index += 1

        # 2. Opakovane karty — skupiny podobnych surodencov
        for parent in soup.find_all(["div", "ul", "section", "main"]):
            if len(out) >= max_blocks:
                break
            if not isinstance(parent, Tag):
                continue
            groups: dict[tuple, list[Tag]] = {}
            for child in parent.children:
                if not isinstance(child, Tag):
                    continue
                cls = tuple(sorted((child.get("class") or [])[:3]))
                key = (child.name, cls)
                groups.setdefault(key, []).append(child)

            for _key, siblings in groups.items():
                if len(siblings) < 3:
                    continue
                if len(out) + len(siblings) > max_blocks:
                    break
                for child in siblings[:20]:
                    text = child.get_text(separator=" ", strip=True)
                    if len(text) < _MIN_TEXT_REPEATED:
                        continue
                    signals = _build_signals(child, text)
                    signals.repeated_structure_count = len(siblings)
                    signals.section_heading = _section_heading_for(child)
                    hint = _classify_block_hint(child, signals, len(siblings))
                    if hint == BlockTypeHint.UNKNOWN.value:
                        continue  # preskoc sum

                    out.append(DetectedBlock(
                        block_type_hint=hint,
                        selector=_build_selector(child),
                        section_heading=signals.section_heading,
                        text=text[:5000],
                        html_snippet=str(child)[:2000],
                        headings=_headings_of(child, 5),
                        images=_images_of(child, 10),
                        links=_links_of(child, 20),
                        signals=signals,
                        position_index=position_index,
                        depth=len(list(child.parents)),
                        parent_selector=_build_selector(parent),
                        confidence=0.70,  # vyssia istota pri opakovanej strukture
                    ))
                    position_index += 1

        # 3. FAQ akordeony — <details> bloky
        for det in soup.find_all("details")[:30]:
            if len(out) >= max_blocks:
                break
            summary = det.find("summary")
            q = summary.get_text(strip=True) if summary else ""
            text = det.get_text(separator=" ", strip=True)
            if not q or not text:
                continue
            signals = _build_signals(det, text)
            signals.has_question = True
            out.append(DetectedBlock(
                block_type_hint=BlockTypeHint.FAQ_CANDIDATE.value,
                selector=_build_selector(det),
                section_heading=None,
                text=text[:5000],
                html_snippet=str(det)[:2000],
                headings=[],
                images=[],
                links=[],
                signals=signals,
                position_index=position_index,
                depth=len(list(det.parents)),
                parent_selector=None,
                confidence=0.85,
            ))
            position_index += 1

    except Exception as e:  # pragma: no cover - defensive
        logger.warning("detect_blocks error: %s", e)
        return out  # vrat ciastocny vysledok

    return out[:max_blocks]
