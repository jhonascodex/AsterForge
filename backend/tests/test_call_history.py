import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import AuthContext, get_auth_context
from app.db.base import Base
from app.db.models import (
    AsteriskServer,
    CallDetailRecord,
    ChannelEventRecord,
    InventoryEnvironment,
)
from app.db.session import get_session
from app.main import app


@pytest.fixture
def history_database() -> tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    environment = InventoryEnvironment(
        id=uuid4(), code="HISTORY", display_name="History tests", enabled=True
    )
    server = AsteriskServer(
        id=uuid4(),
        name="History PBX",
        name_key=f"history-{uuid4()}",
        hostname="history.test",
        environment=environment,
        created_at=now,
        updated_at=now,
    )

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add_all(
                [
                    server,
                    CallDetailRecord(
                        id=uuid4(),
                        server_id=server.id,
                        source_id="cdr-2",
                        unique_id="channel-2",
                        linked_id="call-2",
                        caller_number="1002",
                        connected_number="2002",
                        disposition="NO ANSWER",
                        started_at=now - timedelta(minutes=10),
                        ended_at=now - timedelta(minutes=9),
                        duration_seconds=60,
                        billable_seconds=0,
                    ),
                    CallDetailRecord(
                        id=uuid4(),
                        server_id=server.id,
                        source_id="cdr-1",
                        unique_id="channel-1",
                        linked_id="call-1",
                        caller_number="1001",
                        connected_number="2001",
                        disposition="ANSWERED",
                        started_at=now - timedelta(minutes=20),
                        answered_at=now - timedelta(minutes=19),
                        ended_at=now - timedelta(minutes=18),
                        duration_seconds=120,
                        billable_seconds=60,
                    ),
                    ChannelEventRecord(
                        id=uuid4(),
                        server_id=server.id,
                        source_id="event-b",
                        event_type="BRIDGE_ENTER",
                        event_at=now - timedelta(minutes=19),
                        unique_id="channel-1",
                        linked_id="call-1",
                        channel="PJSIP/1001-a",
                    ),
                    ChannelEventRecord(
                        id=uuid4(),
                        server_id=server.id,
                        source_id="event-a",
                        event_type="ANSWER",
                        event_at=now - timedelta(minutes=19),
                        unique_id="channel-1",
                        linked_id="call-1",
                        channel="PJSIP/1001-a",
                    ),
                ]
            )
            await session.commit()

    asyncio.run(prepare())
    try:
        yield factory, server, now
    finally:
        asyncio.run(engine.dispose())


def _actor(permission_codes: set[str]) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        username="history-reader",
        permissions=frozenset(permission_codes),
        session_id=uuid4(),
        must_change_password=False,
    )


def _install_overrides(
    factory: async_sessionmaker[AsyncSession],
    *,
    cdr_source: str = "postgres",
    cel_source: str = "postgres",
    permissions: set[str] | None = None,
) -> None:
    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def authorize() -> AuthContext:
        return _actor(permissions or {"call.read"})

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_auth_context] = authorize
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        cdr_source=cdr_source, cel_source=cel_source
    )


def test_cdr_search_is_filterable_paginated_and_bounded(
    history_database: tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime],
) -> None:
    factory, server, now = history_database
    _install_overrides(factory)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/calls/history",
                params={
                    "started_from": (now - timedelta(hours=1)).isoformat(),
                    "started_to": now.isoformat(),
                    "server_id": str(server.id),
                    "caller_number": "1001",
                    "disposition": "ANSWERED",
                    "correlation_id": "call-1",
                    "limit": 1,
                },
            )
            assert response.status_code == 200
            assert response.json()["availability"] == "available"
            assert response.json()["total"] == 1
            assert response.json()["items"][0]["source_id"] == "cdr-1"

            oversized = client.get(
                "/api/v1/calls/history",
                params={
                    "started_from": (now - timedelta(days=32)).isoformat(),
                    "started_to": now.isoformat(),
                },
            )
            assert oversized.status_code == 422
            assert oversized.json()["detail"]["code"] == "history_window_too_large"
    finally:
        app.dependency_overrides.clear()


def test_cel_timeline_is_deterministic_and_capability_aware(
    history_database: tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime],
) -> None:
    factory, server, now = history_database
    _install_overrides(factory)
    params = {
        "server_id": str(server.id),
        "correlation_id": "call-1",
        "started_from": (now - timedelta(hours=1)).isoformat(),
        "started_to": now.isoformat(),
    }
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/calls/timeline", params=params)
            assert response.status_code == 200
            assert response.json()["availability"] == "available"
            assert [item["source_id"] for item in response.json()["items"]] == [
                "event-a",
                "event-b",
            ]

            app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
                cdr_source="postgres", cel_source="disabled"
            )
            unconfigured = client.get("/api/v1/calls/timeline", params=params)
            assert unconfigured.status_code == 200
            assert unconfigured.json()["availability"] == "unconfigured"
            assert unconfigured.json()["source"] is None
            assert unconfigured.json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_history_capabilities_and_authorization_are_explicit(
    history_database: tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime],
) -> None:
    factory, _server, _now = history_database
    _install_overrides(factory, cdr_source="disabled", cel_source="disabled")
    try:
        with TestClient(app) as client:
            capabilities = client.get("/api/v1/calls/history/capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json() == {
                "cdr": {"status": "unconfigured", "source": None},
                "cel": {"status": "unconfigured", "source": None},
            }

            async def deny() -> AuthContext:
                return _actor({"server.read"})

            app.dependency_overrides[get_auth_context] = deny
            assert client.get("/api/v1/calls/history/capabilities").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_history_models_define_primary_query_indexes() -> None:
    cdr_indexes = {index.name for index in CallDetailRecord.__table__.indexes}
    cel_indexes = {index.name for index in ChannelEventRecord.__table__.indexes}
    assert {
        "ix_cdr_server_started",
        "ix_cdr_server_linked_started",
        "ix_cdr_server_unique_started",
    }.issubset(cdr_indexes)
    assert {
        "ix_cel_server_event_time",
        "ix_cel_server_linked_time",
        "ix_cel_server_unique_time",
    }.issubset(cel_indexes)
