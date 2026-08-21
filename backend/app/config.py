"""Configuration du backend CONCLAVE (Palier 1-3).

La configuration est lue depuis l'environnement puis depuis le fichier
`.env` à la racine du projet, grâce à `pydantic-settings`. La clé MiniMax
n'est jamais stockée dans le code ni dans git : elle vit uniquement dans
`.env` (ignoré) ou dans les variables d'environnement du serveur.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[0]=app, [1]=backend, [2]=racine du dépôt.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3"
    minimax_max_output_tokens: int = 300
    frontend_origin: str = "http://localhost:5173"

    minimax_max_tool_rounds: int = Field(
        3, ge=1, description="Nombre maximal d'appels MiniMax dans la boucle agent."
    )
    minimax_input_usd_per_million: float = Field(
        0.0,
        ge=0.0,
        description="Tarif input estimatif (USD / 1M jetons). 0.0 = non configuré.",
    )
    minimax_output_usd_per_million: float = Field(
        0.0,
        ge=0.0,
        description="Tarif output estimatif (USD / 1M jetons). 0.0 = non configuré.",
    )
    disabled_tools: str = Field(
        "",
        description=(
            "Outils désactivés à l'initialisation d'une base SQLite neuve "
            "(séparés par des virgules). Ensuite la table tool_states fait foi."
        ),
    )

    database_path: str = Field(
        "./data/conclave.db",
        description="Chemin de la base SQLite durable (crée son dossier).",
    )

    agent_max_rounds: int = Field(
        5, ge=1, description="Nombre maximal de tours d'outils par expert (boucle P4)."
    )
    expert_max_output_tokens: int = Field(
        1500,
        ge=50,
        description="Budget de sortie des experts/arbitre (JSON structuré volumineux).",
    )
    structured_repair_attempts: int = Field(
        2,
        ge=1,
        le=3,
        description="Nombre maximal de tentatives JSON sans outils après une sortie invalide.",
    )
    expert_timeout_seconds: float = Field(
        90.0,
        gt=0,
        description=(
            "Délai maximal d'un expert (Avocat/Procureur/Comptable), "
            "incluant les appels d'outils, le streaming et une éventuelle réparation."
        ),
    )
    arbiter_timeout_seconds: float = Field(
        45.0, gt=0, description="Délai maximal de l'Arbitre, streaming compris."
    )
    analysis_timeout_seconds: float = Field(
        180.0,
        gt=0,
        description=(
            "Délai maximal de l'analyse entière : il doit couvrir les experts "
            "parallèles puis l'arbitrage."
        ),
    )

    sse_poll_interval_ms: int = Field(
        100,
        ge=10,
        le=2000,
        description="Fréquence de scrutation SQLite de la route SSE, en millisecondes.",
    )
    sse_keepalive_seconds: int = Field(
        10,
        ge=1,
        le=300,
        description="Intervalle du commentaire SSE de garde-fou `: keep-alive`, en secondes.",
    )
    stream_max_draft_chars: int = Field(
        4000,
        ge=100,
        le=20000,
        description="Taille maximale du texte live diffusé (brouillon) par rôle et analyse.",
    )
    stream_delta_batch_chars: int = Field(
        16,
        ge=4,
        le=512,
        description="Taille cible d'un événement agent.response.delta, en caractères.",
    )
    max_concurrent_analyses: int = Field(
        3,
        ge=1,
        le=50,
        description=(
            "Nombre maximal d'analyses simultanément queued ou running. "
            "Au-delà, POST /api/analyses répond 429 avec une raison explicite "
            "plutôt que d'accepter dix soumissions et de saturer le fournisseur."
        ),
    )
    queued_analysis_ttl_seconds: int = Field(
        300,
        ge=30,
        le=86400,
        description=(
            "Durée maximale d'une analyse restée queued sans appel /start. "
            "Elle est ensuite marquée failed lors de la prochaine soumission, "
            "afin de ne pas occuper éternellement une place de concurrence."
        ),
    )
    stream_flush_interval_ms: int = Field(
        50,
        ge=10,
        le=2000,
        description=(
            "Délai maximal avant un flush partiel du brouillon live accumulé, "
            "pour ne jamais retenir un petit fragment reçu isolément."
        ),
    )

    @field_validator("disabled_tools")
    @classmethod
    def disabled_tools_must_be_listed(cls, value: str) -> str:
        names = {name.strip() for name in value.split(",") if name.strip()}
        allowed = {
            "measure_current_document",
            "find_security_indicators_in_current_document",
            "estimate_current_analysis_cost",
        }
        unknown = names - allowed
        if unknown:
            raise ValueError(f"unknown tool names in DISABLED_TOOLS: {sorted(unknown)}")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
