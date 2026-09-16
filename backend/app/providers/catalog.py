"""Catalogue providers/modèles généré depuis models.dev (opencode).

Le fichier `catalog_data.json` est un instantané COMMITÉ, régénérable via
`scripts/generate_provider_catalog.py`. Il fournit, pour chaque provider :
label, adapter, base URL (allowlist), variable d'environnement, et modèles
avec capacités + tarifs.

Règle de sécurité : la base URL provient EXCLUSIVEMENT de ce catalogue
contrôlé ; elle n'est jamais saisie par l'utilisateur (pas de SSRF).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent / "catalog_data.json"

try:
    _DATA: dict[str, Any] = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:  # pragma: no cover - filet de sécurité
    _DATA = {"providers": []}

PROVIDERS: list[dict[str, Any]] = _DATA.get("providers", [])
CATALOG: dict[str, dict[str, Any]] = {
    provider["provider_id"]: provider for provider in PROVIDERS
}

#: Allowlist des endpoints réellement appelables côté serveur.
ALLOWED_BASE_URLS: tuple[str, ...] = tuple(
    dict.fromkeys(provider["base_url"] for provider in PROVIDERS)
)


def catalog_provider(provider_id: str) -> dict[str, Any] | None:
    return CATALOG.get(provider_id)


def catalog_model(provider_id: str, model_id: str) -> dict[str, Any] | None:
    provider = CATALOG.get(provider_id)
    if provider is None:
        return None
    for model in provider["models"]:
        if model["model_id"] == model_id:
            return model
    return None