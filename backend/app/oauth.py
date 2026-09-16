"""OAuth officiel — Google Gemini uniquement.

CONCLAVE n'implémente QUE des mécanismes OAuth officiellement supportés pour
l'inférence. À ce jour, seul Google Gemini accepte un jeton OAuth 2.0 pour
l'API Generative Language. Les OAuth ChatGPT/Codex et GitHub Copilot reposent
sur des endpoints non publics et sont volontairement ABSENTS (cf. plan §14/§44).

Sans client OAuth Google configuré, l'option reste indisponible : aucune
capacité factice n'est affichée.

Le jeton obtenu vit UNIQUEMENT en mémoire serveur, indexé par (session,
provider), avec expiration — jamais persisté, jamais renvoyé au client.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx

from .config import Settings

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Seul provider OAuth officiellement supporté pour l'inférence.
OAUTH_SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"gemini"})

#: (session_token, provider_id) -> (access_token, expires_at_monotonic)
_TOKEN_STORE: dict[tuple[str, str], tuple[str, float]] = {}

#: Marge avant expiration pour ne jamais utiliser un jeton sur le point de mourir.
_EXPIRY_MARGIN_SECONDS = 60


def oauth_supported(provider_id: str) -> bool:
    return provider_id in OAUTH_SUPPORTED_PROVIDERS


def google_configured(settings: Settings) -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def oauth_available(provider_id: str, settings: Settings) -> bool:
    if provider_id == "gemini":
        return google_configured(settings)
    return False


def build_authorize_url(state: str, settings: Settings) -> str:
    """Construit l'URL d'autorisation OAuth Google (scope Generative Language)."""
    from urllib.parse import urlencode

    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.google_oauth_scopes,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_google_code(
    code: str, settings: Settings, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Échange le code d'autorisation contre un access token OAuth Google."""
    owned = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    finally:
        if owned:
            await http.aclose()
    if response.status_code != 200:
        raise OAuthError(
            f"Échange de code OAuth refusé par Google (HTTP {response.status_code})."
        )
    payload = response.json()
    if not payload.get("access_token"):
        raise OAuthError("Google n'a pas renvoyé d'access token.")
    return payload


class OAuthError(RuntimeError):
    """Échec du flux OAuth (message borné, sans secret)."""


def new_state(session_token: str) -> str:
    """État OAuth signé portant le jeton de session (anti-CSRF + transport)."""
    return f"{session_token}:{secrets.token_urlsafe(12)}"


def store_token(
    session_token: str, provider_id: str, access_token: str, expires_in: float
) -> None:
    _TOKEN_STORE[(session_token, provider_id)] = (
        access_token,
        time.monotonic() + max(0.0, expires_in - _EXPIRY_MARGIN_SECONDS),
    )


def get_token(session_token: str, provider_id: str) -> str | None:
    entry = _TOKEN_STORE.get((session_token, provider_id))
    if entry is None:
        return None
    token, expires_at = entry
    if time.monotonic() >= expires_at:
        _TOKEN_STORE.pop((session_token, provider_id), None)
        return None
    return token


def delete_token(session_token: str, provider_id: str) -> None:
    _TOKEN_STORE.pop((session_token, provider_id), None)