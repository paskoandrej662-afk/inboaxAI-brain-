"""Generate company operating manual (persona) from extracted markdown.

Second Gemini call (after extraction). Synthesizes 2000-word manual that the
chatbot uses as RAG context per conversation. Temperature 0.3 (slightly higher
than extraction's 0.1) to allow stylistic synthesis without halucinacii.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from google import genai
from google.genai import types as genai_types

from app.core.extractors.hds_v3.types import GeminiBatchResult

logger = logging.getLogger(__name__)


PERSONA_GENERATION_PROMPT = """Si business analyst. Mam tu kompletny obsah webu firmy
extrahovany z {n} podstranok. Tvoja uloha: vytvorit KOMPLETNY OPERATING MANUAL
pre AI chatbota tejto firmy.

Tento manual bude pouzity PRI KAZDEJ konverzacii so zakaznikom.
Musi byt dostatocne podrobny aby AI vedela odpovedat ako ZAMESTNANEC firmy.

===============================================================
STRUKTURA MANUALU (DODRZIAVAJ DOSLOVA):
===============================================================

## 1. PROFIL FIRMY (KTO SME)
- Nazov, sidlo, rok zalozenia (ak uvedene)
- Specializacia v 1 vete
- Velkost (rodinna, mala, stredna - odhadni z webu)
- Hlavna oblast posobenia (geografia)
- Sales hooks (co zdoraznujeme - citujeme z headlinov webu)

## 2. HLAVNE SLUZBY/PRODUKTY (CO ROBIME)
- Zoznam top 5-10 sluzieb/produktov
- Cenove rozpatie (od-do)
- Pre koho je to (target segment)

## 3. KOMUNIKACNY STYL
- Tone: formalny / neformalny / odborny (rozhodni podla webu)
- Persona oslovenia: vy / ty (rozhodni podla webu)
- 3-5 typickych fraz ktore firma pouziva (ODCITUJ ZO SKUTOCNEHO WEBU)
- Co NEHOVORIME (corporate buzzwords ak ich firma nepouziva)

## 4. KONVERZACNE PATTERNY
Pre KAZDU z tychto 8 typov otazok napis:
- Priklad otazky zakaznika
- Idealna AI odpoved (v style firmy, na zaklade skutocnych dat z webu)
- Klucove informacie ktore musime uviest

a) "Kolko stoji X?" -> cena + co zahrna + jednotka
b) "Som z [mesto]" -> doprava/dostupnost/cestne podmienky
c) "Mame [vek/pocet] deti" alebo "Mame [spec. potrebu]" -> odporucanie produktu
d) "Robite aj v [ine mesto/oblast]" -> geograficke pokrytie
e) "Kedy mozete prist?" -> casove podmienky, otvaracie hodiny
f) "Ake formy platby?" -> platobne podmienky
g) "Bolo by mozne [specialna poziadavka]?" -> flexibility, hranice
h) "Hladam info pre [oslavu/firmu/akciu]" -> identifikacia potreby + odporucanie

## 5. PREDAJNE STRATEGIE
- Pri vahani zakaznika - co odporucit (najoblubenejsi/cenovo dobry produkt - IDENTIFIKUJ Z WEBU)
- Pri vacsej akcii - ako kombinovat produkty
- Pri rozpoctovo citlivom zakaznikovi - lacnejsia alternativa (IDENTIFIKUJ Z CENNIKA)
- Cross-sell prilezitosti (ake produkty idu spolu)
- Up-sell prilezitosti (premium varianty)

## 6. HRANICE (CO NEROBIME)
- Geograficke (ak je hranica - napr. iba urcite okresy)
- Casove (otvaracie hodiny, sezonne obmedzenia)
- Service hranice (co NIE JE v ponuke)
- Kapacita (max velkost akcie)

## 7. ESKALACIA - KEDY POSLAT NA CLOVEKA
Definuj presne situacie kedy AI POVEDIE "zavolajte nam":
- Reklamacie a staznosti
- Specialne ceny (zlavy, B2B)
- Komplexne objednavky (multi-day, multi-location)
- Neistota o dostupnosti terminu
- Ak zakaznik vyzaduje zaruku ktora nie je na webe
- Akciova cena, ktora nie je publikovana

Forma odporucania: "Pre presne info a rezervaciu volajte {{realny_telefon_z_webu}}"

## 8. EMOCIONALNE PATTERNY
Ako AI reaguje na rozne emocionalne tony:
- Naliehavost (sobota, urgent) -> potvrd + rychle akcie
- Stres (mama planujuca oslavu) -> upokoj + sumar moznosti
- Skepsa (porovnava ponuky) -> benefits + socialny proof (referencie ak existuju)
- Spokojnost/podakovanie -> potvrdenie + ponuka pre dalsie akcie

===============================================================
PRAVIDLA:
===============================================================
- KONKRETNE patterny, nie vseobecne slogany
- IBA z obsahu webu - ziadne vymyslene informacie
- Slovencina, prirodzena
- Cielova dlzka: 2000 slov celkovo
- Ak nieco nie je na webe -> "neuvedene, eskalovat na cloveka"
- Citujeme skutocne telefonne cisla, ceny, mena produktov z webu

===============================================================
OBSAH WEBU (Z {n} PODSTRANOK):
===============================================================

{markdown_obsah}
"""


class PersonaGenerator:
    """Generates 2000-word company operating manual from extracted markdown."""

    MODEL_NAME = "gemini-2.5-flash"
    INPUT_TOKEN_PRICE_PER_1M = 0.30
    OUTPUT_TOKEN_PRICE_PER_1M = 2.50
    TIMEOUT_SEC = 90

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not set")
        self.client = genai.Client(api_key=key)

    async def generate(self, batches: list[GeminiBatchResult]) -> dict:
        """Generate persona from successful batches.

        Returns dict with persona_text, word_count, tokens, cost, source_urls.
        """
        start = time.time()

        successful_batches = [b for b in batches if b.success and b.markdown]
        all_urls: list[str] = []
        for b in successful_batches:
            all_urls.extend(b.urls)

        empty_result = {
            "success": False,
            "error": "no_successful_batches",
            "persona_text": "",
            "word_count": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "duration_sec": 0.0,
            "source_urls": all_urls,
        }

        if not successful_batches:
            return empty_result

        combined_markdown = "\n\n".join(b.markdown for b in successful_batches)
        prompt = PERSONA_GENERATION_PROMPT.format(
            n=len(all_urls),
            markdown_obsah=combined_markdown,
        )

        try:
            response = await asyncio.wait_for(
                self._gemini_call(prompt),
                timeout=self.TIMEOUT_SEC,
            )

            text = getattr(response, "text", None) or ""
            if (
                not text.strip()
                and hasattr(response, "candidates")
                and response.candidates
            ):
                cand = response.candidates[0]
                if hasattr(cand, "content") and cand.content:
                    text = "".join(
                        getattr(p, "text", "") or "" for p in cand.content.parts
                    )

            if not text.strip():
                raise ValueError("Empty persona response")

            tokens_in = 0
            tokens_out = 0
            um = getattr(response, "usage_metadata", None)
            if um is not None:
                tokens_in = getattr(um, "prompt_token_count", 0) or 0
                tokens_out = getattr(um, "candidates_token_count", 0) or 0

            cost = (
                tokens_in * self.INPUT_TOKEN_PRICE_PER_1M / 1_000_000
                + tokens_out * self.OUTPUT_TOKEN_PRICE_PER_1M / 1_000_000
            )
            word_count = len(text.split())

            return {
                "success": True,
                "error": None,
                "persona_text": text,
                "word_count": word_count,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost,
                "duration_sec": time.time() - start,
                "source_urls": all_urls,
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("Persona generation failed")
            return {
                "success": False,
                "error": str(e)[:200],
                "persona_text": "",
                "word_count": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "duration_sec": time.time() - start,
                "source_urls": all_urls,
            }

    async def _gemini_call(self, prompt: str):
        return await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.MODEL_NAME,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=8192,
            ),
        )
