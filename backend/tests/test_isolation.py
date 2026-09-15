"""Tests d'isolation multi-utilisateur et de non-fuite des secrets.

Propriétés vérifiées :
- un utilisateur B ne peut NI lire NI démarrer l'analyse d'un utilisateur A ;
- les préférences d'outils d'une session ne modifient pas celles d'une autre ;
- la sélection provider/modèle est figée PAR analyse, sans croisement ;
- un credential BYOK réel ne fuit JAMAIS : ni dans le snapshot, ni dans
  l'historique/SSE, ni en base, ni dans les messages envoyés au modèle.

Aucun réseau, aucune clé réelle : le provider est le faux scripté.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import db
from backend.app.config import Settings, get_settings
from backend.app.main import app

from .conftest import FakeClient, scripted_arbiter, scripted_experts

DOC = "Document isolé pour le test."

SECRET = "sk-user-secret-abcdef1234567890"


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "minimax_api_key": "sk-test-not-a-real-key",
        "allow_server_provider_credentials": True,
        "database_path": str(tmp_path / "iso.db"),
        "minimax_input_usd_per_million": 0.30,
        "minimax_output_usd_per_million": 1.20,
        "expert_timeout_seconds": 15.0,
        "arbiter_timeout_seconds": 15.0,
        "analysis_timeout_seconds": 40.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def happy_client(tmp_path, patch_minimax) -> FakeClient:
    scripts = scripted_experts()
    scripts.update(scripted_arbiter())
    client = FakeClient(scripts)
    patch_minimax(client)
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    yield client
    app.dependency_overrides.clear()


def _wait_terminal(tc: TestClient, analysis_id: str) -> dict:
    import time

    snapshot: dict = {}
    for _ in range(300):
        snapshot = tc.get(f"/api/analyses/{analysis_id}").json()
        if snapshot["status"] in {"completed", "degraded", "failed"}:
            return snapshot
        time.sleep(0.05)
    return snapshot


class TestCrossUserIsolation:
    def test_user_b_cannot_read_or_start_user_a_analysis(
        self, happy_client
    ) -> None:
        with TestClient(app) as a, TestClient(app) as b:
            created = a.post("/api/analyses", json={"document": DOC}).json()
            analysis_id = created["analysis_id"]

            assert a.get(f"/api/analyses/{analysis_id}").status_code == 200
            assert b.get(f"/api/analyses/{analysis_id}").status_code == 404
            assert b.post(f"/api/analyses/{analysis_id}/start").status_code == 404
            assert (
                b.get(f"/api/analyses/{analysis_id}/events/history").status_code
                == 404
            )

    def test_user_b_cannot_stream_user_a_events(self, happy_client) -> None:
        with TestClient(app) as a, TestClient(app) as b:
            analysis_id = a.post("/api/analyses", json={"document": DOC}).json()[
                "analysis_id"
            ]
            assert (
                b.get(f"/api/analyses/{analysis_id}/events").status_code == 404
            )

    def test_tool_preferences_do_not_leak_between_sessions(
        self, happy_client
    ) -> None:
        with TestClient(app) as a, TestClient(app) as b:
            a.post(
                "/api/tool-commands",
                json={"command": "/tools disable measure_current_document"},
            )
            a_tools = a.get("/api/tools").json()["tools"]
            b_tools = b.get("/api/tools").json()["tools"]
            a_disabled = {
                t["tool_name"]
                for t in a_tools
                if t["tool_name"] == "measure_current_document" and not t["enabled"]
            }
            assert a_disabled == {"measure_current_document"}
            assert all(t["enabled"] for t in b_tools)

    def test_provider_selection_is_frozen_per_analysis(self, happy_client) -> None:
        with TestClient(app) as a, TestClient(app) as b:
            a_created = a.post(
                "/api/analyses",
                json={"document": DOC, "provider_id": "openai", "model_id": "gpt-4o-mini"},
            ).json()
            b_created = b.post(
                "/api/analyses",
                json={"document": DOC, "provider_id": "minimax", "model_id": "MiniMax-M3"},
            ).json()
            assert a_created["provider_id"] == "openai"
            assert b_created["provider_id"] == "minimax"
            a_snap = a.get(f"/api/analyses/{a_created['analysis_id']}").json()
            b_snap = b.get(f"/api/analyses/{b_created['analysis_id']}").json()
            assert a_snap["provider_id"] == "openai"
            assert a_snap["model_id"] == "gpt-4o-mini"
            assert b_snap["provider_id"] == "minimax"
            assert b_snap["model_id"] == "MiniMax-M3"


class TestSecretNeverLeaks:
    def test_credential_never_leaks_anywhere(self, tmp_path, happy_client) -> None:
        settings = _settings(tmp_path)
        with TestClient(app) as tc:
            created = tc.post("/api/analyses", json={"document": DOC}).json()
            analysis_id = created["analysis_id"]
            tc.post(
                f"/api/analyses/{analysis_id}/start",
                json={"api_key": SECRET},
            )
            snapshot = _wait_terminal(tc, analysis_id)
            history = tc.get(
                f"/api/analyses/{analysis_id}/events/history?after=0&limit=500"
            ).json()

        serialized_snapshot = json.dumps(snapshot, ensure_ascii=False)
        serialized_history = json.dumps(history, ensure_ascii=False)
        assert SECRET not in serialized_snapshot
        assert SECRET not in serialized_history

        async def read_db():
            async with db.open_connection(settings.database_path) as conn:
                analyses = list(await (await conn.execute("SELECT * FROM analyses")).fetchall())
                events = list(
                    await (
                        await conn.execute("SELECT * FROM analysis_events")
                    ).fetchall()
                )
            return analyses, events

        analyses, events = asyncio.run(read_db())
        for row in analyses + events:
            assert SECRET not in json.dumps(dict(row))

        # La clé n'est jamais envoyée au modèle (messages/arguments d'appel).
        for messages in happy_client.created_messages:
            assert SECRET not in json.dumps(messages)
        for kwargs in happy_client.created_kwargs:
            assert SECRET not in json.dumps(kwargs)

    def test_credential_is_required_when_no_server_key(self, tmp_path) -> None:
        settings = _settings(
            tmp_path, allow_server_provider_credentials=False, minimax_api_key=""
        )
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            with TestClient(app) as tc:
                created = tc.post("/api/analyses", json={"document": DOC}).json()
                analysis_id = created["analysis_id"]
                # Aucune clé BYOK, aucune clé serveur autorisée : échec propre
                # et explicite, l'analyse reste `queued` (aucun token consommé).
                response = tc.post(f"/api/analyses/{analysis_id}/start")
                assert response.status_code == 400
                assert "clé API" in response.json()["detail"]
                snapshot = tc.get(f"/api/analyses/{analysis_id}").json()
                assert snapshot["status"] == "queued"
        finally:
            app.dependency_overrides.clear()