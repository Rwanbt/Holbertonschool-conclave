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

from . import agent, db, experts, llm, toolkit
from .config import Settings, get_settings
from .schemas import (
    AgentRequest,
    AgentResponse,
    AnalysisCreateRequest,
    AnalysisCreated,
    AnalysisSnapshot,
    ArbiterVerdict,
    AgentOutput,
    ExecutionUsage,
    ExpertRunView,
    LLMRequest,
    LLMResponse,
    ToolCatalogResponse,
    ToolCommandRequest,
    ToolCommandResponse,
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


@app.post(
    "/api/analyses",
    response_model=AnalysisCreated,
    status_code=201,
)
async def create_analysis(
    request: AnalysisCreateRequest,
    settings: Settings = Depends(get_settings),
) -> AnalysisCreated | JSONResponse:
    if not settings.minimax_api_key:
        return JSONResponse(
            status_code=500,
            content={"detail": "MINIMAX_API_KEY is not configured on the server"},
        )

    analysis_id = uuid.uuid4().hex
    now = db.utc_now_iso()

    async with db.open_connection(settings.database_path) as conn:
        await db.create_analysis(conn, analysis_id, request.document, now)
        await db.insert_analysis_event(
            conn,
            analysis_id,
            "analysis.created",
            {"analysis_id": analysis_id, "created_at": now},
            now,
        )

    task = asyncio.create_task(
        experts.run_analysis(
            analysis_id,
            request.document,
            settings,
            _connection_factory(settings),
        )
    )
    app.state.analysis_tasks[analysis_id] = task
    task.add_done_callback(
        lambda _t, aid=analysis_id: app.state.analysis_tasks.pop(aid, None)
    )

    return AnalysisCreated(analysis_id=analysis_id, status="running", created_at=now)


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
                "analysis": ["running", "completed", "degraded", "failed", "interrupted"],
                "expert": ["pending", "running", "completed", "error", "timeout"],
            },
        },
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
            "Cache-Control": "no-cache",
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
