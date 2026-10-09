from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.errors import install_exception_handlers


def _client(reason: str) -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/write")
    async def write() -> None:
        raise OperationalError("UPDATE brands", {}, sqlite3.OperationalError(reason))

    return TestClient(app, raise_server_exceptions=False)


def test_locked_database_is_a_retryable_503() -> None:
    response = _client("database is locked").post("/write")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "database_busy"
    assert "UPDATE" not in error["message"]


def test_other_database_errors_stay_internal() -> None:
    response = _client("no such table: brands").post("/write")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
