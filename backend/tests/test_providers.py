"""Tests de contrat multi-provider (aucune clé, aucun réseau réel).

Couverture : registre public sans secret, fabrique `build_provider`,
normalisation des réponses OpenAI-compatibles, traductions Anthropic
(Messages API) et Gemini (generateContent), mapping des erreurs, parsing des
flux SSE fournisseur. Les adapters HTTP sont testés via les fonctions pures et
`httpx.MockTransport` : la CI n'a besoin d'aucune clé réelle.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.providers import (
    ProviderError,
    build_provider,
    create_adapter,
    list_provider_specs,
)
from backend.app.providers.anthropic import (
    AnthropicAdapter,
    _parse_completion_payload,
    _parse_sse_line,
    _translate_messages,
    _translate_tools,
)
from backend.app.providers.gemini import (
    GeminiAdapter,
    _parts_to_chunk,
    _translate_contents,
    _translate_tools as _gemini_translate_tools,
)
from backend.app.providers.openai_compatible import (
    OpenAICompatibleAdapter,
    _map_sdk_error,
)


def _openai_adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        api_key="sk-test",
        base_url="https://mock.local/v1",
        model="gpt-4o-mini",
        provider_id="openai",
        label="OpenAI",
    )

# ---------------------------------------------------------------------------
# Registre public — aucun secret
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_four_core_providers_without_secrets(self) -> None:
        specs = list_provider_specs()
        ids = {spec["provider_id"] for spec in specs}
        assert ids == {"minimax", "openai", "anthropic", "gemini"}
        serialized = json.dumps(specs)
        assert "base_url" not in serialized
        assert "Authorization" not in serialized
        assert "sk-" not in serialized
        assert "AIza" not in serialized
        for spec in specs:
            assert spec["auth_modes"] == ["api_key"]
            assert spec["models"]
            for model in spec["models"]:
                assert model["model_id"]
                assert model["supports_tools"] is True

    def test_unknown_provider_and_model_rejected(self) -> None:
        with pytest.raises(ProviderError):
            create_adapter("does-not-exist", "m", "sk-test")
        with pytest.raises(ProviderError):
            create_adapter("openai", "gpt-999", "sk-test")

    def test_models_have_pricing_or_null(self) -> None:
        for spec in list_provider_specs():
            for model in spec["models"]:
                pricing = model["pricing"]
                assert pricing is None or isinstance(pricing, dict)


class TestBuildProvider:
    def _settings(self, **overrides) -> Settings:
        base = {
            "minimax_api_key": "sk-dev-minimax",
            "openai_api_key": "",
            "anthropic_api_key": "",
            "gemini_api_key": "",
            "allow_server_provider_credentials": False,
        }
        base.update(overrides)
        return Settings(**base)

    def test_no_key_and_no_server_credential_is_refused(self) -> None:
        with pytest.raises(ProviderError) as exc:
            build_provider(
                provider_id="minimax",
                model_id="MiniMax-M3",
                api_key=None,
                settings=self._settings(),
            )
        assert exc.value.code == "provider_auth_failed"

    def test_byok_key_has_priority(self) -> None:
        provider = build_provider(
            provider_id="minimax",
            model_id="MiniMax-M3",
            api_key="sk-user-secret",
            settings=self._settings(),
        )
        assert provider.provider_id == "minimax"
        assert provider.model == "MiniMax-M3"

    def test_server_credential_used_only_when_allowed(self) -> None:
        with pytest.raises(ProviderError):
            build_provider(
                provider_id="minimax",
                model_id="MiniMax-M3",
                api_key=None,
                settings=self._settings(minimax_api_key="sk-dev-minimax"),
            )
        provider = build_provider(
            provider_id="minimax",
            model_id="MiniMax-M3",
            api_key=None,
            settings=self._settings(allow_server_provider_credentials=True),
        )
        assert provider is not None


class TestProviderPricing:
    def test_minimax_pricing_from_settings(self) -> None:
        from backend.app.providers import provider_pricing

        settings = Settings(
            minimax_input_usd_per_million=0.30,
            minimax_output_usd_per_million=1.20,
        )
        pricing = provider_pricing(settings, "minimax", "MiniMax-M3")
        assert pricing["input_usd_per_million_tokens"] == 0.30
        assert pricing["output_usd_per_million_tokens"] == 1.20

    def test_zero_minimax_pricing_is_null(self) -> None:
        from backend.app.providers import provider_pricing

        settings = Settings(
            minimax_input_usd_per_million=0.0,
            minimax_output_usd_per_million=0.0,
        )
        assert provider_pricing(settings, "minimax", "MiniMax-M3") is None

    def test_openai_pricing_from_registry(self) -> None:
        from backend.app.providers import provider_pricing

        settings = Settings()
        pricing = provider_pricing(settings, "openai", "gpt-4o-mini")
        assert pricing is not None
        assert pricing["input_usd_per_million_tokens"] == 0.15

    def test_unknown_model_pricing_is_null(self) -> None:
        from backend.app.providers import provider_pricing

        settings = Settings()
        assert provider_pricing(settings, "openai", "does-not-exist") is None


# ---------------------------------------------------------------------------
# OpenAI-compatible — normalisation et erreurs
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message, finish_reason=None):
        self.message = message
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeStreamChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeStreamChunk:
    def __init__(self, delta=None, usage=None, finish_reason=None):
        self.choices = []
        if delta is not None or finish_reason is not None:
            self.choices.append(_FakeStreamChoice(delta or _FakeDelta(), finish_reason))
        self.usage = usage


class TestOpenAICompatible:
    def test_normalize_completion_with_tool_calls_and_usage(self) -> None:
        completion = _FakeCompletion(
            [
                _FakeChoice(
                    _FakeMessage(
                        content="texte",
                        tool_calls=[
                            _FakeToolCall("c1", "measure_current_document", "{}")
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_FakeUsage(10, 5),
        )
        result = _openai_adapter()._normalize_completion(completion)
        assert result.content == "texte"
        assert result.tool_calls[0].id == "c1"
        assert result.tool_calls[0].function.name == "measure_current_document"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    def test_normalize_chunk_maps_delta_and_usage(self) -> None:
        chunk = _FakeStreamChunk(
            delta=_FakeDelta(
                content="bon",
                tool_calls=[
                    _FakeToolCall("c1", "measure_", '{"a')
                ],
            ),
            finish_reason="tool_calls",
            usage=_FakeUsage(1, 2),
        )
        normalized = _openai_adapter()._normalize_chunk(chunk)
        assert normalized.content_delta == "bon"
        assert normalized.finish_reason == "tool_calls"
        assert normalized.tool_calls[0].index == 0
        assert normalized.usage.input_tokens == 1

    def test_error_mapping(self) -> None:
        from openai import (
            APITimeoutError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
        )

        def api_error(cls, status: int):
            request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            response = httpx.Response(status, request=request)
            return cls("boom", response=response, body=None)

        assert _map_sdk_error(api_error(AuthenticationError, 401)).code == "provider_auth_failed"
        assert _map_sdk_error(api_error(RateLimitError, 429)).code == "provider_rate_limited"
        assert _map_sdk_error(api_error(NotFoundError, 404)).code == "model_not_available"
        timeout_request = httpx.Request(
            "POST", "https://api.openai.com/v1/chat/completions"
        )
        assert _map_sdk_error(APITimeoutError(timeout_request)).code == "provider_timeout"


# ---------------------------------------------------------------------------
# Anthropic — traduction Messages API
# ---------------------------------------------------------------------------


class TestAnthropic:
    def test_translate_messages_system_top_level(self) -> None:
        system, messages = _translate_messages(
            [
                {"role": "system", "content": "Sois prudent."},
                {"role": "user", "content": "Bonjour"},
                {
                    "role": "assistant",
                    "content": "Réfléchis.",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "measure_current_document", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "measure_current_document", "content": '{"ok": true}'},
            ]
        )
        assert system == "Sois prudent."
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"][1]["type"] == "tool_use"
        assert messages[1]["content"][1]["name"] == "measure_current_document"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"][0]["type"] == "tool_result"
        assert messages[2]["content"][0]["tool_use_id"] == "c1"

    def test_translate_tools_wraps_without_type_function(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "m",
                    "description": "Mesure",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        native = _translate_tools(tools)
        assert native[0]["name"] == "m"
        assert native[0]["input_schema"] == {"type": "object", "properties": {}}
        assert "type" not in native[0]

    def test_parse_completion_payload(self) -> None:
        payload = {
            "content": [
                {"type": "text", "text": "Résumé"},
                {"type": "tool_use", "id": "t1", "name": "m", "input": {"x": 1}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 11, "output_tokens": 7},
        }
        result = _parse_completion_payload(payload)
        assert result.content == "Résumé"
        assert result.tool_calls[0].id == "t1"
        assert json.loads(result.tool_calls[0].arguments) == {"x": 1}
        assert result.usage.input_tokens == 11
        assert result.usage.output_tokens == 7

    def test_stream_sse_lines(self) -> None:
        start = _parse_sse_line(
            'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}'
        )
        assert start.usage.input_tokens == 5
        delta = _parse_sse_line(
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"cou"}}'
        )
        assert delta.content_delta == "cou"
        tool = _parse_sse_line(
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"a"}}'
        )
        assert tool.tool_calls[0].arguments == '{"a'
        finish = _parse_sse_line(
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":9}}'
        )
        assert finish.finish_reason == "end_turn"
        assert finish.usage.output_tokens == 9

    def test_http_error_mapping(self) -> None:
        from backend.app.providers.anthropic import _map_http_error

        assert _map_http_error(None, 401).code == "provider_auth_failed"
        assert _map_http_error(None, 429).code == "provider_rate_limited"
        assert _map_http_error(None, 404).code == "model_not_available"
        assert _map_http_error(None, 529).code == "provider_unavailable"
        assert _map_http_error(None, 408).code == "provider_timeout"

    def test_complete_via_mock_transport(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["max_tokens"] > 0
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Réponse"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            model="claude-3-5-haiku-latest",
            base_url="https://mock.local/v1",
        )
        adapter._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://mock.local/v1"
        )
        import asyncio

        async def go():
            result = await adapter.complete(
                messages=[{"role": "user", "content": "Salut"}],
                max_output_tokens=100,
                temperature=0.3,
            )
            await adapter.close()
            return result

        result = asyncio.run(go())
        assert result.content == "Réponse"
        assert result.usage.total_tokens == 5


# ---------------------------------------------------------------------------
# Gemini — traduction generateContent
# ---------------------------------------------------------------------------


class TestGemini:
    def test_translate_contents(self) -> None:
        system, contents = _translate_contents(
            [
                {"role": "system", "content": "Consigne"},
                {"role": "user", "content": "Doc"},
                {
                    "role": "assistant",
                    "content": "Pense.",
                    "tool_calls": [
                        {
                            "id": "g1",
                            "type": "function",
                            "function": {"name": "m", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "g1", "name": "m", "content": '{"ok": true}'},
            ]
        )
        assert system["parts"][0]["text"] == "Consigne"
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        assert contents[1]["parts"][1]["functionCall"]["name"] == "m"
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "m"

    def test_translate_tools(self) -> None:
        native = _gemini_translate_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "m",
                        "description": "d",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        )
        assert native["functionDeclarations"][0]["name"] == "m"

    def test_parse_payload(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Résumé"},
                            {"functionCall": {"name": "m", "args": {"x": 2}}},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 4,
                "candidatesTokenCount": 3,
                "totalTokenCount": 7,
            },
        }
        result = GeminiAdapter(
            api_key="AIza-test", model="gemini-2.0-flash", base_url="https://mock.local/v1beta"
        )._parse_payload(payload)
        assert result.content == "Résumé"
        assert result.tool_calls[0].name == "m"
        assert result.usage.total_tokens == 7

    def test_parts_to_chunk(self) -> None:
        chunk = _parts_to_chunk(
            {
                "content": {
                    "parts": [
                        {"text": "hi"},
                        {"functionCall": {"name": "m", "args": {}}},
                    ]
                },
                "finishReason": "STOP",
            }
        )
        assert chunk.content_delta == "hi"
        assert chunk.finish_reason == "STOP"
        assert chunk.tool_calls[0].name == "m"

    def test_stream_sse_lines(self) -> None:
        adapter = GeminiAdapter(
            api_key="AIza-test", model="gemini-2.0-flash", base_url="https://mock.local/v1beta"
        )
        chunk = adapter._parse_sse_line(
            'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":null}],"usageMetadata":{"promptTokenCount":1}}'
        )
        assert chunk.content_delta == "ok"
        assert chunk.usage.input_tokens == 1

    def test_error_mapping(self) -> None:
        from backend.app.providers.gemini import _map_http_error

        assert _map_http_error(None, 401).code == "provider_auth_failed"
        assert _map_http_error(None, 429).code == "provider_rate_limited"
        assert _map_http_error(None, 400).code == "provider_protocol_error"

    def test_complete_via_mock_transport(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "x-goog-api-key" in request.headers
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Réponse Gemini"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 2,
                        "candidatesTokenCount": 2,
                        "totalTokenCount": 4,
                    },
                },
            )

        adapter = GeminiAdapter(
            api_key="AIza-test",
            model="gemini-2.0-flash",
            base_url="https://mock.local/v1beta",
        )
        adapter._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://mock.local/v1beta"
        )
        import asyncio

        async def go():
            result = await adapter.complete(
                messages=[{"role": "user", "content": "Salut"}],
                max_output_tokens=100,
                temperature=0.3,
            )
            await adapter.close()
            return result

        result = asyncio.run(go())
        assert result.content == "Réponse Gemini"
        assert result.usage.total_tokens == 4


class TestMiniMaxAdapter:
    def test_thinking_disabled_is_isolated(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="sk-test",
            base_url="https://mock/v1",
            model="m",
            provider_id="minimax",
            label="MiniMax",
            thinking_disabled=True,
        )
        assert adapter._extra_kwargs() == {"extra_body": {"thinking": {"type": "disabled"}}}

        adapter_openai = OpenAICompatibleAdapter(
            api_key="sk-test",
            base_url="https://mock/v1",
            model="gpt-4o-mini",
            provider_id="openai",
            label="OpenAI",
        )
        assert adapter_openai._extra_kwargs() == {}