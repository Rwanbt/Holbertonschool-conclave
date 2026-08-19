"""Application FastAPI CONCLAVE — Palier 1-3.

Routes :
- GET  /api/health      -> {"status": "ok"}
- POST /api/p2/llm      -> {"answer": str, "model": "MiniMax-M3"} (tuyau seul)
- POST /api/p3/agent    -> {"answer", "model", "trace", "usage"} (boucle agent)

La future route POST /api/analyses (SSE) appartient aux paliers suivants ;
les routes p2/p3 en sont des jalons temporaires, pas des implémentations
précoces de celle-ci.
"""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import agent, llm
from .config import Settings, get_settings
from .schemas import AgentRequest, AgentResponse, LLMRequest, LLMResponse

app = FastAPI(
    title="CONCLAVE backend",
    description="Validation d'entrée + passerelle MiniMax M3.",
    version="0.1.0",
)

_boot_settings = get_settings()


def _parse_origins(value: str) -> list[str]:
    return [origin.strip() for origin in value.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(_boot_settings.frontend_origin),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


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
