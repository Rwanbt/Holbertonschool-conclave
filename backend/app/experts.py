"""Experts, Arbitre et orchestration de l'analyse — Palier 4.

Flot :
    1. Trois experts (avocat, procureur, comptable) tournent en parallèle via
       asyncio.gather(..., return_exceptions=True), sans ordre de fin supposé.
    2. Chaque expert exécute la boucle générique ``run_agent_loop`` avec son
       prompt de rôle, un garde-fou de temps (expert_timeout_seconds), et
       produit en sortie finale un objet JSON ``AgentOutput`` validé par
       Pydantic (une seule tentative de réparation structurée).
    3. Avec au moins deux sorties valides, l'Arbitre reçoit UNIQUEMENT le
       document et les sorties validées, produit un ``ArbiterVerdict`` validé,
       et l'analyse se termine en ``completed`` (ou ``degraded`` si un expert
       manque). En cas de panne de l'Arbitre, les sorties des experts restent
       visibles et l'analyse passe en ``failed`` (error_code=arbiter_error).
    4. Avec 0 ou 1 sortie valide, l'analyse échoue (``failed``) sans verdict.
    5. Le tout est borné par ``analysis_timeout_seconds``.

Le document peut être transmis à MiniMax pour ces rôles (SPEC) mais n'est
jamais journalisé : les événements et traces ne contiennent que des résumés.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from . import db, security
from .agent import AgentLoopResult, AgentSession, run_agent_loop
from .config import Settings
from .llm import ProviderError, build_client
from .schemas import (
    AgentOutput,
    AgentResponseCompleted,
    AgentResponseDelta,
    AgentResponseFailed,
    AgentResponseStarted,
    ArbiterVerdict,
    ExecutionUsage,
    ExpertRole,
    ToolTraceEntry,
)

logger = logging.getLogger(__name__)

EXPERT_ROLES: tuple[ExpertRole, ...] = ("avocat", "procureur", "comptable")

_COMPTABLE_REQUIRED_TOOLS: frozenset[str] = frozenset(
    {"measure_current_document", "estimate_current_analysis_cost"}
)

_EXPERT_ENVELOPE: str = (
    "Quand tu conclus (sans nouvel appel d'outil a ce tour), reponds obligatoirement "
    "avec l'enveloppe suivante : "
    "<LIVE_RESPONSE> ton raisonnement final de conclusion en francais, bref, lisible, "
    "sans JSON, sans chaine de pensee, sans pretendre etre valide avant la fin "
    "</LIVE_RESPONSE> puis "
    "<FINAL_JSON> UNIQUEMENT l'objet JSON conforme au schema AgentOutput : role, summary, "
    "findings (2 a 5 elements avec title, evidence, impact, priority low|medium|high), "
    "score_label, score (0-100), recommendations (0 a 3), unavailable_tools </FINAL_JSON>. "
    "Aucun texte hors de ces deux balises."
)

SYSTEM_PROMPTS: dict[ExpertRole, str] = {
    "avocat": (
        "Tu es l'expert AVOCAT de l'analyse documentaire CONCLAVE. "
        "Le document te parvient dans le message utilisateur. "
        + security.DOCUMENT_IS_DATA_RULE
        + " "
        "Tu peux utiliser les outils serveur sans argument (métriques, indices "
        "de sécurité, coût) pour étayer ton argumentaire. "
        "Un seul outil par tour : si tu as besoin de plusieurs outils, appelle-les "
        "tour à tour. "
        "Rédige une plaidoirie de la solution proposée par le document, en t'appuyant "
        "sur des faits vérifiables. "
        + _EXPERT_ENVELOPE
    ),
    "procureur": (
        "Tu es l'expert PROCUREUR de l'analyse documentaire CONCLAVE. "
        "Le document te parvient dans le message utilisateur. "
        + security.DOCUMENT_IS_DATA_RULE
        + " "
        "Tu peux utiliser les outils serveur sans argument (métriques, indices "
        "de sécurité, coût) pour étayer ton réquisitoire. "
        "Un seul outil par tour : si tu as besoin de plusieurs outils, appelle-les "
        "tour à tour. "
        "Démontre les risques, faiblesses et objections que le document soulève, "
        "en t'appuyant sur des faits vérifiables. "
        + _EXPERT_ENVELOPE
    ),
    "comptable": (
        "Tu es l'expert COMPTABLE de l'analyse documentaire CONCLAVE. "
        "Le document te parvient dans le message utilisateur. "
        + security.DOCUMENT_IS_DATA_RULE
        + " "
        "Tu dois d'abord observer les métriques du document, puis estimer le coût "
        "d'une analyse, puis seulement conclure. "
        "Un seul outil par tour : au premier tour, demande les métriques ; au tour "
        "suivant, demande l'estimation du coût. "
        "Tu ne produis JAMAIS une conclusion chiffrée sans avoir observé les métriques "
        "réelles ni une estimation de coût sans données réelles : si ces mesures "
        "manquent, tu le signales dans summary et findings sans inventer de valeur. "
        + _EXPERT_ENVELOPE
    ),
}

_ARBITER_ENVELOPE: str = (
    "Quand tu conclus (sans nouvel appel d'outil a ce tour), reponds obligatoirement "
    "avec l'enveloppe suivante : "
    "<LIVE_RESPONSE> ton raisonnement final de decision en francais, bref, lisible, "
    "sans JSON, sans chaine de pensee, sans pretendre etre valide avant la fin "
    "</LIVE_RESPONSE> puis "
    "<FINAL_JSON> UNIQUEMENT l'objet JSON conforme au schema ArbiterVerdict : decision "
    "(go|go_with_conditions|no_go), score (0-100), main_disagreement, priority_risks "
    "(0 a 3), actions (0 a 3), accepted_tradeoff, unavailable_agents </FINAL_JSON>. "
    "Aucun texte hors de ces deux balises."
)

ARBITER_SYSTEM_PROMPT: str = (
    "Tu es l'ARBITRE de l'analyse documentaire CONCLAVE. "
    "Tu reçois le document et les sorties validées des experts (avocat, "
    "procureur, comptable). "
    "Tu peux aussi utiliser les outils serveur sans argument si tu dois vérifier "
    "un chiffre, mais ce n'est pas obligatoire. "
    "Départage les désaccords, puis rends une décision finale. "
    + security.DOCUMENT_IS_DATA_RULE
    + " "
    + _ARBITER_ENVELOPE
)

AgentOutputValidator = Callable[[str, dict[str, Any]], AgentOutput]
ArbiterValidator = Callable[[dict[str, Any]], ArbiterVerdict]

_AGENT_OUTPUT_FIELDS = {
    "role",
    "summary",
    "findings",
    "score_label",
    "score",
    "recommendations",
    "unavailable_tools",
}
_ARBITER_FIELDS = {
    "decision",
    "score",
    "main_disagreement",
    "priority_risks",
    "actions",
    "accepted_tradeoff",
    "unavailable_agents",
}


def validate_agent_output(role: ExpertRole, data: dict[str, Any]) -> AgentOutput:
    """Valide strictement une sortie d'expert. Lève ValidationError sinon."""
    return AgentOutput(**{**data, "role": role})


def validate_arbiter_verdict(data: dict[str, Any]) -> ArbiterVerdict:
    return ArbiterVerdict(**data)


def _first_findings_without_evidence(data: dict[str, Any]) -> str | None:
    findings = data.get("findings") or []
    for finding in findings:
        if not isinstance(finding, dict) or not finding.get("evidence"):
            return "au moins un constat est vide ou sans evidence"
    return None


def extract_structured_json(content: str) -> dict[str, Any] | None:
    """Extrait le premier objet JSON d'un texte (supporte la poésie du modèle)."""
    content = content.strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(content[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


@dataclass
class ExpertRunResult:
    role: ExpertRole
    run_id: str
    output: AgentOutput | None
    error_code: str | None
    usage: ExecutionUsage
    executed_tools: list[str] = field(default_factory=list)
    trace: list[ToolTraceEntry] = field(default_factory=list)
    timed_out: bool = False


@dataclass
class AnalysisResult:
    analysis_id: str
    status: str
    error_code: str | None
    experts: list[ExpertRunResult]
    verdict: ArbiterVerdict | None
    usage: ExecutionUsage


# Ordre de priorité des causes d'échec : une panne d'infrastructure explique
# tout le reste et doit être remontée AVANT une conclusion sur la qualité des
# sorties. Sans cet ordre, une coupure réseau était annoncée à l'utilisateur
# comme « pas assez d'experts exploitables » — un mensonge.
_FAILURE_PRIORITY: tuple[str, ...] = (
    "provider_unavailable",
    "internal_error",
    "expert_timeout",
    "protocol_error",
    "max_rounds_reached",
    "repeated_tool_call",
    "structured_output_error",
)


def _dominant_failure_code(codes: list[str | None]) -> str:
    """Cause dominante d'un échec d'analyse, la plus explicative d'abord.

    `insufficient_expertise` reste le repli quand les experts ont bel et bien
    répondu mais que trop peu de sorties sont exploitables : c'est alors une
    description exacte, pas un masque posé sur une panne."""
    present = {code for code in codes if code}
    for candidate in _FAILURE_PRIORITY:
        if candidate in present:
            return candidate
    return "insufficient_expertise"


def _empty_usage() -> ExecutionUsage:
    return ExecutionUsage(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        estimated_cost_usd=None,
        total_latency_ms=0,
        llm_rounds=0,
    )


def _merge_usage(usages: list[ExecutionUsage]) -> ExecutionUsage:
    merged = _empty_usage()
    inputs: list[int] = []
    outputs: list[int] = []
    totals: list[int] = []
    for usage in usages:
        merged.total_latency_ms += usage.total_latency_ms
        merged.llm_rounds += usage.llm_rounds
        if usage.input_tokens is not None:
            inputs.append(usage.input_tokens)
        if usage.output_tokens is not None:
            outputs.append(usage.output_tokens)
        if usage.total_tokens is not None:
            totals.append(usage.total_tokens)
        if usage.estimated_cost_usd is not None and merged.estimated_cost_usd is not None:
            merged.estimated_cost_usd += usage.estimated_cost_usd
        elif usage.estimated_cost_usd is not None:
            merged.estimated_cost_usd = usage.estimated_cost_usd
    if inputs:
        merged.input_tokens = sum(inputs)
    if outputs:
        merged.output_tokens = sum(outputs)
    if totals:
        merged.total_tokens = sum(totals)
    return merged


async def _repair_structured_output(
    client,
    messages: list[dict[str, Any]],
    settings: Settings,
    error_hint: str,
    max_output_tokens: int | None = None,
) -> str | None:
    """Une seule tentative de réparation : nouvel appel MiniMax sans outils."""
    output_budget = max_output_tokens or settings.minimax_max_output_tokens
    messages = messages + [
        {
            "role": "user",
            "content": (
                "Ta réponse n'est pas valide : " + error_hint + ". "
                "Renvoie UNIQUEMENT le JSON corrigé, conforme au schéma demandé, "
                "sans texte hors JSON."
            ),
        }
    ]
    try:
        completion = await client.chat.completions.create(
            model=settings.minimax_model,
            messages=messages,
            max_completion_tokens=output_budget,
            temperature=0.3,
            n=1,
            tools=[],
        )
    except Exception:  # noqa: BLE001
        return None
    if not completion.choices:
        return None
    content = (completion.choices[0].message.content or "").strip()
    return content or None


async def run_expert(
    role: ExpertRole,
    *,
    analysis_id: str,
    document: str,
    session: AgentSession,
    settings: Settings,
    get_connection: Callable[[], Awaitable[Any]],
    allowed_tools: frozenset[str] | None = None,
    document_nonce: str = "",
) -> ExpertRunResult:
    """Exécute un expert : boucle d'outils puis sortie JSON validée (1 réparation).

    `allowed_tools` est la configuration IMMUABLE de l'analyse (lue une seule
    fois dans `analysis_tool_states` par l'orchestrateur) : elle seule décide
    des schémas envoyés à MiniMax et de ce que `execute_tool` autorise.
    """
    run_id = uuid.uuid4().hex
    started_at = db.utc_now_iso()

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        async with (await get_connection()) as conn:
            await db.insert_analysis_event(
                conn, analysis_id, event_type, payload, db.utc_now_iso()
            )

    response_sequences: dict[str, int] = {}

    async def response_sink(kind: str, fields: dict[str, Any]) -> None:
        payload: dict[str, Any] = {"analysis_id": analysis_id, "role": role}
        now = db.utc_now_iso()
        if kind == "agent.response.delta":
            response_sequences[role] = response_sequences.get(role, 0) + 1
            payload["sequence"] = response_sequences[role]
            payload["delta"] = fields["delta"]
            AgentResponseDelta(**payload)
        elif kind == "agent.response.started":
            AgentResponseStarted(**payload)
        elif kind == "agent.response.completed":
            AgentResponseCompleted(**payload)
        elif kind == "agent.response.failed":
            payload["error_code"] = fields.get("error_code", "protocol_error")
            AgentResponseFailed(**payload)
        async with (await get_connection()) as conn:
            await db.insert_analysis_event(conn, analysis_id, kind, payload, now)

    async def sink(kind: str, fields: dict[str, Any]) -> None:
        now = db.utc_now_iso()
        async with (await get_connection()) as conn:
            if kind == "tool.completed" or kind == "tool.failed":
                await db.insert_tool_event(
                    conn,
                    analysis_id=analysis_id,
                    agent_role=role,
                    llm_round=fields["llm_round"],
                    sequence=fields["sequence"],
                    tool_name=fields["tool_name"],
                    status=fields["status"],
                    input_summary_json=json.dumps(
                        fields["input_summary"], ensure_ascii=False
                    ),
                    output_summary_json=(
                        json.dumps(fields["output_summary"], ensure_ascii=False)
                        if fields.get("output_summary") is not None
                        else None
                    ),
                    duration_ms=fields["duration_ms"],
                    error_code=fields.get("error_code"),
                    now=now,
                )
            event_type = (
                "tool.started" if kind == "tool.started" else "tool.completed"
                if kind == "tool.completed"
                else "tool.failed"
            )
            await db.insert_analysis_event(
                conn,
                analysis_id,
                event_type,
                {
                    "analysis_id": analysis_id,
                    "agent_role": role,
                    "llm_round": fields["llm_round"],
                    "tool_name": fields["tool_name"],
                    "status": fields.get("status", "started"),
                },
                now,
            )

    async def round_sink(kind: str, fields: dict[str, Any]) -> None:
        await emit(
            kind,
            {
                "analysis_id": analysis_id,
                "role": role,
                **fields,
            },
        )

    async with (await get_connection()) as conn:
        await db.upsert_expert_run(
            conn,
            run_id,
            analysis_id,
            role,
            "running",
            started_at=started_at,
        )
    await emit(
        "expert.started",
        {"analysis_id": analysis_id, "role": role, "started_at": started_at},
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPTS[role]},
        {
            "role": "user",
            "content": security.wrap_document_as_data(document, document_nonce),
        },
    ]

    async def run() -> AgentLoopResult:
        return await run_agent_loop(
            messages,
            session,
            settings,
            max_rounds=max(1, settings.agent_max_rounds),
            one_tool_per_round=True,
            tool_event_sink=sink,
            agent_role=role,
            get_connection=get_connection,
            max_output_tokens=settings.expert_max_output_tokens,
            response_event_sink=response_sink,
            stream_final_envelope=True,
            allowed_tools=allowed_tools,
            round_event_sink=round_sink,
        )

    async def fail_run(status: str, error_code: str, *, timed_out: bool = False) -> ExpertRunResult:
        """Termine BRUYAMMENT un expert : le run quitte l'état `running` en
        base, l'échec est nommé par son vrai code, et les événements sont
        émis. Sans cela, une exception qui s'échappe laissait le run à
        `running` pour toujours (spinner infini côté interface) et l'analyse
        requalifiait la panne en `insufficient_expertise` — l'application
        annonçait « pas assez d'experts » alors que le fournisseur était
        injoignable. Une application qui échoue bruyamment vaut infiniment
        mieux qu'une application qui invente une explication."""
        if timed_out:
            await emit("expert.timeout", {"analysis_id": analysis_id, "role": role})
        else:
            await emit(
                "expert.failed",
                {"analysis_id": analysis_id, "role": role, "error_code": error_code},
            )
        await response_sink(
            "agent.response.failed", {"role": role, "error_code": error_code}
        )
        async with (await get_connection()) as conn:
            await db.upsert_expert_run(
                conn,
                run_id,
                analysis_id,
                role,
                status,
                error_code=error_code,
                started_at=started_at,
                completed_at=db.utc_now_iso(),
            )
        return ExpertRunResult(
            role=role,
            run_id=run_id,
            output=None,
            error_code=error_code,
            usage=_empty_usage(),
            executed_tools=[],
            trace=[],
            timed_out=timed_out,
        )

    try:
        loop_result = await asyncio.wait_for(
            run(), timeout=settings.expert_timeout_seconds
        )
    except asyncio.TimeoutError:
        return await fail_run("timeout", "expert_timeout", timed_out=True)
    except ProviderError:
        # Réseau coupé, clé invalide, 5xx MiniMax : la cause est CONNUE et
        # doit être dite telle quelle, jamais traduite en autre chose.
        return await fail_run("error", "provider_unavailable")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - jamais avalé : tracé puis nommé
        logger.exception(
            "expert %s failed with an unexpected error (analysis %s)", role, analysis_id
        )
        return await fail_run("error", "internal_error")

    output: AgentOutput | None = None
    error_code: str | None = None

    # Le comptable ne doit une preuve réelle QUE pour les outils réellement
    # disponibles dans la configuration figée de cette analyse : un outil
    # désactivé ne peut pas être exécuté, donc ne peut pas être exigé — le
    # comptable signale alors l'indisponibilité sans inventer de chiffre.
    required_comptable_tools = (
        _COMPTABLE_REQUIRED_TOOLS
        if allowed_tools is None
        else (_COMPTABLE_REQUIRED_TOOLS & allowed_tools)
    )

    raw = (loop_result.answer or "").strip()
    if not raw:
        error_code = loop_result.stop_reason or "empty_output"
    else:
        data = extract_structured_json(raw)
        hint = None
        if data is None:
            hint = "la sortie n'est pas un objet JSON valide"
        elif not _AGENT_OUTPUT_FIELDS.issubset(data.keys()):
            missing = sorted(_AGENT_OUTPUT_FIELDS - set(data.keys()))
            hint = "champs manquants : " + ", ".join(missing)
        elif _first_findings_without_evidence(data) is not None:
            hint = _first_findings_without_evidence(data)
        elif role == "comptable" and any(
            tool not in loop_result.executed_tools for tool in required_comptable_tools
        ):
            hint = (
                "une conclusion chiffrée ne peut pas être validée sans avoir "
                "observé les métriques réelles puis estimé le coût"
            )
        if hint is None:
            try:
                output = validate_agent_output(role, data)
            except ValidationError as exc:
                hint = "erreurs de validation : " + str(exc.errors()[:3])
        if hint is not None:
            async with build_client(settings) as client:
                repaired = await _repair_structured_output(
                    client,
                    messages,
                    settings,
                    hint,
                    max_output_tokens=settings.expert_max_output_tokens,
                )
            if repaired:
                repaired_data = extract_structured_json(repaired)
                if repaired_data is not None:
                    try:
                        output = validate_agent_output(role, repaired_data)
                    except ValidationError:
                        output = None
                    else:
                        if role == "comptable":
                            repaired_executed = loop_result.executed_tools
                            if any(
                                tool not in repaired_executed
                                for tool in required_comptable_tools
                            ):
                                output = None
            if output is None:
                error_code = "structured_output_error"

    if output is not None:
        async with (await get_connection()) as conn:
            await db.upsert_expert_run(
                conn,
                run_id,
                analysis_id,
                role,
                "completed",
                output_json=output.model_dump_json(),
                started_at=started_at,
                completed_at=db.utc_now_iso(),
            )
        await response_sink("agent.response.completed", {"role": role})
        await emit(
            "expert.completed",
            {"analysis_id": analysis_id, "role": role},
        )
    else:
        error_code = error_code or "structured_output_error"
        async with (await get_connection()) as conn:
            await db.upsert_expert_run(
                conn,
                run_id,
                analysis_id,
                role,
                "error",
                error_code=error_code,
                started_at=started_at,
                completed_at=db.utc_now_iso(),
            )
        await response_sink(
            "agent.response.failed", {"role": role, "error_code": error_code}
        )
        await emit(
            "expert.failed",
            {"analysis_id": analysis_id, "role": role, "error_code": error_code},
        )

    return ExpertRunResult(
        role=role,
        run_id=run_id,
        output=output,
        error_code=error_code,
        usage=loop_result.usage,
        executed_tools=loop_result.executed_tools,
        trace=loop_result.trace,
    )


async def run_arbiter(
    *,
    analysis_id: str,
    document: str,
    session: AgentSession,
    valid_outputs: list[AgentOutput],
    unavailable_agents: list[ExpertRole],
    settings: Settings,
    get_connection: Callable[[], Awaitable[Any]],
    allowed_tools: frozenset[str] | None = None,
    document_nonce: str = "",
) -> tuple[ArbiterVerdict | None, ExecutionUsage]:
    """Arbitre : reçoit document + sorties validées, rend un verdict JSON validé."""
    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        async with (await get_connection()) as conn:
            await db.insert_analysis_event(
                conn, analysis_id, event_type, payload, db.utc_now_iso()
            )

    async def round_sink(kind: str, fields: dict[str, Any]) -> None:
        await emit(kind, {"analysis_id": analysis_id, "role": "arbitre", **fields})

    response_sequences: dict[str, int] = {}

    async def response_sink(kind: str, fields: dict[str, Any]) -> None:
        payload: dict[str, Any] = {"analysis_id": analysis_id, "role": "arbitre"}
        now = db.utc_now_iso()
        if kind == "agent.response.delta":
            response_sequences["arbitre"] = response_sequences.get("arbitre", 0) + 1
            payload["sequence"] = response_sequences["arbitre"]
            payload["delta"] = fields["delta"]
            AgentResponseDelta(**payload)
        elif kind == "agent.response.started":
            AgentResponseStarted(**payload)
        elif kind == "agent.response.completed":
            AgentResponseCompleted(**payload)
        elif kind == "agent.response.failed":
            payload["error_code"] = fields.get("error_code", "protocol_error")
            AgentResponseFailed(**payload)
        async with (await get_connection()) as conn:
            await db.insert_analysis_event(conn, analysis_id, kind, payload, now)

    async def sink(kind: str, fields: dict[str, Any]) -> None:
        now = db.utc_now_iso()
        async with (await get_connection()) as conn:
            if kind in ("tool.completed", "tool.failed"):
                await db.insert_tool_event(
                    conn,
                    analysis_id=analysis_id,
                    agent_role="arbitre",
                    llm_round=fields["llm_round"],
                    sequence=fields["sequence"],
                    tool_name=fields["tool_name"],
                    status=fields["status"],
                    input_summary_json=json.dumps(
                        fields["input_summary"], ensure_ascii=False
                    ),
                    output_summary_json=(
                        json.dumps(fields["output_summary"], ensure_ascii=False)
                        if fields.get("output_summary") is not None
                        else None
                    ),
                    duration_ms=fields["duration_ms"],
                    error_code=fields.get("error_code"),
                    now=now,
                )
            event_type = (
                "tool.started" if kind == "tool.started" else "tool.completed"
                if kind == "tool.completed"
                else "tool.failed"
            )
            await db.insert_analysis_event(
                conn,
                analysis_id,
                event_type,
                {
                    "analysis_id": analysis_id,
                    "agent_role": "arbitre",
                    "llm_round": fields["llm_round"],
                    "tool_name": fields["tool_name"],
                    "status": fields.get("status", "started"),
                },
                now,
            )

    await emit(
        "arbiter.started",
        {
            "analysis_id": analysis_id,
            "expert_outputs": [output.role for output in valid_outputs],
        },
    )

    expert_payload = [
        output.model_dump(mode="json") for output in valid_outputs
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ARBITER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": security.wrap_document_as_data(document, document_nonce),
        },
        {
            "role": "user",
            "content": (
                "Trois experts étaient attendus : avocat, procureur, comptable. "
                f"Sorties reçues : {[o.role for o in valid_outputs]}. "
                f"Experts absents : {unavailable_agents}. "
                "Renvoie ces experts absents dans unavailable_agents de ton verdict. "
                "Sorties validées des experts :\n"
                + json.dumps(expert_payload, ensure_ascii=False)
            ),
        },
    ]

    async def run() -> AgentLoopResult:
        return await run_agent_loop(
            messages,
            session,
            settings,
            max_rounds=max(1, settings.agent_max_rounds),
            one_tool_per_round=True,
            tool_event_sink=sink,
            agent_role="arbitre",
            get_connection=get_connection,
            max_output_tokens=settings.expert_max_output_tokens,
            response_event_sink=response_sink,
            stream_final_envelope=True,
            allowed_tools=allowed_tools,
            round_event_sink=round_sink,
        )

    async def fail_arbiter(error_code: str) -> tuple[None, ExecutionUsage]:
        await emit(
            "arbiter.failed",
            {"analysis_id": analysis_id, "error_code": error_code},
        )
        await response_sink(
            "agent.response.failed", {"role": "arbitre", "error_code": error_code}
        )
        return None, _empty_usage()

    try:
        loop_result = await asyncio.wait_for(
            run(), timeout=settings.arbiter_timeout_seconds
        )
    except asyncio.TimeoutError:
        return await fail_arbiter("arbiter_timeout")
    except ProviderError:
        return await fail_arbiter("provider_unavailable")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - jamais avalé : tracé puis nommé
        logger.exception("arbiter failed unexpectedly (analysis %s)", analysis_id)
        return await fail_arbiter("internal_error")

    raw = (loop_result.answer or "").strip()
    verdict: ArbiterVerdict | None = None
    if raw:
        data = extract_structured_json(raw)
        hint = None
        if data is None:
            hint = "ta sortie n'est pas un objet JSON valide"
        elif not _ARBITER_FIELDS.issubset(data.keys()):
            missing = sorted(_ARBITER_FIELDS - set(data.keys()))
            hint = "champs manquants : " + ", ".join(missing)
        if hint is None:
            try:
                verdict = validate_arbiter_verdict(data)
            except ValidationError as exc:
                hint = "erreurs de validation : " + str(exc.errors()[:3])
        if hint is not None:
            async with build_client(settings) as client:
                repaired = await _repair_structured_output(
                    client,
                    messages,
                    settings,
                    hint,
                    max_output_tokens=settings.expert_max_output_tokens,
                )
            if repaired:
                repaired_data = extract_structured_json(repaired)
                if repaired_data is not None:
                    try:
                        verdict = validate_arbiter_verdict(repaired_data)
                    except ValidationError:
                        verdict = None

    if verdict is not None:
        await response_sink("agent.response.completed", {"role": "arbitre"})
        await emit(
            "arbiter.completed",
            {"analysis_id": analysis_id, "decision": verdict.decision},
        )
    else:
        await response_sink(
            "agent.response.failed",
            {"role": "arbitre", "error_code": "structured_output_error"},
        )
        await emit(
            "arbiter.failed",
            {"analysis_id": analysis_id, "error_code": "structured_output_error"},
        )
    return verdict, loop_result.usage


async def run_analysis(
    analysis_id: str,
    document: str,
    settings: Settings,
    get_connection: Callable[[], Awaitable[Any]],
) -> AnalysisResult:
    """Orchestration complète d'une analyse (statuts, événements, persistance).

    Le passage `queued` -> `running` et l'événement `analysis.started` sont la
    responsabilité de l'appelant (route `/start`, avant de lancer cette tâche
    de fond) : cette fonction lit uniquement la configuration des outils déjà
    figée par `snapshot_analysis_tool_states` à la création de l'analyse.
    """
    session = AgentSession(document=document)
    # Nonce régénéré à chaque analyse : le document ne peut pas deviner la
    # borne fermante de sa propre zone de données pour reprendre la main.
    document_nonce = security.new_document_nonce()

    async with (await get_connection()) as conn:
        tool_rows = await db.list_analysis_tool_states(conn, analysis_id)
    # Repli : une analyse créée sans passer par `POST /api/analyses` (tests
    # unitaires appelant `run_analysis`/`run_expert` directement) n'a jamais
    # de configuration figée. `allowed_tools=None` retombe alors sur le
    # registre global `tool_states`, comme avant R1.
    allowed_tools = (
        frozenset(row["tool_name"] for row in tool_rows if row["enabled"])
        if tool_rows
        else None
    )

    results: list[ExpertRunResult] = []

    async def run_one(role: ExpertRole) -> ExpertRunResult:
        return await run_expert(
            role,
            analysis_id=analysis_id,
            document=document,
            session=session,
            settings=settings,
            get_connection=get_connection,
            allowed_tools=allowed_tools,
            document_nonce=document_nonce,
        )

    tasks = [run_one(role) for role in EXPERT_ROLES]
    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=settings.analysis_timeout_seconds,
        )
    except asyncio.TimeoutError:
        async with (await get_connection()) as conn:
            await db.set_analysis_status(
                conn,
                analysis_id,
                "failed",
                completed_at=db.utc_now_iso(),
                error_code="analysis_timeout",
            )
            await db.insert_analysis_event(
                conn,
                analysis_id,
                "analysis.failed",
                {"analysis_id": analysis_id, "error_code": "analysis_timeout"},
                db.utc_now_iso(),
            )
        return AnalysisResult(
            analysis_id=analysis_id,
            status="failed",
            error_code="analysis_timeout",
            experts=[],
            verdict=None,
            usage=_empty_usage(),
        )

    escaped_errors: list[str] = []
    for role, outcome in zip(EXPERT_ROLES, outcomes):
        if isinstance(outcome, BaseException):
            # Filet de dernier recours : `run_expert` nomme déjà ses échecs,
            # donc on ne devrait jamais passer ici. Si ça arrive, on le TRACE
            # et on le compte — jamais un `continue` muet qui ferait passer
            # une panne pour un manque d'experts.
            logger.exception(
                "expert %s escaped run_expert (analysis %s)",
                role,
                analysis_id,
                exc_info=outcome,
            )
            escaped_errors.append("internal_error")
            continue
        results.append(outcome)

    valid = [r.output for r in results if r.output is not None]
    valid_roles = {output.role for output in valid}
    missing_roles = [
        role for role in EXPERT_ROLES if role not in valid_roles
    ]
    verdict: ArbiterVerdict | None = None
    arbiter_usage = _empty_usage()

    if len(valid) >= 2:
        verdict, arbiter_usage = await run_arbiter(
            analysis_id=analysis_id,
            document=document,
            session=session,
            valid_outputs=valid,
            unavailable_agents=missing_roles,
            settings=settings,
            get_connection=get_connection,
            allowed_tools=allowed_tools,
            document_nonce=document_nonce,
        )
        if verdict is not None and missing_roles:
            # Informations structurelles connues du seul orchestrateur : imposées.
            verdict.unavailable_agents = missing_roles

    if verdict is not None:
        all_three = all(r.output is not None for r in results)
        status = "completed" if all_three else "degraded"
        error_code = None
    elif len(valid) >= 2:
        status = "failed"
        error_code = "arbiter_error"
    else:
        status = "failed"
        error_code = _dominant_failure_code(
            [r.error_code for r in results] + escaped_errors
        )

    merged = _merge_usage([r.usage for r in results] + [arbiter_usage])
    completed_at = db.utc_now_iso()

    async with (await get_connection()) as conn:
        event_type = {
            "completed": "analysis.completed",
            "degraded": "analysis.degraded",
            "failed": "analysis.failed",
        }[status]
        await db.finish_analysis(
            conn,
            analysis_id,
            status=status,
            error_code=error_code,
            completed_at=completed_at,
            usage_json=merged.model_dump_json(),
            verdict_json=verdict.model_dump_json() if verdict is not None else None,
            event_type=event_type,
            event_payload={
                "analysis_id": analysis_id,
                "status": status,
                "error_code": error_code,
            },
        )

    return AnalysisResult(
        analysis_id=analysis_id,
        status=status,
        error_code=error_code,
        experts=results,
        verdict=verdict,
        usage=merged,
    )