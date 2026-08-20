"""Tests HTTP du Palier 4 (analyse, snapshot, SSE, commandes d'outils).

La base est temporaire par test (`dependency_overrides[get_settings]`), le
client MiniMax est le faux scripté de `conftest`, et le lifespan réel
s'exécute via `with TestClient(app)`.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import db
from backend.app.config import Settings, get_settings
from backend.app.main import app

from .conftest import (
    FakeClient,
    agent_output_json,
    final_completion,
    scripted_arbiter,
    scripted_experts,
)

DOC = "Un document API soumis au backend."


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "minimax_api_key": "sk-test-not-a-real-key",
        "minimax_max_tool_rounds": 3,
        "minimax_input_usd_per_million": 0.30,
        "minimax_output_usd_per_million": 1.20,
        "database_path": str(tmp_path / "conclave_api.db"),
        "agent_max_rounds": 5,
        "expert_timeout_seconds": 30.0,
        "arbiter_timeout_seconds": 20.0,
        "analysis_timeout_seconds": 60.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def client(tmp_path, patch_minimax):
    settings = _settings(tmp_path)
    scripts = scripted_experts()
    scripts.update(scripted_arbiter())
    patch_minimax(FakeClient(scripts))
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client, settings
    app.dependency_overrides.clear()


def _create(test_client, document: str = DOC) -> dict:
    response = test_client.post("/api/analyses", json={"document": document})
    assert response.status_code == 201
    return response.json()


def _start(test_client, analysis_id: str) -> dict:
    response = test_client.post(f"/api/analyses/{analysis_id}/start")
    assert response.status_code in (200, 202)
    return response.json()


def _create_and_start(test_client, document: str = DOC) -> str:
    """Séquence R1 : créer (queued) puis démarrer explicitement — comme le
    ferait le navigateur après ouverture du SSE."""
    analysis_id = _create(test_client, document)["analysis_id"]
    _start(test_client, analysis_id)
    return analysis_id


def _wait_terminal(test_client, analysis_id: str) -> dict:
    snapshot: dict = {}
    for _ in range(200):
        snapshot = test_client.get(f"/api/analyses/{analysis_id}").json()
        if snapshot["status"] in {"completed", "degraded", "failed"}:
            break
        import time

        time.sleep(0.05)
    return snapshot


def _parse_sse(text: str) -> list[dict]:
    events = []
    current: dict | None = None
    for line in text.splitlines():
        if not line:
            if current is not None:
                events.append(current)
                current = None
            continue
        if line.startswith("id:"):
            current = {"id": int(line[3:].strip())}
        elif line.startswith("event:"):
            if current is None:
                current = {}
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            if current is None:
                current = {}
            current["data"] = json.loads(line[5:].strip())
    if current is not None:
        events.append(current)
    return events


class TestAnalysisLifecycle:
    def test_post_returns_201_queued_without_starting_job(self, client) -> None:
        test_client, _ = client
        body = _create(test_client)
        assert body["status"] == "queued"
        assert body["analysis_id"]
        assert set(body["tool_configuration"]["enabled_tools"]) == {
            "measure_current_document",
            "find_security_indicators_in_current_document",
            "estimate_current_analysis_cost",
        }
        assert body["tool_configuration"]["disabled_tools"] == []

        snapshot = test_client.get(f"/api/analyses/{body['analysis_id']}").json()
        assert snapshot["status"] == "queued"
        assert snapshot["document"] == DOC
        assert snapshot["started_at"] is None

        # Aucune tâche de fond n'a été lancée : le statut reste `queued`
        # même après un court délai, tant que `/start` n'a pas été appelé.
        import time

        time.sleep(0.2)
        still = test_client.get(f"/api/analyses/{body['analysis_id']}").json()
        assert still["status"] == "queued"

    def test_document_length_validation(self, client) -> None:
        test_client, _ = client
        assert test_client.post("/api/analyses", json={"document": ""}).status_code == 422
        too_long = "x" * 12001
        assert test_client.post("/api/analyses", json={"document": too_long}).status_code == 422

    def test_unknown_analysis_is_404(self, client) -> None:
        test_client, _ = client
        assert test_client.get("/api/analyses/does-not-exist").status_code == 404

    def test_snapshot_after_completion(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        snapshot = _wait_terminal(test_client, analysis_id)
        assert snapshot["status"] == "completed"
        assert snapshot["verdict"]["decision"] in {"go", "go_with_conditions", "no_go"}
        assert snapshot["avocat"]["status"] == "completed"
        assert snapshot["procureur"]["status"] == "completed"
        assert snapshot["comptable"]["status"] == "completed"
        assert snapshot["usage"]["input_tokens"] is not None
        assert "statuses" in snapshot["guardrails"]
        assert "queued" in snapshot["guardrails"]["statuses"]["analysis"]


class TestStartAnalysis:
    def test_start_returns_404_for_unknown_analysis(self, client) -> None:
        test_client, _ = client
        assert test_client.post("/api/analyses/does-not-exist/start").status_code == 404

    def test_first_start_returns_202_and_launches_job(self, client) -> None:
        test_client, _ = client
        analysis_id = _create(test_client)["analysis_id"]
        response = test_client.post(f"/api/analyses/{analysis_id}/start")
        assert response.status_code == 202
        body = response.json()
        assert body["already_started"] is False
        assert body["status"] == "running"
        snapshot = _wait_terminal(test_client, analysis_id)
        assert snapshot["status"] == "completed"

    def test_second_start_is_idempotent_without_duplicate_job(self, client) -> None:
        test_client, _ = client
        analysis_id = _create(test_client)["analysis_id"]
        first = test_client.post(f"/api/analyses/{analysis_id}/start")
        second = test_client.post(f"/api/analyses/{analysis_id}/start")
        assert first.status_code == 202
        assert second.status_code == 200
        assert second.json()["already_started"] is True
        snapshot = _wait_terminal(test_client, analysis_id)
        # Une seule tâche de fond : chaque expert n'a tourné qu'une fois
        # (trois `expert.started`, pas six).
        with test_client.stream(
            "GET", f"/api/analyses/{analysis_id}/events"
        ) as response:
            text = "".join(response.iter_text())
        assert text.count("expert.started") == 3
        assert snapshot["status"] == "completed"

    def test_start_after_terminal_reports_already_started(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        _wait_terminal(test_client, analysis_id)
        again = test_client.post(f"/api/analyses/{analysis_id}/start")
        assert again.status_code == 200
        assert again.json()["already_started"] is True
        assert again.json()["status"] == "completed"

    def test_analysis_started_event_precedes_expert_started(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        with test_client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
            text = "".join(response.iter_text())
        events = _parse_sse(text)
        types = [e["event"] for e in events]
        assert types[0] == "analysis.created"
        assert "analysis.started" in types
        assert types.index("analysis.started") < types.index("expert.started")


class TestEventsHistory:
    def test_history_is_ordered_paginated_and_bounded(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        _wait_terminal(test_client, analysis_id)

        full = test_client.get(
            f"/api/analyses/{analysis_id}/events/history?after=0&limit=500"
        ).json()
        assert full["has_more"] is False
        ids = [e["id"] for e in full["events"]]
        assert ids == sorted(ids)
        assert full["last_event_id"] == ids[-1]
        assert all(DOC not in json.dumps(e["payload"]) for e in full["events"])

        first_page = test_client.get(
            f"/api/analyses/{analysis_id}/events/history?after=0&limit=2"
        ).json()
        assert len(first_page["events"]) == 2
        assert first_page["has_more"] is True

        second_page = test_client.get(
            f"/api/analyses/{analysis_id}/events/history"
            f"?after={first_page['last_event_id']}&limit=500"
        ).json()
        assert second_page["events"][0]["id"] > first_page["last_event_id"]

    def test_history_unknown_analysis_is_404(self, client) -> None:
        test_client, _ = client
        assert (
            test_client.get("/api/analyses/does-not-exist/events/history").status_code
            == 404
        )


class TestSse:
    def test_sse_ordered_backlog_and_terminal_end(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        with test_client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
            text = "".join(response.iter_text())
        events = _parse_sse(text)

        assert len(events) >= 5
        assert events[0]["event"] == "analysis.created"
        types = [e["event"] for e in events]
        assert types.count("expert.started") == 3
        assert "arbiter.completed" in types
        assert types[-1] == "analysis.completed"
        ids = [e["id"] for e in events]
        assert ids == sorted(ids)

    def test_resume_from_last_event_id(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        with test_client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
            full = _parse_sse("".join(response.iter_text()))
        with test_client.stream(
            "GET",
            f"/api/analyses/{analysis_id}/events",
            headers={"Last-Event-ID": str(full[0]["id"])},
        ) as response:
            resumed = _parse_sse("".join(response.iter_text()))
        assert resumed[0]["id"] > full[0]["id"]
        assert resumed[-1]["event"] == full[-1]["event"]

    def test_resume_uses_max_of_last_event_id_and_after(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        with test_client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
            full = _parse_sse("".join(response.iter_text()))
        # header = full[1]["id"], after = full[0]["id"] -> reprise depuis max = full[1]
        with test_client.stream(
            "GET",
            f"/api/analyses/{analysis_id}/events?after={full[0]['id']}",
            headers={"Last-Event-ID": str(full[1]["id"])},
        ) as response:
            resumed = _parse_sse("".join(response.iter_text()))
        assert resumed[0]["id"] > full[1]["id"]

    def test_replay_contains_streaming_deltas_in_order(self, client) -> None:
        test_client, _ = client
        analysis_id = _create_and_start(test_client)
        with test_client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
            events = _parse_sse("".join(response.iter_text()))
        deltas = [e for e in events if e["event"] == "agent.response.delta"]
        assert deltas
        by_role: dict[str, list[int]] = {}
        for delta in deltas:
            by_role.setdefault(delta["data"]["role"], []).append(delta["data"]["sequence"])
        assert all(seq == sorted(seq) for seq in by_role.values())
        assert all(len(e["data"]["delta"]) >= 1 for e in deltas)
        assert all(e["data"]["role"] in {"avocat", "procureur", "comptable", "arbitre"} for e in deltas)
        ids = [e["id"] for e in events]
        assert ids == sorted(ids)


class TestToolCommands:
    def test_catalog_returns_three_tools(self, client) -> None:
        test_client, _ = client
        body = test_client.get("/api/tools").json()
        assert [tool["tool_name"] for tool in body["tools"]] == [
            "estimate_current_analysis_cost",
            "find_security_indicators_in_current_document",
            "measure_current_document",
        ]
        assert all(tool["enabled"] for tool in body["tools"])
        assert all(tool["description"].strip() for tool in body["tools"])

    def test_enable_disable_is_idempotent_and_persists(self, client) -> None:
        test_client, settings = client
        response = test_client.post(
            "/api/tool-commands",
            json={"command": "/tools disable measure_current_document"},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

        again = test_client.post(
            "/api/tool-commands",
            json={"command": "/tools disable measure_current_document"},
        )
        assert again.json()["enabled"] is False

        assert (
            test_client.get("/api/tools").json()["tools"][2]["enabled"] is False
        )

        test_client.post(
            "/api/tool-commands",
            json={"command": "/tools enable measure_current_document"},
        )
        assert (
            test_client.get("/api/tools").json()["tools"][2]["enabled"] is True
        )

    def test_invalid_command_is_422_without_side_effect(self, client) -> None:
        test_client, _ = client
        assert (
            test_client.post("/api/tool-commands", json={"command": "/tools foo"}).status_code
            == 422
        )
        assert (
            test_client.post(
                "/api/tool-commands", json={"command": "/tools enable nope"}
            ).status_code
            == 422
        )
        assert all(
            tool["enabled"]
            for tool in test_client.get("/api/tools").json()["tools"]
        )

    def test_post_tools_list_returns_catalog(self, client) -> None:
        test_client, _ = client
        body = test_client.post(
            "/api/tool-commands", json={"command": "/tools list"}
        ).json()
        assert body["action"] == "list"
        assert body["message"]
        assert body["tool_name"] is None
        assert body["enabled"] is None
        assert len(body["tools"]) == 3

    def test_post_tools_bare_returns_catalog(self, client) -> None:
        test_client, _ = client
        body = test_client.post(
            "/api/tool-commands", json={"command": "/tools"}
        ).json()
        assert body["action"] == "list"
        assert len(body["tools"]) == 3

    def test_enable_returns_action_and_full_catalog(self, client) -> None:
        test_client, _ = client
        body = test_client.post(
            "/api/tool-commands",
            json={"command": "/tools enable measure_current_document"},
        ).json()
        assert body["action"] == "enable"
        assert body["tool_name"] == "measure_current_document"
        assert body["enabled"] is True
        assert len(body["tools"]) == 3


class TestDisabledToolDuringAnalysis:
    def test_disabled_tool_traced_without_crash(self, tmp_path, patch_minimax) -> None:
        settings = _settings(tmp_path)
        scripts = scripted_experts()
        scripts.update(scripted_arbiter())
        patch_minimax(FakeClient(scripts))
        app.dependency_overrides[get_settings] = lambda: settings

        with TestClient(app) as test_client:
            test_client.post(
                "/api/tool-commands",
                json={"command": "/tools disable measure_current_document"},
            )
            analysis_id = _create_and_start(test_client)

            for _ in range(200):
                snapshot = test_client.get(f"/api/analyses/{analysis_id}").json()
                if snapshot["status"] in {"completed", "degraded", "failed"}:
                    break
                import time

                time.sleep(0.05)

            async def read_events():
                async with db.open_connection(settings.database_path) as conn:
                    cursor = await conn.execute(
                        "SELECT * FROM tool_events WHERE analysis_id=? ORDER BY id",
                        (analysis_id,),
                    )
                    return list(await cursor.fetchall())

            tool_rows = asyncio.run(read_events())
            assert any(row["error_code"] == "tool_disabled" for row in tool_rows)
            assert snapshot["status"] in {"degraded", "failed"}
        app.dependency_overrides.clear()


class TestPersistence:
    def test_analysis_survives_backend_restart(self, client) -> None:
        test_client, settings = client
        analysis_id = _create_and_start(test_client)
        for _ in range(200):
            snapshot = test_client.get(f"/api/analyses/{analysis_id}").json()
            if snapshot["status"] in {"completed", "degraded", "failed"}:
                break
            import time

            time.sleep(0.05)

        # « Redémarrage » du backend sur la même base.
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as reopened:
            snapshot = reopened.get(f"/api/analyses/{analysis_id}").json()
            assert snapshot["status"] == "completed"
            assert snapshot["document"] == DOC
        app.dependency_overrides.clear()