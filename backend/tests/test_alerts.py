import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.alerts import get_alert_service
from app.core.security import hash_password, new_session_token, token_digest
from app.db.base import Base
from app.db.models import (
    Alert,
    AlertRule,
    AsteriskServer,
    AuditEvent,
    AuthSession,
    CallDetailRecord,
    Incident,
    InventoryEnvironment,
    MaintenanceWindow,
    Permission,
    Profile,
    User,
)
from app.db.session import get_session
from app.main import app
from app.services.alerts import AlertService, ConditionObservation


def _rule(
    code: str,
    kind: str,
    *,
    severity: str = "WARNING",
    threshold: float | None = None,
    clear_threshold: float | None = None,
    duration: int = 0,
    debounce: int = 0,
) -> AlertRule:
    return AlertRule(
        id=uuid4(),
        code=code,
        condition_kind=kind,
        severity=severity,
        threshold=threshold,
        clear_threshold=clear_threshold,
        duration_seconds=duration,
        debounce_seconds=debounce,
        enabled=True,
        created_at=datetime.now(UTC),
    )


async def _database() -> tuple[
    object, async_sessionmaker[AsyncSession], AsteriskServer, User
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    environment = InventoryEnvironment(
        id=uuid4(), code=f"ALERT-{uuid4()}", display_name="Alert tests", enabled=True
    )
    server = AsteriskServer(
        id=uuid4(),
        name="Alert PBX",
        name_key=f"alert-{uuid4()}",
        hostname="alerts.test",
        environment=environment,
        created_at=now,
        updated_at=now,
    )
    user = User(
        id=uuid4(),
        username=f"operator-{uuid4()}",
        display_name="Alert Operator",
        password_hash=hash_password("not used"),
        created_at=now,
        updated_at=now,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add_all(
            [
                server,
                user,
                _rule(
                    "asterisk.connection.unavailable",
                    "categorical_unavailable",
                    severity="CRITICAL",
                ),
                _rule(
                    "trunk.registration.unavailable",
                    "categorical_unavailable",
                    severity="CRITICAL",
                ),
                _rule(
                    "host.cpu.high",
                    "numeric_high",
                    threshold=90,
                    clear_threshold=80,
                ),
                _rule(
                    "recording.storage.low",
                    "numeric_low",
                    severity="CRITICAL",
                    threshold=10,
                    clear_threshold=15,
                ),
                _rule(
                    "endpoint.availability.unavailable",
                    "categorical_unavailable",
                ),
                _rule(
                    "host.memory.high",
                    "numeric_high",
                    threshold=90,
                    clear_threshold=80,
                ),
                _rule(
                    "host.filesystem.low",
                    "numeric_low",
                    threshold=10,
                    clear_threshold=15,
                ),
                _rule(
                    "calls.failed_rate.high",
                    "numeric_high",
                    threshold=20,
                    clear_threshold=10,
                ),
                _rule(
                    "recording.generation.failures",
                    "numeric_high",
                    severity="CRITICAL",
                    threshold=1,
                    clear_threshold=0,
                ),
            ]
        )
        await session.commit()
    return engine, factory, server, user


def test_rules_dedupe_lifecycle_maintenance_and_incident_correlation() -> None:
    async def scenario() -> None:
        engine, factory, server, user = await _database()
        service = AlertService(factory)
        now = datetime.now(UTC)
        try:
            async with factory() as session:
                session.add(
                    MaintenanceWindow(
                        id=uuid4(),
                        server_id=server.id,
                        starts_at=now - timedelta(minutes=1),
                        ends_at=now + timedelta(seconds=10),
                        reason="Controlled PBX work",
                        created_by_user_id=user.id,
                        created_at=now,
                    )
                )
                await session.commit()

            first_id = await service.evaluate_asterisk(
                server.id, False, now, error_code="ami_unavailable"
            )
            duplicate_id = await service.evaluate_asterisk(
                server.id, False, now + timedelta(seconds=1), error_code="ami_unavailable"
            )
            assert first_id == duplicate_id
            await service.evaluate_trunk(
                server.id, "carrier-a", "REJECTED", now + timedelta(seconds=1)
            )
            await service.evaluate_host(
                server.id,
                now + timedelta(seconds=1),
                cpu_usage_percent=95,
                recording_total_bytes=1000,
                recording_available_bytes=50,
            )

            async with factory() as session:
                alerts = list((await session.scalars(select(Alert))).all())
                incidents = list((await session.scalars(select(Incident))).all())
                assert len(alerts) == 4
                assert len(incidents) == 2
                assert {item.condition_scope for item in alerts} == {"availability", "capacity"}
                assert all(item.suppressed for item in alerts)
                assert all(item.maintenance_window_id is not None for item in alerts)

            assert first_id is not None
            await service.evaluate_asterisk(
                server.id,
                False,
                now + timedelta(seconds=11),
                error_code="ami_unavailable",
            )
            async with factory() as session:
                after_expiry = await session.get(Alert, first_id)
                assert after_expiry is not None
                assert not after_expiry.suppressed
                assert after_expiry.maintenance_window_id is None
            acknowledged = await service.acknowledge(
                first_id, user.id, "NOC investigating", now=now + timedelta(seconds=12)
            )
            assert acknowledged is not None and acknowledged.state == "ACKNOWLEDGED"
            await service.evaluate_asterisk(server.id, True, now + timedelta(seconds=13))
            await service.evaluate_trunk(
                server.id, "carrier-a", "REGISTERED", now + timedelta(seconds=13)
            )

            retriggered_id = await service.evaluate_asterisk(
                server.id, False, now + timedelta(seconds=14), error_code="ami_unavailable"
            )
            assert retriggered_id is not None and retriggered_id != first_id
            async with factory() as session:
                original = await session.get(Alert, first_id)
                assert original is not None
                assert original.state == "RESOLVED"
                assert original.acknowledgement_comment == "NOC investigating"
                audit_events = (await session.scalars(select(AuditEvent))).all()
                actions = {item.action for item in audit_events}
                assert {
                    "alert.opened",
                    "alert.acknowledged",
                    "alert.resolved",
                }.issubset(actions)
        finally:
            await engine.dispose()  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_duration_hysteresis_debounce_and_flapping_are_deterministic() -> None:
    async def scenario() -> None:
        engine, factory, server, _user = await _database()
        service = AlertService(factory)
        start = datetime.now(UTC)
        try:
            async with factory() as session:
                rule = await session.scalar(
                    select(AlertRule).where(AlertRule.code == "host.cpu.high")
                )
                assert rule is not None
                rule.duration_seconds = 10
                rule.debounce_seconds = 5
                await session.commit()

            async def sample(seconds: int, value: float) -> None:
                await service.evaluate(
                    ConditionObservation(
                        rule_code="host.cpu.high",
                        server_id=server.id,
                        resource_type="host",
                        resource_key="cpu",
                        condition_scope="capacity",
                        value=value,
                        observed_at=start + timedelta(seconds=seconds),
                        metadata={"cpu_usage_percent": value},
                    )
                )

            await sample(0, 95)
            await sample(5, 70)
            await sample(6, 95)
            await sample(15, 95)
            async with factory() as session:
                assert len(list((await session.scalars(select(Alert))).all())) == 0
            await sample(16, 95)
            await sample(17, 85)
            await sample(18, 75)
            await sample(22, 75)
            async with factory() as session:
                active = await session.scalar(select(Alert))
                assert active is not None and active.state == "OPEN"
            await sample(23, 75)
            async with factory() as session:
                resolved = await session.scalar(select(Alert))
                assert resolved is not None and resolved.state == "RESOLVED"
        finally:
            await engine.dispose()  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_reopened_initial_rules_use_reliable_inputs_and_existing_lifecycle() -> None:
    async def scenario() -> None:
        engine, factory, server, user = await _database()
        now = datetime.now(UTC)
        unavailable_service = AlertService(factory, cdr_enabled=False)
        service = AlertService(factory, cdr_enabled=True)
        try:
            # Missing capabilities and an undersized CDR window are unknown, not healthy/failing.
            await unavailable_service.evaluate_host(
                server.id,
                now,
                cpu_usage_percent=None,
                memory_total_bytes=None,
                memory_available_bytes=None,
                filesystems=(),
                recording_total_bytes=None,
                recording_available_bytes=None,
                recording_failures_since_previous_sample=None,
                recording_failure_source=None,
            )
            assert await service.evaluate_failed_call_rate(server.id, now) is None

            async with factory() as session:
                session.add_all(
                    [
                        CallDetailRecord(
                            id=uuid4(),
                            server_id=server.id,
                            source_id=f"cdr-{index}",
                            disposition="FAILED" if index < 4 else "ANSWERED",
                            started_at=now - timedelta(minutes=1, seconds=index),
                        )
                        for index in range(10)
                    ]
                )
                session.add(
                    MaintenanceWindow(
                        id=uuid4(),
                        server_id=server.id,
                        resource_type="endpoint",
                        resource_key="1001",
                        starts_at=now - timedelta(minutes=1),
                        ends_at=now + timedelta(minutes=1),
                        reason="Endpoint maintenance",
                        created_by_user_id=user.id,
                        created_at=now,
                    )
                )
                await session.commit()

            endpoint_id = await service.evaluate_endpoint(
                server.id, "1001", "UNAVAILABLE", now
            )
            assert endpoint_id == await service.evaluate_endpoint(
                server.id, "1001", "UNAVAILABLE", now + timedelta(seconds=1)
            )
            await service.evaluate_host(
                server.id,
                now,
                cpu_usage_percent=None,
                memory_total_bytes=1000,
                memory_available_bytes=50,
                filesystems=(("/", 10_000, 500),),
                recording_total_bytes=None,
                recording_available_bytes=None,
                recording_failures_since_previous_sample=2,
                recording_failure_source="node_exporter:textfile:test",
            )
            assert await service.evaluate_failed_call_rate(server.id, now) is not None

            async with factory() as session:
                rows = (
                    await session.execute(
                        select(AlertRule.code, Alert)
                        .join(Alert, Alert.rule_id == AlertRule.id)
                        .where(Alert.state == "OPEN")
                    )
                ).all()
                assert {code for code, _alert in rows} == {
                    "endpoint.availability.unavailable",
                    "host.memory.high",
                    "host.filesystem.low",
                    "calls.failed_rate.high",
                    "recording.generation.failures",
                }
                endpoint_alert = next(
                    alert
                    for code, alert in rows
                    if code == "endpoint.availability.unavailable"
                )
                assert endpoint_alert.suppressed
                call_alert = next(
                    alert for code, alert in rows if code == "calls.failed_rate.high"
                )
                assert call_alert.condition_metadata_json.find('"known_outcomes":10') >= 0

            await service.evaluate_endpoint(
                server.id, "1001", "AVAILABLE", now + timedelta(seconds=2)
            )
            async with factory() as session:
                endpoint_alert = await session.get(Alert, endpoint_id)
                assert endpoint_alert is not None and endpoint_alert.state == "RESOLVED"
        finally:
            await engine.dispose()  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_alert_maintenance_and_incident_apis_enforce_permissions() -> None:
    engine, factory, server, user = asyncio.run(_database())
    now = datetime.now(UTC)
    service = AlertService(factory)

    async def prepare() -> tuple[str, str, str]:
        read_token, acknowledge_token, manage_token = (
            new_session_token(),
            new_session_token(),
            new_session_token(),
        )
        async with factory() as session:
            for code in ("alert.read", "incident.read", "alert.acknowledge", "maintenance.manage"):
                session.add(Permission(id=uuid4(), code=code, description=code))
            await session.flush()
            permissions = {
                item.code: item for item in (await session.scalars(select(Permission))).all()
            }
            identities = (
                (read_token, "reader", [permissions["alert.read"], permissions["incident.read"]]),
                (acknowledge_token, "acker", [permissions["alert.acknowledge"]]),
                (manage_token, "manager", [permissions["maintenance.manage"]]),
            )
            for token, name, granted in identities:
                identity = User(
                    id=uuid4(),
                    username=f"{name}-{uuid4()}",
                    display_name=name,
                    password_hash=hash_password("not used"),
                    profiles=[Profile(id=uuid4(), name=f"{name}-{uuid4()}", permissions=granted)],
                    created_at=now,
                    updated_at=now,
                )
                session.add(
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(token),
                        user=identity,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    )
                )
            await session.commit()
        alert_id = await service.evaluate_asterisk(server.id, False, now)
        assert alert_id is not None
        return read_token, acknowledge_token, manage_token

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    read_token, acknowledge_token, manage_token = asyncio.run(prepare())
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_alert_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/alerts").status_code == 401
            alerts = client.get(
                "/api/v1/alerts", headers={"Authorization": f"Bearer {read_token}"}
            )
            assert alerts.status_code == 200
            alert_id = alerts.json()[0]["id"]
            assert client.post(
                f"/api/v1/alerts/{alert_id}/acknowledge",
                json={"comment": "Denied"},
                headers={"Authorization": f"Bearer {read_token}"},
            ).status_code == 403
            assert client.post(
                f"/api/v1/alerts/{alert_id}/acknowledge",
                json={"comment": "Investigating"},
                headers={"Authorization": f"Bearer {acknowledge_token}"},
            ).status_code == 200
            incidents = client.get(
                "/api/v1/incidents", headers={"Authorization": f"Bearer {read_token}"}
            )
            assert incidents.status_code == 200
            assert client.get(
                f"/api/v1/incidents/{incidents.json()[0]['id']}/timeline",
                headers={"Authorization": f"Bearer {read_token}"},
            ).status_code == 200
            maintenance = client.post(
                "/api/v1/maintenance-windows",
                json={
                    "server_id": str(server.id),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(hours=1)).isoformat(),
                    "reason": "Planned work",
                },
                headers={"Authorization": f"Bearer {manage_token}"},
            )
            assert maintenance.status_code == 201
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())  # type: ignore[union-attr]
