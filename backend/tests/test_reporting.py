import asyncio
import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import AuthContext, get_auth_context
from app.db.base import Base
from app.db.models import (
    Alert,
    AlertRule,
    AsteriskServer,
    CallDetailRecord,
    HostTelemetrySample,
    Incident,
    InventoryEnvironment,
    PjsipEndpoint,
    SipTrunk,
    TrunkStateTransition,
)
from app.db.session import get_session
from app.main import app


@pytest.fixture
def reporting_database() -> tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    environment = InventoryEnvironment(
        id=uuid4(), code="REPORT", display_name="Reporting", enabled=True
    )
    server = AsteriskServer(
        id=uuid4(),
        name="=Needle PBX",
        name_key=f"needle-{uuid4()}",
        hostname="needle.test",
        environment=environment,
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(minutes=1),
    )
    trunk = SipTrunk(
        id=uuid4(),
        server_id=server.id,
        source_id="needle-trunk",
        label="Needle trunk",
        state="UNAVAILABLE",
        last_state_change_at=now - timedelta(minutes=5),
        observed_at=now - timedelta(minutes=5),
    )
    availability_rule = AlertRule(
        id=uuid4(),
        code="asterisk.connection.unavailable",
        condition_kind="categorical_unavailable",
        severity="CRITICAL",
        duration_seconds=30,
        debounce_seconds=30,
        created_at=now - timedelta(days=1),
    )

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add_all(
                [
                    server,
                    availability_rule,
                    Alert(
                        id=uuid4(),
                        rule_id=availability_rule.id,
                        server_id=server.id,
                        resource_type="asterisk",
                        resource_key=str(server.id),
                        condition_scope="connection",
                        dedupe_key=f"availability-{server.id}",
                        active_key=f"availability-{server.id}",
                        severity="CRITICAL",
                        state="OPEN",
                        first_seen=now - timedelta(minutes=8),
                        last_seen=now - timedelta(minutes=2),
                        condition_metadata_json="{}",
                    ),
                    PjsipEndpoint(
                        id=uuid4(),
                        server_id=server.id,
                        source_id="needle-endpoint",
                        state="AVAILABLE",
                        observed_at=now - timedelta(minutes=4),
                    ),
                    trunk,
                    TrunkStateTransition(
                        id=uuid4(),
                        trunk_id=trunk.id,
                        from_state="AVAILABLE",
                        to_state="UNAVAILABLE",
                        occurred_at=now - timedelta(minutes=5),
                    ),
                    CallDetailRecord(
                        id=uuid4(),
                        server_id=server.id,
                        source_id="cdr-needle",
                        unique_id="needle-unique",
                        linked_id="needle-linked",
                        caller_number="needle-phone",
                        connected_number="2000",
                        disposition="NO ANSWER",
                        started_at=now - timedelta(minutes=6),
                    ),
                    Incident(
                        id=uuid4(),
                        server_id=server.id,
                        correlation_key="needle-incident",
                        active_key="needle-incident",
                        title="Needle incident",
                        state="OPEN",
                        opened_at=now - timedelta(minutes=7),
                    ),
                    HostTelemetrySample(
                        id=uuid4(),
                        server_id=server.id,
                        provider="node_exporter",
                        capabilities_json='["cpu","memory","recording_storage"]',
                        cpu_usage_percent=20,
                        memory_total_bytes=1_000,
                        memory_available_bytes=400,
                        filesystems_json="[]",
                        recording_mountpoint="/recordings",
                        recording_total_bytes=10_000,
                        recording_available_bytes=2_500,
                        observed_at=now - timedelta(minutes=3),
                    ),
                ]
            )
            await session.commit()

    asyncio.run(prepare())
    try:
        yield factory, server, now
    finally:
        asyncio.run(engine.dispose())


def _actor(*permissions: str) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        username="report-reader",
        permissions=frozenset(permissions),
        session_id=uuid4(),
        must_change_password=False,
    )


def _install_overrides(
    factory: async_sessionmaker[AsyncSession], permissions: tuple[str, ...]
) -> None:
    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def authorize() -> AuthContext:
        return _actor(*permissions)

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_auth_context] = authorize


def _range(now: datetime) -> dict[str, str]:
    return {
        "from_time": (now - timedelta(hours=1)).isoformat(),
        "to_time": now.isoformat(),
    }


def test_global_search_filters_result_types_by_effective_permissions(
    reporting_database: tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime],
) -> None:
    factory, _server, _now = reporting_database
    try:
        with TestClient(app) as client:
            _install_overrides(factory, ("call.read",))
            call_results = client.get("/api/v1/search", params={"q": "needle"})
            assert call_results.status_code == 200
            assert {item["entity_type"] for item in call_results.json()["items"]} == {
                "phone_number",
                "unique_id",
                "linked_id",
            }

            _install_overrides(factory, ("asterisk.read",))
            pjsip_results = client.get("/api/v1/search", params={"q": "needle"})
            assert pjsip_results.status_code == 200
            assert {item["entity_type"] for item in pjsip_results.json()["items"]} == {
                "endpoint",
                "trunk",
            }

            _install_overrides(factory, ("server.read",))
            server_results = client.get("/api/v1/search", params={"q": "needle"})
            assert [item["entity_type"] for item in server_results.json()["items"]] == [
                "server"
            ]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("family", "domain_permission"),
    [
        ("server-availability", "asterisk.read"),
        ("calls", "call.read"),
        ("failures", "call.read"),
        ("trunk-availability", "asterisk.read"),
        ("endpoint-availability", "asterisk.read"),
        ("incidents", "incident.read"),
        ("host-storage", "server.read"),
    ],
)
def test_report_families_are_time_bounded_and_domain_authorized(
    reporting_database: tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime],
    family: str,
    domain_permission: str,
) -> None:
    factory, server, now = reporting_database
    _install_overrides(factory, ("report.read", domain_permission))
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/reports/{family}",
                params={**_range(now), "server_id": str(server.id), "limit": 1},
            )
            assert response.status_code == 200
            assert response.json()["family"] == family
            assert len(response.json()["items"]) == 1

            too_wide = client.get(
                f"/api/v1/reports/{family}",
                params={
                    "from_time": (now - timedelta(days=32)).isoformat(),
                    "to_time": now.isoformat(),
                },
            )
            assert too_wide.status_code == 422
            assert too_wide.json()["detail"]["code"] == "report_window_too_large"

            _install_overrides(factory, ("report.read",))
            assert (
                client.get(f"/api/v1/reports/{family}", params=_range(now)).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()


def test_csv_export_requires_permission_and_mitigates_formula_injection(
    reporting_database: tuple[async_sessionmaker[AsyncSession], AsteriskServer, datetime],
) -> None:
    factory, _server, now = reporting_database
    try:
        with TestClient(app) as client:
            _install_overrides(factory, ("report.read", "report.export", "server.read"))
            response = client.get(
                "/api/v1/reports/host-storage/export",
                params={**_range(now), "limit": 10},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/csv")
            assert response.headers["x-content-type-options"] == "nosniff"
            rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
            assert rows[1][2] == "'=Needle PBX"

            _install_overrides(factory, ("report.read", "server.read"))
            denied = client.get(
                "/api/v1/reports/host-storage/export", params=_range(now)
            )
            assert denied.status_code == 403

            _install_overrides(factory, ("report.read", "report.export", "server.read"))
            oversized_limit = client.get(
                "/api/v1/reports/host-storage/export",
                params={**_range(now), "limit": 1001},
            )
            assert oversized_limit.status_code == 422
    finally:
        app.dependency_overrides.clear()
