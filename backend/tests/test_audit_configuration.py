import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password, new_session_token, token_digest
from app.db.base import Base
from app.db.models import (
    AlertRule,
    AuditEvent,
    AuthSession,
    LogEvent,
    Permission,
    Profile,
    RetentionPolicy,
    User,
)
from app.db.session import get_session
from app.main import app
from app.services.logs import LogRetentionService


def test_audit_and_monitoring_configuration_are_authorized_validated_and_redacted() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    rule_id = uuid4()

    async def prepare() -> tuple[str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        audit_permission = Permission(id=uuid4(), code="audit.read", description="audit")
        config_permission = Permission(
            id=uuid4(), code="monitoring_config.manage", description="configuration"
        )
        allowed_token, denied_token = new_session_token(), new_session_token()
        allowed = User(
            id=uuid4(),
            username="release-admin",
            display_name="Release Admin",
            password_hash=hash_password("not used"),
            profiles=[
                Profile(
                    id=uuid4(),
                    name="release-admin",
                    permissions=[audit_permission, config_permission],
                )
            ],
            created_at=now,
            updated_at=now,
        )
        denied = User(
            id=uuid4(),
            username="release-observer",
            display_name="Release Observer",
            password_hash=hash_password("not used"),
            created_at=now,
            updated_at=now,
        )
        async with factory() as session:
            session.add_all(
                [
                    allowed,
                    denied,
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(allowed_token),
                        user=allowed,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(denied_token),
                        user=denied,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                    AlertRule(
                        id=rule_id,
                        code="host.cpu.high",
                        condition_kind="numeric_high",
                        severity="WARNING",
                        threshold=90,
                        clear_threshold=80,
                        duration_seconds=300,
                        debounce_seconds=60,
                        enabled=True,
                        created_at=now,
                    ),
                    RetentionPolicy(
                        category="operational_logs",
                        retention_days=30,
                        batch_size=5000,
                        updated_at=now,
                    ),
                    AuditEvent(
                        id=uuid4(),
                        occurred_at=now,
                        action="test.redaction",
                        target_type="test",
                        target_id="safe-target",
                        metadata_json=json.dumps(
                            {"nested": {"api_token": "must-not-return"}, "safe": "visible"}
                        ),
                    ),
                ]
            )
            await session.commit()
        return allowed_token, denied_token

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    allowed_token, denied_token = asyncio.run(prepare())
    allowed = {"Authorization": f"Bearer {allowed_token}"}
    denied = {"Authorization": f"Bearer {denied_token}"}
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/audit-events").status_code == 401
            assert client.get("/api/v1/audit-events", headers=denied).status_code == 403
            page = client.get("/api/v1/audit-events?action=test.redaction", headers=allowed)
            assert page.status_code == 200
            assert page.json()["items"][0]["metadata"] == {
                "nested": {"api_token": "[REDACTED]"},
                "safe": "visible",
            }

            rules = client.get("/api/v1/monitoring-config/alert-rules", headers=allowed)
            assert rules.status_code == 200
            invalid = client.patch(
                f"/api/v1/monitoring-config/alert-rules/{rule_id}",
                headers=allowed,
                json={
                    "severity": "WARNING",
                    "threshold": 70,
                    "clear_threshold": 80,
                    "duration_seconds": 300,
                    "debounce_seconds": 60,
                    "enabled": True,
                },
            )
            assert invalid.status_code == 422
            out_of_range = client.patch(
                f"/api/v1/monitoring-config/alert-rules/{rule_id}",
                headers=allowed,
                json={
                    "severity": "WARNING",
                    "threshold": 101,
                    "clear_threshold": 85,
                    "duration_seconds": 300,
                    "debounce_seconds": 60,
                    "enabled": True,
                },
            )
            assert out_of_range.status_code == 422
            assert out_of_range.json()["detail"]["code"] == "threshold_out_of_range"
            updated = client.patch(
                f"/api/v1/monitoring-config/alert-rules/{rule_id}",
                headers=allowed,
                json={
                    "severity": "CRITICAL",
                    "threshold": 95,
                    "clear_threshold": 85,
                    "duration_seconds": 120,
                    "debounce_seconds": 30,
                    "enabled": True,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["threshold"] == 95

            assert client.get(
                "/api/v1/monitoring-config/retention-policies", headers=denied
            ).status_code == 403
            rejected = client.patch(
                "/api/v1/monitoring-config/retention-policies/operational_logs",
                headers=allowed,
                json={"retention_days": 0, "batch_size": 100},
            )
            assert rejected.status_code == 422
            retention = client.patch(
                "/api/v1/monitoring-config/retention-policies/operational_logs",
                headers=allowed,
                json={"retention_days": 14, "batch_size": 100},
            )
            assert retention.status_code == 200
            assert retention.json()["retention_days"] == 14
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_log_retention_uses_persisted_policy_and_is_bounded() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async def scenario() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(
                RetentionPolicy(
                    category="operational_logs",
                    retention_days=7,
                    batch_size=1,
                    updated_at=now,
                )
            )
            for days in (30, 20):
                session.add(
                    LogEvent(
                        id=uuid4(),
                        server_id=uuid4(),
                        source="test",
                        level="INFO",
                        message="expired",
                        occurred_at=now - timedelta(days=days),
                        ingested_at=now,
                    )
                )
            await session.commit()
        deleted = await LogRetentionService(
            factory, retention_days=365, batch_size=500
        ).run_once(now=now)
        assert deleted == 1
        async with factory() as session:
            assert len(list((await session.scalars(select(LogEvent))).all())) == 1
            event = await session.scalar(
                select(AuditEvent).where(AuditEvent.action == "logs.retention_executed")
            )
            assert event is not None
            assert json.loads(event.metadata_json)["retention_days"] == 7

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
