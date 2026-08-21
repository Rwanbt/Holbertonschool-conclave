"""Application FastAPI CONCLAVE — Palier 1 à 4.

Routes :
- GET  /api/health              -> {"status": "ok"}
- POST /api/p2/llm              -> tuyau seul MiniMax (jalon temporaire)
- POST /api/p3/agent            -> boucle agent P3 (jalon temporaire)
- POST /api/analyses            -> lance une analyse (201, SSE ensuite)
- GET  /api/analyses/{id}       -> snapshot persistant de l'analyse
- GET  /api/analyses/{id}/events-> flux SSE rejouable des événements
- GET  /api/tools               -> catalogue des outils + états persistés
- POST /api/tool-commands       -> grammaire /tools (enable|disable)

Les analyses tournent en tâches de fond conservées dans `app.state`
(`analysis_tasks`) : un rafraîchissement du navigateur n'annule jamais le
backend. Le document peut être transmis à MiniMax pour les rôles experts/
arbitre (SPEC) mais n'est jamais journalisé.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import agent, db, experts, llm, security, toolkit
from .config import Settings, get_settings
from .schemas import (
    AgentRequest,
    AgentResponse,
    AnalysisCreateRequest,
    AnalysisCreated,
    AnalysisSnapshot,
    ArbiterVerdict,
    AgentOutput,
    EventsHistoryResponse,
    ExecutionUsage,
    ExpertRunView,
    LLMRequest,
    LLMResponse,
    SecurityReport,
    StartAnalysisResponse,
    ToolCatalogResponse,
    ToolCommandRequest,
    ToolCommandResponse,
    ToolConfiguration,
)

app = FastAPI(
    title="CONCLAVE backend",
    description="Validation d'entrée + passerelle MiniMax M3.",
    version="0.1.0",
)

_boot_settings = get_settings()


def _parse_origins(value: str) -> list[str]:
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def _resolved_settings(app_obj: FastAPI) -> Settings:
    """Settings en tenant compte des dependency_overrides (tests inclus)."""
    if get_settings in app_obj.dependency_overrides:
        return app_obj.dependency_overrides[get_settings]()
    return get_settings()


@asynccontextmanager
async def lifespan(app_obj: FastAPI) -> AsyncIterator[None]:
    settings = _resolved_settings(app_obj)
    await db.initialize(settings.database_path, settings.disabled_tools)
    app_obj.state.analysis_tasks: dict[str, asyncio.Task] = {}
    try:
        yield
    finally:
        for task in app_obj.state.analysis_tasks.values():
            task.cancel()
        app_obj.state.analysis_tasks.clear()


app = FastAPI(
    title="CONCLAVE backend",
    description="Validation d'entrée + passerelle MiniMax M3.",
    version="0.1.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(_boot_settings.frontend_origin),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

#: Le document est plafonné à 12 000 caractères (Pydantic). Mais Pydantic ne
#: décide qu'APRÈS avoir lu et parsé tout le corps : un envoi de 40 Mo serait
#: intégralement chargé en mémoire avant d'être refusé en 422. On coupe donc
#: bien avant, y compris quand `Content-Length` est absent ou mensonger, avec
#: une marge confortable pour l'échappement JSON et l'UTF-8 multi-octets.
MAX_REQUEST_BYTES = 1_000_000


class RequestBodyLimitMiddleware:
    """ASGI pur : borne le flux reçu, même sans ``Content-Length``.

    Les corps acceptés (au plus 1 Mo) sont rejoués à FastAPI. Dès que le flux
    dépasse la limite, la lecture s'arrête et une 413 explicite est renvoyée.
    """

    def __init__(self, application: Any, max_bytes: int) -> None:
        self.application = application
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self.application(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                response = JSONResponse(
                    status_code=400,
                    content={"detail": "En-tête Content-Length invalide."},
                )
                await response(scope, receive, send)
                return
            if declared > self.max_bytes:
                await self._reject(scope, receive, send, declared)
                return

        messages: list[dict[str, Any]] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await self._reject(scope, receive, send, received)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        await self.application(scope, replay_receive, send)

    async def _reject(self, scope, receive, send, size: int) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": (
                    f"Corps de requête trop volumineux ({size} octets). "
                    f"La limite est de {self.max_bytes} octets ; un document "
                    "ne peut de toute façon pas dépasser 12 000 caractères."
                )
            },
        )
        await response(scope, receive, send)


app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)


def _connection_factory(settings: Settings):
    async def open_connection():
        return db.open_connection(settings.database_path)

    return open_connection


async def get_db(settings: Settings = Depends(get_settings)):
    async with db.open_connection(settings.database_path) as conn:
        yield conn


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/p2/llm", response_model=LLMResponse)
async def p2_llm(
    request: LLMRequest,
    settings: Settings = Depends(get_settings),
) -> LLMResponse | JSONResponse:
    if not settings.minimax_api_key:
        return JSONResponse(
            status_code=500,
            content={"detail": "MINIMAX_API_KEY is not configured on the server"},
        )

    try:
        answer = await llm.generate_answer(request.message, settings)
    except llm.ProviderError:
        return JSONResponse(
            status_code=502,
            content={
                "detail": "MiniMax provider unavailable or returned an unusable answer"
            },
        )

    return LLMResponse(answer=answer, model=settings.minimax_model)


@app.post("/api/p3/agent", response_model=AgentResponse)
async def p3_agent(
    request: AgentRequest,
    settings: Settings = Depends(get_settings),
) -> AgentResponse | JSONResponse:
    if not settings.minimax_api_key:
        return JSONResponse(
            status_code=500,
            content={"detail": "MINIMAX_API_KEY is not configured on the server"},
        )

    try:
        return await agent.run_agent(request.instruction, request.document, settings)
    except llm.ProviderError:
        return JSONResponse(
            status_code=502,
            content={
                "detail": "MiniMax provider unavailable or returned an unusable answer"
            },
        )


# ---------------------------------------------------------------------------
# Palier 4 — analyses, snapshot, SSE, outils
# ---------------------------------------------------------------------------


def _tool_configuration_from_rows(rows: list[Any]) -> ToolConfiguration:
    return ToolConfiguration(
        enabled_tools=[row["tool_name"] for row in rows if row["enabled"]],
        disabled_tools=[row["tool_name"] for row in rows if not row["enabled"]],
    )


@app.post(
    "/api/analyses",
    response_model=AnalysisCreated,
    status_code=201,
)
async def create_analysis(
    request: AnalysisCreateRequest,
    settings: Settings = Depends(get_settings),
) -> AnalysisCreated | JSONResponse:
    """Crée l'analyse en `queued` et fige sa configuration d'outils dans la
    même transaction : AUCUNE tâche de fond n'est lancée ici. Le job ne
    démarre qu'après `POST /api/analyses/{id}/start`, appelé par le
    navigateur une fois le flux SSE ouvert (`EventSource.onopen`), afin que
    l'utilisateur ne puisse jamais rater le tout début de l'exécution."""
    if not settings.minimax_api_key:
        return JSONResponse(
            status_code=500,
            content={"detail": "MINIMAX_API_KEY is not configured on the server"},
        )

    analysis_id = uuid.uuid4().hex
    now = db.utc_now_iso()

    # Détection purement informative : elle ne bloque JAMAIS l'analyse (voir
    # SECURITY.md — les défenses réelles sont structurelles). Elle sert à dire
    # à l'utilisateur ce que le serveur a vu dans son document.
    signals = security.detect_injection_signals(request.document)

    try:
        async with db.open_connection(settings.database_path) as conn:
            rows = await db.create_queued_analysis(
                conn,
                analysis_id=analysis_id,
                document=request.document,
                now=now,
                signals=signals,
                max_active=settings.max_concurrent_analyses,
                queued_ttl_seconds=settings.queued_analysis_ttl_seconds,
            )
    except db.ActiveAnalysisLimitReached as exc:
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    f"{exc.active} analyses sont déjà en cours (limite : "
                    f"{settings.max_concurrent_analyses}). Attendez qu'une "
                    "analyse se termine avant d'en lancer une nouvelle."
                )
            },
        )
    tool_configuration = _tool_configuration_from_rows(rows)

    return AnalysisCreated(
        analysis_id=analysis_id,
        status="queued",
        created_at=now,
        tool_configuration=tool_configuration,
        security=SecurityReport(
            prompt_injection_suspected=bool(signals), signals=signals
        ),
    )


@app.post(
    "/api/analyses/{analysis_id}/start",
    response_model=StartAnalysisResponse,
)
async def start_analysis(
    analysis_id: str,
    settings: Settings = Depends(get_settings),
) -> StartAnalysisResponse | JSONResponse:
    """Démarrage idempotent : compare-and-set SQL `queued` -> `running`.

    Seule la requête qui a réellement effectué la transition lance la tâche
    de fond ; les suivantes (double-clic, onglet dupliqué, F5 pendant la
    course) constatent `already_started=True` sans rien recréer. Un
    rechargement de page ne relance donc jamais un job en cours."""
    async with db.open_connection(settings.database_path) as conn:
        row = await db.get_analysis(conn, analysis_id)
        if row is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        if row["status"] != "queued":
            return StartAnalysisResponse(
                analysis_id=analysis_id, status=row["status"], already_started=True
            )
        started_at = db.utc_now_iso()
        transitioned = await db.start_analysis(conn, analysis_id, started_at)
        if not transitioned:
            current = await db.get_analysis(conn, analysis_id)
            return StartAnalysisResponse(
                analysis_id=analysis_id,
                status=current["status"] if current else "running",
                already_started=True,
            )
        document = row["document"]

    task = asyncio.create_task(
        experts.run_analysis(
            analysis_id,
            document,
            settings,
            _connection_factory(settings),
        )
    )
    app.state.analysis_tasks[analysis_id] = task
    task.add_done_callback(
        lambda _t, aid=analysis_id: app.state.analysis_tasks.pop(aid, None)
    )

    return JSONResponse(
        status_code=202,
        content=StartAnalysisResponse(
            analysis_id=analysis_id, status="running", already_started=False
        ).model_dump(mode="json"),
    )


def _parse_expert_output(raw_json: str | None):
    if not raw_json:
        return None
    try:
        return AgentOutput.model_validate_json(raw_json)
    except Exception:  # noqa: BLE001 - sortie corrompue => None
        return None


def _parse_verdict(raw_json: str | None) -> ArbiterVerdict | None:
    if not raw_json:
        return None
    try:
        return ArbiterVerdict.model_validate_json(raw_json)
    except Exception:  # noqa: BLE001
        return None


def _parse_usage(raw_json: str | None) -> ExecutionUsage:
    if not raw_json:
        return ExecutionUsage(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            estimated_cost_usd=None,
            total_latency_ms=0,
            llm_rounds=0,
        )
    try:
        return ExecutionUsage.model_validate_json(raw_json)
    except Exception:  # noqa: BLE001
        return ExecutionUsage(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            estimated_cost_usd=None,
            total_latency_ms=0,
            llm_rounds=0,
        )


@app.get("/api/analyses/{analysis_id}", response_model=AnalysisSnapshot)
async def get_analysis_snapshot(
    analysis_id: str,
    conn: Any = Depends(get_db),
) -> AnalysisSnapshot:
    row = await db.get_analysis(conn, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")

    runs = await db.list_expert_runs(conn, analysis_id)
    views: dict[str, ExpertRunView] = {}
    for role in experts.EXPERT_ROLES:
        views[role] = ExpertRunView(role=role, status="pending", output=None, error_code=None)
    for run in runs:
        views[run["role"]] = ExpertRunView(
            role=run["role"],
            status=run["status"],
            output=_parse_expert_output(run["output_json"]),
            error_code=run["error_code"],
        )

    tool_rows = await db.list_analysis_tool_states(conn, analysis_id)
    security_signals = await db.get_analysis_security(conn, analysis_id)

    return AnalysisSnapshot(
        analysis_id=row["id"],
        document=row["document"],
        status=row["status"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        error_code=row["error_code"],
        avocat=views["avocat"],
        procureur=views["procureur"],
        comptable=views["comptable"],
        verdict=_parse_verdict(row["verdict_json"]),
        usage=_parse_usage(row["usage_json"]),
        guardrails={
            "expert_timeout_seconds": _boot_settings.expert_timeout_seconds,
            "arbiter_timeout_seconds": _boot_settings.arbiter_timeout_seconds,
            "analysis_timeout_seconds": _boot_settings.analysis_timeout_seconds,
            "agent_max_rounds": _boot_settings.agent_max_rounds,
            "document_max_length": 12000,
            "statuses": {
                "analysis": [
                    "queued",
                    "running",
                    "completed",
                    "degraded",
                    "failed",
                    "interrupted",
                ],
                "expert": ["pending", "running", "completed", "error", "timeout"],
            },
        },
        tool_configuration=_tool_configuration_from_rows(tool_rows),
        security=SecurityReport(
            prompt_injection_suspected=bool(security_signals),
            signals=security_signals,
        ),
    )


@app.get(
    "/api/analyses/{analysis_id}/events/history",
    response_model=EventsHistoryResponse,
)
async def get_analysis_events_history(
    analysis_id: str,
    after: int = 0,
    limit: int = 500,
    conn: Any = Depends(get_db),
) -> EventsHistoryResponse:
    """Historique JSON paginé pour hydrater un F5 sans animation artificielle
    (`readStoredLastEventId` reste une optimisation de reprise, jamais la
    seule source : cet historique serveur est autoritaire)."""
    row = await db.get_analysis(conn, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")

    bounded_limit = max(1, min(limit, 500))
    rows = await db.list_events_after(conn, analysis_id, max(0, after))
    truncated = rows[:bounded_limit]
    events = [
        {
            "id": r["id"],
            "event_type": r["event_type"],
            "payload": json.loads(r["payload_json"]),
            "created_at": r["created_at"],
        }
        for r in truncated
    ]
    return EventsHistoryResponse(
        events=events,
        last_event_id=truncated[-1]["id"] if truncated else max(0, after),
        has_more=len(rows) > len(truncated),
    )


def _format_sse(event_id: int, event_type: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


@app.get("/api/analyses/{analysis_id}/events")
async def stream_analysis_events(
    analysis_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    async with db.open_connection(settings.database_path) as conn:
        row = await db.get_analysis(conn, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")

    after_id = 0
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is not None:
        try:
            after_id = max(after_id, int(last_event_id))
        except ValueError:
            after_id = 0
    if "after" in request.query_params:
        try:
            after_id = max(after_id, int(request.query_params["after"]))
        except ValueError:
            after_id = 0

    async def event_source() -> AsyncIterator[str]:
        sent = after_id
        interval = settings.sse_poll_interval_ms / 1000.0
        keepalive = settings.sse_keepalive_seconds
        elapsed = 0.0
        while True:
            async with db.open_connection(settings.database_path) as conn:
                current = await db.get_analysis(conn, analysis_id)
                events = await db.list_events_after(conn, analysis_id, sent)
            if current is None:
                return
            for event in events:
                yield _format_sse(event["id"], event["event_type"], json.loads(event["payload_json"]))
                sent = event["id"]
                if event["event_type"] in db.TERMINAL_EVENTS:
                    return
            if current["status"] in db.TERMINAL_ANALYSIS_STATUSES:
                return
            await asyncio.sleep(interval)
            elapsed += interval
            if keepalive and elapsed >= keepalive:
                yield ": keep-alive\n\n"
                elapsed = 0.0

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/tools", response_model=ToolCatalogResponse)
async def get_tools_catalog(conn: Any = Depends(get_db)) -> ToolCatalogResponse:
    states = await db.list_tool_states(conn)
    return ToolCatalogResponse(
        tools=[toolkit.tool_state_from_row(row) for row in states]
    )


@app.post("/api/tool-commands", response_model=ToolCommandResponse)
async def apply_tool_command(
    request: ToolCommandRequest,
    conn: Any = Depends(get_db),
) -> ToolCommandResponse:
    try:
        action, tool_name = toolkit.parse_tool_command(request.command)
    except toolkit.ToolCommandSyntaxError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid /tools command: {exc}",
        ) from exc

    if action == "list":
        states = await db.list_tool_states(conn)
        tools = [toolkit.tool_state_from_row(row) for row in states]
        return ToolCommandResponse(
            action="list",
            message="Catalogue des outils (états lus depuis tool_states).",
            tool_name=None,
            enabled=None,
            tools=tools,
        )

    enabled = action == "enable"
    await db.set_tool_state(conn, tool_name, enabled)
    states = await db.list_tool_states(conn)
    tools = [toolkit.tool_state_from_row(row) for row in states]
    return ToolCommandResponse(
        action=action,
        message=(
            f"Outil {tool_name} {'activé' if enabled else 'désactivé'} "
            "(état persistant)."
        ),
        tool_name=tool_name,
        enabled=enabled,
        tools=tools,
    )
