"""Smoke test MiniMax-M3 réel — opt-in uniquement (section 14.7 du plan R1).

Ce test ne s'exécute QUE si `MINIMAX_API_KEY` est présent dans l'environnement
au moment de lancer pytest ; sinon il est marqué `skipped` sans faire échouer
la suite déterministe. Il touche le vrai réseau MiniMax : à exécuter à la
main, jamais dans une CI sans clé.

Aucune clé ni contenu intégral de document n'est journalisé ici : seules des
assertions structurelles sont faites sur la réponse.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from backend.app import experts
from backend.app.agent import AgentSession
from backend.app.config import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMAX_API_KEY"),
    reason="MINIMAX_API_KEY absent : smoke test MiniMax réel non exécuté (comportement attendu en CI).",
)


def _settings() -> Settings:
    return Settings(
        minimax_api_key=os.environ["MINIMAX_API_KEY"],
        minimax_base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        minimax_model=os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
        database_path=":memory:",
        expert_timeout_seconds=45.0,
        arbiter_timeout_seconds=30.0,
        analysis_timeout_seconds=90.0,
        expert_max_output_tokens=600,
    )


DOCUMENT = (
    "Proposition : migrer l'authentification vers un fournisseur tiers avec "
    "SSO, MFA obligatoire et rotation automatique des secrets tous les 90 jours. "
    "Le budget prévu couvre douze mois de licence et une migration progressive "
    "par équipe, avec un plan de repli documenté en cas d'incident."
)


def test_real_minimax_streams_observable_deltas_without_thinking() -> None:
    settings = _settings()
    session = AgentSession(document=DOCUMENT)
    deltas: list[str] = []
    started_count = 0
    completed = False

    async def response_sink(kind: str, fields: dict) -> None:
        nonlocal started_count, completed
        if kind == "agent.response.started":
            started_count += 1
        elif kind == "agent.response.delta":
            deltas.append(fields["delta"])
            # Aucune balise de raisonnement ne doit jamais fuiter dans un delta.
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
        )

    result = asyncio.run(run())

    assert result.answer is not None, "MiniMax n'a pas produit de FINAL_JSON exploitable"
    assert started_count == 1
    assert completed is True
    # Au moins deux deltas distincts pour une réponse suffisamment longue :
    # preuve que le texte arrive au fil de l'eau, pas en un seul bloc final.
    assert len(deltas) >= 2, (
        f"attendu au moins 2 deltas, obtenu {len(deltas)} "
        "(réponse peut-être trop courte pour ce modèle/prompt)"
    )
    assert result.usage.llm_rounds >= 1
