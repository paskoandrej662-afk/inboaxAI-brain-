from app.core.extractors.classifier import heuristic_classify


def test_heuristic_home():
    assert heuristic_classify("https://x.sk/")[0] == "home"
    assert heuristic_classify("https://x.sk")[0] == "home"
    assert heuristic_classify("https://x.sk/index")[0] == "home"


def test_heuristic_faq():
    assert heuristic_classify("https://x.sk/faq")[0] == "faq"
    assert heuristic_classify("https://x.sk/najcastejsie-otazky/")[0] == "faq"
    assert heuristic_classify("https://x.sk/faq/")[0] == "faq"


def test_heuristic_pricing():
    assert heuristic_classify("https://x.sk/cennik")[0] == "service_pricing"


def test_heuristic_legal_low_value():
    assert heuristic_classify("https://x.sk/gdpr")[0] == "other_low_value"
    assert heuristic_classify("https://x.sk/cookies")[0] == "other_low_value"


def test_heuristic_unknown_returns_none():
    assert heuristic_classify("https://x.sk/random-page-name") is None
