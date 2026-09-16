"""Contrat abstrait des providers — l'orchestrateur ne voit que cette API.

Un `ProviderAdapter` encapsule :
- l'authentification (clé API utilisateur BYOK, jamais persistée) ;
- la construction de la requête native (messages, outils, streaming) ;
- la traduction de la réponse native vers les structures normalisées de
  `types.py` ;
- la traduction de toute défaillance (réseau, HTTP, protocole) en
  `ProviderError` portant un code normalisé.

Aucun `if provider == …` dans l'orchestrateur : chaque particularité
fournisseur (thinking MiniMax, reasoning Gemini, `max_tokens` obligatoire
d'Anthropic, champs d'usage, erreurs HTTP, modèles) reste dans SON adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from .types import ProviderChunk, ProviderResult, ProviderSpec


class ProviderError(RuntimeError):
    """Échec fournisseur normalisé.

    `code` appartient à un ensemble clos (`provider_auth_failed`,
    `provider_rate_limited`, `provider_unavailable`, `model_not_available`,
    `provider_timeout`, `provider_protocol_error`, `provider_capability_missing`,
    `provider_error`). `detail` est un message borné, sans secret.

    Accepte aussi un appel `ProviderError("un message")` (repli historique) :
    le code vaut alors `provider_error`.
    """

    def __init__(
        self,
        code_or_message: str,
        message: str | None = None,
        *,
        detail: str | None = None,
    ):
        if message is None:
            code = "provider_error"
            message = code_or_message
        else:
            code = code_or_message
        super().__init__(message)
        self.code = code
        self.detail = detail


class ProviderCapabilityMissingError(ProviderError):
    """Le modèle/fournisseur ne prend pas en charge une capacité exigée."""

    def __init__(self, capability: str, provider_id: str, model: str):
        super().__init__(
            "provider_capability_missing",
            f"Le provider {provider_id} / modèle {model} ne prend pas en charge "
            f"la capacité requise : {capability}.",
            detail=capability,
        )


class ProviderAdapter(ABC):
    """Adapter concret d'un fournisseur LLM."""

    provider_id: str
    label: str
    model: str
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = True
    supports_reasoning: bool = False

    @property
    def spec(self) -> ProviderSpec:
        return ProviderSpec(
            provider_id=self.provider_id,
            label=self.label,
            auth_modes=["api_key"],
            models=[self.model],
            supports_tools=self.supports_tools,
            supports_streaming=self.supports_streaming,
            supports_structured_output=self.supports_structured_output,
            supports_reasoning=self.supports_reasoning,
            pricing=self.pricing(),
        )

    def pricing(self) -> dict[str, Any] | None:
        """Tarifs officiels/configurés, ou None. Jamais une valeur inventée."""
        return None

    async def close(self) -> None:
        """Libère les ressources éventuelles (client HTTP, connexions)."""

    @abstractmethod
    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        n: int = 1,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResult:
        """Appel non streamé normalisé."""

    @abstractmethod
    def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        n: int = 1,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        """Appel streamé : itère des deltas normalisés (ProviderChunk)."""

    async def test_connection(self) -> str:
        """Vérifie la clé sans gaspillage de jetons quand c'est possible.

        - succès : renvoie un message court ;
        - clé invalide/refusée : lève `ProviderError(provider_auth_failed)` ;
        - fournisseur ne permettant pas de vérifier sans inférence : lève
          `ProviderError(provider_verification_unavailable)` — JAMAIS un état
          « valide » inventé. La validation réelle est alors laissée au
          premier appel d'exécution."""
        raise ProviderError(
            "provider_verification_unavailable",
            "Ce fournisseur ne permet pas de vérifier une clé sans inférence : "
            "la validation aura lieu au premier appel réel.",
        )