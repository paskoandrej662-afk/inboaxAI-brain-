"""Testy pre sibling cluster detector (Phase 3)."""

from bs4 import BeautifulSoup

from app.core.extractors.hds.cluster_detector import _jaccard, find_siblings


def test_jaccard_basic():
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a"}, {"b"}) == 0.0
    # {a,b,c} vs {a,b,d} -> 2/4 = 0.5
    assert _jaccard({"a", "b", "c"}, {"a", "b", "d"}) == 0.5


def test_find_siblings_matches_class_similarity():
    html = """
    <div class="grid">
        <div class="card flip-box">A</div>
        <div class="card flip-box">B</div>
        <div class="card flip-box">C</div>
        <div class="totally-different">X</div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    first_card = soup.find("div", class_="card")
    siblings = find_siblings(first_card)
    assert len(siblings) == 3


def test_find_siblings_skip_non_matching_tag():
    html = """
    <section>
        <article class="prod">A</article>
        <article class="prod">B</article>
        <div class="prod">X</div>
    </section>
    """
    soup = BeautifulSoup(html, "html.parser")
    first = soup.find("article")
    siblings = find_siblings(first)
    assert len(siblings) == 2
    for s in siblings:
        assert s.name == "article"


def test_find_siblings_partial_class_match():
    # {card,a,b} vs {card,a,c} = 2/4 = 0.5 (presne na hrane) → match
    html = """
    <div>
        <div class="card a b">x</div>
        <div class="card a c">y</div>
        <div class="card a b">z</div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    first = soup.find("div", class_="card")
    siblings = find_siblings(first)
    assert len(siblings) == 3


def test_find_siblings_returns_self_when_no_parent():
    html = "<div class='card'>A</div>"
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("div")
    # Parent je [document] — funkcia stale vrati len match samotnych
    siblings = find_siblings(el)
    assert len(siblings) >= 1
    assert el in siblings


def test_find_siblings_handles_none():
    siblings = find_siblings(None)
    assert siblings == []
