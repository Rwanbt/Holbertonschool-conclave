"""Tests de l'orchestration Palier 4 (experts, arbitre, garde-fous).

MiniMax est simulé par le faux client scripté de `conftest` (par rôle, avec
réparations et mode HANG). Les outils métier restent le vrai code, la base
SQLite est temporaire par test. Aucun appel réseau, aucun coût, aucune clé.
"""

import asyncio
import json

import pytest

from backend.app import db, experts
from backend.app.config import Settings

from .conftest import (
    FakeClient,
    FakeCompletion,
    FakeChoice,
    FakeMessage,
    HANG,
    agent_output_json,
    final_completion,
    scripted_arbiter,
    scripted_experts,
    tool_call,
    verdict_json,
)

DOC = "Un document court mais analysable."


def test_agent_output_is_bounded_before_validation() -> None:
    data = json.loads(agent_output_json("avocat"))
    data["summary"] = "s" * 2000
    data["findings"][0]["evidence"] = "e" * 1200
    data["recommendations"] = ["r" * 700] * 5

    output = experts.validate_agent_output("avocat", data)

    assert len(output.summary) == 1200
    assert len(output.findings[0].evidence) == 800
    assert len(output.recommendations) == 3
    assert all(len(value) == 500 for value in output.recommendations)


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "minimax_api_key": "sk-test-not-a-real-key",
        "minimax_max_tool_rounds": 3,
        "minimax_input_usd_per_million": 0.30,
        "minimax_output_usd_per_million": 1.20,
        "database_path": str(tmp_path / "conclave.db"),
        "agent_max_rounds": 5,
        "expert_timeout_seconds": 30.0,
        "arbiter_timeout_seconds": 20.0,
        "analysis_timeout_seconds": 60.0,
        "structured_repair_attempts": 1,
    }
    base.update(overrides)
    return Settings(**base)


def _factory(path: str):
    async def get_connection():
        return db.open_connection(path)

    return get_connection


def _run_analysis(tmp_path, settings, client):
    async def go():
        await db.initialize(settings.database_path, "")
        now = db.utc_now_iso()
        async with db.open_connection(settings.database_path) as conn:
            await db.create_analysis(conn, "a1", DOC, now)
            await db.insert_analysis_event(
                conn, "a1", "analysis.created", {"analysis_id": "a1"}, now
            )
        return await experts.run_analysis(
            "a1", DOC, settings, _factory(settings.database_path)
        )

    return asyncio.run(go())


class TestFullHappyPath:
    def test_completed_with_three_experts_and_arbiter(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "completed"
        assert result.error_code is None
        assert len(result.experts) == 3
        for expert in result.experts:
            assert expert.output is not None
            assert expert.output.role == expert.role
            assert 2 <= len(expert.output.findings) <= 5
        assert result.verdict is not None
        assert result.verdict.decision in {"go", "go_with_conditions", "no_go"}
        assert result.usage.input_tokens is not None
        assert result.usage.llm_rounds >= 5  # 3 experts multi-tours + arbitre

    def test_events_are_persisted_ordered_and_document_free(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)
        _run_analysis(tmp_path, _settings(tmp_path), client)

        async def read():
            async with db.open_connection(_settings(tmp_path).database_path) as conn:
                events = await db.list_events_after(conn, "a1")
                traces = []
                cursor = await conn.execute(
                    "SELECT * FROM tool_events WHERE analysis_id='a1' ORDER BY id"
                )
                traces = list(await cursor.fetchall())
            return events, traces

        events, traces = asyncio.run(read())
        types = [e["event_type"] for e in events]
        assert types[0] == "analysis.created"
        assert types.count("expert.started") == 3
        assert types.count("expert.completed") == 3
        assert "arbiter.started" in types
        assert "arbiter.completed" in types
        assert types[-1] == "analysis.completed"
        ids = [e["id"] for e in events]
        assert ids == sorted(ids)

        for trace in traces:
            assert DOC not in trace["input_summary_json"]
            assert trace["output_summary_json"] is None or DOC not in trace["output_summary_json"]


class TestArbiterAndDegradation:
    def test_escaped_expert_exception_is_persisted_and_degrades(self, tmp_path, patch_minimax, monkeypatch) -> None:
        scripts = scripted_experts()
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)
        original_run_expert = experts.run_expert

        async def escaped(role, **kwargs):
            if role == "procureur":
                raise RuntimeError("unexpected expert failure")
            return await original_run_expert(role, **kwargs)

        monkeypatch.setattr(experts, "run_expert", escaped)
        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "degraded"
        assert result.verdict is not None

        async def read():
            async with db.open_connection(_settings(tmp_path).database_path) as conn:
                return (
                    await db.list_expert_runs(conn, "a1"),
                    await db.list_events_after(conn, "a1"),
                )

        runs, events = asyncio.run(read())
        escaped_runs = [run for run in runs if run["role"] == "procureur"]
        assert escaped_runs[0]["status"] == "error"
        assert escaped_runs[0]["error_code"] == "internal_error"
        assert any(
            event["event_type"] == "expert.failed"
            and '"error_code": "internal_error"' in event["payload_json"]
            for event in events
        )

    def test_degraded_when_one_expert_missing(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        # Le procureur répond un texte sans JSON : il échoue structurellement.
        scripts["procureur"] = [final_completion("texte sans objet JSON")]
        scripts.update(scripted_arbiter())
        client = FakeClient(
            scripts, repairs=[final_completion("toujours pas")]
        )
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "degraded"
        assert result.verdict is not None
        assert result.verdict.unavailable_agents == ["procureur"]
        failed = [e for e in result.experts if e.role == "procureur"][0]
        assert failed.error_code == "structured_output_error"

    def test_no_verdict_with_less_than_two_valid(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts["avocat"] = [final_completion("sans json")]
        scripts["procureur"] = [final_completion("sans json encore")]
        client = FakeClient(
            scripts,
            repairs=[
                final_completion("toujours invalide"),
                final_completion("encore invalide"),
            ],
        )
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "failed"
        # P5 : on remonte la CAUSE, pas la conséquence. Les experts ont bien
        # répondu — c'est leur sortie qui était inexploitable. Annoncer
        # « insufficient_expertise » masquerait la vraie raison, alors que le
        # checkpoint exige de pouvoir répondre à « pourquoi ? » depuis l'app.
        assert result.error_code == "structured_output_error"
        assert result.verdict is None

    def test_arbiter_failure_keeps_expert_outputs_visible(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts["arbitre"] = [final_completion("pas un verdict json")]
        client = FakeClient(
            scripts, repairs=[final_completion("toujours pas un verdict json")]
        )
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "failed"
        # La cause précise de l'échec arbitre remonte jusqu'à l'analyse.
        assert result.error_code == "structured_output_error"
        assert result.verdict is None
        assert sum(1 for e in result.experts if e.output is not None) == 3

    def test_arbiter_repair_provider_failure_reaches_global_status(
        self, tmp_path, patch_minimax
    ) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts["arbitre"] = [final_completion("pas un verdict json")]
        # Aucun script de réparation : le double simule ici une requête de
        # réparation fournisseur qui échoue.
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "failed"
        assert result.error_code == "provider_unavailable"
        assert sum(1 for expert in result.experts if expert.output is not None) == 3


class TestStructuredValidation:
    def test_invalid_json_is_repaired_once(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        # L'avocat renvoie un mauvais JSON, puis une réparation valide.
        scripts["avocat"] = [
            final_completion("{corrompu"),
            final_completion(agent_output_json("avocat")),
        ]
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts, repairs=[final_completion(agent_output_json("avocat"))])
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        assert result.status == "completed"
        avocat = [e for e in result.experts if e.role == "avocat"][0]
        assert avocat.output is not None
        assert avocat.usage.input_tokens == 60
        assert avocat.usage.output_tokens == 80

    def test_expert_repair_provider_failure_is_named(
        self, tmp_path, patch_minimax
    ) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts["avocat"] = [final_completion("sortie invalide")]
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        avocat = [expert for expert in result.experts if expert.role == "avocat"][0]
        assert avocat.error_code == "provider_unavailable"
        assert result.status == "degraded"

    def test_unrepairable_output_fails_expert(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts["avocat"] = [final_completion("encore un texte"), final_completion("toujours pas")]
        scripts.update(scripted_arbiter())
        client = FakeClient(
            scripts, repairs=[final_completion("toujours pas")]
        )
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        avocat = [e for e in result.experts if e.role == "avocat"][0]
        assert avocat.output is None
        assert avocat.error_code == "structured_output_error"

    def test_comptable_cannot_conclude_without_required_tools(self, tmp_path, patch_minimax) -> None:
        # Le Comptable insiste pour conclure sans jamais mesurer ni estimer :
        # l'orchestrateur refuse chaque conclusion prématurée jusqu'à la
        # limite, au lieu de tenter de fabriquer les preuves par réparation.
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts["comptable"] = [
            final_completion(agent_output_json("comptable", score=70))
        ] * 5
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        comptable = [e for e in result.experts if e.role == "comptable"][0]
        assert comptable.output is None
        assert comptable.error_code == "max_rounds_reached"
        assert comptable.executed_tools == []


class TestGuardrails:
    def test_comptable_scripted_multi_turn_measure_then_cost(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts(
            comptable_extra=[final_completion(agent_output_json("comptable", score=70))]
        )
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        comptable = [e for e in result.experts if e.role == "comptable"][0]
        assert comptable.executed_tools == [
            "measure_current_document",
            "estimate_current_analysis_cost",
        ]
        assert comptable.output is not None

    def test_round_limit_reached(self, tmp_path, patch_minimax) -> None:
        settings = _settings(tmp_path, agent_max_rounds=2)
        scripts = scripted_experts()
        # Comptable boucle sur l'outil métriques sans jamais conclure.
        scripts["comptable"] = [
            FakeCompletion(
                [FakeChoice(FakeMessage(content=None, tool_calls=[tool_call("measure_current_document", "m1")]))]
            )
        ] * 5
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, settings, client)

        comptable = [e for e in result.experts if e.role == "comptable"][0]
        assert comptable.error_code == "max_rounds_reached"
        assert result.status in {"degraded", "failed"}

    def test_repeated_identical_call_stops(self, tmp_path, patch_minimax) -> None:
        scripts = scripted_experts()
        scripts["comptable"] = [
            FakeCompletion(
                [FakeChoice(FakeMessage(content=None, tool_calls=[tool_call("measure_current_document", "m1")]))]
            ),
            FakeCompletion(
                [FakeChoice(FakeMessage(content=None, tool_calls=[tool_call("measure_current_document", "m2")]))]
            ),
        ]
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, _settings(tmp_path), client)

        comptable = [e for e in result.experts if e.role == "comptable"][0]
        assert comptable.error_code == "repeated_tool_call"
        assert comptable.executed_tools == ["measure_current_document"]

    def test_expert_timeout(self, tmp_path, patch_minimax) -> None:
        settings = _settings(tmp_path, expert_timeout_seconds=0.2)
        scripts = scripted_experts()
        scripts["comptable"] = [HANG]
        scripts.update(scripted_arbiter())
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, settings, client)

        comptable = [e for e in result.experts if e.role == "comptable"][0]
        assert comptable.timed_out is True
        assert comptable.error_code == "expert_timeout"
        # L'analyse se termine malgré tout : dégradée (verdict possible) ou échouée.
        assert result.status in {"degraded", "failed"}

    def test_analysis_timeout(self, tmp_path, patch_minimax) -> None:
        settings = _settings(tmp_path, analysis_timeout_seconds=0.2)
        scripts = {
            "avocat": [HANG],
            "procureur": [HANG],
            "comptable": [HANG],
        }
        client = FakeClient(scripts)
        patch_minimax(client)

        result = _run_analysis(tmp_path, settings, client)

        assert result.status == "failed"
        assert result.error_code == "analysis_timeout"
