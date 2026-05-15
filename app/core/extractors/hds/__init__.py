"""HDS-Lite — Hybrid DOM-Segmentation engine.

Pure-Python deterministicky extractor pre product listing stranky.
Sonnet vision len ako seed-generator (Phase 1) a arbiter pre neisty (Phase 6).
"""

from app.core.extractors.hds.types import ExtractionResult, ProductCard, Seed

__all__ = ["ExtractionResult", "ProductCard", "Seed"]
