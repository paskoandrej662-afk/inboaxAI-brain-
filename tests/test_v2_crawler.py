"""Offline testy pre `app/core/ingest_v2/crawler.py` — ziaden network.

Testuju cisto synchronnu cast `CrawlerV2`: `_normalize_url` a `_priority_for_url`.
"""
from __future__ import annotations

from app.core.ingest_v2.crawler import CrawlerV2


def _crawler() -> CrawlerV2:
    return CrawlerV2(renderer=None, max_pages=10)


# ----------------------------------------------------------------- _normalize_url
def test_normalize_url_basic():
    assert _crawler()._normalize_url('/contact', 'https://x.sk/') == 'https://x.sk/contact'


def test_normalize_url_trailing_slash():
    n = _crawler()._normalize_url('https://x.sk/about/', 'https://x.sk/')
    assert n in ('https://x.sk/about', 'https://x.sk/about/')


def test_normalize_url_fragment_stripped():
    n = _crawler()._normalize_url('https://x.sk/p#section', 'https://x.sk/')
    assert '#' not in (n or '')


def test_normalize_url_external_returns_none():
    assert _crawler()._normalize_url('https://other.com/x', 'https://x.sk/') is None


def test_normalize_url_mailto_returns_none():
    assert _crawler()._normalize_url('mailto:info@x.sk', 'https://x.sk/') is None


def test_normalize_url_strips_tracking_params():
    n = _crawler()._normalize_url('https://x.sk/p?utm_source=fb&id=7', 'https://x.sk/')
    assert n is not None
    assert 'utm_source' not in n
    assert 'id=7' in n


# ----------------------------------------------------------------- _priority_for_url
def test_priority_high_cennik():
    assert _crawler()._priority_for_url('https://x.sk/cennik') >= 0.85


def test_priority_high_kontakt():
    assert _crawler()._priority_for_url('https://x.sk/kontakt') >= 0.85


def test_priority_low_gdpr():
    assert _crawler()._priority_for_url('https://x.sk/gdpr') < 0.2


def test_priority_low_cookies():
    assert _crawler()._priority_for_url('https://x.sk/cookies') < 0.2


def test_priority_default_for_unknown():
    p = _crawler()._priority_for_url('https://x.sk/random-page')
    assert 0.2 <= p <= 0.7


def test_priority_homepage_high():
    assert _crawler()._priority_for_url('https://x.sk/') >= 0.7


def test_priority_skips_binary_extensions():
    c = _crawler()
    assert c._priority_for_url('https://x.sk/foo.pdf') < 0.2
    assert c._priority_for_url('https://x.sk/img.jpg') < 0.2
