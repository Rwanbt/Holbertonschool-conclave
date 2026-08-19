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
    expert_timeout_seconds: float = Field(
        30.0, gt=0, description="Délai maximal d'un expert (Avocat/Procureur/Comptable)."
    )
    arbiter_timeout_seconds: float = Field(
        20.0, gt=0, description="Délai maximal de l'Arbitre."
    )
    analysis_timeout_seconds: float = Field(
        60.0, gt=0, description="Délai maximal de l'analyse entière."
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
