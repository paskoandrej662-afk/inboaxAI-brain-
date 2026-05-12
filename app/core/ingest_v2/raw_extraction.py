"""Raw extraction (Layer A) pre Universal Ingestion Engine v2 (Phase 2A).

Cisto funkcionalny modul — ZIADEN stav, ZIADNY LLM, ZIADNA siet. Kazda funkcia
berie HTML alebo viditelny text + base_url a vracia Pydantic objekty z `types.py`
alebo jednoduche kontajnery (dict / list).

Defensive kontrakt: ZIADNA funkcia tu nesmie raisnut. Pri chybe parsovania vrati
prazdny vysledok (`[]` / `{}` / `""`) a zaloguje `debug`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional  # noqa: F401  (Any/Optional vyhradene pre buduce signatury)
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.ingest_v2.types import (
    ContactPatterns,
    HeadingItem,
    ImageCandidate,
    LinkItem,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Predkompilovane regexy (kompilujeme raz na import).
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE_SK_RE = re.compile(r'(?:\+421|\+420|00421|00420|0)\s?\d{2,3}[\s.-]?\d{3}[\s.-]?\d{3}\b')
ICO_RE = re.compile(r'I[ČC][\s.]*O[\s.]*:?\s*(\d{8})', re.IGNORECASE)
DIC_RE = re.compile(r'D[IÍ]?[ČC]\s*:?\s*(\d{10})', re.IGNORECASE)
IC_DPH_RE = re.compile(r'I[ČC]\s*DPH\s*:?\s*(SK\d{10})', re.IGNORECASE)
IBAN_RE = re.compile(r'\b[A-Z]{2}\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{0,4}\b')
ADDRESS_RE = re.compile(
    r'([A-ZŠČŤŽÝÁÍÉÚÔŇĎĽ][A-Za-zšščťžýáíéúôňďľ]+(?:\s+\d+[a-zA-Z]?)?(?:\s+[A-Za-zšščťžýáíéúôňďľ]+)*\s+\d+[a-zA-Z]?)\s*,?\s*(\d{3}\s?\d{2})\s+([A-ZŠČŤŽÝÁÍÉÚÔŇĎĽ][A-Za-zšščťžýáíéúôňďľ]+)'
)

SOCIAL_DOMAINS = (
    'facebook.com', 'instagram.com', 'tiktok.com', 'linkedin.com',
    'twitter.com', 'x.com', 'youtube.com',
)
MAP_DOMAINS = ('google.com/maps', 'maps.app.goo.gl', 'maps.google.', 'mapy.cz', 'mapy.sk')
ICON_FILENAME_HINTS = ('icon', 'logo', 'sprite', 'pixel', 'spacer', 'tracking')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _soup(html: str) -> BeautifulSoup:
    """BeautifulSoup s html.parser — tolerantny voci rozbitemu markup-u."""
    return BeautifulSoup(html or '', 'html.parser')


def _strip_www(netloc: str) -> str:
    """Odstrani 'www.' prefix z netloc (case-insensitive)."""
    low = (netloc or '').lower()
    return low[4:] if low.startswith('www.') else low


def _is_internal(url: str, base_netloc: str) -> bool:
    """True ak `url` patri na rovnaku domenu ako `base_netloc` (www. ekvivalencia)."""
    try:
        host = urlparse(url).netloc or base_netloc
        return _strip_www(host) == _strip_www(base_netloc)
    except Exception:
        return False


def _is_likely_icon(url: str, alt: Optional[str], width: Optional[int], height: Optional[int]) -> bool:
    """Heuristika: maly rozmer alebo ikona/logo v nazve suboru → kandidat 'icon'."""
    url_low = (url or '').lower()
    for hint in ICON_FILENAME_HINTS:
        if hint in url_low:
            return True
    if width is not None and height is not None and width < 32 and height < 32:
        return True
    return False


def _safe_int(value: Any) -> Optional[int]:
    """int() s ochranou — vrati None ak hodnota nie je ciste cislo."""
    try:
        s = str(value or '').strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Structured metadata
# ---------------------------------------------------------------------------
def extract_json_ld(html: str) -> list[dict]:
    """Naparsuj vsetky `<script type="application/ld+json">`. `@graph` polia flattujeme.

    Vracia zoznam dictov. Neplatne/prazdne bloky preskakujeme (log debug).
    """
    out: list[dict] = []
    try:
        for script in _soup(html).find_all('script', {'type': 'application/ld+json'}):
            text = script.string or script.get_text()
            if not text or not text.strip():
                continue
            try:
                data = json.loads(text)
            except Exception as e:
                logger.debug("json-ld parse skip: %s", e)
                continue
            if isinstance(data, dict) and '@graph' in data:
                for it in data['@graph']:
                    if isinstance(it, dict):
                        out.append(it)
            elif isinstance(data, list):
                for it in data:
                    if isinstance(it, dict):
                        out.append(it)
            elif isinstance(data, dict):
                out.append(data)
    except Exception as e:
        logger.debug("extract_json_ld error: %s", e)
    return out


def extract_microdata(html: str) -> list[dict]:
    """Jednoducha itemscope/itemprop extrakcia. Stranky bez microdata vratia []."""
    out: list[dict] = []
    try:
        for el in _soup(html).find_all(attrs={'itemscope': True}):
            item_type = (el.get('itemtype') or '').split('/')[-1]
            props: dict[str, str] = {}
            for child in el.find_all(attrs={'itemprop': True}):
                key = child.get('itemprop')
                val = child.get('content') or child.get('href') or child.get_text(strip=True)
                if key and val:
                    props[key] = str(val)[:500]
            if props:
                out.append({'type': item_type, 'properties': props})
    except Exception as e:
        logger.debug("extract_microdata error: %s", e)
    return out


def extract_meta(html: str) -> dict[str, str]:
    """`<meta name=...>` pre description/keywords/author/robots/viewport/theme-color/generator
    + `<link rel="canonical">`."""
    out: dict[str, str] = {}
    try:
        soup = _soup(html)
        wanted = ('description', 'keywords', 'author', 'robots', 'viewport', 'theme-color', 'generator')
        for m in soup.find_all('meta'):
            name = (m.get('name') or '').lower()
            if name in wanted:
                content = m.get('content')
                if content:
                    out[name] = content.strip()[:500]
        canonical = soup.find('link', {'rel': 'canonical'})
        if canonical and canonical.get('href'):
            out['canonical'] = canonical['href']
    except Exception as e:
        logger.debug("extract_meta error: %s", e)
    return out


def extract_open_graph(html: str) -> dict[str, str]:
    """`og:*` + `twitter:*` meta tagy (kluc v lowercase)."""
    out: dict[str, str] = {}
    try:
        for m in _soup(html).find_all('meta'):
            prop = (m.get('property') or '').lower()
            if not prop:
                prop = (m.get('name') or '').lower()
            if prop.startswith('og:') or prop.startswith('twitter:'):
                content = m.get('content')
                if content:
                    out[prop] = content.strip()[:500]
    except Exception as e:
        logger.debug("extract_open_graph error: %s", e)
    return out


# ---------------------------------------------------------------------------
# Document structure
# ---------------------------------------------------------------------------
def extract_headings(html: str) -> list[HeadingItem]:
    """`h1`–`h6` s textom. Prazdne preskakujeme, text limit 200 znakov."""
    out: list[HeadingItem] = []
    try:
        soup = _soup(html)
        for level in range(1, 7):
            for h in soup.find_all(f'h{level}'):
                text = h.get_text(strip=True)
                if text:
                    out.append(HeadingItem(level=level, text=text[:200]))
    except Exception as e:
        logger.debug("extract_headings error: %s", e)
    return out


def extract_links(html: str, base_url: str) -> list[LinkItem]:
    """Vsetky `<a href>`. Relativne resolvuje, oznaci internal/external.
    Preskakuje mailto/tel/javascript a ciste `#`-kotvy. Deduplikuje podla resolved URL."""
    out: list[LinkItem] = []
    seen: set[str] = set()
    try:
        soup = _soup(html)
        base_netloc = urlparse(base_url).netloc
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            if not href or href.startswith(('mailto:', 'tel:', 'javascript:')) or href.startswith('#'):
                continue
            resolved = urljoin(base_url, href)
            if not resolved.startswith(('http://', 'https://')):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            text = a.get_text(strip=True)[:200]
            out.append(LinkItem(href=resolved, text=text, internal=_is_internal(resolved, base_netloc)))
    except Exception as e:
        logger.debug("extract_links error: %s", e)
    return out


def extract_images(html: str, base_url: str) -> list[ImageCandidate]:
    """Z `<img>`: src / srcset / data-src / data-lazy-src / data-original. Relativne resolvuje,
    pripoji alt/title/rozmery/section heading. Inline `data:image/...` preskakuje."""
    out: list[ImageCandidate] = []
    seen: set[str] = set()
    try:
        for img in _soup(html).find_all('img'):
            src = None
            source_attr = 'src'
            is_lazy = False
            for attr in ('src', 'data-src', 'data-lazy-src', 'data-lazy', 'data-original'):
                v = img.get(attr)
                if v and not v.strip().startswith('data:'):
                    src = v.strip()
                    source_attr = attr
                    is_lazy = attr != 'src'
                    break
            if not src:
                continue
            resolved = urljoin(base_url, src)
            if resolved.startswith('data:') or resolved in seen:
                continue
            seen.add(resolved)

            alt = img.get('alt') or None
            title = img.get('title') or None

            srcset_list: list[str] = []
            if img.get('srcset'):
                srcset_list = [s.strip().split(' ')[0] for s in img['srcset'].split(',') if s.strip()]

            width = _safe_int(img.get('width'))
            height = _safe_int(img.get('height'))

            # Najblizsi <section> a jeho nadpis (ak existuje).
            section_heading = None
            for parent in img.parents:
                if getattr(parent, 'name', None) == 'section':
                    h = parent.find(['h1', 'h2', 'h3'])
                    if h:
                        section_heading = h.get_text(strip=True)[:200]
                    break

            role = 'icon' if _is_likely_icon(resolved, alt, width, height) else 'unknown'

            out.append(ImageCandidate(
                src=src,
                resolved_url=resolved,
                srcset=srcset_list,
                alt=alt,
                title=title,
                width=width,
                height=height,
                section_heading=section_heading,
                is_lazy=is_lazy,
                source_attr=source_attr,
                candidate_role=role,
            ))
    except Exception as e:
        logger.debug("extract_images error: %s", e)
    return out


def extract_tables(html: str) -> list[list[list[str]]]:
    """Tabulky ako 2D polia. Prazdne tabulky preskakujeme. Limity: 20 tabuliek x 100 riadkov x 30 stlpcov."""
    out: list[list[list[str]]] = []
    try:
        for table in _soup(html).find_all('table')[:20]:
            rows: list[list[str]] = []
            for tr in table.find_all('tr')[:100]:
                row = [cell.get_text(strip=True)[:500] for cell in tr.find_all(['td', 'th'])[:30]]
                if row:
                    rows.append(row)
            if rows:
                out.append(rows)
    except Exception as e:
        logger.debug("extract_tables error: %s", e)
    return out


def extract_lists(html: str) -> list[list[str]]:
    """Vrchne `<ul>`/`<ol>` ako ploche polia stringov. Limity: 30 zoznamov x 50 poloziek."""
    out: list[list[str]] = []
    try:
        for ul in _soup(html).find_all(['ul', 'ol'])[:30]:
            items: list[str] = []
            for li in ul.find_all('li', recursive=False)[:50]:
                t = li.get_text(strip=True)[:500]
                if t:
                    items.append(t)
            if items:
                out.append(items)
    except Exception as e:
        logger.debug("extract_lists error: %s", e)
    return out


def extract_forms(html: str) -> list[dict]:
    """`<form>` s method/action/inputs."""
    out: list[dict] = []
    try:
        for f in _soup(html).find_all('form'):
            inputs = []
            for inp in f.find_all(['input', 'textarea', 'select']):
                inputs.append({
                    'name': inp.get('name'),
                    'type': inp.get('type', inp.name),
                    'required': inp.has_attr('required'),
                })
            out.append({
                'method': (f.get('method') or 'get').lower(),
                'action': f.get('action') or '',
                'inputs': inputs,
            })
    except Exception as e:
        logger.debug("extract_forms error: %s", e)
    return out


def extract_pdfs(html: str, base_url: str) -> list[str]:
    """Vsetky `.pdf` linky. Resolvuje + deduplikuje (case-insensitive na priponu)."""
    out: list[str] = []
    seen: set[str] = set()
    try:
        for a in _soup(html).find_all('a', href=True):
            href = a['href'].strip()
            if href.lower().split('?')[0].endswith('.pdf'):
                resolved = urljoin(base_url, href)
                if resolved not in seen:
                    seen.add(resolved)
                    out.append(resolved)
    except Exception as e:
        logger.debug("extract_pdfs error: %s", e)
    return out


def extract_social_links(html: str, base_url: str) -> list[str]:
    """Facebook, Instagram, TikTok, LinkedIn, Twitter/X, YouTube — deduplikovane."""
    out: list[str] = []
    seen: set[str] = set()
    try:
        for a in _soup(html).find_all('a', href=True):
            href = a['href'].strip()
            if any(d in href.lower() for d in SOCIAL_DOMAINS):
                resolved = urljoin(base_url, href)
                if resolved not in seen:
                    seen.add(resolved)
                    out.append(resolved)
    except Exception as e:
        logger.debug("extract_social_links error: %s", e)
    return out


# ---------------------------------------------------------------------------
# Text + contact patterns
# ---------------------------------------------------------------------------
def extract_visible_text(html: str) -> str:
    """Odstrani `<script>` / `<style>` / `<noscript>`, vrati cisty text s collapsnutymi medzerami."""
    try:
        soup = _soup(html)
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        return re.sub(r'\s+', ' ', text).strip()
    except Exception as e:
        logger.debug("extract_visible_text error: %s", e)
        return ''


def extract_contact_patterns(visible_text: str) -> ContactPatterns:
    """Regex extrakcia kontaktnych udajov z viditelneho textu. NIKDY neraisne.

    `social_links` / `map_links` sa nezbieraju tu (idu cez `extract_social_links` z HTML).
    """
    text = visible_text or ''
    try:
        emails = sorted({m.group(0).lower() for m in EMAIL_RE.finditer(text)})

        # Telefony: zachovaj povodny zapis, deduplikuj podla ciste-cislic.
        phone_dedup: dict[str, str] = {}
        for m in PHONE_SK_RE.finditer(text):
            raw = m.group(0).strip()
            digits = re.sub(r'\D', '', raw)
            if len(digits) >= 9 and digits not in phone_dedup:
                phone_dedup[digits] = raw
        phones = list(phone_dedup.values())

        icos = sorted({m.group(1) for m in ICO_RE.finditer(text)})
        dics = sorted({m.group(1) for m in DIC_RE.finditer(text)})
        ic_dphs = sorted({m.group(1).upper() for m in IC_DPH_RE.finditer(text)})
        ibans = sorted({re.sub(r'\s+', '', m.group(0)) for m in IBAN_RE.finditer(text)})

        addresses: list[str] = []
        addr_seen: set[str] = set()
        for m in ADDRESS_RE.finditer(text):
            candidate = ' '.join(m.groups()).strip()
            norm = candidate.lower()
            if norm not in addr_seen:
                addr_seen.add(norm)
                addresses.append(candidate)

        return ContactPatterns(
            emails=emails,
            phones=phones,
            ico=icos,
            dic=dics,
            ic_dph=ic_dphs,
            iban=ibans,
            social_links=[],
            map_links=[],
            addresses_candidates=addresses,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("extract_contact_patterns error: %s", e)
        return ContactPatterns()
