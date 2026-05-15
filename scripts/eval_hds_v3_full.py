"""Real-API E2E eval: crawler -> gemini -> parse -> validate -> dedup -> persona.

Cost: ~$0.15-0.20 per run. Run before commit/push.

Usage:
    PYTHONPATH=. python3 scripts/eval_hds_v3_full.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("GEMINI_API_KEY"):
    print("ERROR: GEMINI_API_KEY required")
    sys.exit(1)


async def main():
    from app.core.extractors.hds_v3.engine import HDSv3Engine

    engine = HDSv3Engine()
    test_url = "https://skakaciehradyorava.sk/"
    test_company_id = "test-company-id"

    print("=== HDS-v3 Full Pipeline Eval ===")
    print(f"URL: {test_url}")

    result = await engine.ingest(test_url, test_company_id)

    print("\n=== RESULT ===")
    for k, v in result.items():
        print(f"  {k}: {v}")

    parsed = engine._last_parsed
    persona = engine._last_persona

    if parsed is not None:
        print("\n=== PARSED DATA ===")
        print(f"Company: {parsed.company_name}")
        print(f"ICO: {parsed.company_ico}")
        print(f"DIC: {parsed.company_dic}")
        print(f"Products ({len(parsed.products)}):")
        for prod in parsed.products[:10]:
            attrs_preview = ", ".join(
                f"{k}={v[:30]}" for k, v in (prod.attributes or {}).items()
            )[:200]
            print(
                f"  - {prod.name}: {prod.price_eur}€ ({prod.price_unit}) [{attrs_preview}]"
            )
        print(f"Contacts ({len(parsed.contacts)}):")
        for c in parsed.contacts[:10]:
            print(f"  - [{c.type}] {c.content[:120]}")
        print(f"Facts ({len(parsed.facts)}):")
        for f in parsed.facts[:5]:
            print(f"  - [{f.type}] {f.content[:120]}")
        print(f"FAQs ({len(parsed.faqs)}):")
        for f in parsed.faqs[:3]:
            print(f"  Q: {f.question[:100]}")
            print(f"  A: {f.answer[:150]}")

    if persona is not None:
        print(f"\n=== PERSONA ({persona['word_count']} words) ===")
        print(persona["persona_text"][:3000])
        print("...\n")

    out_path = "/tmp/hds_v3_persona.md"
    if persona and persona.get("persona_text"):
        with open(out_path, "w") as f:
            f.write(persona["persona_text"])
        print(f"Persona saved: {out_path}")

    # Assertions
    assert result["success"], f"Pipeline failed: {result.get('error')}"
    assert parsed is not None, "No parsed data"
    assert len(parsed.products) >= 5, f"Expected 5+ products, got {len(parsed.products)}"
    assert any(
        ("Tiger" in p.name) or ("Rozprávkovo" in p.name) or ("Aladin" in p.name)
        for p in parsed.products
    ), "Expected real Skákačky products (Tiger/Rozprávkovo/Aladin), got hallucinations"
    assert persona["success"], f"Persona failed: {persona.get('error')}"
    assert persona["word_count"] >= 800, (
        f"Persona too short: {persona['word_count']} words"
    )

    print("\nALL ASSERTIONS PASSED")
    print(f"Total cost: ${result['total_cost_usd']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
