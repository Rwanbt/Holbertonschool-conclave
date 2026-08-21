"""Fonctions métier déterministes du Palier 3 — aucun effet de bord.

Ce module contient les FONCTIONS MÉTIER : `measure_document`,
`find_security_indicators` et `estimate_analysis_cost`. Elles sont pures
(pas de réseau, pas d'écriture, pas de coût facturé) et sont testées
unitairement sans jamais être simulées.

Les ADAPTATEURS (qui exposent le document chargé sur le serveur aux agents)
vivent dans `agent.py`. Rien de ce module ne reçoit ni ne transmet de
document à un tiers.
"""

import re
from typing import Any

MAX_DOCUMENT_LENGTH: int = 12_000
MAX_FINDINGS: int = 10
MAX_MATCHED_TEXT_LENGTH: int = 40
TOKENS_PER_CHARACTER_ESTIMATE: float = 4.0

SECURITY_CATEGORIES: tuple[str, ...] = (
    "secret",
    "authentication",
    "authorization",
    "injection",
    "privacy",
    "availability",
)


class InvalidDocumentError(ValueError):
    """Le document est vide ou dépasse la limite de taille."""


class InvalidPatternError(ValueError):
    """Un motif de sécurité est invalide et a été rejeté avant analyse."""


class UnknownPricingError(ValueError):
    """Aucun tarif configuré pour le modèle demandé."""


DEFAULT_SECURITY_PATTERNS: tuple[dict[str, str], ...] = (
    {"name": "api_key_assignment", "literal_or_regex": r"re:(?i)(api[_-]?key|secret|token)\s*[:=]\s*\S{8,}", "category": "secret"},
    {"name": "password_assignment", "literal_or_regex": r"re:(?i)password\s*[:=]\s*\S{6,}", "category": "secret"},
    {"name": "private_key_block", "literal_or_regex": r"re:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "category": "secret"},
    {"name": "bearer_token", "literal_or_regex": r"re:(?i)bearer\s+[A-Za-z0-9._-]{10,}", "category": "authentication"},
    {"name": "basic_auth", "literal_or_regex": r"re:(?i)authorization\s*[:=]\s*basic\s+\S+", "category": "authentication"},
    {"name": "grant_all_privileges", "literal_or_regex": r"re:(?i)grant\s+all\s+privileges", "category": "authorization"},
    {"name": "eval_call", "literal_or_regex": r"re:(?i)\beval\s*\(", "category": "injection"},
    {"name": "exec_call", "literal_or_regex": r"re:(?i)\bexec\s*\(", "category": "injection"},
    {"name": "sql_concat", "literal_or_regex": r"re:(?i)(select|insert|update|delete)\b[^\n]*\+\s*(\"|')", "category": "injection"},
    {"name": "email_literal", "literal_or_regex": r"re:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "category": "privacy"},
    {"name": "unbounded_attempts", "literal_or_regex": r"re:(?i)(max[._-]?attempts|retries)\s*[:=]\s*(?:0|-1|999|infinity)", "category": "availability"},
    {"name": "timeout_disabled", "literal_or_regex": r"re:(?i)(?:timeout|read[._-]?timeout|connect[._-]?timeout)\s*[:=]\s*(?:0|-1|none|null)", "category": "availability"},
)


def _compile_pattern(pattern: dict[str, Any]) -> re.Pattern[str] | str:
    """Valide un motif et le compile : préfixe `re:` -> regex, sinon littéral."""
    name = str(pattern.get("name", "")).strip()
    raw = str(pattern.get("literal_or_regex", "")).strip()
    category = str(pattern.get("category", "")).strip()
    if not name:
        raise InvalidPatternError("pattern name is empty")
    if not raw:
        raise InvalidPatternError(f"pattern '{name}' has an empty literal_or_regex")
    if category not in SECURITY_CATEGORIES:
        raise InvalidPatternError(
            f"pattern '{name}' has unknown category '{category}'"
        )
    if raw.startswith("re:"):
        try:
            return re.compile(raw[3:])
        except re.error as exc:
            raise InvalidPatternError(
                f"pattern '{name}' has an invalid regex: {exc}"
            ) from exc
    return raw


def _literal_matches(text: str, needle: str):
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return
        yield index, needle
        start = index + len(needle)


def _mask_interior(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def _mask_text(value: str) -> str:
    masked = _mask_interior(value)
    if len(masked) > MAX_MATCHED_TEXT_LENGTH:
        return masked[: MAX_MATCHED_TEXT_LENGTH - 1] + "…"
    return masked


def measure_document(text: str) -> dict[str, int]:
    """Calcule les métriques d'un document. Échec : `InvalidDocumentError`."""
    if not text or not text.strip():
        raise InvalidDocumentError("document is empty")
    if len(text) > MAX_DOCUMENT_LENGTH:
        raise InvalidDocumentError(
            f"document exceeds {MAX_DOCUMENT_LENGTH} characters"
        )
    character_count = len(text)
    word_count = len(text.split())
    line_count = len(text.splitlines())
    estimated_input_tokens = max(1, round(character_count / TOKENS_PER_CHARACTER_ESTIMATE))
    return {
        "character_count": character_count,
        "word_count": word_count,
        "line_count": line_count,
        "estimated_input_tokens": estimated_input_tokens,
    }


def find_security_indicators(
    text: str,
    patterns: tuple[dict[str, Any], ...] = DEFAULT_SECURITY_PATTERNS,
) -> list[dict[str, Any]]:
    """Localise des indices factuels (jamais une certification).

    Retourne au plus `MAX_FINDINGS` indices, triés par ligne puis nom de
    motif. Chaque `matched_text` est masqué puis tronqué. Un motif invalide
    est rejeté avant toute analyse. Échec : `InvalidPatternError`,
    `InvalidDocumentError` si le texte dépasse la limite.
    """
    if not text or not text.strip():
        return []
    if len(text) > MAX_DOCUMENT_LENGTH:
        raise InvalidDocumentError(
            f"document exceeds {MAX_DOCUMENT_LENGTH} characters"
        )
    compiled = {p["name"]: _compile_pattern(p) for p in patterns}
    findings: list[dict[str, Any]] = []
    for pattern in patterns:
        name = pattern["name"]
        matcher = compiled[name]
        if isinstance(matcher, re.Pattern):
            occurrences = (
                (match.start(), match.group(0)) for match in matcher.finditer(text)
            )
        else:
            occurrences = _literal_matches(text, matcher)
        for start, matched in occurrences:
            if len(findings) >= MAX_FINDINGS:
                break
            line_number = text.count("\n", 0, start) + 1
            findings.append(
                {
                    "pattern_name": name,
                    "matched_text": _mask_text(matched),
                    "line_number": line_number,
                    "category": pattern["category"],
                }
            )
    findings.sort(key=lambda finding: (finding["line_number"], finding["pattern_name"]))
    return findings[:MAX_FINDINGS]


def estimate_analysis_cost(
    input_tokens: int,
    output_token_budget: int,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    """Estime un coût d'analyse de façon déterministe (USD, arrondi 6 déc.).

    `pricing` = {"model_name", "input_usd_per_million_tokens",
    "output_usd_per_million_tokens"}. Quand les tarifs sont absents, nuls,
    négatifs ou non numériques, l'estimation reste contrôlée : le coût vaut
    `None` et `pricing_configured` vaut `False`.
    """
    if input_tokens < 0 or output_token_budget < 0:
        raise ValueError("token counts must be non-negative")

    model_name = str(pricing.get("model_name", "unknown")).strip() or "unknown"
    input_price = pricing.get("input_usd_per_million_tokens")
    output_price = pricing.get("output_usd_per_million_tokens")
    try:
        input_price_f = float(input_price) if input_price is not None else None
        output_price_f = float(output_price) if output_price is not None else None
    except (TypeError, ValueError):
        input_price_f = output_price_f = None
    if (
        input_price_f is None
        or output_price_f is None
        or input_price_f <= 0.0
        or output_price_f <= 0.0
    ):
        return {
            "model_name": model_name,
            "input_tokens": input_tokens,
            "output_token_budget": output_token_budget,
            "estimated_cost_usd": None,
            "currency": "USD",
            "pricing_configured": False,
        }
    cost_usd = (
        input_tokens * input_price_f + output_token_budget * output_price_f
    ) / 1_000_000.0
    return {
        "model_name": model_name,
        "input_tokens": input_tokens,
        "output_token_budget": output_token_budget,
        "estimated_cost_usd": round(cost_usd, 6),
        "currency": "USD",
        "pricing_configured": True,
    }
