"""Adapter générique pour les APIs réellement OpenAI-compatibles.

Utilise le SDK `openai` (`AsyncOpenAI`) en pointant `base_url` vers
l'endpoint compatible. Couvre OpenAI, MiniMax et toute API compatible
(DeepSeek, Mistral, Groq, …) via une sous-classe ou une configuration.

Les particularités d'un fournisseur restent dans SA sous-classe :
- MiniMax désactive `thinking` via `extra_body` (voir minimax.py) ;
- OpenAI n'a aucune spécificité ici (voir openai.py).

La `base_url` n'est JAMAIS saisie librement par un utilisateur : chaque
endpoint compatible est déclaré dans le registre contrôlé côté serveur
(registry.py) afin d'éviter une primitive SSRF.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from .base import ProviderAdapter, ProviderError
from .types import (
    ProviderChunk,
    ProviderChoice,
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
    ProviderToolCallDelta,
    ProviderUsage,
)


def _normalize_usage(usage: Any) -> ProviderUsage | None:
    if usage is None:
        return None
    return ProviderUsage(
        input_tokens=getattr(usage, "prompt_tokens", None) or None,
        output_tokens=getattr(usage, "completion_tokens", None) or None,
        total_tokens=getattr(usage, "total_tokens", None) or None,
    )


def _map_sdk_error(exc: Exception) -> ProviderError:
    """Traduit une exception du SDK `openai` en `ProviderError` normalisée."""
    status = getattr(exc, "status_code", None)

    def _detail(prefix: str) -> str:
        raw = str(exc).replace("\n", " ").strip()
        return f"{prefix}: {raw[:160]}" if raw else prefix

    if isinstance(exc, AuthenticationError):
        return ProviderError(
            "provider_auth_failed",
            "Clé API invalide ou refusée par le fournisseur (401).",
        )
    if isinstance(exc, PermissionDeniedError):
        if status == 403 and "model" in str(exc).lower():
            return ProviderError(
                "model_not_available",
                "Accès au modèle refusé pour cette clé (403).",
            )
        return ProviderError(
            "provider_auth_failed",
            "Autorisation refusée par le fournisseur (403).",
        )
    if isinstance(exc, RateLimitError):
        return ProviderError(
            "provider_rate_limited",
            "Quota ou limite de débit atteint chez le fournisseur (429).",
        )
    if isinstance(exc, NotFoundError):
        return ProviderError(
            "model_not_available",
            "Modèle ou endpoint introuvable chez le fournisseur (404).",
        )
    if isinstance(exc, (UnprocessableEntityError, BadRequestError)):
        lowered = str(exc).lower()
        if "does not support" in lowered or "not supported" in lowered:
            return ProviderError(
                "provider_capability_missing",
                "Le modèle refuse une capacité demandée.",
                detail="incompatible_capability",
            )
        return ProviderError(
            "provider_protocol_error",
            "Le fournisseur a rejeté la requête (400/422).",
        )
    if isinstance(exc, APITimeoutError):
        return ProviderError(
            "provider_timeout",
            "Délai dépassé chez le fournisseur.",
        )
    if isinstance(exc, APIConnectionError):
        return ProviderError(
            "provider_unavailable",
            "Fournisseur injoignable (réseau ou DNS).",
            detail=_detail("connection_error"),
        )
    return ProviderError(
        "provider_error",
        f"Erreur fournisseur inattendue : {exc.__class__.__name__}",
        detail=_detail(exc.__class__.__name__),
    )


class OpenAICompatibleAdapter(ProviderAdapter):
    """Adapter OpenAI-compatible : SDK `openai` pointé sur une base URL sûre.

    `thinking_disabled` permet à MiniMax de désactiver son raisonnement
    approfondi (`extra_body={"thinking": …}`) sans contaminer les autres
    fournisseurs.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider_id: str,
        label: str,
        thinking_disabled: bool = False,
        timeout: float = 30.0,
        max_retries: int = 1,
        max_output_tokens: int | None = None,
    ):
        self.provider_id = provider_id
        self.label = label
        self.model = model
        self._thinking_disabled = thinking_disabled
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def close(self) -> None:
        await self._client.close()

    async def test_connection(self) -> str:
        """Vérifie la clé via `GET /models` (cheap) quand le provider l'expose."""
        try:
            await self._client.models.list()
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise _map_sdk_error(exc) from exc
        except Exception:  # noqa: BLE001 - endpoint /models non exposé
            raise ProviderError(
                "provider_verification_unavailable",
                f"{self.label} ne permet pas de vérifier une clé via son "
                "catalogue de modèles : la validation aura lieu au premier "
                "appel réel.",
            ) from None
        return f"{self.label} : connexion validée (clé acceptée)."

    def _extra_kwargs(self) -> dict[str, Any]:
        if self._thinking_disabled:
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}

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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_output_tokens,
            "temperature": temperature,
            "n": n,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if response_format:
            kwargs["response_format"] = response_format
        kwargs.update(self._extra_kwargs())
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - traduction en ProviderError
            raise _map_sdk_error(exc) from exc
        return self._normalize_completion(completion)

    def _normalize_completion(self, completion: Any) -> ProviderResult:
        choices: list[ProviderChoice] = []
        for choice in completion.choices or []:
            message = choice.message
            tool_calls: list[ProviderToolCall] = []
            for call in message.tool_calls or []:
                tool_calls.append(
                    ProviderToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=call.function.arguments or "",
                    )
                )
            choices.append(
                ProviderChoice(
                    message=ProviderMessage(
                        content=message.content, tool_calls=tool_calls or None
                    ),
                    finish_reason=getattr(choice, "finish_reason", None),
                )
            )
        return ProviderResult(
            choices=choices,
            usage=_normalize_usage(getattr(completion, "usage", None)),
        )

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        n: int = 1,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_output_tokens,
            "temperature": temperature,
            "n": n,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        kwargs.update(self._extra_kwargs())
        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _map_sdk_error(exc) from exc
        async for chunk in stream:
            yield self._normalize_chunk(chunk)

    def _normalize_chunk(self, chunk: Any) -> ProviderChunk:
        content_delta = ""
        tool_calls: list[ProviderToolCallDelta] | None = None
        finish_reason: str | None = None
        if getattr(chunk, "choices", None):
            choice = chunk.choices[0]
            delta = getattr(choice, "delta", None)
            if delta is not None:
                content_delta = getattr(delta, "content", None) or ""
                raw_calls = getattr(delta, "tool_calls", None) or []
                if raw_calls:
                    tool_calls = []
                    for call in raw_calls:
                        function = getattr(call, "function", None)
                        tool_calls.append(
                            ProviderToolCallDelta(
                                index=getattr(call, "index", 0) or 0,
                                id=getattr(call, "id", None) or None,
                                name=(
                                    getattr(function, "name", None)
                                    if function is not None
                                    else None
                                ),
                                arguments=(
                                    getattr(function, "arguments", None)
                                    if function is not None
                                    else None
                                ),
                            )
                        )
            finish_reason = getattr(choice, "finish_reason", None)
        usage = _normalize_usage(getattr(chunk, "usage", None))
        return ProviderChunk(
            content_delta=content_delta,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )