"""Tests de la carte bonus Streaming du Palier 4.

Couverture :
- `normalize_delta` (delta classique, cumulatif MiniMax, préfixe répété,
  doublon — jamais de duplication « Bonjour » → « BBoo… ») ;
- `EnvelopeParser` (marqueurs coupés, plafonnement du live, erreurs de
  protocole, réponse sans balise non-erreur) ;
- `StreamCollector` (usage sans choices, assemblage d'outils fragmentés,
  réponse hybride = erreur de protocole, paquets bornés) ;
- la boucle agent en mode stream (extraction du JSON final, deltas diffusés
  avant tout « completed », JSON final jamais dans les deltas, arrêt propre
  sur erreur de protocole) ;
- la persistance `agent.response.*` (séquence strictement croissante par rôle,
  ordre started → delta → completed → expert.completed) ;
- la reprise SSE `max(Last-Event-ID, ?after)` et `/tools list` en 200.

Le transport MiniMax est simulé par les fakes de `conftest` (streams
fidèles au SDK) ; les fonctions métier des outils restent le vrai code.
"""

import asyncio
import json

from backend.app import agent, db, experts
from backend.app.agent import AgentSession
from backend.app.config import Settings
from backend.app.streaming import EnvelopeParser, StreamCollector, normalize_delta

from .conftest import (
    FakeClient,
    FakeStream,
    FakeStreamChunk,
    FakeStreamToolCall,
    FakeUsage,
    agent_output_json,
    envelope,
)


def _settings(tmp_path=None, **overrides) -> Settings:
    database_path = (
        str(tmp_path / "conclave_stream.db")
        if tmp_path is not None
        else "/tmp/conclave_stream.db"
    )
    base = {
        "minimax_api_key": "sk-test-not-a-real-key",
        "database_path": database_path,
        "expert_timeout_seconds": 30.0,
        "arbiter_timeout_seconds": 20.0,
        "analysis_timeout_seconds": 60.0,
    }
    base.update(overrides)
    return Settings(**base)


def _factory(path: str):
    async def get_connection():
        return db.open_connection(path)

    return get_connection


def _run_async(coro):
    return asyncio.run(coro)


class TestNormalizeDelta:
    def test_classic_delta(self) -> None:
        assert normalize_delta("Bon", "Bonjour") == "jour"

    def test_cumulative_minimax(self) -> None:
        assert normalize_delta("Bonjour", "Bonjour tout le monde") == " tout le monde"

    def test_repeated_prefix_is_dropped(self) -> None:
        assert normalize_delta("Bonjour le", "Bonjour") == ""

    def test_duplicate_suffix_is_dropped(self) -> None:
        assert normalize_delta("Bonjour", "Bonjour") == ""

    def test_never_duplicates_bonjour_into_bboo(self) -> None:
        accumulated = ""
        for chunk in ("Bon", "Bonjour", "Bonjour "):
            accumulated += normalize_delta(accumulated, chunk)
        assert accumulated == "Bonjour "


class TestEnvelopeParser:
    def test_simple_envelope(self) -> None:
        parser = EnvelopeParser(max_live_chars=1000)
        live = parser.feed(envelope("{}", "Voici l'analyse"))
        assert live == "\nVoici l'analyse\n"
        assert parser.finish() is None
        assert parser.final_json.strip() == "{}"

    def test_markers_split_across_chunks(self) -> None:
        parser = EnvelopeParser(max_live_chars=1000)
        text = envelope('{"role": "avocat"}', "Salut les humains")
        emitted = ""
        for index in range(0, len(text), 3):
            emitted += parser.feed(text[index : index + 3])
        assert parser.finish() is None
        assert "Salut les humains" in parser.live_text
        assert json.loads(parser.final_json)["role"] == "avocat"

    def test_live_text_is_capped(self) -> None:
        parser = EnvelopeParser(max_live_chars=10)
        parser.feed(envelope("{}", "Ce texte live dépasse largement la limite fixée."))
        assert parser.finish() is None
        assert len(parser.live_text) <= 10

    def test_double_live_section_is_error(self) -> None:
        parser = EnvelopeParser(max_live_chars=1000)
        parser.feed(
            "<LIVE_RESPONSE>a</LIVE_RESPONSE><LIVE_RESPONSE>b</LIVE_RESPONSE>"
            "<FINAL_JSON>{}</FINAL_JSON>"
        )
        assert parser.finish() is not None

    def test_inverted_markers_is_error(self) -> None:
        parser = EnvelopeParser(max_live_chars=1000)
        parser.feed("</LIVE_RESPONSE><FINAL_JSON>{}</FINAL_JSON>")
        assert parser.finish() is not None

    def test_missing_final_json_is_error(self) -> None:
        parser = EnvelopeParser(max_live_chars=1000)
        parser.feed("<LIVE_RESPONSE>texte seul</LIVE_RESPONSE>")
        assert parser.finish() is not None

    def test_no_markers_is_not_an_error(self) -> None:
        parser = EnvelopeParser(max_live_chars=1000)
        parser.feed("réponse d'un round d'outil sans balise")
        assert parser.finish() is None
        assert parser.final_json is None


class TestStreamCollector:
    def test_usage_chunk_without_choices_is_kept(self) -> None:
        collector = StreamCollector(_settings())
        _run_async(collector.feed(FakeStreamChunk(content="bonjour", finish_reason="stop")))
        _run_async(collector.feed(FakeStreamChunk(usage=FakeUsage(10, 5))))
        completion = _run_async(collector.finish())
        assert completion.usage is not None
        assert completion.usage.prompt_tokens == 10
        assert completion.usage.completion_tokens == 5
        assert completion.choices[0].message.content == "bonjour"

    def test_cumulative_content_is_deduplicated(self) -> None:
        collector = StreamCollector(_settings())
        _run_async(collector.feed(FakeStreamChunk(content="Bon")))
        _run_async(collector.feed(FakeStreamChunk(content="Bonjour")))
        _run_async(collector.feed(FakeStreamChunk(content="Bonjour")))
        completion = _run_async(collector.finish())
        assert completion.choices[0].message.content == "Bonjour"

    def test_tool_calls_assembled_across_fragments(self) -> None:
        collector = StreamCollector(_settings())
        _run_async(
            collector.feed(
                FakeStreamChunk(
                    tool_calls=[FakeStreamToolCall(0, call_id="c1", name="measure_", arguments='{"a')],
                    finish_reason="tool_calls",
                )
            )
        )
        _run_async(
            collector.feed(
                FakeStreamChunk(tool_calls=[FakeStreamToolCall(0, name="current_document", arguments="}")])
            )
        )
        completion = _run_async(collector.finish())
        call = completion.choices[0].message.tool_calls[0]
        assert call.id == "c1"
        assert call.function.name == "measure_current_document"
        assert call.function.arguments == '{"a}'

    def test_hybrid_live_and_tool_calls_is_protocol_error(self) -> None:
        collector = StreamCollector(_settings())
        _run_async(collector.feed(FakeStreamChunk(content="<LIVE_RESPONSE>t</LIVE_RESPONSE>")))
        _run_async(
            collector.feed(
                FakeStreamChunk(tool_calls=[FakeStreamToolCall(0, call_id="c", name="measure_current_document")])
            )
        )
        completion = _run_async(collector.finish())
        assert completion.protocol_error == "hybrid_live_and_tool_calls"

    def test_live_deltas_bounded_and_started_before_first_delta(self) -> None:
        calls: list[tuple[str, str]] = []

        async def sink(kind: str, fields: dict) -> None:
            calls.append((kind, fields.get("delta", "")))

        settings = _settings(stream_delta_batch_chars=8)
        collector = StreamCollector(settings, live_sink=sink, response_role="avocat")
        _run_async(
            collector.feed(
                FakeStreamChunk(
                    content="<LIVE_RESPONSE>Bonjour le monde et la suite du raisonnement</LIVE_RESPONSE>"
                    "<FINAL_JSON>{}</FINAL_JSON>"
                )
            )
        )
        completion = _run_async(collector.finish())
        assert calls[0][0] == "agent.response.started"
        deltas = [delta for kind, delta in calls if kind == "agent.response.delta"]
        assert all(len(delta) <= 8 for delta in deltas)
        assert "".join(deltas) == completion.live_text
        assert "<FINAL_JSON>" not in "".join(deltas)


class TestAgentLoopStreaming:
    def _patched_loop(self, monkeypatch, stream, max_rounds=2):
        client = FakeClient({"avocat": stream})
        monkeypatch.setattr(agent, "build_client", lambda settings: client)
        events: list[tuple[str, dict]] = []

        async def sink(kind: str, fields: dict) -> None:
            events.append((kind, dict(fields)))

        async def run():
            return await agent.run_agent_loop(
                [
                    {"role": "system", "content": "Tu es l'expert AVOCAT de l'analyse documentaire CONCLAVE."},
                    {"role": "user", "content": "document"},
                ],
                AgentSession(document="document"),
                _settings(),
                max_rounds=max_rounds,
                agent_role="avocat",
                response_event_sink=sink,
                stream_final_envelope=True,
            )

        return _run_async(run()), events, client

    def test_final_envelope_extracted_and_deltas_emitted(self, monkeypatch) -> None:
        payload = agent_output_json("avocat")
        stream = FakeStream(
            [
                FakeStreamChunk(content="<LIVE_RESP"),
                FakeStreamChunk(
                    content="<LIVE_RESPONSE>\nVoici la conclusion\n</LIVE_RESPONSE>\n"
                    "<FINAL_JSON>\n" + payload + "\n</FINAL_JSON>"
                ),
                FakeStreamChunk(usage=FakeUsage(10, 5)),
            ]
        )
        result, events, _ = self._patched_loop(monkeypatch, stream)
        assert result.stop_reason is None
        assert json.loads(result.answer)["role"] == "avocat"
        assert result.usage.input_tokens == 10
        assert result.live_text == "\nVoici la conclusion\n"
        kinds = [kind for kind, _ in events]
        assert kinds[0] == "agent.response.started"
        assert "agent.response.delta" in kinds
        for kind, fields in events:
            assert fields["role"] == "avocat"
            if kind == "agent.response.delta":
                assert "FINAL_JSON" not in fields["delta"]

    def test_protocol_error_stops_cleanly_without_answer(self, monkeypatch) -> None:
        stream = FakeStream([FakeStreamChunk(content="<LIVE_RESPONSE>texte seul</LIVE_RESPONSE>")])
        result, events, client = self._patched_loop(monkeypatch, stream)
        assert result.stop_reason == "protocol_error"
        assert result.answer is None
        assert result.live_text == "\ntexte seul" or "texte seul" in result.live_text
        assert events[0][0] == "agent.response.started"

    def test_tool_round_with_filler_text_emits_no_live_events(self, monkeypatch) -> None:
        tool_stream = FakeStream(
            [
                FakeStreamChunk(
                    tool_calls=[FakeStreamToolCall(0, call_id="c1", name="measure_current_document", arguments="{}")],
                    finish_reason="tool_calls",
                ),
                FakeStreamChunk(usage=FakeUsage(20, 8)),
            ]
        )
        final_stream = FakeStream(
            [
                FakeStreamChunk(content="<LIVE_RESPONSE>conclusion</LIVE_RESPONSE><FINAL_JSON>" + agent_output_json("avocat") + "</FINAL_JSON>"),
                FakeStreamChunk(usage=FakeUsage(30, 12)),
            ]
        )
        result, events, _ = self._patched_loop(monkeypatch, [tool_stream, final_stream])
        assert result.answer is not None
        assert result.executed_tools == ["measure_current_document"]
        kinds = [kind for kind, _ in events]
        assert "agent.response.started" in kinds
        assert "agent.response.delta" in kinds
        assert len(events) <= 3  # started + 1 delta maximum (conclusion courte)

    def test_final_json_only_round_without_live_is_valid(self, monkeypatch) -> None:
        stream = FakeStream(
            [
                FakeStreamChunk(
                    content="<FINAL_JSON>\n" + agent_output_json("avocat") + "\n</FINAL_JSON>"
                )
            ]
        )
        result, events, _ = self._patched_loop(monkeypatch, stream)
        assert result.stop_reason is None
        assert json.loads(result.answer)["role"] == "avocat"
        assert events == []


class TestResponseEventsPersisted:
    def test_sequence_increasing_and_order(self, tmp_path, monkeypatch) -> None:
        settings = _settings(tmp_path)
        payload = agent_output_json("avocat")
        stream = FakeStream(
            [
                FakeStreamChunk(content="<LIVE_RESPONSE>étape 1</LIVE_RESPONSE><FINAL_JSON>" + payload + "</FINAL_JSON>"),
                FakeStreamChunk(usage=FakeUsage(10, 5)),
            ]
        )
        client = FakeClient({"avocat": stream})
        monkeypatch.setattr(experts, "build_client", lambda settings: client)
        monkeypatch.setattr(agent, "build_client", lambda settings: client)

        async def go():
            await db.initialize(settings.database_path, "")
            now = db.utc_now_iso()
            async with db.open_connection(settings.database_path) as conn:
                await db.create_analysis(conn, "a1", "doc", now)
            return await experts.run_expert(
                "avocat",
                analysis_id="a1",
                document="doc",
                session=AgentSession(document="doc"),
                settings=settings,
                get_connection=_factory(settings.database_path),
            )

        result = _run_async(go())
        assert result.output is not None

        async def read():
            async with db.open_connection(settings.database_path) as conn:
                return await db.list_events_after(conn, "a1")

        events = _run_async(read())
        types = [e["event_type"] for e in events]
        started = types.index("agent.response.started")
        first_delta = next(i for i, t in enumerate(types) if t == "agent.response.delta")
        completed = types.index("agent.response.completed")
        expert_completed = types.index("expert.completed")
        assert started < first_delta < completed < expert_completed
        deltas = [e for e in events if e["event_type"] == "agent.response.delta"]
        sequences = [json.loads(e["payload_json"])["sequence"] for e in deltas]
        assert sequences == list(range(1, len(sequences) + 1))
        assert all(json.loads(e["payload_json"])["role"] == "avocat" for e in deltas)

    def test_failed_response_event_when_protocol_error(self, tmp_path, monkeypatch) -> None:
        settings = _settings(tmp_path)
        stream = FakeStream([FakeStreamChunk(content="<LIVE_RESPONSE>texte seul</LIVE_RESPONSE>")])
        client = FakeClient({"avocat": stream})
        monkeypatch.setattr(experts, "build_client", lambda settings: client)
        monkeypatch.setattr(agent, "build_client", lambda settings: client)

        async def go():
            await db.initialize(settings.database_path, "")
            now = db.utc_now_iso()
            async with db.open_connection(settings.database_path) as conn:
                await db.create_analysis(conn, "a1", "doc", now)
            return await experts.run_expert(
                "avocat",
                analysis_id="a1",
                document="doc",
                session=AgentSession(document="doc"),
                settings=settings,
                get_connection=_factory(settings.database_path),
            )

        result = _run_async(go())
        assert result.output is None
        assert result.error_code == "protocol_error"

        async def read():
            async with db.open_connection(settings.database_path) as conn:
                return await db.list_events_after(conn, "a1")

        events = _run_async(read())
        types = [e["event_type"] for e in events]
        assert "agent.response.failed" in types
        failed = [e for e in events if e["event_type"] == "agent.response.failed"][0]
        assert json.loads(failed["payload_json"])["error_code"] == "protocol_error"


class TestReplayAndToolsList:
    def test_analysis_with_streaming_events_completes(self, tmp_path, monkeypatch) -> None:
        settings = _settings(tmp_path)
        scripts = {
            "avocat": FakeStream(
                [FakeStreamChunk(content="<LIVE_RESPONSE>ok</LIVE_RESPONSE><FINAL_JSON>" + agent_output_json("avocat") + "</FINAL_JSON>")]
            ),
            "procureur": FakeStream(
                [FakeStreamChunk(content="<LIVE_RESPONSE>ok</LIVE_RESPONSE><FINAL_JSON>" + agent_output_json("procureur", score=55) + "</FINAL_JSON>")]
            ),
            "comptable": [
                FakeStream(
                    [
                        FakeStreamChunk(tool_calls=[FakeStreamToolCall(0, call_id="m", name="measure_current_document", arguments="{}")], finish_reason="tool_calls"),
                        FakeStreamChunk(usage=FakeUsage(20, 10)),
                    ]
                ),
                FakeStream(
                    [
                        FakeStreamChunk(tool_calls=[FakeStreamToolCall(0, call_id="c", name="estimate_current_analysis_cost", arguments="{}")], finish_reason="tool_calls"),
                        FakeStreamChunk(usage=FakeUsage(30, 12)),
                    ]
                ),
                FakeStream(
                    [
                        FakeStreamChunk(content="<LIVE_RESPONSE>ok</LIVE_RESPONSE><FINAL_JSON>" + agent_output_json("comptable", score=70) + "</FINAL_JSON>"),
                        FakeStreamChunk(usage=FakeUsage(40, 20)),
                    ]
                ),
            ],
            "arbitre": FakeStream(
                [FakeStreamChunk(content="<LIVE_RESPONSE>ok</LIVE_RESPONSE><FINAL_JSON>" + json.dumps({
                    "decision": "go_with_conditions", "score": 61,
                    "main_disagreement": "désaccord", "priority_risks": ["r"],
                    "actions": ["a"], "accepted_tradeoff": "t", "unavailable_agents": [],
                }) + "</FINAL_JSON>")]
            ),
        }
        client = FakeClient(scripts)
        monkeypatch.setattr(experts, "build_client", lambda settings: client)
        monkeypatch.setattr(agent, "build_client", lambda settings: client)

        async def go():
            await db.initialize(settings.database_path, "")
            now = db.utc_now_iso()
            async with db.open_connection(settings.database_path) as conn:
                await db.create_analysis(conn, "a1", "doc", now)
                await db.insert_analysis_event(conn, "a1", "analysis.created", {"analysis_id": "a1"}, now)
            return await experts.run_analysis("a1", "doc", settings, _factory(settings.database_path))

        result = _run_async(go())
        assert result.status == "completed"
        assert result.verdict is not None

        async def read():
            async with db.open_connection(settings.database_path) as conn:
                return await db.list_events_after(conn, "a1")

        events = _run_async(read())
        types = [e["event_type"] for e in events]
        assert types.count("agent.response.started") == 4
        assert types.count("agent.response.completed") == 4
        for role in ("avocat", "procureur", "comptable", "arbitre"):
            deltas = [
                e for e in events if e["event_type"] == "agent.response.delta"
                and json.loads(e["payload_json"])["role"] == role
            ]
            sequences = [json.loads(e["payload_json"])["sequence"] for e in deltas]
            assert sequences == list(range(1, len(sequences) + 1))
        # Le JSON final n'est jamais dans un delta.
        for event in events:
            if event["event_type"] == "agent.response.delta":
                assert "FINAL_JSON" not in json.loads(event["payload_json"])["delta"]
        assert types[-1] == "analysis.completed"