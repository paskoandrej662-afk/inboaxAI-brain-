"""Testy pre LCA algoritmus (Phase 2 — anchor mapping)."""

from bs4 import BeautifulSoup

from app.core.extractors.hds.lca_finder import (
    _common_ancestor,
    _find_deepest_containing,
    _normalize,
    _price_search_fragment,
    find_lca,
)
from app.core.extractors.hds.types import Seed


def test_normalize_handles_diacritics():
    assert _normalize("Skákací HRAD") == "skakaci hrad"


def test_normalize_strips_whitespace():
    assert _normalize("  Hello  World  ") == "hello  world"


def test_price_fragment_extracts_number():
    assert _price_search_fragment("160€") == "160"
    assert _price_search_fragment("180€/Den") == "180"
    assert _price_search_fragment("od 100€") == "100"


def test_price_fragment_fallback_for_soft_price():
    # 'dohodou' nemá číslo
    frag = _price_search_fragment("dohodou")
    assert frag == "dohodou"


def test_find_deepest_containing_returns_leaf():
    html = """
    <div>
        <div>
            <h3>Tiger</h3>
            <p>Iny text</p>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    el = _find_deepest_containing(soup, "Tiger")
    assert el is not None
    # Najhlbsi match je <h3>
    assert el.name == "h3"


def test_common_ancestor_basic():
    html = """
    <div id="root">
        <div id="card">
            <h3>Name</h3>
            <span>160€</span>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    name_el = soup.find("h3")
    price_el = soup.find("span")
    lca = _common_ancestor(name_el, price_el)
    assert lca is not None
    assert lca.get("id") == "card"


def test_find_lca_returns_container():
    html = """
    <div class="card">
        <div class="content">
            <h3>Tiger</h3>
            <div><span class="price">160€</span></div>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    seed = Seed(name="Tiger", price="160€")
    lca = find_lca(soup, seed)
    assert lca is not None
    assert lca.name in ("div", "section", "article", "li")


def test_find_lca_returns_none_when_name_missing():
    html = "<div><h3>Lion</h3><span>160€</span></div>"
    soup = BeautifulSoup(html, "html.parser")
    seed = Seed(name="NonExistent", price="160€")
    assert find_lca(soup, seed) is None


def test_find_lca_handles_diacritics():
    html = """
    <article class="prod">
        <h2>Skákací Hrad Tiger</h2>
        <p>Cena: 160 €</p>
    </article>
    """
    soup = BeautifulSoup(html, "html.parser")
    seed = Seed(name="Skákací Hrad Tiger", price="160€")
    lca = find_lca(soup, seed)
    assert lca is not None
    assert lca.name == "article"
