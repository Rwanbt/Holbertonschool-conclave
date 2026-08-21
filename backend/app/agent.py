"""Boucle agentique générique (Palier 3 & 4) — MiniMax décide, le serveur exécute.

Ce module contient :
- la boucle générique `run_agent_loop` : envoie les descriptions typées,
  laisse MiniMax choisir (`tool_choice="auto"`), valide le JSON d'arguments,
  vérifie l'état SQLite de l'outil au moment de l'exécution, exécute
  l'adaptateur réel, ajoute le résultat `role="tool"` avec le bon
  `tool_call_id`, persiste la trace via un callback, et s'arrête sur une
  sortie finale, une limite ou une répétition ;
- le wrapper public `run_agent` (Palier 3) qui préserve sa signature et ses
  tests et utilise la même boucle.

Le prompt système et les descriptions d'outils sont recopiés dans AGENTS.md :
toute modification ici doit y être répercutée.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import toolkit
from .config import Settings, get_settings
from .llm import ProviderError, build_client
from .schemas import AgentResponse, ExecutionUsage, ToolTraceEntry
from .streaming import LiveSinkError, stream_chat_completion

SYSTEM_PROMPT: str = (
    "Tu es un agent d'analyse documentaire du backend CONCLAVE. "
    "Le document est déjà chargé sur le serveur : tu ne le reçois jamais dans la conversation. "
    "Tu peux utiliser les trois outils suivants, sans argument : measure_current_document, "
    "find_security_indicators_in_current_document et estimate_current_analysis_cost. "
    "Tu ne fabriques jamais un résultat : si un outil est indisponible ou en erreur, "
    "tu déclares que tu ne peux pas vérifier l'information plutôt que d'inventer une valeur. "
    "Tu n'inventes jamais de chemin de fichier ni de route. "
    "Chaque demande est traitée étape par étape : 1) décider quels outils sont nécessaires, "
    "2) les appeler, 3) examiner les résultats réels obtenus, "
    "4) rédiger la réponse finale en français, courte et précise, "
    "en citant uniquement des valeurs observées dans les résultats d'outils."
)

# Alias préservé pour les tests Palier 3.
_ALLOWED_TOOL_NAMES = toolkit.ALLOWED_TOOL_NAMES

AgentSession = toolkit.AgentSession
adapter_measure_current_document = toolkit.adapter_measure_current_document
adapter_find_security_indicators_in_current_document = (
    toolkit.adapter_find_security_indicators_in_current_document
)
adapter_estimate_current_analysis_cost = toolkit.adapter_estimate_current_analysis_cost


def registry_tool_schemas(
    allowed_tools: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Schémas `tools` OpenAI-compatibles exposés au modèle (jamais le document).

    Avec `allowed_tools` fourni, seuls ces noms sont inclus (un ensemble vide
    renvoie une liste vide, à charge de l'appelant d'omettre `tools`)."""
    return toolkit.registry_tool_schemas(allowed_tools)


@dataclass
class AgentLoopResult:
    """Résultat d'une boucle d'outils.

    `answer` peut être None quand la boucle s'est arrêtée sur une limite ou
    une répétition sans sortie finale.
    """

    answer: str | None
    trace: list[ToolTraceEntry]
    usage: ExecutionUsage
    rounds: int
    stop_reason: str | None = None
    executed_tools: list[str] = field(default_factory=list)
    live_text: str = field(default="", repr=False)


ToolEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
"""Sink de trace : `(kind, fields)` avec kind in
{"tool.started", "tool.completed", "tool.failed"} et `fields` un dict borné
sans le document ni le `analysis_id` (fournis par le fermeture de l'appelant).
"""


ResponseEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
"""Sink de réponse live : `(kind, fields)` avec kind in
{"agent.response.started", "agent.response.delta", "agent.response.completed",
"agent.response.failed"} et `fields` bornés (sans document, sans JSON final).
`run_agent_loop` n'émet que `started`/`delta` ; `completed`/`failed` sont
émis par l'appelant après validation Pydantic du JSON final.
"""


_PROTOCOL_REPAIR_HINT: str = (
    "Ta réponse n'est pas valide : un tour final doit obligatoirement "
    "contenir <LIVE_RESPONSE>un texte non vide</LIVE_RESPONSE> suivi de "
    "<FINAL_JSON>{...}</FINAL_JSON>, sans texte hors de ces deux balises. "
    "Renvoie une réponse corrigée respectant strictement cette enveloppe."
)

RoundEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
"""Sink de progression observable : `(kind, fields)` avec kind in
{"agent.round.started", "agent.round.completed"} et fields
{"round", "max_rounds"} / {"round", "outcome", "latency_ms"}. `outcome` in
{"tool_calls", "final_response", "protocol_error", "provider_error",
"max_rounds"}. Décrit l'exécution sans exposer la réflexion privée du modèle.
"""


async def run_agent_loop(
    messages: list[dict[str, Any]],
    session: AgentSession,
    settings: Settings,
    *,
    max_rounds: int,
    one_tool_per_round: bool = False,
    tool_event_sink: ToolEventSink | None = None,
    agent_role: str = "assistant",
    get_connection: Callable[[], Awaitable[Any]] | None = None,
    max_output_tokens: int | None = None,
    response_event_sink: ResponseEventSink | None = None,
    stream_final_envelope: bool = False,
    allowed_tools: frozenset[str] | None = None,
    round_event_sink: RoundEventSink | None = None,
) -> AgentLoopResult:
    """Boucle générique bornée. `get_connection` alimente l'état SQLite des outils
    (repli Palier 3 quand `allowed_tools` n'est pas fourni).

    `allowed_tools`, quand fourni (Palier 4), fige la configuration des outils
    de CETTE analyse : seuls ces schémas sont envoyés à MiniMax (un ensemble
    vide omet `tools`/`tool_choice` plutôt que d'envoyer une liste vide), et
    `toolkit.execute_tool` revérifie cette même liste à l'exécution — un appel
    fabriqué pour un outil non autorisé est refusé même si son schéma n'a
    jamais été envoyé au modèle.

    Avec `stream_final_envelope=True` (Palier 4), chaque appel MiniMax est
    streamé et la réponse finale doit être l'enveloppe
    `<LIVE_RESPONSE>…</LIVE_RESPONSE><FINAL_JSON>…</FINAL_JSON>` : le texte live
    est diffusé par `response_event_sink`, le JSON final est extrait dans
    `answer`. Une violation de protocole (JSON seul, live vide, marqueurs
    invalides) déclenche UNE seule requête MiniMax de réparation, toujours en
    streaming ; si elle échoue aussi, la boucle s'arrête proprement
    (`stop_reason="protocol_error"`) sans exécuter d'outil.
    """
    output_budget = max_output_tokens or settings.minimax_max_output_tokens
    executed_calls: set[tuple[str, str]] = set()
    trace: list[ToolTraceEntry] = []
    executed_tools: list[str] = []
    tool_schemas = registry_tool_schemas(allowed_tools)

    total_input = 0
    total_output = 0
    total_tokens = 0
    any_usage = False
    total_latency_ms = 0
    answer: str | None = None
    live_text = ""
    stop = False
    stop_reason: str | None = None

    def record_usage(completion: Any) -> None:
        nonlocal any_usage, total_input, total_output, total_tokens
        if completion.usage is None:
            return
        any_usage = True
        total_input += completion.usage.prompt_tokens or 0
        total_output += completion.usage.completion_tokens or 0
        total_tokens += completion.usage.total_tokens or 0

    async def emit_round_completed(
        round_number: int, outcome: str, latency_ms: int
    ) -> None:
        if round_event_sink is not None:
            await round_event_sink(
                "agent.round.completed",
                {
                    "round": round_number,
                    "outcome": outcome,
                    "latency_ms": latency_ms,
                },
            )

    async with build_client(settings) as client:
        # Boucle visible et montrable : chaque round est un appel MiniMax.
        for round_number in range(1, max_rounds + 1):
            if round_event_sink is not None:
                await round_event_sink(
                    "agent.round.started",
                    {"round": round_number, "max_rounds": max_rounds},
                )
            started = time.monotonic()

            async def _call(msgs: list[dict[str, Any]]):
                if stream_final_envelope:
                    return await stream_chat_completion(
                        client,
                        model=settings.minimax_model,
                        messages=msgs,
                        max_completion_tokens=output_budget,
                        temperature=0.3,
                        n=1,
                        tools=tool_schemas,
                        tool_choice="auto" if tool_schemas else None,
                        settings=settings,
                        live_sink=response_event_sink,
                        response_role=agent_role,
                    )
                kwargs: dict[str, Any] = {
                    "model": settings.minimax_model,
                    "messages": msgs,
                    "max_completion_tokens": output_budget,
                    "temperature": 0.3,
                    "n": 1,
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                    kwargs["tool_choice"] = "auto"
                return await client.chat.completions.create(**kwargs)

            try:
                completion = await _call(messages)
            except LiveSinkError:
                # Une panne SQLite/UI dans le sink n'est pas une panne MiniMax.
                # L'appelant la trace comme erreur interne.
                raise
            except Exception as exc:  # noqa: BLE001 - toute cause mène au 502
                round_latency_ms = int((time.monotonic() - started) * 1000)
                total_latency_ms += round_latency_ms
                await emit_round_completed(
                    round_number, "provider_error", round_latency_ms
                )
                raise ProviderError(
                    f"MiniMax agent request failed: {exc.__class__.__name__}"
                ) from exc
            record_usage(completion)

            if (
                stream_final_envelope
                and completion.protocol_error is not None
            ):
                try:
                    completion = await _call(
                        messages + [{"role": "user", "content": _PROTOCOL_REPAIR_HINT}]
                    )
                except LiveSinkError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    round_latency_ms = int((time.monotonic() - started) * 1000)
                    total_latency_ms += round_latency_ms
                    await emit_round_completed(
                        round_number, "provider_error", round_latency_ms
                    )
                    raise ProviderError(
                        f"MiniMax agent repair request failed: {exc.__class__.__name__}"
                    ) from exc
                record_usage(completion)

            round_latency_ms = int((time.monotonic() - started) * 1000)
            total_latency_ms += round_latency_ms

            if not completion.choices:
                await emit_round_completed(
                    round_number, "provider_error", round_latency_ms
                )
                raise ProviderError("MiniMax agent returned no choices")

            message = completion.choices[0].message
            tool_calls = message.tool_calls or []

            if stream_final_envelope:
                if completion.protocol_error is not None:
                    live_text = completion.live_text or live_text
                    stop = True
                    stop_reason = "protocol_error"
                    await emit_round_completed(
                        round_number, "protocol_error", round_latency_ms
                    )
                    break
                final_json = (completion.final_json or "").strip()
                if completion.live_text:
                    live_text = completion.live_text
            else:
                final_json = ""

            if not tool_calls:
                content = (message.content or "").strip()
                if not content and not final_json:
                    await emit_round_completed(
                        round_number, "provider_error", round_latency_ms
                    )
                    raise ProviderError("MiniMax agent returned an empty answer")
                answer = final_json or content
                await emit_round_completed(
                    round_number, "final_response", round_latency_ms
                )
                break

            if round_number >= max_rounds:
                await emit_round_completed(
                    round_number, "max_rounds", round_latency_ms
                )
                for call in tool_calls:
                    _append_trace_error(
                        trace,
                        call.function.name,
                        "max_rounds_reached",
                        {"tool": call.function.name},
                    )
                    if tool_event_sink is not None:
                        await tool_event_sink(
                            "tool.failed",
                            {
                                "agent_role": agent_role,
                                "llm_round": round_number,
                                "sequence": len(trace),
                                "tool_name": call.function.name,
                                "status": "error",
                                "input_summary": {"tool": call.function.name},
                                "output_summary": None,
                                "duration_ms": 0,
                                "error_code": "max_rounds_reached",
                            },
                        )
                stop = True
                stop_reason = "max_rounds_reached"
                break

            await emit_round_completed(round_number, "tool_calls", round_latency_ms)
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            deferred = tool_calls[1:] if one_tool_per_round else []
            for index, call in enumerate(tool_calls):
                if deferred and index > 0:
                    _append_trace_error(
                        trace,
                        call.function.name,
                        "one_tool_per_round",
                        {"tool": call.function.name},
                    )
                    if tool_event_sink is not None:
                        await tool_event_sink(
                            "tool.failed",
                            {
                                "agent_role": agent_role,
                                "llm_round": round_number,
                                "sequence": len(trace),
                                "tool_name": call.function.name,
                                "status": "error",
                                "input_summary": {"tool": call.function.name},
                                "output_summary": None,
                                "duration_ms": 0,
                                "error_code": "one_tool_per_round",
                            },
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.function.name,
                            "content": json.dumps(
                                {
                                    "error": {
                                        "code": "one_tool_per_round",
                                        "message": (
                                            "Un seul outil par tour : redemande cet appel "
                                            "au tour suivant."
                                        ),
                                    }
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue

                name = call.function.name
                arguments_raw = (call.function.arguments or "").strip()
                try:
                    arguments = json.loads(arguments_raw) if arguments_raw else {}
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (ValueError, json.JSONDecodeError) as exc:
                    _append_trace_error(
                        trace,
                        name,
                        "invalid_arguments",
                        {"requested_tool": name, "arguments_preview": arguments_raw[:200]},
                    )
                    if tool_event_sink is not None:
                        await tool_event_sink(
                            "tool.failed",
                            {
                                "agent_role": agent_role,
                                "llm_round": round_number,
                                "sequence": len(trace),
                                "tool_name": name,
                                "status": "error",
                                "input_summary": {"requested_tool": name},
                                "output_summary": None,
                                "duration_ms": 0,
                                "error_code": "invalid_arguments",
                            },
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": name,
                            "content": json.dumps(
                                {
                                    "error": {
                                        "code": "invalid_arguments",
                                        "message": f"Tool arguments must be a JSON object: {exc}",
                                    }
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue

                call_key = (name, json.dumps(arguments, sort_keys=True))
                if call_key in executed_calls:
                    _append_trace_error(
                        trace,
                        name,
                        "repeated_tool_call",
                        {"tool": name, "identical_call": True},
                    )
                    if tool_event_sink is not None:
                        await tool_event_sink(
                            "tool.failed",
                            {
                                "agent_role": agent_role,
                                "llm_round": round_number,
                                "sequence": len(trace),
                                "tool_name": name,
                                "status": "error",
                                "input_summary": {"tool": name, "identical_call": True},
                                "output_summary": None,
                                "duration_ms": 0,
                                "error_code": "repeated_tool_call",
                            },
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": name,
                            "content": json.dumps(
                                {
                                    "error": {
                                        "code": "repeated_tool_call",
                                        "message": (
                                            "Identical tool call repeated; "
                                            "stopping to avoid an infinite loop."
                                        ),
                                    }
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    stop = True
                    stop_reason = "repeated_tool_call"
                    continue
                executed_calls.add(call_key)

                if tool_event_sink is not None:
                    await tool_event_sink(
                        "tool.started",
                        {"agent_role": agent_role, "llm_round": round_number, "tool_name": name},
                    )

                started_tool = time.monotonic()
                status, payload, error_code, output_summary = await toolkit.execute_tool(
                    name, session, settings, get_connection, allowed_tools=allowed_tools
                )
                duration_ms = int((time.monotonic() - started_tool) * 1000)
                trace.append(
                    ToolTraceEntry(
                        sequence=len(trace) + 1,
                        tool_name=name,
                        status=status,
                        input_summary={"tool": name},
                        output_summary=output_summary if status == "success" else None,
                        duration_ms=duration_ms,
                        error_code=error_code,
                    )
                )
                if status == "success":
                    executed_tools.append(name)
                if tool_event_sink is not None:
                    await tool_event_sink(
                        "tool.completed" if status == "success" else "tool.failed",
                        {
                            "agent_role": agent_role,
                            "llm_round": round_number,
                            "sequence": len(trace),
                            "tool_name": name,
                            "status": status,
                            "input_summary": {"tool": name},
                            "output_summary": output_summary if status == "success" else None,
                            "duration_ms": duration_ms,
                            "error_code": error_code,
                        },
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": name,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )

            if stop:
                break

    usage = ExecutionUsage(
        input_tokens=total_input if any_usage else None,
        output_tokens=total_output if any_usage else None,
        total_tokens=total_tokens if any_usage else None,
        estimated_cost_usd=(
            toolkit.estimated_cost_usd(settings, total_input, total_output)
            if any_usage
            else None
        ),
        total_latency_ms=total_latency_ms,
        llm_rounds=max(1, round_number if answer is not None else round_number),
    )
    return AgentLoopResult(
        answer=answer,
        trace=trace,
        usage=usage,
        rounds=round_number,
        stop_reason=stop_reason,
        executed_tools=executed_tools,
        live_text=live_text,
    )


def _append_trace_error(
    trace: list[ToolTraceEntry],
    tool_name: str,
    error_code: str,
    input_summary: dict[str, Any],
) -> None:
    trace.append(
        ToolTraceEntry(
            sequence=len(trace) + 1,
            tool_name=tool_name,
            status="error",
            input_summary=input_summary,
            output_summary=None,
            duration_ms=0,
            error_code=error_code,
        )
    )


async def run_agent(
    instruction: str, document: str, settings: Settings | None = None
) -> AgentResponse:
    """Wrapper Palier 3 — signature publique et tests préservés."""
    current = settings if settings is not None else get_settings()
    session = AgentSession(document=document)
    result = await run_agent_loop(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ],
        session,
        current,
        max_rounds=max(1, current.minimax_max_tool_rounds),
        agent_role="assistant",
        get_connection=None,
    )
    if result.answer is None:
        if result.rounds >= max(1, current.minimax_max_tool_rounds):
            answer = (
                "Je n'ai pas pu finaliser la demande : la limite d'itérations a été "
                "atteinte. Je ne peux pas vérifier le résultat demandé."
            )
        else:
            answer = (
                "Je ne peux pas vérifier l'information demandée avec les outils "
                "disponibles sur le serveur."
            )
    else:
        answer = result.answer
    return AgentResponse(
        answer=answer,
        model=current.minimax_model,
        trace=result.trace,
        usage=result.usage,
    )
