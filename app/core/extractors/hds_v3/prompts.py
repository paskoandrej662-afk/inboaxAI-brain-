"""Gemini extraction prompts."""
from __future__ import annotations


BATCH_EXTRACTION_SYSTEM = """Si profesionalny web research analyst pre slovensky SMB trh.

Tvoja uloha: PODROBNA analyza 1-3 konkretnych podstranok webu.

ZAKLADNE PRAVIDLA (DODRZIAVAJ DOSLOVA):
1. Prejdi LEN URLs uvedene v prompte. NIKDY nehladaj na inych strankach.
2. Pre KAZDU URL vrat SAMOSTATNU sekciu vystupu.
3. NIKDY NEVYMYSLAJ. Ak udaj nie je explicitne na danej stranke, napis "neuvedene".
4. NIKDY nepridavaj informacie ktore nie su na tejto konkretnej stranke.
5. Ak vidis cenu "dohodou", "na vyziadanie", "individualne" - zapis PRESNE tak ako je.
6. Pre KAZDY udaj uved z ktorej URL pochadza (cez sekciu === STRANKA N ===).
"""


def build_batch_prompt(urls: list[str]) -> str:
    """Build user prompt for a batch of 1-3 URLs."""
    if not urls or len(urls) > 3:
        raise ValueError(f"Batch must have 1-3 URLs, got {len(urls)}")

    urls_section = "\n".join(f"URL {i + 1}: {url}" for i, url in enumerate(urls))
    page_sections = "\n\n".join(
        _page_section_template(i + 1, url) for i, url in enumerate(urls)
    )

    return f"""===============================================================
URL NA ANALYZU (PRESNE TIETO, NIC INE):
===============================================================

{urls_section}

===============================================================
FORMAT VYSTUPU:
===============================================================

{page_sections}

===============================================================
METADATA EXTRAKCIE
===============================================================
- Skutocne navstivene URLs: [zoznam - ak niektora zlyhala, uved ktora]
- Chybajuce data: [co by bolo uzitocne mat ale na tychto strankach nie je]
"""


def _page_section_template(num: int, url: str) -> str:
    """Template for one page section in the output."""
    return f"""=======================================
=== STRANKA {num}: {url} ===
=======================================

## IDENTIFIKACIA FIRMY (ak je na tejto stranke)
- Nazov firmy: [presny nazov alebo "neuvedene"]
- ICO: [cislo alebo "neuvedene"]
- DIC: [cislo alebo "neuvedene"]
- Slogan/popis: [text alebo "neuvedene"]

## KONTAKTY (ak su na tejto stranke)
Tabulka VSETKYCH kontaktov (kazdy clovek + telefon + email + pozicia):

| Meno | Pozicia | Telefon | Email |
|------|---------|---------|-------|

Adresy:
- Sidlo: [adresa alebo "neuvedene"]
- Prevadzka: [adresa alebo "neuvedene"]

Socialne siete:
- Facebook: [URL alebo "neuvedene"]
- Instagram: [URL alebo "neuvedene"]

Otvaracie hodiny: [text alebo "neuvedene"]

## PRODUKTY / SLUZBY (ak su na tejto stranke)
Pre KAZDY produkt/sluzbu samostatny riadok:

| Nazov | Cena | Jednotka | Popis | Atributy |
|-------|------|----------|-------|----------|

Atributy = vsetky parametre: kapacita, rozmery, vyska, vek, material, vykon, farba, atd.

## CENOVE PODMIENKY
- Zlavy: [text alebo "neuvedene"]
- Doprava: [text alebo "neuvedene"]
- Platba: [text alebo "neuvedene"]

## PROCES OBJEDNAVKY
- Ako objednat: [text alebo "neuvedene"]
- Lehoty: [text alebo "neuvedene"]
- Storno: [text alebo "neuvedene"]

## TECHNICKE PODMIENKY
- Doprava/dovoz: [text alebo "neuvedene"]
- Instalacia: [text alebo "neuvedene"]
- Poziadavky na priestor: [text alebo "neuvedene"]
- Bezpecnost: [text alebo "neuvedene"]

## FAQ (ak su na tejto stranke)
Tabulka FAQ:

| Otazka | Odpoved |
|--------|---------|

## REFERENCIE
- Citaty zakaznikov: [presne citacie ak existuju]
- Ratingy: [Google, Heureka, atd. ak uvedene]
- Certifikaty: [zoznam alebo "neuvedene"]

## GEOGRAFIA
- Hlavna oblast: [region/mesto]
- Vedlajsie oblasti: [region]

## SPECIALNE POZNAMKY
- [cokolvek dolezite co nezapada do inych kategorii]
"""
