"""Registre, exécuteur et commandes d'outils — Palier 3 & 4 partagés.

Ce module centralise tout ce qui concerne les outils :
- les DESCRIPTIONS typées lues par le modèle (`TOOL_DEFINITIONS`) ;
- les ADAPTATEURS réels (aucun effet de bord) qui exposent le document
  chargé, jamais transmis au modèle ;
- l'EXÉCUTEUR asynchrone qui vérifie l'état SQLite (`tool_states`) au moment
  de l'appel, puis exécute l'adaptateur réel ;
- le PARSEUR STRICT de la grammaire `/tools` et la lecture/écriture des
  états d'outils (effet de bord SQLite, atomique et idempotent).

Aucun routage par mots-clés : c'est toujours MiniMax qui choisit le nom d'un
outil (`tool_choice="auto"`), et le dispatch n'accepte que ce nom.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .schemas import ToolName, ToolState

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
            "Appelle-le APRÈS avoir observé les métriques du document. "
            "Cet outil ne prend aucun argument."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
)

ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    definition["name"] for definition in TOOL_DEFINITIONS
)


@dataclass
class AgentSession:
    """État partagé d'une analyse : le document reste côté serveur."""

    document: str
    metrics: dict[str, int] | None = None
    findings: list[dict[str, Any]] | None = None


def adapter_measure_current_document(
    session: AgentSession, settings: Settings
) -> dict[str, int]:
    from . import tools

    session.metrics = tools.measure_document(session.document)
    return session.metrics


def adapter_find_security_indicators_in_current_document(
    session: AgentSession, settings: Settings
) -> list[dict[str, Any]]:
    from . import tools

    session.findings = tools.find_security_indicators(
        session.document, tools.DEFAULT_SECURITY_PATTERNS
    )
    return session.findings


def adapter_estimate_current_analysis_cost(
    session: AgentSession, settings: Settings
) -> dict[str, Any]:
    from . import tools

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


TOOL_EXECUTORS: dict[str, Any] = {
    "measure_current_document": adapter_measure_current_document,
    "find_security_indicators_in_current_document": adapter_find_security_indicators_in_current_document,
    "estimate_current_analysis_cost": adapter_estimate_current_analysis_cost,
}


def registry_tool_schemas() -> list[dict[str, Any]]:
    """Schémas `tools` OpenAI-compatibles exposés au modèle (jamais le document)."""
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


def output_summary(tool_name: str, result: Any) -> dict[str, Any]:
    """Résumé borné d'un résultat d'outil — jamais le document ni une clé."""
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


async def execute_tool(
    name: str,
    session: AgentSession,
    settings: Settings,
    get_connection=None,
) -> tuple[str, dict[str, Any], str | None, dict[str, Any] | None]:
    """Exécute un outil : (status, payload, error_code, output_summary).

    Vérifie l'état SQLite courant avant l'exécution. `get_connection` est une
    fabrique async de connexion (dépendance ou connexion de tâche de fond).
    Sans connexion (Palier 3), retombe sur `settings.disabled_tools`.
    """
    state = None
    if get_connection is not None:
        async with (await get_connection()) as conn:
            from . import db

            state = await db.get_tool_state(conn, name)
    else:
        disabled = {
            name_.strip()
            for name_ in settings.disabled_tools.split(",")
            if name_.strip()
        }
        if name in disabled:
            state = {"enabled": 0}
    if state is not None and not state["enabled"]:
        return (
            "error",
            {"error": {"code": "tool_disabled", "message": "Requested tool is unavailable"}},
            "tool_disabled",
            None,
        )
    executor = TOOL_EXECUTORS.get(name)
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
    return "success", result, None, output_summary(name, result)


def pricing_from_settings(settings: Settings) -> dict[str, Any]:
    return {
        "model_name": settings.minimax_model,
        "input_usd_per_million_tokens": settings.minimax_input_usd_per_million,
        "output_usd_per_million_tokens": settings.minimax_output_usd_per_million,
    }


def estimated_cost_usd(
    settings: Settings, input_tokens: int, output_tokens: int
) -> float | None:
    from . import tools

    try:
        estimate = tools.estimate_analysis_cost(
            input_tokens, output_tokens, pricing_from_settings(settings)
        )
    except (tools.UnknownPricingError, ValueError):
        return None
    return estimate["estimated_cost_usd"]


# ---------------------------------------------------------------------------
# Commandes /tools — parseur strict, lecture et écriture des états
# ---------------------------------------------------------------------------


class ToolCommandSyntaxError(ValueError):
    """Syntaxe ou nom de commande /tools inconnu."""


def parse_tool_command(command: str) -> tuple[str, ToolName | None]:
    """Analyse une commande /tools.

    Grammaire acceptée :
        /tools
        /tools list
        /tools enable <name>
        /tools disable <name>
    Retourne (action, tool_name) avec action in {"list", "enable", "disable"}.
    Lève `ToolCommandSyntaxError` en cas de syntaxe ou de nom inconnu.
    """
    tokens = command.strip().split()
    if not tokens or tokens[0] != "/tools":
        raise ToolCommandSyntaxError("commande must start with /tools")
    if len(tokens) == 1:
        return "list", None
    if len(tokens) == 2:
        if tokens[1] == "list":
            return "list", None
        raise ToolCommandSyntaxError(f"unknown /tools subcommand: {tokens[1]}")
    if len(tokens) == 3 and tokens[1] in ("enable", "disable"):
        name = tokens[2]
        if name not in ALLOWED_TOOL_NAMES:
            raise ToolCommandSyntaxError(f"unknown tool name: {name}")
        return tokens[1], name  # type: ignore[return-value]
    raise ToolCommandSyntaxError("usage: /tools [list|enable <name>|disable <name>]")


def tool_state_from_row(row: Any) -> ToolState:
    return ToolState(
        tool_name=row["tool_name"],
        enabled=bool(row["enabled"]),
        description=next(
            d["description"] for d in TOOL_DEFINITIONS if d["name"] == row["tool_name"]
        ),
    )