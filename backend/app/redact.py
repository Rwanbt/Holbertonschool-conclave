"""Redaction centralisée des secrets avant log, SSE, base ou réponse HTTP.

Toute chaîne destinée à être journalisée, diffusée, persistée ou renvoyée
doit passer par `redact_text` : une clé API, un en-tête Authorization ou une
réponse provider éventuellement capturée dans une erreur est masquée avant
de quitter le backend.
"""

from __future__ import annotations

import re
from typing import Iterable

_MASK = "[REDACTED]"

#: Motifs de clés réalistes (OpenAI/MiniMax/Anthropic/Gemini/DeepSeek…).
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"x-api-key[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"x-goog-api-key[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key[=:]\s*\S{16,}", re.IGNORECASE),
    re.compile(r"Authorization[=:]\s*\S+", re.IGNORECASE),
)


class SecretRedactor:
    """Redacteur configuré avec un jeu de valeurs connues + motifs."""

    def __init__(self, known_secrets: Iterable[str] = ()):
        self._known = {value for value in known_secrets if value and len(value) >= 8}

    def add(self, value: str | None) -> None:
        if value and len(value) >= 8:
            self._known.add(value)

    def redact(self, text: str | None) -> str | None:
        if text is None:
            return None
        result = text
        for value in self._known:
            if value in result:
                result = result.replace(value, _MASK)
        for pattern in _SECRET_PATTERNS:
            result = pattern.sub(_MASK, result)
        return result


#: Redacteur global partagé : chaque credential runtime y est enregistré avant
#: toute manipulation susceptible de fuiter.
_global_redactor = SecretRedactor()


def register_secret(value: str | None) -> None:
    """Enregistre un credential runtime pour qu'il soit masqué partout."""
    _global_redactor.add(value)


def redact_text(text: str | None) -> str | None:
    """Masque les secrets connus et les motifs de clés dans `text`."""
    return _global_redactor.redact(text)


def redact_payload(payload: dict) -> dict:
    """Redaction récursive d'un dictionnaire (événements SSE, traces)."""
    return _redact_value(payload)


def _redact_value(value):
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _global_redactor.redact(value) or value
    return value


def build_secret_redactor(known_secrets: Iterable[str]) -> SecretRedactor:
    """Redacteur isolé pour les tests (ne touche pas l'état global)."""
    return SecretRedactor(known_secrets)