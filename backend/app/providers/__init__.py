"""Abstraction provider de CONCLAVE.

Agent → Provider abstraction → Provider concret.

`build_provider` est la SEULE fabrique utilisée par l'application : elle
résout un provider/model autorisé depuis le registre et lui injecte le
credential RUNTIME (clé BYOK de l'utilisateur, jamais persistée).
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter, ProviderCapabilityMissingError, ProviderError
from .registry import (
    REGISTRY,
    ALLOWED_BASE_URLS,
    SUPPORTED_PROVIDER_IDS,
    create_adapter,
    list_provider_specs,
    model_entry,
    provider_pricing,
)
from .types import (
    ProviderChunk,
    ProviderChoice,
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
    ProviderToolCallDelta,
    ProviderUsage,
)

__all__ = [
    "ProviderAdapter",
    "ProviderCapabilityMissingError",
    "ProviderError",
    "ProviderChunk",
    "ProviderChoice",
    "ProviderMessage",
    "ProviderResult",
    "ProviderToolCall",
    "ProviderToolCallDelta",
    "ProviderUsage",
    "REGISTRY",
    "ALLOWED_BASE_URLS",
    "SUPPORTED_PROVIDER_IDS",
    "build_provider",
    "create_adapter",
    "list_provider_specs",
    "model_entry",
    "provider_pricing",
]


def build_provider(
    *,
    provider_id: str,
    model_id: str,
    api_key: str | None,
    settings: Any,
    auth_mode: str = "api_key",
) -> ProviderAdapter:
    """Fabrique l'adapter pour une sélection + un credential runtime.

    - `api_key` (BYOK) a toujours priorité ;
    - à défaut, le credential serveur (dev/smoke uniquement) n'est utilisé que
      si `ALLOW_SERVER_PROVIDER_CREDENTIALS=true` : JAMAIS de fallback
      silencieux vers la clé du propriétaire ;
    - ni clé ni permission -> `ProviderError(provider_auth_failed)`.
    - `auth_mode="oauth"` (Google Gemini officiel seulement) : `api_key`
      contient l'access token OAuth.
    """
    if not api_key:
        if not getattr(settings, "allow_server_provider_credentials", False):
            raise ProviderError(
                "provider_auth_failed",
                "Aucune clé API fournie. Connectez votre fournisseur "
                "dans l'interface (BYOK) puis relancez.",
            )
        api_key = _server_credential(settings, provider_id)
    if not api_key:
        raise ProviderError(
            "provider_auth_failed",
            "Aucune clé API fournie et aucune clé serveur configurée.",
        )
    return create_adapter(provider_id, model_id, api_key, auth_mode=auth_mode)


def _server_credential(settings: Any, provider_id: str) -> str | None:
    """Clé serveur DEV/SMOKE (désactivée par défaut en production)."""
    keys = {
        "minimax": getattr(settings, "minimax_api_key", ""),
        "openai": getattr(settings, "openai_api_key", ""),
        "anthropic": getattr(settings, "anthropic_api_key", ""),
        "gemini": getattr(settings, "gemini_api_key", ""),
    }
    return keys.get(provider_id) or None