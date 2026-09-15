"""Application FastAPI CONCLAVE — Palier 1 à 4 + production BYOK multi-provider.

Routes :
- GET  /api/health                    -> {"status": "ok"}
- GET  /api/providers                 -> registre public des providers/modèles
- POST /api/providers/test-connection -> test de clé BYOK (sans jeter de tokens)
- POST /api/p2/llm                    -> tuyau seul (jalon temporaire)
- POST /api/p3/agent                  -> boucle agent P3 (jalon temporaire)
- POST /api/analyses                  -> crée une analyse (201, sélection figée)
- POST /api/analyses/{id}/start       -> démarre avec le credential runtime BYOK
- GET  /api/analyses/{id}             -> snapshot persistant de l'analyse
- GET  /api/analyses/{id}/events      -> flux SSE rejouable des événements
- GET  /api/tools                     -> catalogue des outils (defaults)
- POST /api/tool-commands             -> grammaire /tools (enable|disable local)

Sécurité :
- chaque analyse est isolée par session anonyme signée (cookie HttpOnly) :
  les routes de lecture/écriture vérifient le propriétaire (404 sinon) ;
- le credential provider BYOK vit uniquement en mémoire, transité au `/start`,
  JAMAIS persisté, loggué, diffusé ou renvoyé ;
- la configuration des outils et la sélection provider sont figées à la
  création de l'analyse et ne changent jamais ensuite ;
- les analyses tournent en tâches de fond conservées dans `app.state`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import agent, db, experts, llm, redact, security, sessions, toolkit
from .config import Settings, get_settings
from .providers import (
    ProviderError,
    build_provider,
    create_adapter,
    list_provider_specs,
)
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
    ProviderCatalogResponse,
    ProviderInfo,
    ProviderModelInfo,
    StartAnalysisRequest,
    StartAnalysisResponse,
    SecurityReport,
    TestConnectionRequest,
    TestConnectionResponse,
    ToolCatalogResponse,
    ToolCommandRequest,
    ToolCommandResponse,
    ToolConfiguration,
)

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
    description="Validation d'entrée + orchestrateur multi-provider BYOK.",
    version="1.1.0",
    lifespan=lifespan,
)

_boot_settings = get_settings()

# CORS production : origines explicites uniquement (jamais `*` avec
# credentials). Le cookie de session exige `allow_credentials=True` et
# l'en-tête Authorization/Content-Type autorisé.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(_boot_settings.frontend_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Session-Token"],
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


def _session_token_from_request(request: Request, settings: Settings) -> str | None:
    """Token de session : en-tête `X-Session-Token` d'abord (transport
    cross-site, indépendant du SameSite du cookie), puis cookie signé."""
    header_token = request.headers.get("x-session-token")
    if header_token and header_token.strip():
        return header_token.strip()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    return sessions.unsign_session(raw, settings)


def _owns_analysis(row: Any, session_token: str | None) -> bool:
    """L'utilisateur courant est-il propriétaire de cette analyse ?"""
    if session_token is None:
        return False
    return row["owner_session"] == session_token


def _validate_selection(
    settings: Settings, provider_id: str, model_id: str | None
) -> tuple[str, str]:
    """Valide le provider/modèle contre le registre ; renvoie (provider, model)."""
    spec = next(
        (item for item in list_provider_specs() if item["provider_id"] == provider_id),
        None,
    )
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail=f"Fournisseur inconnu : {provider_id}.",
        )
    if model_id is None:
        model_id = spec["models"][0]["model_id"]
    if model_id not in {model["model_id"] for model in spec["models"]}:
        raise HTTPException(
            status_code=422,
            detail=f"Modèle {model_id} non autorisé pour {provider_id}.",
        )
    return provider_id, model_id


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/session")
async def create_session(
    response: Response,
    request_http: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Établit (ou confirme) la session anonyme : le cookie est posé (mode
    même-site) et le token est RENVOYÉ pour être transporté en en-tête
    `X-Session-Token` (mode cross-site, Netlify ↔ backend)."""
    existing = _session_token_from_request(request_http, settings)
    token = existing if existing is not None else sessions.new_session_token()
    response.set_cookie(
        settings.session_cookie_name,
        sessions.sign_session(token, settings),
        **sessions.cookie_attributes(settings),
    )
    return {"status": "ok", "session_token": token}


@app.get("/api/providers", response_model=ProviderCatalogResponse)
async def list_providers() -> ProviderCatalogResponse:
    specs = list_provider_specs()
    providers = [
        ProviderInfo(
            provider_id=spec["provider_id"],
            label=spec["label"],
            auth_modes=spec["auth_modes"],
            supports_tools=spec["supports_tools"],
            supports_streaming=spec["supports_streaming"],
            supports_structured_output=spec["supports_structured_output"],
            supports_reasoning=spec["supports_reasoning"],
            models=[
                ProviderModelInfo(**model)
                for model in spec["models"]
            ],
        )
        for spec in specs
    ]
    return ProviderCatalogResponse(providers=providers)


@app.post(
    "/api/providers/test-connection",
    response_model=TestConnectionResponse,
)
async def test_provider_connection(
    request: TestConnectionRequest,
) -> TestConnectionResponse:
    redact.register_secret(request.api_key)
    adapter = create_adapter(request.provider_id, request.model_id, request.api_key)
    try:
        message = await adapter.test_connection()
    except ProviderError as exc:
        return TestConnectionResponse(
            provider_id=request.provider_id,
            model_id=request.model_id,
            ok=False,
            message=redact.redact_text(str(exc)) or str(exc),
            needs_inference=exc.code == "provider_verification_unavailable",
        )
    finally:
        await adapter.close()
    return TestConnectionResponse(
        provider_id=request.provider_id,
        model_id=request.model_id,
        ok=True,
        message=message,
    )


@app.post("/api/p2/llm", response_model=LLMResponse)
async def p2_llm(
    request: LLMRequest,
    settings: Settings = Depends(get_settings),
) -> LLMResponse | JSONResponse:
    if not _server_credential_available(settings, "minimax"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Aucune clé API configurée côté serveur pour ce jalon."},
        )

    try:
        answer = await llm.generate_answer(request.message, settings)
    except ProviderError:
        return JSONResponse(
            status_code=502,
            content={"detail": "Provider unavailable or returned an unusable answer"},
        )

    return LLMResponse(answer=answer, model=settings.minimax_model)


@app.post("/api/p3/agent", response_model=AgentResponse)
async def p3_agent(
    request: AgentRequest,
    settings: Settings = Depends(get_settings),
) -> AgentResponse | JSONResponse:
    if not _server_credential_available(settings, "minimax"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Aucune clé API configurée côté serveur pour ce jalon."},
        )

    try:
        return await agent.run_agent(request.instruction, request.document, settings)
    except ProviderError:
        return JSONResponse(
            status_code=502,
            content={"detail": "Provider unavailable or returned an unusable answer"},
        )


def _server_credential_available(settings: Settings, provider_id: str) -> bool:
    if not settings.allow_server_provider_credentials:
        return False
    keys = {
        "minimax": settings.minimax_api_key,
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
    }
    return bool(keys.get(provider_id))


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
    response: Response,
    request_http: Request,
    settings: Settings = Depends(get_settings),
) -> AnalysisCreated | JSONResponse:
    """Crée l'analyse en `queued` et fige la sélection provider + outils dans
    la même transaction : AUCUNE tâche de fond n'est lancée ici. Le job ne
    démarre qu'après `POST /api/analyses/{id}/start`, appelé par le navigateur
    une fois le flux SSE ouvert. Le credential BYOK n'est jamais présent ici."""
    provider_id, model_id = _validate_selection(
        settings, request.provider_id, request.model_id
    )

    if request.enabled_tools is not None:
        unknown = set(request.enabled_tools) - set(toolkit.ALLOWED_TOOL_NAMES)
        if unknown:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": (
                        "Outils inconnus dans enabled_tools : "
                        + ", ".join(sorted(unknown))
                    )
                },
            )

    session_token = _session_token_from_request(request_http, settings)
    if session_token is None:
        session_token = sessions.new_session_token()
        response.set_cookie(
            settings.session_cookie_name,
            sessions.sign_session(session_token, settings),
            **sessions.cookie_attributes(settings),
        )

    analysis_id = uuid.uuid4().hex
    now = db.utc_now_iso()

    # Détection purement informative : elle ne bloque JAMAIS l'analyse.
    signals = security.detect_injection_signals(request.document)

    try:
        async with db.open_connection(settings.database_path) as conn:
            await db.ensure_session_tool_states(conn, session_token)
            rows = await db.create_queued_analysis(
                conn,
                analysis_id=analysis_id,
                document=request.document,
                now=now,
                signals=signals,
                max_active=settings.max_concurrent_analyses,
                queued_ttl_seconds=settings.queued_analysis_ttl_seconds,
                enabled_tools=request.enabled_tools,
                owner_session=session_token,
                provider_id=provider_id,
                model_id=model_id,
                max_active_per_session=settings.max_analyses_per_session,
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
    except db.SessionAnalysisLimitReached as exc:
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    f"Cette session a déjà {exc.active} analyses actives "
                    f"(limite par session : {settings.max_analyses_per_session}). "
                    "Attendez la fin d'une analyse ou repartez d'une nouvelle session."
                )
            },
        )
    tool_configuration = _tool_configuration_from_rows(rows)

    return AnalysisCreated(
        analysis_id=analysis_id,
        status="queued",
        created_at=now,
        provider_id=provider_id,
        model_id=model_id,
        session_token=session_token,
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
    request_http: Request,
    request: StartAnalysisRequest | None = None,
    settings: Settings = Depends(get_settings),
) -> StartAnalysisResponse | JSONResponse:
    """Démarrage idempotent : compare-and-set SQL `queued` -> `running`.

    Le credential BYOK (`api_key`) est reçu ici, enregistré pour la redaction,
    VALIDÉ (adapter construit) puis transmis EN MÉMOIRE à la tâche
    `run_analysis` ; il n'est jamais persisté, diffusé, loggué ou renvoyé.
    Un credential absent/invalide échoue proprement ici (400) SANS transition :
    l'analyse reste `queued` et l'utilisateur peut reconnecter puis réessayer."""
    session_token = _session_token_from_request(request_http, settings)
    async with db.open_connection(settings.database_path) as conn:
        row = await db.get_analysis(conn, analysis_id)
        if row is None or not _owns_analysis(row, session_token):
            raise HTTPException(status_code=404, detail="analysis not found")
        if row["status"] != "queued":
            return StartAnalysisResponse(
                analysis_id=analysis_id, status=row["status"], already_started=True
            )
        document = row["document"]
        provider_id = row["provider_id"] or "minimax"
        model_id = row["model_id"] or settings.minimax_model

    api_key = request.api_key if request is not None else None
    if api_key:
        redact.register_secret(api_key)

    try:
        provider = build_provider(
            provider_id=provider_id,
            model_id=model_id,
            api_key=api_key,
            settings=settings,
        )
    except ProviderError as exc:
        return JSONResponse(
            status_code=400,
            content={"detail": redact.redact_text(str(exc)) or str(exc)},
        )

    async with db.open_connection(settings.database_path) as conn:
        started_at = db.utc_now_iso()
        transitioned = await db.start_analysis(conn, analysis_id, started_at)
        if not transitioned:
            current = await db.get_analysis(conn, analysis_id)
            await provider.close()
            return StartAnalysisResponse(
                analysis_id=analysis_id,
                status=current["status"] if current else "running",
                already_started=True,
            )

    task = asyncio.create_task(
        experts.run_analysis(
            analysis_id,
            document,
            settings,
            _connection_factory(settings),
            provider_id=provider_id,
            model=model_id,
            api_key=api_key,
            provider=provider,
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


async def _authorized_analysis(
    analysis_id: str,
    request: Request,
    settings: Settings,
    conn: Any,
) -> Any:
    """Charge une analyse et vérifie l'ownership (404 sinon)."""
    row = await db.get_analysis(conn, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    session_token = _session_token_from_request(request, settings)
    if not _owns_analysis(row, session_token):
        raise HTTPException(status_code=404, detail="analysis not found")
    return row


@app.get("/api/analyses/{analysis_id}", response_model=AnalysisSnapshot)
async def get_analysis_snapshot(
    analysis_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    conn: Any = Depends(get_db),
) -> AnalysisSnapshot:
    row = await _authorized_analysis(analysis_id, request, settings, conn)

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
        provider_id=row["provider_id"],
        model_id=row["model_id"],
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
            "structured_repair_attempts": _boot_settings.structured_repair_attempts,
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
    request: Request,
    after: int = 0,
    limit: int = 500,
    settings: Settings = Depends(get_settings),
    conn: Any = Depends(get_db),
) -> EventsHistoryResponse:
    """Historique JSON paginé pour hydrater un F5 sans animation artificielle."""
    await _authorized_analysis(analysis_id, request, settings, conn)

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
    session_token = _session_token_from_request(request, settings)
    if not _owns_analysis(row, session_token):
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
                payload = json.loads(event["payload_json"])
                yield _format_sse(event["id"], event["event_type"], payload)
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
async def get_tools_catalog(
    request_http: Request,
    settings: Settings = Depends(get_settings),
    conn: Any = Depends(get_db),
) -> ToolCatalogResponse:
    """Catalogue des outils pour CETTE session (jamais celui d'un autre
    utilisateur). Une session sans préférences hérite des defaults globaux."""
    session_token = _session_token_from_request(request_http, settings)
    if session_token is None:
        states = await db.list_tool_states(conn)
    else:
        await db.ensure_session_tool_states(conn, session_token)
        states = await db.list_session_tool_states(conn, session_token)
    return ToolCatalogResponse(
        tools=[toolkit.tool_state_from_row(row) for row in states]
    )


@app.post("/api/tool-commands", response_model=ToolCommandResponse)
async def apply_tool_command(
    request: ToolCommandRequest,
    response: Response,
    request_http: Request,
    settings: Settings = Depends(get_settings),
    conn: Any = Depends(get_db),
) -> ToolCommandResponse:
    """Commande `/tools` — préférence de CETTE session uniquement. La
    configuration d'une analyse déjà créée reste immuable (`enabled_tools`
    figé à la création)."""
    session_token = _session_token_from_request(request_http, settings)
    if session_token is None:
        session_token = sessions.new_session_token()
        response.set_cookie(
            settings.session_cookie_name,
            sessions.sign_session(session_token, settings),
            **sessions.cookie_attributes(settings),
        )
    await db.ensure_session_tool_states(conn, session_token)

    try:
        action, tool_name = toolkit.parse_tool_command(request.command)
    except toolkit.ToolCommandSyntaxError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid /tools command: {exc}",
        ) from exc

    if action == "list":
        states = await db.list_session_tool_states(conn, session_token)
        tools = [toolkit.tool_state_from_row(row) for row in states]
        return ToolCommandResponse(
            action="list",
            message="Catalogue des outils (préférences de cette session).",
            tool_name=None,
            enabled=None,
            tools=tools,
        )

    enabled = action == "enable"
    await db.set_session_tool_state(conn, session_token, tool_name, enabled)
    states = await db.list_session_tool_states(conn, session_token)
    tools = [toolkit.tool_state_from_row(row) for row in states]
    return ToolCommandResponse(
        action=action,
        message=(
            f"Outil {tool_name} {'activé' if enabled else 'désactivé'} "
            "(préférence de cette session, figée par analyse)."
        ),
        tool_name=tool_name,
        enabled=enabled,
        tools=tools,
    )