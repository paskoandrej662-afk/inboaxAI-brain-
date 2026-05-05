"""Anthropic tool schemas for Coach Mode.

Each tool maps to a concrete database mutation in applier.py.
Schemas use Anthropic's tool-use format (input_schema = JSON Schema).
"""
from __future__ import annotations

from typing import Any

VALID_TONES = ("casual", "formal", "friendly", "professional")
VALID_ADDRESSING = ("tykanie", "vykanie")
VALID_LANGUAGES = ("sk", "cs", "en")
VALID_EMOJI = ("never", "sometimes", "often")
VALID_LENGTH = ("short", "medium", "long")
VALID_PERSONA_FIELDS = ("tone", "addressing", "language", "emoji_use", "length_preference")


UPDATE_PERSONA_FIELD: dict[str, Any] = {
    "name": "update_persona_field",
    "description": (
        "Aktualizuj jedno pole v brain_persona pre danú firmu. Použi pre zmeny "
        "ako tonalita, tykanie/vykanie, jazyk, emoji_use, length_preference."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "enum": list(VALID_PERSONA_FIELDS),
                "description": "Ktoré pole zmeniť.",
            },
            "value": {
                "type": "string",
                "description": (
                    "Nová hodnota. Pre tone: casual|formal|friendly|professional. "
                    "Pre addressing: tykanie|vykanie. Pre language: sk|cs|en. "
                    "Pre emoji_use: never|sometimes|often. "
                    "Pre length_preference: short|medium|long."
                ),
            },
        },
        "required": ["field", "value"],
    },
}

ADD_PERSONA_RULE: dict[str, Any] = {
    "name": "add_persona_rule",
    "description": (
        "Pridaj pozitívne pravidlo správania asistenta (napr. 'Pri rezerváciách vždy potvrď čas a meno'). "
        "Pravidlo bude pripojené k brain_persona.rules."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": "Pravidlo v slovenčine. Maximálne 200 znakov.",
                "maxLength": 200,
            },
        },
        "required": ["rule"],
    },
}

REMOVE_PERSONA_RULE: dict[str, Any] = {
    "name": "remove_persona_rule",
    "description": (
        "Odstráň pravidlo z brain_persona.rules podľa indexu (0-based) v aktuálnom poli rules."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rule_index": {
                "type": "integer",
                "minimum": 0,
                "description": "0-based index pravidla v rules array.",
            },
        },
        "required": ["rule_index"],
    },
}

ADD_NEGATIVE_FACT: dict[str, Any] = {
    "name": "add_negative_fact",
    "description": (
        "Pridaj negatívny fakt — čo firma NEROBÍ alebo NEPONÚKA "
        "(napr. 'Nefarbíme vlasy', 'Nerobíme manikúru'). "
        "Bude pridané do brain_persona.negative_facts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "Negatívny fakt v slovenčine. Maximálne 200 znakov.",
                "maxLength": 200,
            },
        },
        "required": ["fact"],
    },
}

UPSERT_FACT: dict[str, Any] = {
    "name": "upsert_fact",
    "description": (
        "Vlož alebo aktualizuj fakt v brain_facts. Existujúci záznam sa identifikuje "
        "podľa (company_id, key, subject). Pre ceny použi key='price' a subject ako "
        "názov služby (napr. 'Strihanie pánskych vlasov'). Pre kontakty použi "
        "key='phone'|'email'|'address'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Typ faktu: price, phone, email, hours, address, ico, dic, alebo iný stručný kľúč.",
            },
            "subject": {
                "type": ["string", "null"],
                "description": (
                    "Predmet faktu (napr. 'Strihanie' pre cenu strihu). "
                    "Pre unikátne fakty (1× telefón) môže byť null."
                ),
            },
            "value": {
                "type": "string",
                "description": (
                    "Konkrétna hodnota. Pre ceny ju formátuj ako '17 EUR' alebo '17.50 €'. "
                    "Nesmie byť 'neviem' ani prázdny string."
                ),
                "minLength": 1,
                "maxLength": 500,
            },
            "evidence": {
                "type": "string",
                "description": (
                    "Krátky citát alebo kontext odkiaľ pochádza fakt "
                    "(napr. originálny text od majiteľa firmy)."
                ),
                "maxLength": 500,
            },
        },
        "required": ["key", "value", "evidence"],
    },
}

DELETE_FACT: dict[str, Any] = {
    "name": "delete_fact",
    "description": (
        "Vymaž fakt z brain_facts identifikovaný podľa (key, subject)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Typ faktu."},
            "subject": {
                "type": ["string", "null"],
                "description": "Predmet faktu, alebo null ak je fakt jediný pre key.",
            },
        },
        "required": ["key"],
    },
}

ADD_FAQ: dict[str, Any] = {
    "name": "add_faq",
    "description": (
        "Pridaj otázku a odpoveď do brain_faqs. Otázka by mala byť taká, akú by zákazník reálne mohol položiť."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Otázka v slovenčine, max 500 znakov.",
                "minLength": 3,
                "maxLength": 500,
            },
            "answer": {
                "type": "string",
                "description": "Odpoveď v slovenčine, max 2000 znakov.",
                "minLength": 1,
                "maxLength": 2000,
            },
        },
        "required": ["question", "answer"],
    },
}

MARK_CHUNK_OUTDATED: dict[str, Any] = {
    "name": "mark_chunk_outdated",
    "description": (
        "Označ chunk v brain_chunks za zastaraný (nastav superseded_at = now()). "
        "Cieľ chunku sa identifikuje podľa target_text — server urobí vector search "
        "v rámci company_id a označí najpodobnejší aktívny chunk."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target_text": {
                "type": "string",
                "description": "Krátky popis alebo úryvok zastaranej informácie.",
                "minLength": 5,
                "maxLength": 500,
            },
            "reason": {
                "type": "string",
                "description": "Prečo je informácia zastaraná (napr. 'cena sa zmenila', 'služba zrušená').",
                "maxLength": 300,
            },
        },
        "required": ["target_text", "reason"],
    },
}

ADD_CHUNK: dict[str, Any] = {
    "name": "add_chunk",
    "description": (
        "Pridaj nový chunk do brain_chunks (source_type='coach:owner_update'). "
        "Použi keď majiteľ poskytne novú faktickú informáciu, ktorá zatiaľ nie je v KB."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Plný text chunku v slovenčine. Faktický, jasný, 50-1500 znakov.",
                "minLength": 50,
                "maxLength": 1500,
            },
            "section": {
                "type": "string",
                "description": "Sekcia: pricing|services|contact|about|faq|hours|home|general.",
            },
        },
        "required": ["text", "section"],
    },
}

REQUEST_CLARIFICATION: dict[str, Any] = {
    "name": "request_clarification",
    "description": (
        "Použi tento tool ak je zámer majiteľa nejasný a potrebuješ ďalšiu informáciu "
        "skôr, než urobíš zmenu (napr. 'zmeň cenu' a v DB je 5 cien — ktorú?)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Vysvetli prečo potrebuješ vyjasnenie.",
                "minLength": 5,
                "maxLength": 500,
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Konkrétne možnosti, z ktorých si môže majiteľ vybrať.",
                "maxItems": 10,
            },
            "question": {
                "type": "string",
                "description": "Konkrétna otázka pre majiteľa v slovenčine.",
                "minLength": 5,
                "maxLength": 300,
            },
        },
        "required": ["reason", "question"],
    },
}


COACH_TOOLS: list[dict[str, Any]] = [
    UPDATE_PERSONA_FIELD,
    ADD_PERSONA_RULE,
    REMOVE_PERSONA_RULE,
    ADD_NEGATIVE_FACT,
    UPSERT_FACT,
    DELETE_FACT,
    ADD_FAQ,
    MARK_CHUNK_OUTDATED,
    ADD_CHUNK,
    REQUEST_CLARIFICATION,
]


TOOL_NAMES = {t["name"] for t in COACH_TOOLS}


def get_tool(name: str) -> dict[str, Any] | None:
    for t in COACH_TOOLS:
        if t["name"] == name:
            return t
    return None
