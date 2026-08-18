"""Application FastAPI CONCLAVE — Palier 1-2.

Routes (temporaires pour ce palier, sans SSE ni agents) :
- GET  /api/health  -> {"status": "ok"}
- POST /api/p2/llm  -> {"answer": str, "model": "MiniMax-M3"}

La future route POST /api/analyses (SSE) appartient aux paliers suivants ;
/api/p2/llm n'en est pas une implémentation précoce, juste un test du tuyau.
"""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import llm
from .config import Settings, get_settings
from .schemas import LLMRequest, LLMResponse

app = FastAPI(
    title="CONCLAVE backend",
    description="Validation d'entrée + passerelle MiniMax M3.",
    version="0.1.0",
)

_boot_settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_boot_settings.frontend_origin],
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
