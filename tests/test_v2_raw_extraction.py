"""Offline testy pre `app/core/ingest_v2/raw_extraction.py` — ziaden network."""
from __future__ import annotations

from app.core.ingest_v2.raw_extraction import (
    extract_contact_patterns,
    extract_headings,
    extract_images,
    extract_json_ld,
    extract_links,
    extract_lists,
    extract_meta,
    extract_microdata,
    extract_open_graph,
    extract_pdfs,
    extract_social_links,
    extract_tables,
    extract_visible_text,
)


# ----------------------------------------------------------------- json-ld
def test_extract_json_ld_basic():
    html = '<script type="application/ld+json">{"@type":"Product","name":"X","price":"100"}</script>'
    items = extract_json_ld(html)
    assert len(items) == 1
    assert items[0]['@type'] == 'Product'


def test_extract_json_ld_graph_flattens():
    html = '<script type="application/ld+json">{"@graph":[{"@type":"A"},{"@type":"B"}]}</script>'
    items = extract_json_ld(html)
    assert len(items) == 2


def test_extract_json_ld_invalid_skipped():
    html = (
        '<script type="application/ld+json">not valid</script>'
        '<script type="application/ld+json">{"@type":"X"}</script>'
    )
    items = extract_json_ld(html)
    assert len(items) == 1


def test_extract_json_ld_empty_block():
    html = '<script type="application/ld+json"></script>'
    assert extract_json_ld(html) == []


# ----------------------------------------------------------------- meta / og
def test_extract_meta_basic():
    html = '<meta name="description" content="Test"><meta name="keywords" content="a,b">'
    m = extract_meta(html)
    assert m['description'] == 'Test'
    assert m['keywords'] == 'a,b'


def test_extract_open_graph():
    html = '<meta property="og:title" content="T"><meta property="og:image" content="x.jpg">'
    og = extract_open_graph(html)
    assert og.get('og:title') == 'T'
    assert og.get('og:image') == 'x.jpg'


# ----------------------------------------------------------------- headings
def test_extract_headings():
    html = '<h1>One</h1><h2>Two</h2><h2>Three</h2><h3></h3>'
    hs = extract_headings(html)
    assert len(hs) == 3
    assert hs[0].level == 1
    assert hs[0].text == 'One'


# ----------------------------------------------------------------- links
def test_extract_links_internal_external():
    html = '<a href="/contact">A</a><a href="https://other.com/x">B</a><a href="https://x.sk/p">C</a>'
    links = extract_links(html, 'https://x.sk/')
    by_href = {l.href: l.internal for l in links}
    assert by_href['https://x.sk/contact'] is True
    assert by_href['https://other.com/x'] is False


def test_extract_links_skip_mailto_tel_js():
    html = (
        '<a href="mailto:x@y.sk">M</a><a href="tel:+421900">T</a>'
        '<a href="javascript:void(0)">J</a><a href="#section">F</a>'
    )
    assert extract_links(html, 'https://x.sk/') == []


# ----------------------------------------------------------------- images
def test_extract_images_basic():
    html = '<img src="/foo.jpg" alt="Foo"><img src="https://other.com/bar.png">'
    imgs = extract_images(html, 'https://x.sk/page')
    urls = [i.resolved_url for i in imgs]
    assert 'https://x.sk/foo.jpg' in urls


def test_extract_images_data_src_lazy():
    html = '<img data-src="/lazy.jpg" alt="L">'
    imgs = extract_images(html, 'https://x.sk/')
    assert imgs[0].resolved_url == 'https://x.sk/lazy.jpg'
    assert imgs[0].is_lazy is True


def test_extract_images_skip_data_uri():
    html = '<img src="data:image/png;base64,abc">'
    imgs = extract_images(html, 'https://x.sk/')
    assert imgs == []


# ----------------------------------------------------------------- tables / lists
def test_extract_tables_basic():
    html = '<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>'
    tables = extract_tables(html)
    assert len(tables) == 1
    assert tables[0] == [['A', 'B'], ['1', '2']]


def test_extract_lists_basic():
    html = '<ul><li>One</li><li>Two</li></ul>'
    lists = extract_lists(html)
    assert lists[0] == ['One', 'Two']


# ----------------------------------------------------------------- pdfs / social
def test_extract_pdfs():
    html = '<a href="/doc.pdf">D</a><a href="https://x.sk/info.PDF">I</a><a href="/page.html">P</a>'
    pdfs = extract_pdfs(html, 'https://x.sk/')
    assert any(p.lower().endswith('.pdf') for p in pdfs)
    assert any('.PDF' in p for p in pdfs)


def test_extract_social_links():
    html = '<a href="https://facebook.com/me">FB</a><a href="https://www.instagram.com/me/">IG</a>'
    links = extract_social_links(html, 'https://x.sk/')
    assert any('facebook' in l for l in links)
    assert any('instagram' in l for l in links)


# ----------------------------------------------------------------- contact patterns
def test_extract_contact_phones_sk():
    txt = "Volajte 0911 815 051 alebo +421 905 123 456"
    c = extract_contact_patterns(txt)
    assert len(c.phones) >= 2


def test_extract_contact_email():
    txt = "Kontakt: info@firma.sk, eva.novakova@firma.com"
    c = extract_contact_patterns(txt)
    assert len(c.emails) == 2


def test_extract_contact_ico():
    txt = "Spoločnosť ABC s.r.o., IČO: 12345678"
    c = extract_contact_patterns(txt)
    assert '12345678' in c.ico


# ----------------------------------------------------------------- visible text / microdata
def test_extract_visible_text_strips_scripts():
    html = (
        '<html><head><style>body{color:red}</style></head>'
        '<body>Hello<script>alert(1)</script> World</body></html>'
    )
    t = extract_visible_text(html)
    assert 'Hello' in t
    assert 'World' in t
    assert 'alert' not in t


def test_extract_microdata_empty_ok():
    """Stranky bez microdata vracaju []."""
    assert extract_microdata('<html></html>') == []
