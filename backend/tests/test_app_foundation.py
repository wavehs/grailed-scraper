"""Offline contract tests for the phase-one backend foundation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.logging import mask_sensitive_data
from app.main import app


def test_health_returns_service_status_and_request_id() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"X-Request-ID": "test-request-1"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-1"
    assert response.json() == {
        "status": "ok",
        "service": "Grailed Liquidity Analyzer",
        "source_mode": "mock",
        "request_id": "test-request-1",
    }


def test_openapi_exposes_the_release_version() -> None:
    assert app.version == "1.0.0"
    assert app.openapi()["info"]["version"] == "1.0.0"


def test_unknown_route_uses_the_error_envelope() -> None:
    with TestClient(app) as client:
        response = client.get("/api/missing", headers={"X-Request-ID": "test-request-2"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Request failed",
            "request_id": "test-request-2",
        }
    }


def test_settings_read_app_prefixed_environment_variables(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_SOURCE_MODE", "replay")
    monkeypatch.setenv("APP_REQUESTS_PER_MINUTE", "12")

    settings = Settings()

    assert settings.source_mode == "replay"
    assert settings.requests_per_minute == 12


def test_logging_masks_nested_secrets() -> None:
    event = {
        "api_key": "abcdef1234",
        "nested": {"authorization": "Bearer secret-value"},
        "safe": "visible",
    }

    assert mask_sensitive_data(event) == {
        "api_key": "abcd****",
        "nested": {"authorization": "Bear****"},
        "safe": "visible",
    }


def test_proxy_test_endpoint_returns_only_masked_proxy_details(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.routes as api_routes

    async def successful_probe(_: str) -> bool:
        return True

    monkeypatch.setattr(api_routes, "_probe_proxy", successful_probe)
    app.dependency_overrides[get_settings] = lambda: Settings(
        proxy_enabled=True,
        proxy_list_http=["http://username:password@proxy.test:50100"],
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/settings/proxies/test")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["proxies"][0]["proxy"] == "http://***:***@proxy.test:50100"
    assert "password" not in str(body)
