"""Adapter OpenAI — API officielle, sans aucune spécificité ajoutée."""

from __future__ import annotations

from typing import Any

from .openai_compatible import OpenAICompatibleAdapter

OPENAI_BASE_URL: str = "https://api.openai.com/v1"

#: Modèles autorisés par CONCLAVE. Tarifs officiels publics (USD / 1M jetons),
#: à la date d'implémentation — remplaçables par configuration. Seule la
#: facturation réelle d'OpenAI fait foi.
OPENAI_MODELS: dict[str, Any] = {
    "gpt-4o-mini": {
        "input_usd_per_million_tokens": 0.15,
        "output_usd_per_million_tokens": 0.60,
    },
    "gpt-4o": {
        "input_usd_per_million_tokens": 2.50,
        "output_usd_per_million_tokens": 10.00,
    },
    "gpt-4.1-mini": {
        "input_usd_per_million_tokens": 0.40,
        "output_usd_per_million_tokens": 1.60,
    },
    "gpt-4.1": {
        "input_usd_per_million_tokens": 2.00,
        "output_usd_per_million_tokens": 8.00,
    },
}


class OpenAIAdapter(OpenAICompatibleAdapter):
    """OpenAI officiel : aucun `extra_body`, aucune particularité."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = OPENAI_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 1,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider_id="openai",
            label="OpenAI",
            thinking_disabled=False,
            timeout=timeout,
            max_retries=max_retries,
        )

    def pricing(self) -> dict[str, Any] | None:
        price = OPENAI_MODELS.get(self.model)
        if price is None:
            return None
        return dict(price)