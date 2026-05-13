"""Smoke eval that the Sonnet vision pipeline still returns products.

NOT a unit test (calls real Sonnet API ~$0.10-$1 per run).
Run manually BEFORE each push that touches vision.py / VISION_SYSTEM / VISION_TOOL_SCHEMA.

Uses a fixture HTML with 3 clear products and asserts Sonnet extracts at least 2.
If it returns 0 -> regression -> revert before push.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ANTHROPIC_API_KEY not set — skipping eval")
    sys.exit(0)


FIXTURE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test produkty</title></head>
<body>
<div class="produkty">
    <div class="produkt">
        <h3>Produkt A</h3>
        <p>Najlepsi produkt. Cena 100€/ks.</p>
        <p>Kapacita: 10 ks. Rozmery: 5x5 m.</p>
    </div>
    <div class="produkt">
        <h3>Produkt B</h3>
        <p>Druhy produkt. Cena 200€/ks.</p>
        <p>Kapacita: 5 ks.</p>
    </div>
    <div class="produkt">
        <h3>Produkt C</h3>
        <p>Specialna ponuka. Cena dohodou.</p>
        <p>Individualne riesenie.</p>
    </div>
</div>
</body>
</html>
"""


async def main():
    from app.core.browser import BrowserPool, TiledPageResult
    from app.core.extractors.vision import extract_page_with_tiled_vision

    pool = BrowserPool()
    await pool.start()
    page = await pool._context.new_page()
    await page.set_content(FIXTURE_HTML)

    screenshot = await page.screenshot(full_page=True, type="png")
    visible_text = await page.evaluate("() => document.body.innerText || ''")

    tiled = TiledPageResult(
        url="https://test.local/",
        final_url="https://test.local/",
        html=FIXTURE_HTML,
        visible_text=visible_text,
        title="Test produkty",
        scroll_height=800,
        viewport_w=1280,
        viewport_h=2200,
        segments=[screenshot],
    )

    print("Calling Sonnet vision...")
    products, facts, faqs, images, summary, cost = await extract_page_with_tiled_vision(
        tiled=tiled,
        page_type="product_listing",
        raw_text=visible_text,
    )

    print(f"\n=== EVAL RESULT ===")
    print(f"Cost: ${cost:.4f}")
    print(f"Products extracted: {len(products)}")
    for p in products:
        print(
            f"  - name={p.name!r} "
            f"price_eur={p.price_eur} "
            f"price_text={getattr(p, 'price_text', None)!r}"
        )

    await page.close()
    await pool.close()

    if len(products) >= 2:
        print(f"\nPASS — extracted {len(products)} >= 2 products")
        sys.exit(0)
    else:
        print(f"\nFAIL — only {len(products)} products extracted (expected >= 2)")
        print("Revert vision.py changes BEFORE pushing.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
