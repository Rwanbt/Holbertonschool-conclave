"""Smoke tests providers réels — opt-in uniquement (plan §34).

Chaque test ne s'exécute QUE si la variable de clé correspondante est présente
dans l'environnement ; sinon il est marqué `skipped` sans faire échouer la
suite déterministe. Ils touchent le vrai réseau : à exécuter à la main, jamais
dans une CI (aucun secret dans GitHub).

Aucune clé ni contenu intégral de document n'est journalisé : seules des
assertions structurelles sont faites sur la réponse.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from backend.app import experts
from backend.app.agent import AgentSession
from backend.app.config import Settings

#: (env_key, provider_id, env_model) — smoke réel par fournisseur, skip sinon.
PROVIDER_SMOKES = (
    ("MINIMAX_API_KEY", "minimax", "MINIMAX_MODEL"),
    ("OPENAI_API_KEY", "openai", "OPENAI_MODEL"),
    ("ANTHROPIC_API_KEY", "anthropic", "ANTHROPIC_MODEL"),
    ("GEMINI_API_KEY", "gemini", "GEMINI_MODEL"),
)


def _settings(env_key: str, env_model: str) -> Settings:
    model = os.environ.get(env_model, "")
    return Settings(
        database_path=":memory:",
        allow_server_provider_credentials=True,
        minimax_api_key=os.environ.get("MINIMAX_API_KEY", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        expert_timeout_seconds=60.0,
        arbiter_timeout_seconds=40.0,
        analysis_timeout_seconds=120.0,
        expert_max_output_tokens=600,
    )


DOCUMENT = (
    "Proposition : migrer l'authentification vers un fournisseur tiers avec "
    "SSO, MFA obligatoire et rotation automatique des secrets tous les 90 jours. "
    "Le budget prévu couvre douze mois de licence et une migration progressive "
    "par équipe, avec un plan de repli documenté en cas d'incident."
)


@pytest.mark.parametrize("env_key,provider_id,env_model", PROVIDER_SMOKES)
def test_real_provider_streams_observable_deltas(env_key, provider_id, env_model) -> None:
    if not os.environ.get(env_key):
        pytest.skip(f"{env_key} absent : smoke test {provider_id} réel non exécuté.")
    settings = _settings(env_key, env_model)
    session = AgentSession(document=DOCUMENT, provider_id=provider_id)
    deltas: list[str] = []
    started_count = 0
    completed = False

    async def response_sink(kind: str, fields: dict) -> None:
        nonlocal started_count, completed
        if kind == "agent.response.started":
            started_count += 1
        elif kind == "agent.response.delta":
            deltas.append(fields["delta"])
            # Aucun raisonnement brut ne doit jamais fuiter dans un delta.
            assert "<think" not in fields["delta"].lower()
            assert "reasoning_content" not in fields["delta"].lower()
        elif kind == "agent.response.completed":
            completed = True

    async def sink(kind: str, fields: dict) -> None:
        return None

    async def get_connection():
        raise AssertionError("no persistence expected in this smoke test")

    async def run():
        from backend.app.agent import run_agent_loop

        return await run_agent_loop(
            [
                {"role": "system", "content": experts.SYSTEM_PROMPTS["avocat"]},
                {"role": "user", "content": DOCUMENT},
            ],
            session,
            settings,
            max_rounds=settings.agent_max_rounds,
            one_tool_per_round=True,
            tool_event_sink=sink,
            agent_role="avocat",
            max_output_tokens=settings.expert_max_output_tokens,
            response_event_sink=response_sink,
            stream_final_envelope=True,
            allowed_tools=frozenset(),
            provider_id=provider_id,
            model=os.environ.get(env_model) or None,
            api_key=os.environ.get(env_key),
        )

    result = asyncio.run(run())

    assert result.answer is not None, f"{provider_id} n'a pas produit de FINAL_JSON exploitable"
    assert started_count == 1
    assert completed is True
    assert len(deltas) >= 2, (
        f"attendu au moins 2 deltas, obtenu {len(deltas)} "
        "(réponse peut-être trop courte pour ce modèle/prompt)"
    )
    assert result.usage.llm_rounds >= 1