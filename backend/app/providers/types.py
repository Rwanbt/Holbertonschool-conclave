"""Structures normalisées inter-fournisseurs.

Chaque adapter concret (`openai_compatible`, `minimax`, `anthropic`,
`gemini`, …) traduit son format natif vers ces structures : l'orchestrateur,
les experts, l'Arbitre et le streaming ne connaissent JAMAIS le format natif
d'un fournisseur particulier.

Messages : le format interne est celui déjà produit par `agent.py` (rôles
`system`/`user`/`assistant`/`tool` au sens OpenAI, avec `tool_calls` sur les
messages assistant). Chaque adapter le traduit vers sa forme native.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderUsage:
    """Consommation normalisée (jetons), jamais une estimation de coût."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class ProviderFunction:
    """Accès uniforme `call.function.name/arguments`, partagé avec les appels
    streamés (voir streaming.StreamedToolCall)."""

    name: str
    arguments: str


@dataclass
class ProviderToolCall:
    """Appel d'outil final (assemblé), prêt à être exécuté côté serveur."""

    id: str
    name: str
    arguments: str

    @property
    def function(self) -> ProviderFunction:
        return ProviderFunction(name=self.name, arguments=self.arguments)


@dataclass
class ProviderToolCallDelta:
    """Fragment d'appel d'outil en cours de streaming (par index)."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None


@dataclass
class ProviderChunk:
    """Delta de streaming normalisé produit par un adapter."""

    content_delta: str = ""
    tool_calls: list[ProviderToolCallDelta] | None = None
    finish_reason: str | None = None
    usage: ProviderUsage | None = None


@dataclass
class ProviderMessage:
    content: str | None = None
    tool_calls: list[ProviderToolCall] | None = None


@dataclass
class ProviderChoice:
    message: ProviderMessage
    finish_reason: str | None = None


@dataclass
class ProviderResult:
    """Réponse complète (non streamée) normalisée d'un adapter.

    `protocol_error`, `final_json` et `live_text` sont utilisés par le mode
    « enveloppe » streamé (voir streaming.py) : une réponse non streamée ne
    les renseigne pas.
    """

    choices: list[ProviderChoice]
    usage: ProviderUsage | None
    protocol_error: str | None = None
    final_json: str | None = None
    live_text: str = ""

    @property
    def content(self) -> str | None:
        if not self.choices:
            return None
        return self.choices[0].message.content

    @property
    def tool_calls(self) -> list[ProviderToolCall] | None:
        if not self.choices:
            return None
        return self.choices[0].message.tool_calls


@dataclass
class ProviderSpec:
    """Métadonnées PUBLIQUES d'un provider (aucun secret). Exposées par
    `GET /api/providers` pour construire le sélecteur du frontend."""

    provider_id: str
    label: str
    auth_modes: list[str]
    models: list[str]
    supports_tools: bool
    supports_streaming: bool
    supports_structured_output: bool
    supports_reasoning: bool = False
    pricing: dict[str, Any] | None = None