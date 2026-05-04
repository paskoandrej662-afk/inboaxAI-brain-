from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import trafilatura
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

# Slovak-specific characters used for crude language heuristic
SK_SPECIFIC_CHARS = set("ľĽščťžýáíéóúäôňĺŕČŠŤŽÝÁÍÉÓÚÄÔŇĹŔ")


@dataclass
class ExtractedContent:
    text: str
    title: str | None = None
    language: str | None = None
    section: str = "general"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedFact:
    key: str
    subject: str | None
    value: dict[str, Any]
    evidence: str
    source_url: str
    confidence: float = 1.0


@dataclass
class ExtractedFaq:
    question: str
    answer: str
    source_url: str


def _detect_language(text: str) -> str | None:
    if not text:
        return None
    sample = text[:2000]
    sk_chars = sum(1 for c in sample if c in SK_SPECIFIC_CHARS)
    # If we see any Slovak-specific letters, mark as 'sk'
    if sk_chars >= 3:
        return "sk"
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        return detect(sample)
    except Exception:
        return None


def _extract_title(html: str) -> str | None:
    try:
        tree = HTMLParser(html)
        t = tree.css_first("title")
        if t and t.text():
            return t.text().strip()
    except Exception:
        pass
    return None


def extract_text(html: str, url: str) -> ExtractedContent:
    """Primary: trafilatura. Fallback: selectolax over <main>/<article>/<body>."""
    section = extract_section(url, html)
    title = _extract_title(html)

    text: str | None = None
    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            url=url,
        )
    except Exception as exc:
        logger.warning("trafilatura failed for %s: %s", url, exc)
        text = None

    if not text or len(text.strip()) < 50:
        # Fallback to selectolax over candidate containers
        try:
            tree = HTMLParser(html)
            # Strip noise
            for sel in ("script", "style", "noscript", "nav", "footer", "header", "form"):
                for n in tree.css(sel):
                    n.decompose()
            candidates = (
                tree.css_first("main")
                or tree.css_first("article")
                or tree.css_first('[role="main"]')
                or tree.css_first("body")
            )
            if candidates is not None:
                text = candidates.text(separator="\n", strip=True)
        except Exception as exc:
            logger.warning("selectolax fallback failed for %s: %s", url, exc)
            text = ""

    text = (text or "").strip()
    return ExtractedContent(
        text=text,
        title=title,
        language=_detect_language(text),
        section=section,
        meta={},
    )


_SECTION_PATTERNS = (
    ("pricing", ("/cennik", "/cenník", "/cena", "/ceny", "/pricing", "/price")),
    ("about", ("/o-nas", "/o-nás", "/about", "/about-us", "/o-firme")),
    ("contact", ("/kontakt", "/kontakty", "/contact", "/contacts")),
    ("faq", ("/faq", "/q-a", "/q&a", "/casto-kladene", "/často-kladené")),
    ("services", ("/sluzby", "/služby", "/services", "/ponuka", "/produkty", "/products")),
    ("blog", ("/blog", "/clanky", "/články", "/news", "/aktuality")),
)


def extract_section(url: str, html: str | None = None) -> str:
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return "home"
    for section, fragments in _SECTION_PATTERNS:
        for frag in fragments:
            if frag in path:
                return section
    return "general"


# --- Fact extraction ---------------------------------------------------------

# Slovak phone formats: +421..., 09xx..., +420 (Czech) — allow spaces, dots, dashes, slashes between digits
_PHONE_PATTERNS = (
    re.compile(r"\+421(?:[\s\-\.\/]{0,3}\d){9}"),
    re.compile(r"\+420(?:[\s\-\.\/]{0,3}\d){9}"),
    re.compile(r"(?<!\d)0\d(?:[\s\-\.\/]{0,3}\d){8}(?!\d)"),
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ICO_RE = re.compile(r"(?:I[ČC]O[\s:\-]*)\s*(\d{6,8})", re.IGNORECASE)
_DIC_RE = re.compile(r"(?:DI[ČC]|IČ\s*DPH|IC\s*DPH)[\s:\-]*((?:SK)?\d{9,10})", re.IGNORECASE)
_PRICE_RE = re.compile(r"(\d{1,4}(?:[\s ]?\d{3})*(?:[,\.]\d{1,2})?)\s*(?:€|(?:EUR|eur)(?!\w))")
_PSC_RE = re.compile(r"\b(\d{3}\s?\d{2})\b")
_DAY_TOKENS = (
    "Po", "Ut", "St", "Št", "Pi", "So", "Ne",
    "Pondelok", "Utorok", "Streda", "Štvrtok", "Piatok", "Sobota", "Nedeľa",
)
_HOURS_RE = re.compile(
    r"((?:Po|Ut|St|Št|Pi|So|Ne|Pondelok|Utorok|Streda|Štvrtok|Piatok|Sobota|Nedeľa)"
    r"(?:[\s\-–—]{1,3}(?:Pi|Pia|Ut|St|Št|So|Ne|Pondelok|Utorok|Streda|Štvrtok|Piatok|Sobota|Nedeľa))?"
    r"[:\s\-–—]{1,4}\d{1,2}[:\.\s]\d{2}\s*[\-–—]\s*\d{1,2}[:\.\s]\d{2})",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[\.\!\?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _find_evidence(text: str, match_start: int, match_end: int, window: int = 160) -> str:
    lo = max(0, match_start - window)
    hi = min(len(text), match_end + window)
    snippet = text[lo:hi].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet


def extract_facts(text: str, url: str) -> list[ExtractedFact]:
    out: list[ExtractedFact] = []
    if not text:
        return out

    seen_keys: set[tuple[str, str | None]] = set()

    def _push(key: str, subject: str | None, value: dict, evidence: str, confidence: float = 0.95):
        sig = (key, subject)
        if sig in seen_keys:
            return
        seen_keys.add(sig)
        out.append(
            ExtractedFact(
                key=key,
                subject=subject,
                value=value,
                evidence=evidence[:500],
                source_url=url,
                confidence=confidence,
            )
        )

    # Phone
    for pat in _PHONE_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(0).strip()
            normalized = re.sub(r"[\s\-\. \/]", "", raw)
            _push(
                "phone",
                normalized,
                {"raw": raw, "normalized": normalized},
                _find_evidence(text, m.start(), m.end()),
            )

    # Email
    for m in _EMAIL_RE.finditer(text):
        email = m.group(0).strip().lower()
        _push("email", email, {"email": email}, _find_evidence(text, m.start(), m.end()))

    # IČO
    for m in _ICO_RE.finditer(text):
        ico = m.group(1)
        _push("ico", ico, {"ico": ico}, _find_evidence(text, m.start(), m.end()), confidence=0.9)

    # DIČ
    for m in _DIC_RE.finditer(text):
        dic = m.group(1)
        _push("dic", dic, {"dic": dic}, _find_evidence(text, m.start(), m.end()), confidence=0.9)

    # Prices — try to grab subject from the same sentence
    sentences = _split_sentences(text)
    for s in sentences:
        for m in _PRICE_RE.finditer(s):
            price_raw = m.group(1)
            try:
                value_eur = float(price_raw.replace(" ", "").replace(" ", "").replace(",", "."))
            except ValueError:
                continue
            # Subject = first ~6 words of sentence trimmed
            subj = re.sub(r"\s+", " ", s).strip()
            subj_short = " ".join(subj.split()[:8])[:120]
            _push(
                "price",
                subj_short,
                {"amount": value_eur, "currency": "EUR", "raw": m.group(0)},
                s.strip(),
                confidence=0.8,
            )

    # Opening hours
    for m in _HOURS_RE.finditer(text):
        raw = re.sub(r"\s+", " ", m.group(0)).strip()
        _push(
            "hours",
            raw[:60],
            {"raw": raw},
            _find_evidence(text, m.start(), m.end()),
            confidence=0.7,
        )

    # Address — PSČ + nearby context
    for m in _PSC_RE.finditer(text):
        psc = m.group(1).replace(" ", "")
        # Get a sentence-like window
        ev = _find_evidence(text, m.start(), m.end(), window=120)
        # Heuristic: must contain at least one capitalized word (city/street)
        if re.search(r"\b[A-ZÁČĎÉÍĽŇÓŠŤÚÝŽ][a-záčďéíľňóšťúýž]{2,}", ev):
            _push(
                "address",
                psc,
                {"psc": psc, "raw": ev},
                ev,
                confidence=0.6,
            )

    return out


# --- FAQ extraction ----------------------------------------------------------

def _faq_from_jsonld(html: str, url: str) -> list[ExtractedFaq]:
    out: list[ExtractedFaq] = []
    try:
        tree = HTMLParser(html)
    except Exception:
        return out

    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for entry in _iter_jsonld_objects(data):
            if not isinstance(entry, dict):
                continue
            t = entry.get("@type")
            types = t if isinstance(t, list) else [t]
            if not any(str(x).lower() == "faqpage" for x in types if x):
                continue
            for q in entry.get("mainEntity", []) or []:
                if not isinstance(q, dict):
                    continue
                question = (q.get("name") or "").strip()
                answer_block = q.get("acceptedAnswer") or {}
                if isinstance(answer_block, list) and answer_block:
                    answer_block = answer_block[0]
                answer_text = ""
                if isinstance(answer_block, dict):
                    answer_text = (answer_block.get("text") or "").strip()
                if question and answer_text:
                    # Strip residual HTML in answer
                    answer_text = re.sub(r"<[^>]+>", " ", answer_text)
                    answer_text = re.sub(r"\s+", " ", answer_text).strip()
                    out.append(ExtractedFaq(question=question, answer=answer_text, source_url=url))
    return out


def _iter_jsonld_objects(data: Any):
    if isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_objects(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                yield from _iter_jsonld_objects(item)


_QUESTION_PREFIX = re.compile(r"^(?:Otázka[:\s]|Q[:\s]|\?)", re.IGNORECASE)


def _faq_from_text(text: str, url: str) -> list[ExtractedFaq]:
    out: list[ExtractedFaq] = []
    if not text:
        return out
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    i = 0
    while i < len(paras):
        p = paras[i]
        is_question = p.endswith("?") or _QUESTION_PREFIX.match(p)
        if is_question and i + 1 < len(paras):
            ans = paras[i + 1]
            if len(ans) > 5 and not ans.endswith("?"):
                question = re.sub(r"^(?:Otázka:|Q:)\s*", "", p, flags=re.IGNORECASE).strip()
                out.append(ExtractedFaq(question=question, answer=ans, source_url=url))
                i += 2
                continue
        i += 1
    return out


def extract_faqs(text: str, html: str, url: str = "") -> list[ExtractedFaq]:
    primary = _faq_from_jsonld(html, url) if html else []
    if primary:
        return primary
    return _faq_from_text(text, url)
