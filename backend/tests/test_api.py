"""Tests du tuyau HTTP minimal — sans aucun appel réseau réel.

L'appel MiniMax est remplacé par un faux (`monkeypatch` sur
`backend.app.llm.generate_answer`) et la configuration est injectée via
`app.dependency_overrides`, donc aucun coût API ni clé n'est nécessaire.
"""

from fastapi.testclient import TestClient

from backend.app import llm
from backend.app.config import Settings, get_settings
from backend.app.main import app

client = TestClient(app)

_FAKE_MODEL = "MiniMax-M3"


def _settings_with_key() -> Settings:
    return Settings(minimax_api_key="sk-test-not-a-real-key")


def test_health_ok() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_message_rejected() -> None:
    response = client.post("/api/p2/llm", json={"message": ""})
    assert response.status_code == 422


def test_blank_message_rejected() -> None:
    response = client.post("/api/p2/llm", json={"message": "   \t  "})
    assert response.status_code == 422


def test_missing_message_rejected() -> None:
    response = client.post("/api/p2/llm", json={})
    assert response.status_code == 422


def test_too_long_message_rejected() -> None:
    response = client.post("/api/p2/llm", json={"message": "a" * 12_001})
    assert response.status_code == 422


def test_simulated_llm_success(monkeypatch) -> None:
    async def fake_generate_answer(message: str, settings: Settings) -> str:
        return "Réponse simulée pour le test."

    app.dependency_overrides[get_settings] = lambda: _settings_with_key()
    monkeypatch.setattr(llm, "generate_answer", fake_generate_answer)
    try:
        response = client.post("/api/p2/llm", json={"message": "Bonjour"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"answer": "Réponse simulée pour le test.", "model": _FAKE_MODEL}


def test_simulated_provider_error(monkeypatch) -> None:
    async def fake_boom(message: str, settings: Settings) -> str:
        raise llm.ProviderError("simulated provider failure")

    app.dependency_overrides[get_settings] = lambda: _settings_with_key()
    monkeypatch.setattr(llm, "generate_answer", fake_boom)
    try:
        response = client.post("/api/p2/llm", json={"message": "Bonjour"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502


def test_missing_server_configuration() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(minimax_api_key="")
    try:
        response = client.post("/api/p2/llm", json={"message": "Bonjour"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
