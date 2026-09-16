"""Parcours de production reproductible (plan §39) — niveau contrat API.

Rejoue les 14 étapes nominales + le parcours d'échec (credential invalide)
sans navigateur : le frontend réel est couvert par les tests Vitest/RTL, le
backend l'est ici via TestClient avec un provider scripté. Aucun réseau, aucune
clé.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.main import app

from .conftest import FakeClient, scripted_arbiter, scripted_experts

DOC = "Un document de production soumis au Conclave."
FAKE_KEY = "sk-e2e-not-a-real-key"


@pytest.fixture
def setup(tmp_path, patch_minimax):
    settings = Settings(
        minimax_api_key="sk-test-not-a-real-key",
        allow_server_provider_credentials=True,
        database_path=str(tmp_path / "e2e.db"),
        expert_timeout_seconds=15.0,
        arbiter_timeout_seconds=15.0,
        analysis_timeout_seconds=40.0,
    )
    client = FakeClient({**scripted_experts(), **scripted_arbiter()})
    patch_minimax(client)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as tc:
        yield tc, client
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
    if current is not None:
        events.append(current)
    return events


class TestProductionHappyPath:
    def test_full_conclave_lifecycle(self, setup) -> None:
        tc, client = setup

        # 1. Ouverture : santé + registre public + session.
        assert tc.get("/api/health").json() == {"status": "ok"}
        providers = tc.get("/api/providers").json()["providers"]
        assert {p["provider_id"] for p in providers} >= {
            "minimax",
            "openai",
            "anthropic",
            "gemini",
        }
        assert tc.post("/api/session").status_code == 200

        # 2. Provider non connecté : l'analyse reste `queued` tant que le
        #    credential n'est pas fourni au /start (le refus 400 du cas sans
        #    clé est couvert par TestProductionFailurePath).
        queued = tc.post(
            "/api/analyses", json={"document": DOC, "enabled_tools": []}
        ).json()
        assert tc.get(f"/api/analyses/{queued['analysis_id']}").json()["status"] == "queued"

        # 3-5. Connexion (clé BYOK), modèle et outils sélectionnés à la création.
        enabled_tools = [
            "measure_current_document",
            "find_security_indicators_in_current_document",
        ]
        created = tc.post(
            "/api/analyses",
            json={
                "document": DOC,
                "provider_id": "minimax",
                "model_id": "MiniMax-M3",
                "enabled_tools": enabled_tools,
            },
        ).json()
        analysis_id = created["analysis_id"]
        assert created["provider_id"] == "minimax"
        assert set(created["tool_configuration"]["enabled_tools"]) == set(enabled_tools)

        # 6. Démarrage avec la clé BYOK.
        assert (
            tc.post(
                f"/api/analyses/{analysis_id}/start",
                json={"api_key": FAKE_KEY},
            ).status_code
            == 202
        )

        # 7-10. Trois experts, flux live, arbitre, verdict.
        with tc.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
            sse_events = _parse_sse("".join(response.iter_text()))
        types = [e["event"] for e in sse_events]
        assert types.count("expert.started") == 3
        assert any(e["event"] == "agent.response.delta" for e in sse_events)
        assert "arbiter.completed" in types
        assert types[-1] == "analysis.completed"

        snapshot = _wait_terminal(tc, analysis_id)
        assert snapshot["status"] == "completed"
        assert snapshot["verdict"] is not None
        assert snapshot["verdict"]["decision"] in {
            "go",
            "go_with_conditions",
            "no_go",
        }

        # 11-12. F5 : snapshot + historique rejoués, état terminal inchangé.
        history = tc.get(
            f"/api/analyses/{analysis_id}/events/history?after=0&limit=500"
        ).json()
        assert history["last_event_id"] == sse_events[-1]["id"]
        reloaded = tc.get(f"/api/analyses/{analysis_id}").json()
        assert reloaded["status"] == "completed"
        assert reloaded["verdict"] == snapshot["verdict"]

        # 13. Nouvelle analyse dans la même session.
        second = tc.post(
            "/api/analyses", json={"document": "Second document."}
        ).json()
        assert second["analysis_id"] != analysis_id

        # 14. Aucun secret dans les artefacts.
        assert FAKE_KEY not in json.dumps(sse_events)
        assert FAKE_KEY not in json.dumps(snapshot)
        assert FAKE_KEY not in json.dumps(history)
        for messages in client.created_messages:
            assert FAKE_KEY not in json.dumps(messages)


class TestProductionFailurePath:
    def test_missing_credential_fails_cleanly_without_fuites(self, tmp_path) -> None:
        settings = Settings(
            database_path=str(tmp_path / "e2e_fail.db"),
            allow_server_provider_credentials=False,
            minimax_api_key="",
        )
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            with TestClient(app) as tc:
                tc.post("/api/session")
                created = tc.post(
                    "/api/analyses", json={"document": DOC}
                ).json()
                analysis_id = created["analysis_id"]

                # Aucun credential, aucune clé serveur autorisée : 400 explicite,
                # l'analyse reste `queued` (aucun spinner infini possible).
                bad = tc.post(f"/api/analyses/{analysis_id}/start")
                assert bad.status_code == 400
                detail = bad.json()["detail"]
                assert "clé API" in detail
                assert "sk-" not in detail

                snapshot = tc.get(f"/api/analyses/{analysis_id}").json()
                assert snapshot["status"] == "queued"
        finally:
            app.dependency_overrides.clear()