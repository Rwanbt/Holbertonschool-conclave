"""Boucle agentique du Palier 3 — MiniMax décide, le serveur exécute.

Architecture :
- ADAPTATEURS : exposent le document chargé (jamais transmis au modèle) à
  travers trois outils SANS argument. Ils n'ont pas d'effet de bord.
- REGISTRE : associe un nom d'outil à sa description, son schéma JSON et son
  exécuteur. Aucun routage par mots-clés : c'est MiniMax qui choisit le nom.
- EXÉCUTEUR : valide le nom et les arguments, applique `DISABLED_TOOLS` puis
  exécute. Toute erreur d'outil est une entrée de trace (jamais un 500).
- BOUCLE : au plus `MINIMAX_MAX_TOOL_ROUNDS` appels MiniMax, avec détection
  des appels identiques répétés, accumulation des jetons et de la latence.

Le prompt système et les descriptions d'outils sont recopiés dans AGENTS.md :
toute modification ici doit y être répercutée.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

from . import tools
from .config import Settings, get_settings
from .llm import ProviderError, build_client
from .schemas import AgentResponse, ExecutionUsage, ToolTraceEntry

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

TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "measure_current_document",
        "description": (
            "Analyse le document chargé sur le serveur et renvoie ses métriques : "
            "nombre de caractères, de mots, de lignes et une estimation du nombre "
            "de jetons d'entrée. Cet outil ne prend aucun argument."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "find_security_indicators_in_current_document",
        "description": (
            "Analyse le document chargé sur le serveur et renvoie jusqu'à dix indices "
            "de sécurité locaux (clé, authentification, autorisation, injection, "
            "vie privée, disponibilité). Cet outil ne prend aucun argument."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "estimate_current_analysis_cost",
        "description": (
            "Estime le coût en dollars d'une analyse du document chargé sur le serveur, "
            "en fonction des tarifs MiniMax configurés et du budget de sortie. "
            "Cet outil ne prend aucun argument."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
)

_ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    definition["name"] for definition in TOOL_DEFINITIONS
)


@dataclass
class AgentSession:
    """État de l'analyse : le document reste côté serveur."""

    document: str
    metrics: dict[str, int] | None = None
    findings: list[dict[str, Any]] | None = None


def adapter_measure_current_document(
    session: AgentSession, settings: Settings
) -> dict[str, int]:
    session.metrics = tools.measure_document(session.document)
    return session.metrics


def adapter_find_security_indicators_in_current_document(
    session: AgentSession, settings: Settings
) -> list[dict[str, Any]]:
    session.findings = tools.find_security_indicators(
        session.document, tools.DEFAULT_SECURITY_PATTERNS
    )
    return session.findings


def adapter_estimate_current_analysis_cost(
    session: AgentSession, settings: Settings
) -> dict[str, Any]:
    if session.metrics is None:
        session.metrics = tools.measure_document(session.document)
    pricing = {
        "model_name": settings.minimax_model,
        "input_usd_per_million_tokens": settings.minimax_input_usd_per_million,
        "output_usd_per_million_tokens": settings.minimax_output_usd_per_million,
    }
    return tools.estimate_analysis_cost(
        session.metrics["estimated_input_tokens"],
        settings.minimax_max_output_tokens,
        pricing,
    )


_TOOL_EXECUTORS: dict[str, Any] = {
    "measure_current_document": adapter_measure_current_document,
    "find_security_indicators_in_current_document": adapter_find_security_indicators_in_current_document,
    "estimate_current_analysis_cost": adapter_estimate_current_analysis_cost,
}


def registry_tool_schemas() -> list[dict[str, Any]]:
    """Schémas `tools` OpenAI-compatibles exposés au modèle (jamais le document).

    MiniMax attend le format complet : `{"type": "function", "function": {...}}`.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": definition["name"],
                "description": definition["description"],
                "parameters": definition["parameters"],
            },
        }
        for definition in TOOL_DEFINITIONS
    ]


def _disabled_tool_names(settings: Settings) -> set[str]:
    return {name.strip() for name in settings.disabled_tools.split(",") if name.strip()}


def _output_summary(tool_name: str, result: Any) -> dict[str, Any]:
    if tool_name == "measure_current_document":
        return {
            key: result[key]
            for key in ("character_count", "word_count", "line_count", "estimated_input_tokens")
        }
    if tool_name == "find_security_indicators_in_current_document":
        return {
            "findings_count": len(result),
            "categories": sorted({finding["category"] for finding in result}),
        }
    if tool_name == "estimate_current_analysis_cost":
        return {
            key: result[key]
            for key in (
                "model_name",
                "input_tokens",
                "output_token_budget",
                "estimated_cost_usd",
                "currency",
            )
        }
    return {}


def _execute_tool(
    name: str, session: AgentSession, settings: Settings
) -> tuple[str, dict[str, Any], str | None, dict[str, Any] | None]:
    """Exécute un outil : (status, payload, error_code, output_summary)."""
    if name in _disabled_tool_names(settings):
        return (
            "error",
            {"error": {"code": "tool_disabled", "message": "Requested tool is unavailable"}},
            "tool_disabled",
            None,
        )
    executor = _TOOL_EXECUTORS.get(name)
    if executor is None:
        return (
            "error",
            {"error": {"code": "unknown_tool", "message": f"Unknown tool: {name}"}},
            "unknown_tool",
            None,
        )
    try:
        result = executor(session, settings)
    except Exception as exc:  # noqa: BLE001 - toute panne d'outil devient une trace
        return (
            "error",
            {
                "error": {
                    "code": "internal_error",
                    "message": f"Tool execution failed: {exc.__class__.__name__}",
                }
            },
            "internal_error",
            None,
        )
    return "success", result, None, _output_summary(name, result)


def _pricing_from_settings(settings: Settings) -> dict[str, Any]:
    return {
        "model_name": settings.minimax_model,
        "input_usd_per_million_tokens": settings.minimax_input_usd_per_million,
        "output_usd_per_million_tokens": settings.minimax_output_usd_per_million,
    }


def _estimated_cost_usd(settings: Settings, input_tokens: int, output_tokens: int) -> float | None:
    try:
        estimate = tools.estimate_analysis_cost(
            input_tokens, output_tokens, _pricing_from_settings(settings)
        )
    except (tools.UnknownPricingError, ValueError):
        return None
    return estimate["estimated_cost_usd"]


async def run_agent(
    instruction: str, document: str, settings: Settings | None = None
) -> AgentResponse:
    """Boucle agentique : MiniMax appelle des outils, le serveur les exécute."""
    current = settings if settings is not None else get_settings()
    max_rounds = max(1, current.minimax_max_tool_rounds)

    session = AgentSession(document=document)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]
    trace: list[ToolTraceEntry] = []
    executed_calls: set[tuple[str, str]] = set()

    total_input = 0
    total_output = 0
    total_tokens = 0
    any_usage = False
    total_latency_ms = 0
    rounds = 0
    answer: str | None = None

    async with build_client(current) as client:
        while rounds < max_rounds:
            rounds += 1
            started = time.monotonic()
            try:
                completion = await client.chat.completions.create(
                    model=current.minimax_model,
                    messages=messages,
                    max_completion_tokens=current.minimax_max_output_tokens,
                    temperature=0.3,
                    n=1,
                    tools=registry_tool_schemas(),
                    tool_choice="auto",
                    extra_body={"thinking": {"type": "disabled"}},
                )
            except Exception as exc:  # noqa: BLE001 - toute cause mène au 502
                raise ProviderError(
                    f"MiniMax agent request failed: {exc.__class__.__name__}"
                ) from exc
            total_latency_ms += int((time.monotonic() - started) * 1000)

            if completion.usage is not None:
                any_usage = True
                total_input += completion.usage.prompt_tokens or 0
                total_output += completion.usage.completion_tokens or 0
                total_tokens += completion.usage.total_tokens or 0

            if not completion.choices:
                raise ProviderError("MiniMax agent returned no choices")

            message = completion.choices[0].message
            tool_calls = message.tool_calls or []

            if not tool_calls:
                content = (message.content or "").strip()
                if not content:
                    raise ProviderError("MiniMax agent returned an empty answer")
                answer = content
                break

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

            if rounds >= max_rounds:
                for call in tool_calls:
                    trace.append(
                        ToolTraceEntry(
                            sequence=len(trace) + 1,
                            tool_name=call.function.name,
                            status="error",
                            input_summary={"tool": call.function.name},
                            output_summary=None,
                            duration_ms=0,
                            error_code="max_rounds_reached",
                        )
                    )
                break

            stop = False
            for call in tool_calls:
                name = call.function.name
                arguments_raw = (call.function.arguments or "").strip()
                try:
                    arguments = json.loads(arguments_raw) if arguments_raw else {}
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (ValueError, json.JSONDecodeError) as exc:
                    trace.append(
                        ToolTraceEntry(
                            sequence=len(trace) + 1,
                            tool_name=name,
                            status="error",
                            input_summary={
                                "requested_tool": name,
                                "arguments_preview": arguments_raw[:200],
                            },
                            output_summary=None,
                            duration_ms=0,
                            error_code="invalid_arguments",
                        )
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
                    trace.append(
                        ToolTraceEntry(
                            sequence=len(trace) + 1,
                            tool_name=name,
                            status="error",
                            input_summary={"tool": name, "identical_call": True},
                            output_summary=None,
                            duration_ms=0,
                            error_code="repeated_tool_call",
                        )
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
                    continue
                executed_calls.add(call_key)

                started_tool = time.monotonic()
                status, payload, error_code, output_summary = _execute_tool(
                    name, session, current
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

    if answer is None:
        if rounds >= max_rounds:
            answer = (
                "Je n'ai pas pu finaliser la demande : la limite d'itérations a été "
                "atteinte. Je ne peux pas vérifier le résultat demandé."
            )
        else:
            answer = (
                "Je ne peux pas vérifier l'information demandée avec les outils "
                "disponibles sur le serveur."
            )

    usage = ExecutionUsage(
        input_tokens=total_input if any_usage else None,
        output_tokens=total_output if any_usage else None,
        total_tokens=total_tokens if any_usage else None,
        estimated_cost_usd=(
            _estimated_cost_usd(current, total_input, total_output) if any_usage else None
        ),
        total_latency_ms=total_latency_ms,
        llm_rounds=rounds,
    )
    return AgentResponse(
        answer=answer, model=current.minimax_model, trace=trace, usage=usage
    )
