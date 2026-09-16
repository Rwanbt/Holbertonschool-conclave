"""Sessions applicatives anonymes pour l'isolation multi-utilisateur.

Chaque visiteur reçoit un cookie signé (HMAC) contenant un jeton aléatoire
cryptographiquement fort. Le jeton (pas le cookie complet) est stocké comme
`owner_session` de chaque analyse : toutes les routes de lecture/écriture
d'une analyse vérifient l'égalité avant de répondre.

- Aucun secret provider dans ce mécanisme.
- Si `SESSION_COOKIE_SECRET` est vide, une clé aléatoire par processus est
  utilisée : les sessions sont invalidées au redémarrage (documenté, acceptable).
- Le cookie est HttpOnly + SameSite : le JS ne peut ni le lire ni le réécrire.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .config import Settings

_process_key: bytes | None = None


def _signing_key(settings: Settings) -> bytes:
    global _process_key
    if settings.session_cookie_secret:
        return settings.session_cookie_secret.encode("utf-8")
    if _process_key is None:
        _process_key = secrets.token_bytes(32)
    return _process_key


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def sign_session(token: str, settings: Settings) -> str:
    key = _signing_key(settings)
    digest = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{token}.{digest}"


def unsign_session(value: str, settings: Settings) -> str | None:
    try:
        token, digest = value.rsplit(".", 1)
    except ValueError:
        return None
    if not token or not digest:
        return None
    expected = hmac.new(
        _signing_key(settings), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(digest, expected):
        return None
    return token


def cookie_attributes(settings: Settings) -> dict[str, object]:
    return {
        "httponly": True,
        "samesite": "lax",
        "max_age": settings.session_max_age_days * 24 * 3600,
        "path": "/",
    }