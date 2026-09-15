"""Registre contrôlé des providers et modèles — aucune base URL libre.

Le frontend construit son sélecteur depuis `GET /api/providers` (métadonnées
publiques uniquement). L'ajout d'un nouveau provider OpenAI-compatible
(DeepSeek, Mistral, Groq, …) consiste à déclarer ici son endpoint officiel
(allowlist) et sa fabrique : la base URL n'est JAMAIS saisie par l'utilisateur,
afin d'éviter une primitive SSRF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .base import ProviderAdapter, ProviderError
from .minimax import MINIMAX_PRICING, MINIMAX_DEFAULT_MODEL, MiniMaxAdapter
from .openai import OPENAI_MODELS, OPENAI_BASE_URL, OpenAIAdapter
from .anthropic import ANTHROPIC_BASE_URL, AnthropicAdapter
from .gemini import GEMINI_BASE_URL, GeminiAdapter


@dataclass(frozen=True)
class ModelEntry:
    model_id: str
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = True
    pricing: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderEntry:
    provider_id: str
    label: str
    auth_modes: list[str]
    models: list[ModelEntry]
    factory: Callable[[str, str], ProviderAdapter]
    supports_reasoning: bool = False


#: Fabrique -> (api_key, model_id) -> adapter configuré.
def _openai_factory(api_key: str, model_id: str) -> ProviderAdapter:
    return OpenAIAdapter(api_key=api_key, model=model_id)


def _minimax_factory(api_key: str, model_id: str) -> ProviderAdapter:
    return MiniMaxAdapter(api_key=api_key, model=model_id)


def _anthropic_factory(api_key: str, model_id: str) -> ProviderAdapter:
    return AnthropicAdapter(api_key=api_key, model=model_id)


def _gemini_factory(api_key: str, model_id: str) -> ProviderAdapter:
    return GeminiAdapter(api_key=api_key, model=model_id)


def _minimax_models() -> list[ModelEntry]:
    return [
        ModelEntry(
            model_id=MINIMAX_DEFAULT_MODEL,
            pricing=dict(MINIMAX_PRICING),
        )
    ]


def _openai_models() -> list[ModelEntry]:
    return [
        ModelEntry(model_id=model_id, pricing=dict(price))
        for model_id, price in OPENAI_MODELS.items()
    ]


REGISTRY: dict[str, ProviderEntry] = {
    "minimax": ProviderEntry(
        provider_id="minimax",
        label="MiniMax",
        auth_modes=["api_key"],
        models=_minimax_models(),
        factory=_minimax_factory,
        supports_reasoning=False,
    ),
    "openai": ProviderEntry(
        provider_id="openai",
        label="OpenAI",
        auth_modes=["api_key"],
        models=_openai_models(),
        factory=_openai_factory,
        supports_reasoning=True,
    ),
    "anthropic": ProviderEntry(
        provider_id="anthropic",
        label="Anthropic",
        auth_modes=["api_key"],
        models=[
            ModelEntry(
                model_id="claude-3-5-haiku-latest",
                pricing={
                    "input_usd_per_million_tokens": 0.80,
                    "output_usd_per_million_tokens": 4.00,
                },
            ),
            ModelEntry(
                model_id="claude-3-5-sonnet-latest",
                pricing={
                    "input_usd_per_million_tokens": 3.00,
                    "output_usd_per_million_tokens": 15.00,
                },
            ),
        ],
        factory=_anthropic_factory,
        supports_reasoning=True,
    ),
    "gemini": ProviderEntry(
        provider_id="gemini",
        label="Google Gemini",
        auth_modes=["api_key"],
        models=[
            ModelEntry(
                model_id="gemini-2.0-flash",
                pricing={
                    "input_usd_per_million_tokens": 0.10,
                    "output_usd_per_million_tokens": 0.40,
                },
            ),
            ModelEntry(
                model_id="gemini-2.5-flash",
                pricing={
                    "input_usd_per_million_tokens": 0.30,
                    "output_usd_per_million_tokens": 2.50,
                },
            ),
        ],
        factory=_gemini_factory,
        supports_reasoning=True,
    ),
}

#: Endpoints autorisés, utilisés par l'audit et la documentation. Aucun
#: endpoint arbitraire ne peut être ajouté à l'exécution.
ALLOWED_BASE_URLS: tuple[str, ...] = (
    "https://api.minimax.io/v1",
    OPENAI_BASE_URL,
    ANTHROPIC_BASE_URL,
    GEMINI_BASE_URL,
)

SUPPORTED_PROVIDER_IDS: frozenset[str] = frozenset(REGISTRY)


def list_provider_specs() -> list[dict[str, Any]]:
    """Métadonnées PUBLIQUES de chaque provider (aucun secret)."""
    specs: list[dict[str, Any]] = []
    for entry in REGISTRY.values():
        specs.append(
            {
                "provider_id": entry.provider_id,
                "label": entry.label,
                "auth_modes": entry.auth_modes,
                "supports_tools": True,
                "supports_streaming": True,
                "supports_structured_output": True,
                "supports_reasoning": entry.supports_reasoning,
                "models": [
                    {
                        "model_id": model.model_id,
                        "supports_tools": model.supports_tools,
                        "supports_streaming": model.supports_streaming,
                        "supports_structured_output": model.supports_structured_output,
                        "pricing": model.pricing,
                    }
                    for model in entry.models
                ],
            }
        )
    return specs


def get_provider_spec(provider_id: str) -> dict[str, Any] | None:
    for spec in list_provider_specs():
        if spec["provider_id"] == provider_id:
            return spec
    return None


def model_entry(provider_id: str, model_id: str) -> ModelEntry | None:
    entry = REGISTRY.get(provider_id)
    if entry is None:
        return None
    for model in entry.models:
        if model.model_id == model_id:
            return model
    return None


def create_adapter(
    provider_id: str, model_id: str, api_key: str
) -> ProviderAdapter:
    """Fabrique un adapter configuré, OU lève une `ProviderError` propre.

    La base URL provient exclusivement de la définition du registre : un
    `provider_id` inconnu ou un modèle inconnu est refusé avant tout réseau.
    """
    entry = REGISTRY.get(provider_id)
    if entry is None:
        raise ProviderError(
            "model_not_available",
            f"Fournisseur inconnu : {provider_id}.",
        )
    if model_id not in {model.model_id for model in entry.models}:
        raise ProviderError(
            "model_not_available",
            f"Modèle {model_id} non autorisé pour le fournisseur {provider_id}.",
        )
    return entry.factory(api_key, model_id)


def provider_pricing(
    settings: Any, provider_id: str, model_id: str
) -> dict[str, Any] | None:
    """Tarifs officiels/configurés du modèle choisi, ou None.

    - MiniMax : les tarifs sont CONFIGURABLES dans les settings (DEV), sinon
      les tarifs officiels du registre font foi ; 0.0 = non configuré -> None.
    - OpenAI/Anthropic/Gemini : tarifs officiels du registre (ModelEntry).
    - Aucun tarif connu -> None : le coût reste `null` et n'empêche jamais une
      analyse (philosophie CONCLAVE).
    """
    if provider_id == "minimax":
        input_price = getattr(settings, "minimax_input_usd_per_million", 0.0)
        output_price = getattr(settings, "minimax_output_usd_per_million", 0.0)
        if input_price and output_price and input_price > 0 and output_price > 0:
            return {
                "model_name": model_id,
                "input_usd_per_million_tokens": input_price,
                "output_usd_per_million_tokens": output_price,
            }
        return None
    entry = model_entry(provider_id, model_id)
    if entry is None or not entry.pricing:
        return None
    price = entry.pricing
    return {
        "model_name": model_id,
        "input_usd_per_million_tokens": price.get("input_usd_per_million_tokens"),
        "output_usd_per_million_tokens": price.get("output_usd_per_million_tokens"),
    }