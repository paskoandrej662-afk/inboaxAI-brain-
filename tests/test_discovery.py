from app.core.knowledge_hub import _prioritize_urls


def test_prioritize_same_domain_filter():
    """External URLs (different domain) sa odfiltruju."""
    urls = [
        "https://example.sk/",
        "https://example.sk/cennik",
        "https://facebook.com/x",
        "https://google.com",
        "https://example.sk/produkty",
    ]
    out = _prioritize_urls(urls, "example.sk", 10)
    netlocs = {u.split("/")[2] for u in out}
    assert netlocs == {"example.sk"}


def test_prioritize_legal_pages_excluded():
    """Legal/cookies/gdpr URLs maju score 0 a v top-2 byt nesmu."""
    urls = [
        "https://example.sk/cookies",
        "https://example.sk/gdpr",
        "https://example.sk/cennik",
        "https://example.sk/produkty",
    ]
    out = _prioritize_urls(urls, "example.sk", 2)
    paths = [u.split("example.sk")[-1].split("?")[0] for u in out]
    for p in paths:
        assert "cookies" not in p
        assert "gdpr" not in p


def test_prioritize_dedupe():
    """Trailing slash a fragment sa normalizuju."""
    urls = [
        "https://example.sk/cennik",
        "https://example.sk/cennik/",
        "https://example.sk/cennik#anchor",
    ]
    out = _prioritize_urls(urls, "example.sk", 10)
    assert len(out) == 1


def test_prioritize_max_pages_cap():
    """Vrati max max_pages."""
    urls = [f"https://example.sk/page{i}" for i in range(50)]
    out = _prioritize_urls(urls, "example.sk", 10)
    assert len(out) == 10


def test_prioritize_high_value_first():
    """FAQ/cennik su PRED nezaradenymi URL."""
    urls = [
        "https://example.sk/random-thing",     # unknown -> 0.5
        "https://example.sk/faq",              # 0.95
        "https://example.sk/another-random",   # unknown -> 0.5
    ]
    out = _prioritize_urls(urls, "example.sk", 3)
    assert "/faq" in out[0]
