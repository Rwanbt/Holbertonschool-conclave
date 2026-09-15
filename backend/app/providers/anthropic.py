"""Adapter Anthropic — API Messages officielle, via httpx.

Particularités isolées ici (jamais ailleurs) :
- `system` est un champ TOP-LEVEL, pas un message ;
- `max_tokens` est OBLIGATOIRE ;
- les appels d'outil sont des blocs `tool_use` dans `content`, et les
  résultats des blocs `tool_result` dans un message `user` ;
- les schémas d'outils sont `{name, description, input_schema}`, sans
  wrapper `type: function` ;
- le streaming SSE a ses propres événements
  (`message_start`, `content_block_start/delta/stop`, `message_delta`) ;
- les champs d'usage sont `input_tokens`/`output_tokens`.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

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

ANTHROPIC_BASE_URL: str = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION: str = "2023-06-01"

_MAX_TOKENS_DEFAULT: int = 8192


def _translate_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    translated: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            translated.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": content or "",
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls") or []:
                try:
                    input_value = json.loads(call["function"]["arguments"])
                except (ValueError, TypeError):
                    input_value = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": input_value,
                    }
                )
            translated.append({"role": "assistant", "content": blocks})
            continue
        # user
        if isinstance(content, str):
            translated.append({"role": "user", "content": content})
        else:
            translated.append({"role": "user", "content": content or ""})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, translated


def _translate_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "name": tool["function"]["name"],
            "description": tool["function"].get("description", ""),
            "input_schema": tool["function"].get("parameters", {"type": "object"}),
        }
        for tool in tools
    ]


def _map_http_error(exc: httpx.HTTPError, status: int | None = None) -> ProviderError:
    if status is not None:
        if status == 401 or status == 403:
            return ProviderError(
                "provider_auth_failed",
                "Clé API invalide ou refusée par Anthropic.",
            )
        if status == 404:
            return ProviderError(
                "model_not_available",
                "Modèle introuvable chez Anthropic (404).",
            )
        if status == 429:
            return ProviderError(
                "provider_rate_limited",
                "Quota ou limite de débit atteint chez Anthropic (429).",
            )
        if status == 529:
            return ProviderError(
                "provider_unavailable",
                "Anthropic est momentanément surchargé (529).",
            )
        if status == 400:
            return ProviderError(
                "provider_protocol_error",
                "Anthropic a rejeté la requête (400).",
            )
        if 500 <= status < 600:
            return ProviderError(
                "provider_unavailable",
                f"Erreur Anthropic côté serveur ({status}).",
            )
        return ProviderError(
            "provider_error",
            f"Erreur Anthropic inattendue (HTTP {status}).",
        )
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("provider_timeout", "Délai dépassé chez Anthropic.")
    if isinstance(exc, httpx.ConnectError):
        return ProviderError(
            "provider_unavailable",
            "Anthropic injoignable (réseau ou DNS).",
        )
    return ProviderError(
        "provider_error",
        f"Erreur Anthropic inattendue : {exc.__class__.__name__}",
    )


def _parse_completion_payload(payload: dict[str, Any]) -> ProviderResult:
    content_parts = payload.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[ProviderToolCall] = []
    for block in content_parts:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text") or "")
        elif block_type == "tool_use":
            tool_calls.append(
                ProviderToolCall(
                    id=block.get("id") or "",
                    name=block.get("name") or "",
                    arguments=json.dumps(block.get("input") or {}, ensure_ascii=False),
                )
            )
    usage_raw = payload.get("usage") or {}
    usage = ProviderUsage(
        input_tokens=usage_raw.get("input_tokens") or None,
        output_tokens=usage_raw.get("output_tokens") or None,
        total_tokens=(
            (usage_raw.get("input_tokens") or 0) + (usage_raw.get("output_tokens") or 0)
            if usage_raw
            else None
        ),
    )
    return ProviderResult(
        choices=[
            ProviderChoice(
                message=ProviderMessage(
                    content="".join(text_parts) or None,
                    tool_calls=tool_calls or None,
                ),
                finish_reason=payload.get("stop_reason"),
            )
        ],
        usage=usage,
    )


class AnthropicAdapter(ProviderAdapter):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = ANTHROPIC_BASE_URL,
        timeout: float = 30.0,
    ):
        self.provider_id = "anthropic"
        self.label = "Anthropic"
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def test_connection(self) -> str:
        """Vérifie la clé via `GET /v1/models` (aucune inférence)."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc
        if response.status_code in (401, 403):
            raise _map_http_error(None, response.status_code)
        if response.status_code != 200:
            raise ProviderError(
                "provider_verification_unavailable",
                "Anthropic ne permet pas de vérifier la clé via son catalogue "
                "de modèles : la validation aura lieu au premier appel réel.",
            )
        return "Anthropic : connexion validée (clé acceptée)."

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _body(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | None,
        response_format: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        system, translated = _translate_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens or _MAX_TOKENS_DEFAULT,
            "messages": translated,
            "temperature": temperature,
            "stream": stream,
        }
        if system:
            body["system"] = system
        native_tools = _translate_tools(tools)
        if native_tools:
            body["tools"] = native_tools
            if tool_choice == "none":
                body["tool_choice"] = {"type": "none"}
            else:
                body["tool_choice"] = {"type": "auto"}
        if response_format is not None:
            # Anthropic n'expose pas response_format : la validation
            # structurée CONCLAVE (extraction + Pydantic) reste l'autorité.
            pass
        return body

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        n: int = 1,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResult:
        body = self._body(
            messages=messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=False,
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/messages", headers=self._headers(), json=body
            )
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc
        if response.status_code != 200:
            raise _map_http_error(None, response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_protocol_error",
                "Anthropic a renvoyé une réponse illisible.",
            ) from exc
        return _parse_completion_payload(payload)

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        n: int = 1,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        body = self._body(
            messages=messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            response_format=None,
            stream=True,
        )
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/messages",
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code != 200:
                    raise _map_http_error(None, response.status_code)
                async for line in response.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc


def _parse_sse_line(line: str) -> ProviderChunk | None:
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data:
        return None
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    event_type = payload.get("type")
    if event_type == "message_start":
        message = payload.get("message") or {}
        usage = message.get("usage") or {}
        return ProviderChunk(
            usage=ProviderUsage(
                input_tokens=usage.get("input_tokens") or None,
                output_tokens=usage.get("output_tokens") or None,
            )
        )
    if event_type == "content_block_start":
        block = payload.get("content_block") or {}
        block_type = block.get("type")
        index = payload.get("index", 0) or 0
        if block_type == "tool_use":
            return ProviderChunk(
                tool_calls=[
                    ProviderToolCallDelta(
                        index=index,
                        id=block.get("id"),
                        name=block.get("name"),
                        arguments="",
                    )
                ]
            )
        return None
    if event_type == "content_block_delta":
        delta = payload.get("delta") or {}
        delta_type = delta.get("type")
        index = payload.get("index", 0) or 0
        if delta_type == "text_delta":
            return ProviderChunk(content_delta=delta.get("text") or "")
        if delta_type == "input_json_delta":
            return ProviderChunk(
                tool_calls=[
                    ProviderToolCallDelta(
                        index=index,
                        arguments=delta.get("partial_json") or "",
                    )
                ]
            )
        return None
    if event_type == "message_delta":
        delta = payload.get("delta") or {}
        usage = payload.get("usage") or {}
        output_tokens = usage.get("output_tokens")
        return ProviderChunk(
            finish_reason=delta.get("stop_reason"),
            usage=(
                ProviderUsage(output_tokens=output_tokens)
                if output_tokens is not None
                else None
            ),
        )
    return None