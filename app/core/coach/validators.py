from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.coach.proposal_generator import Proposal, ToolCall
from app.core.coach.tools import (
    VALID_ADDRESSING,
    VALID_EMOJI,
    VALID_LANGUAGES,
    VALID_LENGTH,
    VALID_PERSONA_FIELDS,
    VALID_TONES,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sanitized_proposal: Proposal | None = None


# Hostility / unsafe instruction patterns. Reject the whole proposal if matched
# in the original query OR in any tool argument.
_HOSTILITY_PATTERNS = [
    re.compile(r"\bspros[tť]o\b", re.IGNORECASE),
    re.compile(r"\bur[áa][žz](?:a|l)", re.IGNORECASE),
    re.compile(r"\bvulg[áa]r", re.IGNORECASE),
    re.compile(r"\bnad[áa]vaj", re.IGNORECASE),
    re.compile(r"\bklam(?:i|aj|me)?\b", re.IGNORECASE),
    re.compile(r"\boklam(?:i|aj|me)?\b", re.IGNORECASE),
    re.compile(r"\bzav[áa]dzaj", re.IGNORECASE),
    re.compile(r"\bdiskrimin", re.IGNORECASE),
    re.compile(r"\bracist", re.IGNORECASE),
    re.compile(r"\bsex(?:istic|izmus)", re.IGNORECASE),
    re.compile(r"\binsult\b", re.IGNORECASE),
    re.compile(r"\babuse\b", re.IGNORECASE),
    re.compile(r"\bdeceiv", re.IGNORECASE),
    re.compile(r"\boffensive\b", re.IGNORECASE),
]

# Secret/key patterns to strip + warn. Don't reject — owner may paste accidentally.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]


def detect_hostility(text: str) -> bool:
    if not text:
        return False
    for pat in _HOSTILITY_PATTERNS:
        if pat.search(text):
            return True
    return False


def strip_secrets(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    found = False
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            found = True
            text = pat.sub("[REDACTED]", text)
    return text, found


def _validate_persona_field(args: dict) -> str | None:
    f = args.get("field")
    v = args.get("value")
    if f not in VALID_PERSONA_FIELDS:
        return f"update_persona_field: neznáme pole '{f}'"
    if not isinstance(v, str) or not v:
        return f"update_persona_field: hodnota musí byť neprázdny string"
    if f == "tone" and v not in VALID_TONES:
        return f"tone musí byť jeden z {sorted(VALID_TONES)}"
    if f == "addressing" and v not in VALID_ADDRESSING:
        return f"addressing musí byť tykanie alebo vykanie"
    if f == "language" and v not in VALID_LANGUAGES:
        return f"language musí byť sk|cs|en"
    if f == "emoji_use" and v not in VALID_EMOJI:
        return f"emoji_use musí byť never|sometimes|often"
    if f == "length_preference" and v not in VALID_LENGTH:
        return f"length_preference musí byť short|medium|long"
    return None


def _validate_rule_or_neg(args: dict, key: str) -> str | None:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        return f"{key} musí byť neprázdny text"
    if len(v) > 200:
        return f"{key} max 200 znakov (má {len(v)})"
    return None


def _validate_upsert_fact(args: dict) -> str | None:
    if not args.get("key"):
        return "upsert_fact: key je povinný"
    v = args.get("value")
    if not isinstance(v, str) or not v.strip():
        return "upsert_fact: value musí byť neprázdny string"
    if v.lower().strip() in ("neviem", "neviem.", "nevime", "nevíme", "?"):
        return "upsert_fact: value nesmie byť 'neviem'"
    if len(v) > 500:
        return f"upsert_fact: value max 500 znakov (má {len(v)})"
    if not args.get("evidence") or not str(args.get("evidence")).strip():
        return "upsert_fact: evidence je povinný"
    return None


def _validate_add_chunk(args: dict) -> str | None:
    text = args.get("text")
    section = args.get("section")
    if not isinstance(text, str) or len(text) < 50:
        return "add_chunk: text musí mať aspoň 50 znakov"
    if len(text) > 1500:
        return "add_chunk: text max 1500 znakov"
    if not isinstance(section, str) or not section:
        return "add_chunk: section je povinný"
    return None


def _validate_add_faq(args: dict) -> str | None:
    q = args.get("question")
    a = args.get("answer")
    if not isinstance(q, str) or len(q) < 3:
        return "add_faq: question musí mať aspoň 3 znaky"
    if not isinstance(a, str) or not a.strip():
        return "add_faq: answer je povinný"
    if len(q) > 500 or len(a) > 2000:
        return "add_faq: question max 500, answer max 2000 znakov"
    return None


def _validate_mark_chunk_outdated(args: dict) -> str | None:
    t = args.get("target_text")
    if not isinstance(t, str) or len(t) < 5:
        return "mark_chunk_outdated: target_text musí mať aspoň 5 znakov"
    return None


def validate_proposal(
    proposal: Proposal,
    requested_company_id: str,
    original_query: str,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    # Hostility check on original query
    if detect_hostility(original_query):
        errors.append(
            "Tento návrh nemôžem aplikovať. Asistent musí byť k zákazníkom korektný a slušný."
        )

    # Strip secrets from preview + tool args
    sanitized_calls: list[ToolCall] = []
    for tc in proposal.tool_calls:
        new_args = dict(tc.args)
        for k, v in list(new_args.items()):
            if isinstance(v, str):
                new_v, found = strip_secrets(v)
                if found:
                    new_args[k] = new_v
                    warnings.append(
                        f"V argumente '{k}' tool '{tc.name}' bol nájdený citlivý token a bol odstránený."
                    )
        sanitized_calls.append(ToolCall(name=tc.name, args=new_args))

    # Cross-tenant: every tool implicitly targets company_id from request.
    # Reject if proposal.company_id != requested_company_id.
    if proposal.company_id != requested_company_id:
        errors.append(
            f"Cross-tenant guard: proposal company_id ({proposal.company_id}) "
            f"sa nezhoduje s requestom ({requested_company_id})."
        )

    # Per-tool validation
    for tc in sanitized_calls:
        err: str | None = None
        if tc.name == "update_persona_field":
            err = _validate_persona_field(tc.args)
        elif tc.name == "add_persona_rule":
            err = _validate_rule_or_neg(tc.args, "rule")
        elif tc.name == "remove_persona_rule":
            idx = tc.args.get("rule_index")
            if not isinstance(idx, int) or idx < 0:
                err = "remove_persona_rule: rule_index musí byť non-negative integer"
        elif tc.name == "add_negative_fact":
            err = _validate_rule_or_neg(tc.args, "fact")
        elif tc.name == "upsert_fact":
            err = _validate_upsert_fact(tc.args)
        elif tc.name == "delete_fact":
            if not tc.args.get("key"):
                err = "delete_fact: key je povinný"
        elif tc.name == "add_faq":
            err = _validate_add_faq(tc.args)
        elif tc.name == "mark_chunk_outdated":
            err = _validate_mark_chunk_outdated(tc.args)
        elif tc.name == "add_chunk":
            err = _validate_add_chunk(tc.args)
        elif tc.name == "request_clarification":
            pass  # always allowed
        else:
            err = f"unknown tool '{tc.name}'"

        if err:
            errors.append(f"{tc.name}: {err}")

        # Also hostility-check tool string args
        for k, v in tc.args.items():
            if isinstance(v, str) and detect_hostility(v):
                errors.append(f"{tc.name}.{k}: obsahuje neprípustný obsah (hostility/abuse).")

    sanitized = Proposal(
        proposal_id=proposal.proposal_id,
        company_id=proposal.company_id,
        tool_calls=sanitized_calls,
        preview_text=strip_secrets(proposal.preview_text)[0],
        preview_diff=proposal.preview_diff,
        needs_clarification=proposal.needs_clarification,
        clarification=proposal.clarification,
        proposal_hash=proposal.proposal_hash,
        intent=proposal.intent,
        created_at=proposal.created_at,
        raw_response=(strip_secrets(proposal.raw_response or "")[0] if proposal.raw_response else None),
        token_usage=proposal.token_usage,
    )

    return ValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        sanitized_proposal=sanitized if not errors else None,
    )
