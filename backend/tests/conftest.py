"""Fixtures partagées des tests Palier 4.

Le faux client MiniMax est scripté PAR RÔLE (avocat / procureur / comptable /
arbitre) : chaque expert consomme sa propre file de réponses, ce qui rend les
trois `asyncio.gather` déterministes. La détection du rôle se fait sur le
prompt système. Les appels de réparation (« Ta réponse n'est pas valide »)
consomment une file `repairs` dédiée.

Aucun réseau, aucune clé, aucun coût : uniquement des scripts en mémoire.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app import agent, experts


class FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str = "{}"):
        self.id = call_id
        self.function = FakeFunction(name, arguments)


class _Hang(Exception):
    """Sentinel : l'appel MiniMax ne répond jamais (test des garde-fous de temps)."""


HANG: object = object()


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class FakeCompletion:
    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


class FakeStreamDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeStreamChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class FakeStreamToolCall:
    def __init__(self, index, call_id=None, name=None, arguments=None):
        self.index = index
        self.id = call_id
        self.function = FakeFunction(name or "", arguments or "")


class FakeStreamChunk:
    """Morceau de stream OpenAI : `content`/`tool_calls` dans le delta + usage."""

    def __init__(self, content=None, tool_calls=None, usage=None, finish_reason=None):
        self.choices = []
        if content or tool_calls:
            self.choices.append(
                FakeStreamChoice(
                    FakeStreamDelta(content=content, tool_calls=tool_calls),
                    finish_reason,
                )
            )
        self.usage = usage


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


def _completion_to_stream(completion: FakeCompletion) -> FakeStream:
    """Convertit une réponse non-streamée en stream d'un seul morceau.

    La réponse est reconstituée à l'identique : contenu (enveloppe) et appels
    d'outil par index, puis un chunk final `choices=[]` porteur de l'usage.
    """
    chunks: list[FakeStreamChunk] = []
    if completion.choices:
        message = completion.choices[0].message
        tool_calls = message.tool_calls or []
        if tool_calls:
            for index, call in enumerate(tool_calls):
                last = index == len(tool_calls) - 1
                chunks.append(
                    FakeStreamChunk(
                        tool_calls=[
                            FakeStreamToolCall(
                                index,
                                call_id=call.id,
                                name=call.function.name,
                                arguments=call.function.arguments,
                            )
                        ],
                        finish_reason="tool_calls" if last else None,
                    )
                )
        else:
            chunks.append(
                FakeStreamChunk(
                    content=message.content or "",
                    finish_reason="stop",
                )
            )
    chunks.append(FakeStreamChunk(usage=completion.usage))
    return FakeStream(chunks)


class FakeClient:
    """Client scripté par rôle avec support des réparations et du streaming."""

    def __init__(
        self,
        scripts: dict[str, list[FakeCompletion] | FakeStream],
        repairs: list[FakeCompletion] | None = None,
    ):
        self.chat = _FakeChat(self)
        self._scripts = {
            key: list(value) if isinstance(value, (list, tuple)) else [value]
            for key, value in scripts.items()
        }
        self._repairs = list(repairs or [])
        self.created_messages: list[list[dict[str, Any]]] = []
        self.created_kwargs: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _role_key(self, system_content: str) -> str:
        upper = system_content.upper()
        if "ARBITRE" in upper:
            return "arbitre"
        if "AVOCAT" in upper:
            return "avocat"
        if "PROCUREUR" in upper:
            return "procureur"
        if "COMPTABLE" in upper:
            return "comptable"
        raise AssertionError("unknown role in system prompt")

    def next_response(self, messages: list[dict[str, Any]]) -> FakeCompletion:
        self.created_messages.append(messages)
        last = messages[-1] if messages else {}
        if last.get("role") == "user" and str(last.get("content", "")).startswith(
            "Ta réponse n'est pas valide"
        ):
            if not self._repairs:
                raise AssertionError("repair requested but no repair script")
            return self._repairs.pop(0)
        system = next(
            (m for m in messages if m.get("role") == "system"), None
        )
        key = self._role_key(system["content"])
        queue = self._scripts.get(key, [])
        if not queue:
            raise AssertionError(f"no script left for role {key}")
        response = queue.pop(0)
        if response is HANG:
            raise _Hang()
        return response


class _FakeCompletions:
    def __init__(self, owner: FakeClient):
        self._owner = owner

    async def create(self, **kwargs):
        messages = kwargs.get("messages", [])
        self._owner.created_kwargs.append(kwargs)
        try:
            response = self._owner.next_response(messages)
        except _Hang:
            import asyncio

            await asyncio.sleep(3600)
            raise AssertionError("unreachable")
        if kwargs.get("stream", False) and isinstance(response, FakeCompletion):
            return _completion_to_stream(response)
        return response


class _FakeChat:
    def __init__(self, owner: FakeClient):
        self.completions = _FakeCompletions(owner)


def fake_client_factory(client: FakeClient):
    def _factory(_settings=None):
        return client

    return _factory


@pytest.fixture
def patch_minimax(monkeypatch):
    """Patche `build_client` dans agent et experts pour la durée du test."""

    def _patch(client: FakeClient) -> None:
        monkeypatch.setattr(agent, "build_client", fake_client_factory(client))
        monkeypatch.setattr(experts, "build_client", fake_client_factory(client))

    return _patch


def tool_call(name: str, call_id: str, arguments: str = "{}") -> FakeToolCall:
    return FakeToolCall(call_id, name, arguments)


def agent_output_json(role: str, **overrides: Any) -> str:
    """Sortie AgentOutput valide sérialisée (JSON) pour le rôle donné."""
    payload = {
        "role": role,
        "summary": "Synthèse argumentée du constat.",
        "findings": [
            {
                "title": "Constat principal",
                "evidence": "Le document contient un élément vérifiable.",
                "impact": "Impact mesurable sur la solution.",
                "priority": "high",
            },
            {
                "title": "Constat secondaire",
                "evidence": "Un second élément est observable.",
                "impact": "Impact modéré.",
                "priority": "medium",
            },
        ],
        "score_label": "acceptable",
        "score": 62,
        "recommendations": ["Vérifier le point principal."],
        "unavailable_tools": [],
    }
    payload.update(overrides)
    import json

    return json.dumps(payload, ensure_ascii=False)


def verdict_json(**overrides: Any) -> str:
    payload = {
        "decision": "go_with_conditions",
        "score": 61,
        "main_disagreement": "Désaccord entre experts sur la priorité.",
        "priority_risks": ["Risque identifié par le procureur."],
        "actions": ["Appliquer les conditions de l'arbitre."],
        "accepted_tradeoff": "Coût maîtrisé contre couverture des risques.",
        "unavailable_agents": [],
    }
    payload.update(overrides)
    import json

    return json.dumps(payload, ensure_ascii=False)


def envelope(final_content: str, live: str = "Analyse terminée : conclusion ci-après.") -> str:
    """Enveloppe de réponse finale : texte live puis objet JSON exact."""
    return (
        "<LIVE_RESPONSE>\n"
        + live
        + "\n</LIVE_RESPONSE>\n"
        "<FINAL_JSON>\n"
        + final_content
        + "\n</FINAL_JSON>"
    )


def final_completion(content: str, prompt: int = 30, completion: int = 40) -> FakeCompletion:
    """Réponse finale conforme : contenu enveloppé dans <LIVE_RESPONSE>/<FINAL_JSON>."""
    return FakeCompletion(
        [FakeChoice(FakeMessage(content=envelope(content)))],
        FakeUsage(prompt, completion),
    )


def scripted_experts(comptable_extra: list[FakeCompletion] | None = None) -> dict[str, list[FakeCompletion]]:
    """Script nominal : avocat/procureur répondent directement, comptable mesure puis coûte puis conclut."""
    scripts: dict[str, list[FakeCompletion]] = {
        "avocat": [final_completion(agent_output_json("avocat"))],
        "procureur": [final_completion(agent_output_json("procureur", score=55))],
        "comptable": [
            FakeCompletion(
                [FakeChoice(FakeMessage(content=None, tool_calls=[tool_call("measure_current_document", "m1")]))],
                FakeUsage(20, 10),
            ),
            FakeCompletion(
                [FakeChoice(FakeMessage(content=None, tool_calls=[tool_call("estimate_current_analysis_cost", "c1")]))],
                FakeUsage(30, 12),
            ),
        ]
        + list(comptable_extra or [])
        + [final_completion(agent_output_json("comptable", score=70))],
    }
    return scripts


def scripted_arbiter() -> dict[str, list[FakeCompletion]]:
    return {"arbitre": [final_completion(verdict_json())]}