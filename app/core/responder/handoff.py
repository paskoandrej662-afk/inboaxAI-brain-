from __future__ import annotations

import re

# Slovak + English signals that the customer wants a human or has a complaint.
_HANDOFF_PATTERNS = [
    re.compile(r"\bchcem hovori[ťt]\b", re.IGNORECASE),
    re.compile(r"\bchcem (?:s )?(?:mana[žz]|maj?ite|[čc]love)", re.IGNORECASE),
    re.compile(r"\bmana[žz][ée]ra?\b", re.IGNORECASE),
    re.compile(r"\bmaj?ite[ľl]a?\b", re.IGNORECASE),
    re.compile(r"\bs[ťt]a[žz]nos[ťt]\b", re.IGNORECASE),
    re.compile(r"\bnespokojn", re.IGNORECASE),
    re.compile(r"\bnahnevan", re.IGNORECASE),
    re.compile(r"\breklam[áa]ci", re.IGNORECASE),
    re.compile(r"\bvr[áa]ti[ťt] peniaze\b", re.IGNORECASE),
    re.compile(r"\brefund", re.IGNORECASE),
    re.compile(r"\b(speak|talk) to (?:a )?(?:human|manager|person)\b", re.IGNORECASE),
    re.compile(r"\bcomplaint\b", re.IGNORECASE),
    re.compile(r"\bhuman agent\b", re.IGNORECASE),
]


def detect_handoff_signals(query: str, route: str | None = None) -> bool:
    if route == "handoff":
        return True
    if not query:
        return False
    for pat in _HANDOFF_PATTERNS:
        if pat.search(query):
            return True
    return False


# Prompt-injection heuristic — not a security boundary, only a flag for the audit log.
_INJECTION_PATTERNS = [
    re.compile(r"\bignore (?:all |the |previous |prior )?instructions?\b", re.IGNORECASE),
    re.compile(r"\bdisregard (?:all |the |previous |prior )?instructions?\b", re.IGNORECASE),
    re.compile(r"\bignoruj\s+(?:všetky|predošlé|predchádzajúce)\s+(?:pokyny|inštrukcie)", re.IGNORECASE),
    re.compile(r"\byou are now\b", re.IGNORECASE),
    re.compile(r"\bteraz si\b", re.IGNORECASE),
    re.compile(r"\bsystem\s*prompt\b", re.IGNORECASE),
    re.compile(r"\bsystémov[ýy] prompt\b", re.IGNORECASE),
    re.compile(r"^\s*system\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\breveal (?:your )?(?:system )?prompt\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\bDAN mode\b", re.IGNORECASE),
    re.compile(r"\bact as (?:a |an )?(?:dan|jailbroken|unfiltered)\b", re.IGNORECASE),
]


def detect_prompt_injection(query: str) -> bool:
    if not query:
        return False
    for pat in _INJECTION_PATTERNS:
        if pat.search(query):
            return True
    return False
