from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

# Use the unicode-aware sentence splitter — fall back to a regex.
_SENT_SPLIT_RE = re.compile(r"(?<=[\.\!\?…])\s+(?=[A-ZÁČĎÉÍĽŇÓŠŤÚÝŽ0-9])")


@dataclass
class Chunk:
    text: str
    content_hash: str
    char_count: int
    paragraph_idx: int = 0


def normalize_text(text: str) -> str:
    """Strip whitespace, collapse multiple spaces to one. Preserves diacritics + paragraphs."""
    if not text:
        return ""
    # Normalize Unicode form (NFC keeps composed Slovak diacritics).
    text = unicodedata.normalize("NFC", text)
    # Convert NBSP & other unicode spaces to plain space, but keep newlines.
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Trim each line, then drop trailing whitespace
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    # Collapse 3+ blank lines to a single blank line (paragraph break)
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln == "":
            blank_run += 1
            if blank_run <= 1:
                out.append("")
        else:
            blank_run = 0
            out.append(ln)
    result = "\n".join(out).strip()
    return result


def compute_hash(text: str) -> str:
    """SHA256 of normalized text — for idempotent dedupe."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_into_paragraphs(text: str) -> list[str]:
    """Paragraphs delimited by blank lines."""
    paras = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paras if p.strip()]


def _split_into_sentences(paragraph: str) -> list[str]:
    sents = _SENT_SPLIT_RE.split(paragraph)
    out: list[str] = []
    for s in sents:
        s = s.strip()
        if s:
            out.append(s)
    return out


def _hard_split(s: str, max_size: int) -> list[str]:
    """Last-resort splitter for super long sentences. Tries word boundary."""
    pieces: list[str] = []
    cur = s
    while len(cur) > max_size:
        # Try to split at a space close to max_size
        cut = cur.rfind(" ", 0, max_size)
        if cut == -1 or cut < max_size // 2:
            cut = max_size
        pieces.append(cur[:cut].strip())
        cur = cur[cut:].lstrip()
    if cur:
        pieces.append(cur)
    return pieces


def _take_overlap(text: str, overlap: int) -> str:
    """Take ~overlap trailing characters from text, snapped to a sentence start."""
    if len(text) <= overlap:
        return text
    tail = text[-overlap:]
    # Snap forward to first sentence boundary so overlap starts mid-sentence-aware
    m = re.search(r"[\.\!\?…]\s+", tail)
    if m:
        return tail[m.end():]
    # Otherwise snap to first space
    sp = tail.find(" ")
    if sp != -1:
        return tail[sp + 1:]
    return tail


def chunk_text(
    text: str,
    target_size: int = 800,
    overlap: int = 100,
    min_size: int = 100,
    max_size: int = 1500,
) -> list[Chunk]:
    """Sentence-respecting chunker. Never cuts mid-sentence."""
    if not text:
        return []
    text = normalize_text(text)
    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    para_idx = 0

    def _flush_buf() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        merged = " ".join(buf).strip()
        if not merged:
            buf = []
            buf_len = 0
            return
        if len(merged) >= min_size:
            chunks.append(
                Chunk(
                    text=merged,
                    content_hash=compute_hash(merged),
                    char_count=len(merged),
                    paragraph_idx=para_idx,
                )
            )
        buf = []
        buf_len = 0

    for p_idx, para in enumerate(paragraphs):
        # Decompose paragraph into sentences once we know it overflows
        sentences = [para] if len(para) <= max_size else _split_into_sentences(para)

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            # If a single sentence exceeds max_size, hard-split it (rare).
            if len(sent) > max_size:
                # Flush current buffer first
                para_idx = p_idx
                _flush_buf()
                for piece in _hard_split(sent, max_size):
                    if len(piece) >= min_size:
                        chunks.append(
                            Chunk(
                                text=piece,
                                content_hash=compute_hash(piece),
                                char_count=len(piece),
                                paragraph_idx=p_idx,
                            )
                        )
                # Seed next chunk with overlap
                if chunks and overlap > 0:
                    seed = _take_overlap(chunks[-1].text, overlap)
                    if seed:
                        buf = [seed]
                        buf_len = len(seed)
                continue

            extra = (1 if buf_len else 0) + len(sent)
            if buf_len + extra > target_size and buf:
                # Flush, then seed next buf with overlap
                para_idx = p_idx
                _flush_buf()
                if overlap > 0 and chunks:
                    seed = _take_overlap(chunks[-1].text, overlap)
                    if seed:
                        buf = [seed]
                        buf_len = len(seed)
                buf.append(sent)
                buf_len += (1 if buf_len else 0) + len(sent)
            else:
                buf.append(sent)
                buf_len += extra

        # End of paragraph — add a soft boundary unless we'd overflow
        if buf_len >= target_size:
            para_idx = p_idx
            _flush_buf()
            if overlap > 0 and chunks:
                seed = _take_overlap(chunks[-1].text, overlap)
                if seed:
                    buf = [seed]
                    buf_len = len(seed)

    para_idx = len(paragraphs) - 1
    _flush_buf()

    # Dedupe by content_hash within this batch (paranoia)
    seen: set[str] = set()
    unique: list[Chunk] = []
    for c in chunks:
        if c.content_hash in seen:
            continue
        seen.add(c.content_hash)
        unique.append(c)
    return unique
