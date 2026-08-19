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


# --- Palier 3 : POST /api/p3/agent ---

_FAKE_AGENT_RESPONSE = {
    "answer": "Analyse terminée.",
    "model": "MiniMax-M3",
    "trace": [],
    "usage": {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "estimated_cost_usd": None,
        "total_latency_ms": 0,
        "llm_rounds": 1,
    },
}


async def _fake_run_agent(instruction: str, document: str, settings: Settings) -> dict:
    return _FAKE_AGENT_RESPONSE


def test_p3_agent_simulated_success(monkeypatch) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings_with_key()
    monkeypatch.setattr("backend.app.main.agent.run_agent", _fake_run_agent)
    try:
        response = client.post(
            "/api/p3/agent",
            json={"instruction": "Mesure le document.", "document": "abc def ghi"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Analyse terminée."
    assert body["model"] == "MiniMax-M3"
    assert body["trace"] == []
    assert body["usage"]["llm_rounds"] == 1


def test_p3_agent_empty_instruction_rejected() -> None:
    response = client.post(
        "/api/p3/agent", json={"instruction": "", "document": "abc"}
    )
    assert response.status_code == 422


def test_p3_agent_blank_document_rejected() -> None:
    response = client.post(
        "/api/p3/agent", json={"instruction": "Analyse", "document": "   \t "}
    )
    assert response.status_code == 422


def test_p3_agent_missing_document_rejected() -> None:
    response = client.post("/api/p3/agent", json={"instruction": "Analyse"})
    assert response.status_code == 422


def test_p3_agent_too_long_document_rejected() -> None:
    response = client.post(
        "/api/p3/agent",
        json={"instruction": "Analyse", "document": "a" * 12_001},
    )
    assert response.status_code == 422


def test_p3_agent_missing_server_configuration() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(minimax_api_key="")
    try:
        response = client.post(
            "/api/p3/agent",
            json={"instruction": "Analyse", "document": "abc"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500


def test_p3_agent_provider_error(monkeypatch) -> None:
    async def fake_boom(instruction: str, document: str, settings: Settings):
        raise llm.ProviderError("simulated provider failure")

    app.dependency_overrides[get_settings] = lambda: _settings_with_key()
    monkeypatch.setattr("backend.app.main.agent.run_agent", fake_boom)
    try:
        response = client.post(
            "/api/p3/agent",
            json={"instruction": "Analyse", "document": "abc"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
