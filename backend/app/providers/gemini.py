"""Adapter Google Gemini — API generateContent officielle, via httpx.

Particularités isolées ici (jamais ailleurs) :
- `contents` avec rôles `user`/`model` (jamais `assistant`) ;
- `systemInstruction` est un champ TOP-LEVEL ;
- les appels d'outil sont des parts `functionCall` et les résultats des parts
  `functionResponse` (sans identifiant : un id synthétique est généré) ;
- la clé passe par l'en-tête `x-goog-api-key` (jamais dans l'URL) ;
- le streaming passe par `:streamGenerateContent?alt=sse` ;
- les champs d'usage sont `promptTokenCount`/`candidatesTokenCount`.
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

GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

#: Ids synthétiques stables par (nom, index) : Gemini n'associe pas d'identifiant
#: à un functionCall, mais la boucle CONCLAVE exige un tool_call_id.
def _synthetic_tool_id(name: str, index: int) -> str:
    return f"gemini-{name}-{index}"


def _translate_contents(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            name = message.get("name") or "tool"
            try:
                response_value = json.loads(content or "{}")
            except (ValueError, TypeError):
                response_value = {}
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": response_value,
                            }
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            parts: list[dict[str, Any]] = []
            if content:
                parts.append({"text": content})
            for call in message.get("tool_calls") or []:
                try:
                    args = json.loads(call["function"]["arguments"])
                except (ValueError, TypeError):
                    args = {}
                parts.append(
                    {
                        "functionCall": {
                            "name": call["function"]["name"],
                            "args": args,
                        }
                    }
                )
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        # user
        if isinstance(content, str):
            contents.append({"role": "user", "parts": [{"text": content}]})
        else:
            contents.append({"role": "user", "parts": [{"text": content or ""}]})
    system = {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
    return system, contents


def _translate_tools(tools: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not tools:
        return None
    return {
        "functionDeclarations": [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "parameters": tool["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for tool in tools
        ]
    }


def _map_http_error(exc: httpx.HTTPError, status: int | None = None) -> ProviderError:
    if status is not None:
        if status == 401 or status == 403:
            return ProviderError(
                "provider_auth_failed",
                "Clé API invalide ou refusée par Google Gemini.",
            )
        if status == 404:
            return ProviderError(
                "model_not_available",
                "Modèle introuvable chez Google Gemini (404).",
            )
        if status == 429:
            return ProviderError(
                "provider_rate_limited",
                "Quota ou limite de débit atteint chez Google Gemini (429).",
            )
        if status == 400:
            return ProviderError(
                "provider_protocol_error",
                "Google Gemini a rejeté la requête (400).",
            )
        if status == 408:
            return ProviderError(
                "provider_timeout", "Délai dépassé chez Google Gemini."
            )
        if 500 <= status < 600:
            return ProviderError(
                "provider_unavailable",
                f"Erreur Google Gemini côté serveur ({status}).",
            )
        return ProviderError(
            "provider_error",
            f"Erreur Google Gemini inattendue (HTTP {status}).",
        )
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            "provider_timeout", "Délai dépassé chez Google Gemini."
        )
    if isinstance(exc, httpx.ConnectError):
        return ProviderError(
            "provider_unavailable",
            "Google Gemini injoignable (réseau ou DNS).",
        )
    return ProviderError(
        "provider_error",
        f"Erreur Google Gemini inattendue : {exc.__class__.__name__}",
    )


def _normalize_usage(usage: dict[str, Any] | None) -> ProviderUsage | None:
    if not usage:
        return None
    input_tokens = usage.get("promptTokenCount")
    output_tokens = usage.get("candidatesTokenCount")
    total = usage.get("totalTokenCount")
    return ProviderUsage(
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
        total_tokens=total or None,
    )


def _parts_to_chunk(candidate: dict[str, Any]) -> ProviderChunk:
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    text_parts: list[str] = []
    tool_deltas: list[ProviderToolCallDelta] = []
    for index, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"] or "")
        if "functionCall" in part:
            call = part["functionCall"]
            name = call.get("name") or ""
            args = call.get("args") or {}
            tool_deltas.append(
                ProviderToolCallDelta(
                    index=index,
                    id=_synthetic_tool_id(name, index),
                    name=name,
                    arguments=json.dumps(args, ensure_ascii=False),
                )
            )
    return ProviderChunk(
        content_delta="".join(text_parts),
        tool_calls=tool_deltas or None,
        finish_reason=candidate.get("finishReason"),
    )


class GeminiAdapter(ProviderAdapter):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = GEMINI_BASE_URL,
        timeout: float = 30.0,
        auth_mode: str = "api_key",
    ):
        self.provider_id = "gemini"
        self.label = "Google Gemini"
        self.model = model
        self._api_key = api_key
        self._auth_mode = auth_mode
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def test_connection(self) -> str:
        """Vérifie la clé via `GET /models` (aucune inférence)."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models?pageSize=1", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc
        if response.status_code in (401, 403):
            raise _map_http_error(None, response.status_code)
        if response.status_code != 200:
            raise ProviderError(
                "provider_verification_unavailable",
                "Google Gemini ne permet pas de vérifier la clé via son "
                "catalogue de modèles : la validation aura lieu au premier "
                "appel réel.",
            )
        return "Google Gemini : connexion validée (clé acceptée)."

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._auth_mode == "oauth":
            # Jeton OAuth 2.0 officiel (Bearer) au lieu de la clé API.
            headers["Authorization"] = f"Bearer {self._api_key}"
        else:
            headers["x-goog-api-key"] = self._api_key
        return headers

    def _endpoint(self, stream: bool) -> str:
        suffix = ":streamGenerateContent?alt=sse" if stream else ":generateContent"
        return f"{self._base_url}/models/{self.model}{suffix}"

    def _body(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        tool_choice: Any | None = None,
    ) -> dict[str, Any]:
        system, contents = _translate_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system:
            body["systemInstruction"] = system
        native_tools = _translate_tools(tools)
        if native_tools:
            body["tools"] = native_tools
            if isinstance(tool_choice, dict):
                # Appel d'outil FORCÉ : {"type":"function","function":{"name":…}}
                name = tool_choice.get("function", {}).get("name")
                if name:
                    body["toolConfig"] = {
                        "functionCallingConfig": {
                            "mode": "ANY",
                            "allowedFunctionNames": [name],
                        }
                    }
        return body

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
        body = self._body(
            messages=messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )
        try:
            response = await self._client.post(
                self._endpoint(stream=False), headers=self._headers(), json=body
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
                "Google Gemini a renvoyé une réponse illisible.",
            ) from exc
        return self._parse_payload(payload)

    def _parse_payload(self, payload: dict[str, Any]) -> ProviderResult:
        candidates = payload.get("candidates") or []
        choices: list[ProviderChoice] = []
        for candidate in candidates:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            text_parts: list[str] = []
            tool_calls: list[ProviderToolCall] = []
            for index, part in enumerate(parts):
                if "text" in part:
                    text_parts.append(part["text"] or "")
                if "functionCall" in part:
                    call = part["functionCall"]
                    name = call.get("name") or ""
                    choices_index = len(choices)
                    tool_calls.append(
                        ProviderToolCall(
                            id=_synthetic_tool_id(name, index + choices_index * 100),
                            name=name,
                            arguments=json.dumps(call.get("args") or {}, ensure_ascii=False),
                        )
                    )
            choices.append(
                ProviderChoice(
                    message=ProviderMessage(
                        content="".join(text_parts) or None,
                        tool_calls=tool_calls or None,
                    ),
                    finish_reason=candidate.get("finishReason"),
                )
            )
        return ProviderResult(
            choices=choices,
            usage=_normalize_usage(payload.get("usageMetadata")),
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
        body = self._body(
            messages=messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )
        try:
            async with self._client.stream(
                "POST",
                self._endpoint(stream=True),
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code != 200:
                    raise _map_http_error(None, response.status_code)
                async for line in response.aiter_lines():
                    chunk = self._parse_sse_line(line)
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            raise _map_http_error(exc) from exc

    def _parse_sse_line(self, line: str) -> ProviderChunk | None:
        if not line.startswith("data:"):
            return None
        data = line[len("data:") :].strip()
        if not data:
            return None
        try:
            payload = json.loads(data)
        except ValueError:
            return None
        chunk = ProviderChunk()
        candidates = payload.get("candidates") or []
        if candidates:
            part_chunk = _parts_to_chunk(candidates[0])
            chunk.content_delta = part_chunk.content_delta
            chunk.tool_calls = part_chunk.tool_calls
            chunk.finish_reason = part_chunk.finish_reason
        usage = _normalize_usage(payload.get("usageMetadata"))
        if usage is not None:
            chunk.usage = usage
        if not chunk.content_delta and not chunk.tool_calls and not chunk.finish_reason and not chunk.usage:
            return None
        return chunk