"""Adapter MiniMax — API OpenAI-compatible, avec sa particularité isolée.

La seule particularité MiniMax utilisée par CONCLAVE — désactivation du
raisonnement approfondi `thinking` via `extra_body` — reste ici et ne
contamine jamais OpenAI, Anthropic ou Gemini.

MiniMax-M3 émet parfois, même `thinking` désactivé, des marqueurs de canal
internes (`]<]minimax[>[`, `<]minimax[>`) dans le contenu. Ils sont retirés
ici (particularité fournisseur) pour ne pas polluer le brouillon live ni
l'analyse d'enveloppe.
"""

from __future__ import annotations

import re
from typing import Any

from .openai_compatible import OpenAICompatibleAdapter
from .types import ProviderChunk, ProviderResult

MINIMAX_BASE_URL: str = "https://api.minimax.io/v1"
MINIMAX_DEFAULT_MODEL: str = "MiniMax-M3"

#: Marqueurs de canal internes MiniMax-M3 (délimiteurs de raisonnement/réponse).
_MINIMAX_MARKERS = re.compile(r"\]<\]minimax\[>\[|\[<\]minimax\[>\[|<\]minimax\[>\[?")


def _strip_markers(text: str | None) -> str:
    if not text:
        return text or ""
    return _MINIMAX_MARKERS.sub("", text)


#: Tarifs ESTIMATIFS MiniMax-M3 (USD / 1M jetons), standard <=512K. Vérifiés
#: le 19/08/2026 via des agrégateurs tiers (OpenRouter, TokenMix, TokenCost,
#: AI//COST). Uniquement des estimations : seule la facturation réelle du
#: fournisseur fait foi. Peut être surchargé par configuration.
MINIMAX_PRICING: dict[str, Any] = {
    "input_usd_per_million_tokens": 0.30,
    "output_usd_per_million_tokens": 1.20,
}


class MiniMaxAdapter(OpenAICompatibleAdapter):
    """MiniMax via le SDK `openai`, `thinking` désactivé.

    `max_retries=2` : MiniMax limite la concurrence et peut couper une
    connexion sous charge ; le SDK réessaie alors avec backoff (connexion,
    408, 429, 5xx) au lieu d'échouer l'analyse sur un incident transitoire.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = MINIMAX_DEFAULT_MODEL,
        base_url: str = MINIMAX_BASE_URL,
        timeout: float = 45.0,
        max_retries: int = 2,
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

    def _normalize_completion(self, completion: Any) -> ProviderResult:
        result = super()._normalize_completion(completion)
        for choice in result.choices:
            if choice.message.content:
                choice.message.content = _strip_markers(choice.message.content)
        return result

    def _normalize_chunk(self, chunk: Any) -> ProviderChunk:
        normalized = super()._normalize_chunk(chunk)
        if normalized.content_delta:
            normalized.content_delta = _strip_markers(normalized.content_delta)
        return normalized