"""Configuration du backend CONCLAVE (Palier 1-2).

La configuration est lue depuis l'environnement puis depuis le fichier
`.env` à la racine du projet, grâce à `pydantic-settings`. La clé MiniMax
n'est jamais stockée dans le code ni dans git : elle vit uniquement dans
`.env` (ignoré) ou dans les variables d'environnement du serveur.
"""

from functools import lru_cache
from pathlib import Path

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
