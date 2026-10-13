import os
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

os.environ.setdefault(
    "ASTERFORGE_DATABASE_URL",
    "postgresql+asyncpg://asterforge:test-only@localhost:5432/asterforge",
)

from app.db.session import get_session
from app.main import app


class HealthySession:
    async def execute(self, _statement: object) -> None:
        return None


class UnhealthySession:
    async def execute(self, _statement: object) -> None:
        raise ConnectionRefusedError("sensitive database diagnostics")


async def healthy_session() -> AsyncIterator[HealthySession]:
    yield HealthySession()


async def unhealthy_session() -> AsyncIterator[UnhealthySession]:
    yield UnhealthySession()


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness() -> None:
    app.dependency_overrides[get_session] = healthy_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_sanitizes_database_errors() -> None:
    app.dependency_overrides[get_session] = unhealthy_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "database_unavailable", "message": "Service is not ready"}
    }
    assert "sensitive" not in response.text
