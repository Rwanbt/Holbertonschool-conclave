"""Application FastAPI CONCLAVE — Palier 1-2.

Route de santé du socle :
- GET /api/health -> {"status": "ok"}
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings

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
