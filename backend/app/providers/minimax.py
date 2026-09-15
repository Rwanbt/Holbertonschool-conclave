"""Adapter MiniMax — API OpenAI-compatible, avec sa particularité isolée.

La seule particularité MiniMax utilisée par CONCLAVE — désactivation du
raisonnement approfondi `thinking` via `extra_body` — reste ici et ne
contamine jamais OpenAI, Anthropic ou Gemini.
"""

from __future__ import annotations

from typing import Any

from .openai_compatible import OpenAICompatibleAdapter

MINIMAX_BASE_URL: str = "https://api.minimax.io/v1"
MINIMAX_DEFAULT_MODEL: str = "MiniMax-M3"

#: Tarifs ESTIMATIFS MiniMax-M3 (USD / 1M jetons), standard <=512K. Vérifiés
#: le 19/08/2026 via des agrégateurs tiers (OpenRouter, TokenMix, TokenCost,
#: AI//COST). Uniquement des estimations : seule la facturation réelle du
#: fournisseur fait foi. Peut être surchargé par configuration.
MINIMAX_PRICING: dict[str, Any] = {
    "input_usd_per_million_tokens": 0.30,
    "output_usd_per_million_tokens": 1.20,
}


class MiniMaxAdapter(OpenAICompatibleAdapter):
    """MiniMax via le SDK `openai`, `thinking` désactivé."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = MINIMAX_DEFAULT_MODEL,
        base_url: str = MINIMAX_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 1,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider_id="minimax",
            label="MiniMax",
            thinking_disabled=True,
            timeout=timeout,
            max_retries=max_retries,
        )

    def pricing(self) -> dict[str, Any] | None:
        return dict(MINIMAX_PRICING)