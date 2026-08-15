"""Source-independent contracts for the backend foundation."""

from __future__ import annotations

import sys
from typing import Any

from fastapi.testclient import TestClient

from app.cli import main as cli_main
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
        "source_mode": "live",
        "request_id": "test-request-1",
        "version": "1.0.0",
        "revision": response.json()["revision"],
        "environment": "development",
    }
    assert response.json()["revision"] != "unknown"


def test_trusted_host_and_exact_cors_origin() -> None:
    with TestClient(app) as client:
        rejected = client.get("/api/health", headers={"Host": "192.168.1.10"})
        allowed = client.options(
            "/api/health",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert rejected.status_code == 400
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "access-control-allow-origin" not in denied.headers


def test_openapi_exposes_the_release_version() -> None:
    assert app.version == "1.0.0"
    assert app.openapi()["info"]["version"] == "1.0.0"


def test_public_diagnostics_do_not_expose_configured_secrets(
    monkeypatch: Any, capsys: Any
) -> None:
    secret = "phase-one-secret"
    app.dependency_overrides[get_settings] = lambda: Settings(
        proxy_url=f"http://user:{secret}@proxy.test:8080",
        seller_identity_salt=secret,
    )
    try:
        with TestClient(app) as client:
            health = client.get("/api/health")
            openapi = client.get("/openapi.json")
    finally:
        app.dependency_overrides.clear()

    monkeypatch.setattr(sys, "argv", ["python -m app.cli", "doctor"])
    assert cli_main() == 0
    doctor = capsys.readouterr().out
    assert secret not in health.text
    assert secret not in openapi.text
    assert secret not in doctor


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
    monkeypatch.setenv("APP_REQUESTS_PER_MINUTE", "12")

    settings = Settings()

    assert settings.source_mode == "live"
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
