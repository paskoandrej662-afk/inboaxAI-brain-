import uuid

import pytest

from app.core.ingest_v2.types import (
    BlockSignals,
    BlockTypeHint,
    BudgetLimits,
    BudgetStatus,
    ContactPatterns,
    DiscoveryMethod,
    EvidenceRecord,
    HeadingItem,
    ImageCandidate,
    IngestMode,
    JobStatus,
    LinkItem,
    RawPageData,
    RenderStatus,
    SourceType,
)


def test_evidence_record_minimum():
    e = EvidenceRecord(
        source_url="https://x.sk/",
        source_type=SourceType.DOM,
        evidence_text="ABC",
        confidence=0.9,
        extraction_method="visible_text",
    )
    assert e.source_type == "dom"
    assert e.confidence == 0.9


def test_evidence_record_confidence_range():
    with pytest.raises(Exception):
        EvidenceRecord(
            source_url="x",
            source_type=SourceType.DOM,
            evidence_text="x",
            confidence=1.5,
            extraction_method="x",
        )


def test_block_signals_defaults():
    s = BlockSignals()
    assert s.has_price is False
    assert s.price_count == 0
    assert s.cta_texts == []


def test_image_candidate_minimum():
    img = ImageCandidate(src="/x.jpg", resolved_url="https://x.sk/x.jpg")
    assert img.candidate_role == "unknown"
    assert img.is_lazy is False


def test_raw_page_data_default_empty():
    d = RawPageData()
    assert d.headings == []
    assert d.contact_patterns.emails == []


def test_budget_limits_defaults():
    b = BudgetLimits()
    assert b.max_pages == 12
    assert b.hard_limit_eur == 1.20


def test_enums_values():
    assert IngestMode.STANDARD == "standard"
    assert JobStatus.PARTIAL == "partial"
    assert SourceType.HEURISTIC_BLOCK == "heuristic_block"
    assert BlockTypeHint.PRICING_CANDIDATE == "pricing_candidate"
