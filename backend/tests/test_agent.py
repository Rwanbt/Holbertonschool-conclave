"""Tests de la boucle agent — MiniMax est simulé, les outils sont réels.

Le client OpenAI-compatible est remplacé par un faux scripté
(`_FakeClient`) via `monkeypatch` sur `backend.app.agent.build_client`.
Les outils métier restent le vrai code (seule une panne interne est
provoquée via `monkeypatch` sur `tools.measure_document` pour vérifier le
trajet d'erreur). Aucun appel réseau, aucun coût, aucune clé.
"""

import asyncio

import pytest

from backend.app import agent, tools
from backend.app.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "minimax_api_key": "sk-test-not-a-real-key",
        "minimax_max_tool_rounds": 3,
        "minimax_input_usd_per_million": 0.30,
        "minimax_output_usd_per_million": 1.20,
    }
    base.update(overrides)
    return Settings(**base)


def _run(monkeypatch, responses, settings=None) -> agent.AgentResponse:
    settings = settings or _settings()
    monkeypatch.setattr(agent, "build_client", lambda s: _FakeClient(responses))
    return asyncio.run(agent.run_agent("Analyse le document.", _DOC, settings))


_DOC = "abc def ghi\npassword=topSecret123"


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
    def __init__(self, message):
        self.message = message


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeCompletion:
    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.created_kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.created_kwargs.append(kwargs)
        if not self._responses:
            raise AssertionError("agent loop called the provider too many times")
        return self._responses.pop(0)


class _FakeChat:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestRegistry:
    def test_three_tools_with_descriptions_and_valid_schemas(self) -> None:
        schemas = agent.registry_tool_schemas()
        assert len(schemas) == 3
        names = {schema["function"]["name"] for schema in schemas}
        assert names == set(agent._ALLOWED_TOOL_NAMES)
        assert names == {
            "measure_current_document",
            "find_security_indicators_in_current_document",
            "estimate_current_analysis_cost",
        }
        for schema in schemas:
            assert schema["type"] == "function"
            assert schema["function"]["description"].strip()
            assert schema["function"]["parameters"]["type"] == "object"
            assert schema["function"]["parameters"]["additionalProperties"] is False
            # Aucun paramètre texte libre : le document ne peut pas transiter
            # par les arguments d'outil.
            assert schema["function"]["parameters"]["properties"] == {}

    def test_allowed_tools_filters_schemas(self) -> None:
        allowed = frozenset({"measure_current_document"})
        schemas = agent.registry_tool_schemas(allowed)
        names = {schema["function"]["name"] for schema in schemas}
        assert names == allowed

    def test_empty_allowed_tools_yields_empty_schema_list(self) -> None:
        assert agent.registry_tool_schemas(frozenset()) == []

    def test_zero_tools_omits_tools_kwarg_without_provider_error(self, monkeypatch) -> None:
        client = _FakeClient(
            [_FakeCompletion([_FakeChoice(_FakeMessage(content="Je ne peux pas vérifier."))])]
        )
        monkeypatch.setattr(agent, "build_client", lambda s: client)

        result = asyncio.run(
            agent.run_agent_loop(
                [
                    {"role": "system", "content": agent.SYSTEM_PROMPT},
                    {"role": "user", "content": "Analyse le document."},
                ],
                agent.AgentSession(document=_DOC),
                _settings(),
                max_rounds=3,
                allowed_tools=frozenset(),
            )
        )
        assert result.answer == "Je ne peux pas vérifier."
        assert "tools" not in client.chat.completions.created_kwargs[0]
        assert "tool_choice" not in client.chat.completions.created_kwargs[0]


class TestAgentLoop:
    def test_premature_final_response_is_rejected_until_required_tool_runs(
        self, monkeypatch
    ) -> None:
        client = _FakeClient(
            [
                _FakeCompletion([_FakeChoice(_FakeMessage(content="Conclusion prématurée."))]),
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_1", "measure_current_document", "{}"
                                    )
                                ]
                            )
                        )
                    ]
                ),
                _FakeCompletion([_FakeChoice(_FakeMessage(content="Conclusion valide."))]),
            ]
        )
        monkeypatch.setattr(agent, "build_client", lambda settings: client)
        events: list[tuple[str, dict]] = []

        async def round_sink(kind: str, fields: dict) -> None:
            events.append((kind, dict(fields)))

        result = asyncio.run(
            agent.run_agent_loop(
                [
                    {"role": "system", "content": agent.SYSTEM_PROMPT},
                    {"role": "user", "content": "Analyse."},
                ],
                agent.AgentSession(document=_DOC),
                _settings(),
                max_rounds=4,
                required_tools_before_final=frozenset(
                    {"measure_current_document"}
                ),
                round_event_sink=round_sink,
            )
        )

        assert result.answer == "Conclusion valide."
        assert result.executed_tools == ["measure_current_document"]
        completed = [fields for kind, fields in events if kind == "agent.round.completed"]
        assert completed[0]["outcome"] == "missing_required_tools"

    def test_rejected_tool_call_is_sent_to_event_sink(self, monkeypatch) -> None:
        events: list[tuple[str, dict]] = []

        async def sink(kind: str, fields: dict) -> None:
            events.append((kind, fields))

        client = _FakeClient(
            [
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(
                        content=None,
                        tool_calls=[_FakeToolCall("call_1", "measure_current_document", "not-json")],
                    ))]
                ),
                _FakeCompletion([_FakeChoice(_FakeMessage(content="Refus."))]),
            ]
        )
        monkeypatch.setattr(agent, "build_client", lambda s: client)
        result = asyncio.run(
            agent.run_agent_loop(
                [
                    {"role": "system", "content": agent.SYSTEM_PROMPT},
                    {"role": "user", "content": "Analyse le document."},
                ],
                agent.AgentSession(document=_DOC),
                _settings(),
                max_rounds=3,
                tool_event_sink=sink,
            )
        )

        assert result.trace[0].error_code == "invalid_arguments"
        assert events[0][0] == "tool.failed"
        assert events[0][1]["error_code"] == "invalid_arguments"

    def test_simple_request_without_tools(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Réponse directe."))],
                    usage=_FakeUsage(10, 5),
                )
            ],
        )
        assert response.answer == "Réponse directe."
        assert response.trace == []
        assert response.usage.llm_rounds == 1
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 5
        assert response.usage.total_tokens == 15
        assert response.usage.estimated_cost_usd == pytest.approx(0.000009)
        assert response.usage.total_latency_ms >= 0
        assert response.model == "MiniMax-M3"

    def test_metrics_then_answer(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall("call_1", "measure_current_document", "{}")
                                ],
                            )
                        )
                    ],
                    usage=_FakeUsage(20, 8),
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Le document fait 28 caractères."))],
                    usage=_FakeUsage(30, 12),
                ),
            ],
        )
        assert response.answer == "Le document fait 28 caractères."
        assert len(response.trace) == 1
        entry = response.trace[0]
        assert entry.sequence == 1
        assert entry.status == "success"
        assert entry.error_code is None
        assert entry.tool_name == "measure_current_document"
        assert entry.output_summary["character_count"] == len(_DOC)
        assert entry.input_summary == {"tool": "measure_current_document"}
        assert entry.duration_ms >= 0
        assert response.usage.llm_rounds == 2
        assert response.usage.input_tokens == 50
        assert response.usage.output_tokens == 20

    def test_security_then_answer(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_1",
                                        "find_security_indicators_in_current_document",
                                        "{}",
                                    )
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Un indice secret trouvé."))]
                ),
            ],
        )
        assert len(response.trace) == 1
        entry = response.trace[0]
        assert entry.status == "success"
        assert entry.output_summary["findings_count"] >= 1
        assert "secret" in entry.output_summary["categories"]
        assert response.answer == "Un indice secret trouvé."

    def test_cost_without_prior_measure_is_missing_prerequisite(self, monkeypatch) -> None:
        # R1 : plus de mesure implicite — estimer le coût sans avoir mesuré
        # le document échoue proprement (missing_prerequisite) au lieu
        # d'inventer une mesure silencieuse.
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_1", "estimate_current_analysis_cost", "{}"
                                    )
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Je ne peux pas vérifier."))]
                ),
            ],
        )
        entry = response.trace[0]
        assert entry.status == "error"
        assert entry.error_code == "missing_prerequisite"

    def test_measure_then_cost_succeeds(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_1", "measure_current_document", "{}"
                                    )
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_2", "estimate_current_analysis_cost", "{}"
                                    )
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Le coût estimé est calculé."))]
                ),
            ],
            settings=_settings(minimax_max_tool_rounds=3),
        )
        entry = response.trace[1]
        assert entry.status == "success"
        assert entry.output_summary["currency"] == "USD"
        assert entry.output_summary["estimated_cost_usd"] >= 0.0

    def test_measure_then_cost_without_pricing_is_controlled(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(
                        content=None,
                        tool_calls=[_FakeToolCall("call_1", "measure_current_document", "{}")],
                    ))]
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(
                        content=None,
                        tool_calls=[_FakeToolCall("call_2", "estimate_current_analysis_cost", "{}")],
                    ))]
                ),
                _FakeCompletion([_FakeChoice(_FakeMessage(content="Tarifs non configurés."))]),
            ],
            settings=_settings(
                minimax_input_usd_per_million=0.0,
                minimax_output_usd_per_million=0.0,
            ),
        )
        entry = response.trace[1]
        assert entry.status == "success"
        assert entry.error_code is None
        assert entry.output_summary["estimated_cost_usd"] is None
        assert entry.output_summary["pricing_configured"] is False
        assumptions = entry.output_summary["assumptions"]
        assert assumptions["expert_count"] == 3
        assert assumptions["arbiter_count"] == 1
        assert assumptions["estimated_llm_calls"] > 1

    def test_unknown_tool_is_traced_not_fatal(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[_FakeToolCall("call_1", "do_evil", "{}")],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Je ne peux pas exécuter cela."))]
                ),
            ],
        )
        assert len(response.trace) == 1
        assert response.trace[0].status == "error"
        assert response.trace[0].error_code == "unknown_tool"
        assert response.answer == "Je ne peux pas exécuter cela."

    def test_invalid_arguments_traced(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_1", "measure_current_document", "not-json"
                                    )
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion([_FakeChoice(_FakeMessage(content="Suite."))]),
            ],
        )
        assert response.trace[0].status == "error"
        assert response.trace[0].error_code == "invalid_arguments"

    def test_disabled_tool_traced(self, monkeypatch) -> None:
        settings = _settings(disabled_tools="measure_current_document")
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall("call_1", "measure_current_document", "{}")
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content="Je ne peux pas vérifier les métriques car l'outil est indisponible."
                            )
                        )
                    ]
                ),
            ],
            settings=settings,
        )
        assert response.trace[0].status == "error"
        assert response.trace[0].error_code == "tool_disabled"
        assert "indisponible" in response.answer

    def test_internal_tool_error_traced(self, monkeypatch) -> None:
        def boom(text):
            raise RuntimeError("simulated internal failure")

        monkeypatch.setattr(tools, "measure_document", boom)
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                content=None,
                                tool_calls=[
                                    _FakeToolCall("call_1", "measure_current_document", "{}")
                                ],
                            )
                        )
                    ]
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Je ne peux pas vérifier."))]
                ),
            ],
        )
        assert response.trace[0].status == "error"
        assert response.trace[0].error_code == "internal_error"

    def test_turn_limit_reached(self, monkeypatch) -> None:
        settings = _settings(minimax_max_tool_rounds=3)
        tools_names = [
            "measure_current_document",
            "find_security_indicators_in_current_document",
            "estimate_current_analysis_cost",
        ]
        responses = [
            _FakeCompletion(
                [
                    _FakeChoice(
                        _FakeMessage(
                            content=None,
                            tool_calls=[_FakeToolCall(f"call_{i}", name, "{}")],
                        )
                    )
                ]
            )
            for i, name in enumerate(tools_names)
        ]
        response = _run(monkeypatch, responses, settings=settings)
        assert response.usage.llm_rounds == 3
        assert len(response.trace) == 3
        assert response.trace[0].status == "success"
        assert response.trace[1].status == "success"
        assert response.trace[2].status == "error"
        assert response.trace[2].error_code == "max_rounds_reached"
        assert "limite d'itérations" in response.answer

    def test_repeated_identical_call_stops(self, monkeypatch) -> None:
        responses = [
            _FakeCompletion(
                [
                    _FakeChoice(
                        _FakeMessage(
                            content=None,
                            tool_calls=[
                                _FakeToolCall("call_1", "measure_current_document", "{}")
                            ],
                        )
                    )
                ]
            ),
            _FakeCompletion(
                [
                    _FakeChoice(
                        _FakeMessage(
                            content=None,
                            tool_calls=[
                                _FakeToolCall("call_2", "measure_current_document", "{}")
                            ],
                        )
                    )
                ]
            ),
        ]
        response = _run(monkeypatch, responses)
        assert response.usage.llm_rounds == 2
        assert len(response.trace) == 2
        assert response.trace[0].status == "success"
        assert response.trace[1].status == "error"
        assert response.trace[1].error_code == "repeated_tool_call"
        assert "ne peux pas vérifier" in response.answer

    def test_usage_accumulates_tokens_and_latency(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content=None, tool_calls=[_FakeToolCall("c1", "measure_current_document", "{}")]))],
                    usage=_FakeUsage(25, 6),
                ),
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Fait."))],
                    usage=_FakeUsage(40, 9),
                ),
            ],
        )
        assert response.usage.input_tokens == 65
        assert response.usage.output_tokens == 15
        assert response.usage.total_tokens == 80
        assert response.usage.llm_rounds == 2

    def test_no_usage_keeps_null_tokens(self, monkeypatch) -> None:
        response = _run(
            monkeypatch,
            [_FakeCompletion([_FakeChoice(_FakeMessage(content="Sans statistiques."))])],
        )
        assert response.usage.input_tokens is None
        assert response.usage.output_tokens is None
        assert response.usage.total_tokens is None
        assert response.usage.estimated_cost_usd is None

    def test_zero_pricing_yields_null_cost(self, monkeypatch) -> None:
        settings = _settings(
            minimax_input_usd_per_million=0.0, minimax_output_usd_per_million=0.0
        )
        response = _run(
            monkeypatch,
            [
                _FakeCompletion(
                    [_FakeChoice(_FakeMessage(content="Sans tarifs."))],
                    usage=_FakeUsage(10, 5),
                )
            ],
            settings=settings,
        )
        assert response.usage.estimated_cost_usd is None

    def test_provider_error_is_propagated(self, monkeypatch) -> None:
        with pytest.raises(Exception) as exc_info:
            _run(
                monkeypatch,
                [
                    _FakeCompletion([], usage=None),
                ],
            )
        assert isinstance(exc_info.value, agent.ProviderError)

    def test_provider_error_closes_the_visible_round(self, monkeypatch) -> None:
        client = _FakeClient([_FakeCompletion([], usage=None)])
        monkeypatch.setattr(agent, "build_client", lambda _settings: client)
        events: list[tuple[str, dict]] = []

        async def round_sink(kind: str, fields: dict) -> None:
            events.append((kind, dict(fields)))

        async def run() -> None:
            await agent.run_agent_loop(
                [
                    {"role": "system", "content": agent.SYSTEM_PROMPT},
                    {"role": "user", "content": "Analyse le document."},
                ],
                agent.AgentSession(document=_DOC),
                _settings(),
                max_rounds=3,
                round_event_sink=round_sink,
            )

        with pytest.raises(agent.ProviderError):
            asyncio.run(run())
        assert events[0][0] == "agent.round.started"
        assert events[-1][0] == "agent.round.completed"
        assert events[-1][1]["outcome"] == "provider_error"

    def test_round_limit_has_the_correct_visible_outcome(self, monkeypatch) -> None:
        client = _FakeClient(
            [
                _FakeCompletion(
                    [
                        _FakeChoice(
                            _FakeMessage(
                                tool_calls=[
                                    _FakeToolCall(
                                        "call_1", "measure_current_document", "{}"
                                    )
                                ]
                            )
                        )
                    ]
                )
            ]
        )
        monkeypatch.setattr(agent, "build_client", lambda _settings: client)
        events: list[tuple[str, dict]] = []

        async def round_sink(kind: str, fields: dict) -> None:
            events.append((kind, dict(fields)))

        result = asyncio.run(
            agent.run_agent_loop(
                [
                    {"role": "system", "content": agent.SYSTEM_PROMPT},
                    {"role": "user", "content": "Analyse le document."},
                ],
                agent.AgentSession(document=_DOC),
                _settings(),
                max_rounds=1,
                round_event_sink=round_sink,
            )
        )
        assert result.stop_reason == "max_rounds_reached"
        assert events[-1][1]["outcome"] == "max_rounds"
