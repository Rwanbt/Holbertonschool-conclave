"""Tests OAuth officiel (Google Gemini) — aucun réseau, aucune clé réelle.

Cadre : CONCLAVE n'implémente QUE l'OAuth officiellement supporté pour
l'inférence (Google Gemini). Les OAuth ChatGPT/Copilot non officiels sont
volontairement absents.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import oauth
from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.providers.gemini import GeminiAdapter


def _settings(**overrides) -> Settings:
    base = {
        "database_path": "/tmp/conclave_oauth_test.db",
        "google_oauth_client_id": "",
        "google_oauth_client_secret": "",
        "google_oauth_redirect_uri": "http://localhost:8001/api/oauth/google/callback",
        "oauth_frontend_redirect": "http://localhost:5173",
    }
    base.update(overrides)
    return Settings(**base)


class TestOAuthSupport:
    def test_only_google_is_supported(self) -> None:
        assert oauth.oauth_supported("gemini") is True
        assert oauth.oauth_supported("openai") is False
        assert oauth.oauth_supported("anthropic") is False

    def test_available_only_when_configured(self) -> None:
        assert oauth.oauth_available("gemini", _settings()) is False
        configured = _settings(
            google_oauth_client_id="cid",
            google_oauth_client_secret="csecret",
        )
        assert oauth.oauth_available("gemini", configured) is True
        # Un provider non supporté reste indisponible même si un client existe.
        assert oauth.oauth_available("openai", configured) is False

    def test_authorize_url_contains_required_params(self) -> None:
        settings = _settings(
            google_oauth_client_id="client-123",
            google_oauth_client_secret="secret",
        )
        url = oauth.build_authorize_url("state-abc", settings)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "client_id=client-123" in url
        assert "state=state-abc" in url
        assert "response_type=code" in url
        assert "generative-language" in url


class TestTokenStore:
    def test_store_get_delete_and_expiry(self) -> None:
        oauth.store_token("sess-1", "gemini", "tok-abc", 3600)
        assert oauth.get_token("sess-1", "gemini") == "tok-abc"
        # Une session différente n'y a pas accès (isolation).
        assert oauth.get_token("sess-2", "gemini") is None
        oauth.delete_token("sess-1", "gemini")
        assert oauth.get_token("sess-1", "gemini") is None

    def test_expired_token_is_not_returned(self) -> None:
        # expires_in minus la marge (60 s) => déjà expiré.
        oauth.store_token("sess-x", "gemini", "tok-old", 10)
        assert oauth.get_token("sess-x", "gemini") is None


class TestExchange:
    def test_exchange_success_and_failure(self) -> None:
        import asyncio

        settings = _settings(
            google_oauth_client_id="cid", google_oauth_client_secret="sec"
        )

        def ok_handler(request: httpx.Request) -> httpx.Response:
            assert request.url == httpx.URL(oauth.GOOGLE_TOKEN_URL)
            return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})

        good = httpx.AsyncClient(transport=httpx.MockTransport(ok_handler))
        result = asyncio.run(oauth.exchange_google_code("code", settings, good))
        assert result["access_token"] == "at-1"

        def bad_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        bad = httpx.AsyncClient(transport=httpx.MockTransport(bad_handler))
        with pytest.raises(oauth.OAuthError):
            asyncio.run(oauth.exchange_google_code("code", settings, bad))


class TestGeminiAuthMode:
    def test_api_key_uses_header(self) -> None:
        adapter = GeminiAdapter(api_key="AIza-x", model="gemini-2.5-pro")
        assert adapter._headers()["x-goog-api-key"] == "AIza-x"
        assert "Authorization" not in adapter._headers()

    def test_oauth_uses_bearer(self) -> None:
        adapter = GeminiAdapter(
            api_key="oauth-token", model="gemini-2.5-pro", auth_mode="oauth"
        )
        assert adapter._headers()["Authorization"] == "Bearer oauth-token"
        assert "x-goog-api-key" not in adapter._headers()


class TestOAuthRoutes:
    def test_status_endpoint(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        try:
            with TestClient(app) as tc:
                body = tc.get("/api/oauth/gemini/status").json()
                assert body["supported"] is True
                assert body["configured"] is False
                assert body["connected"] is False
        finally:
            app.dependency_overrides.clear()

    def test_start_refused_without_client(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        try:
            with TestClient(app, follow_redirects=False) as tc:
                response = tc.get("/api/oauth/gemini/start")
                assert response.status_code == 400
        finally:
            app.dependency_overrides.clear()

    def test_start_redirects_to_google_when_configured(self) -> None:
        configured = _settings(
            google_oauth_client_id="cid", google_oauth_client_secret="secret"
        )
        app.dependency_overrides[get_settings] = lambda: configured
        try:
            with TestClient(app, follow_redirects=False) as tc:
                response = tc.get("/api/oauth/gemini/start")
                assert response.status_code in (302, 307)
                assert "accounts.google.com" in response.headers["location"]
        finally:
            app.dependency_overrides.clear()

    def test_providers_expose_oauth_fields(self) -> None:
        app.dependency_overrides[get_settings] = lambda: _settings()
        try:
            with TestClient(app) as tc:
                providers = tc.get("/api/providers").json()["providers"]
                gemini = next(
                    p for p in providers if p["provider_id"] == "gemini"
                )
                openai = next(
                    p for p in providers if p["provider_id"] == "openai"
                )
                assert gemini["oauth_supported"] is True
                assert openai["oauth_supported"] is False
        finally:
            app.dependency_overrides.clear()