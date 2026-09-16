"""Registre contrôlé des providers et modèles — piloté par le catalogue.

Le catalogue (`catalog.py`, généré depuis models.dev) est l'UNIQUE source des
providers, modèles, capacités, tarifs et endpoints (allowlist). Aucune base URL
n'est saisie par l'utilisateur : l'ajout d'un provider se fait en l'ajoutant au
catalogue puis en régénérant (`scripts/generate_provider_catalog.py`).

Chaque provider est servi par l'adapter correspondant à son type :
OpenAI-compatible (le plus courant), Anthropic (Messages), Gemini
(generateContent), MiniMax (particularité `thinking` isolée).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .base import ProviderAdapter, ProviderError
from .anthropic import AnthropicAdapter
from .gemini import GeminiAdapter
from .minimax import MiniMaxAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .catalog import ALLOWED_BASE_URLS, CATALOG


@dataclass(frozen=True)
class ModelEntry:
    model_id: str
    label: str = ""
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = True
    supports_reasoning: bool = False
    context: int | None = None
    max_output: int | None = None
    pricing: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderEntry:
    provider_id: str
    label: str
    auth_modes: list[str]
    models: list[ModelEntry]
    factory: Callable[[str, str], ProviderAdapter]
    base_url: str
    adapter: str
    env: str = ""
    supports_reasoning: bool = False


def _factory_for(spec: dict[str, Any]) -> Callable[[str, str], ProviderAdapter]:
    adapter = spec["adapter"]
    provider_id = spec["provider_id"]
    label = spec["label"]
    base_url = spec["base_url"]

    if adapter == "anthropic":

        def _anthropic(api_key: str, model_id: str) -> ProviderAdapter:
            return AnthropicAdapter(api_key=api_key, model=model_id)

        return _anthropic
    if adapter == "gemini":

        def _gemini(api_key: str, model_id: str) -> ProviderAdapter:
            return GeminiAdapter(api_key=api_key, model=model_id)

        return _gemini
    if adapter == "minimax":

        def _minimax(api_key: str, model_id: str) -> ProviderAdapter:
            return MiniMaxAdapter(api_key=api_key, model=model_id)

        return _minimax

    def _openai_compatible(api_key: str, model_id: str) -> ProviderAdapter:
        return OpenAICompatibleAdapter(
            api_key=api_key,
            base_url=base_url,
            model=model_id,
            provider_id=provider_id,
            label=label,
        )

    return _openai_compatible


def _build_registry() -> dict[str, ProviderEntry]:
    registry: dict[str, ProviderEntry] = {}
    for provider_id, spec in CATALOG.items():
        models = [
            ModelEntry(
                model_id=model["model_id"],
                label=model.get("label", model["model_id"]),
                supports_tools=model.get("supports_tools", True),
                supports_streaming=model.get("supports_streaming", True),
                supports_structured_output=model.get(
                    "supports_structured_output", True
                ),
                supports_reasoning=model.get("supports_reasoning", False),
                context=model.get("context"),
                max_output=model.get("max_output"),
                pricing=model.get("pricing"),
            )
            for model in spec["models"]
        ]
        registry[provider_id] = ProviderEntry(
            provider_id=provider_id,
            label=spec["label"],
            auth_modes=list(spec.get("auth_modes", ["api_key"])),
            models=models,
            factory=_factory_for(spec),
            base_url=spec["base_url"],
            adapter=spec["adapter"],
            env=spec.get("env", ""),
            supports_reasoning=any(model.supports_reasoning for model in models),
        )
    return registry


REGISTRY: dict[str, ProviderEntry] = _build_registry()

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
                        "label": model.label,
                        "supports_tools": model.supports_tools,
                        "supports_streaming": model.supports_streaming,
                        "supports_structured_output": model.supports_structured_output,
                        "supports_reasoning": model.supports_reasoning,
                        "context": model.context,
                        "max_output": model.max_output,
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


def create_adapter(provider_id: str, model_id: str, api_key: str) -> ProviderAdapter:
    """Fabrique un adapter configuré, OU lève une `ProviderError` propre.

    La base URL provient exclusivement du catalogue : un `provider_id` inconnu
    ou un modèle inconnu est refusé avant tout réseau.
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

    - MiniMax : tarifs CONFIGURABLES dans les settings (DEV) ; 0.0 = non
      configuré -> None.
    - Autres : tarifs officiels du catalogue (models.dev).
    - Aucun tarif connu -> None : le coût reste `null` et n'empêche jamais une
      analyse.
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