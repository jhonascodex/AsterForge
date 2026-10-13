import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.logs import get_log_source_read_model
from app.core.security import hash_password, new_session_token, token_digest
from app.db.base import Base
from app.db.models import (
    AsteriskServer,
    AuditEvent,
    AuthSession,
    InventoryEnvironment,
    LogEvent,
    Permission,
    Profile,
    User,
)
from app.db.session import get_session
from app.integrations.logs import (
    AsteriskFileLogAdapter,
    LogBatch,
    LogTarget,
    NormalizedLogRecord,
)
from app.main import app
from app.services.logs import (
    DatabaseLogStore,
    LogIngestionManager,
    LogRetentionService,
    LogSourceReadModel,
)


def _record(message: str, occurred_at: datetime, *, level: str = "INFO") -> NormalizedLogRecord:
    return NormalizedLogRecord(
        source="asterisk/messages",
        level=level,
        module="res_pjsip.c",
        message=message,
        endpoint="1001",
        trunk="carrier-a",
        unique_id="unique-1",
        linked_id="linked-1",
        occurred_at=occurred_at,
    )


def test_asterisk_file_adapter_normalizes_bounds_and_redacts(tmp_path: Path) -> None:
    server_id = uuid4()
    source = tmp_path / str(server_id)
    source.mkdir()
    (source / "messages").write_text(
        "[2026-09-12T12:00:00Z] WARNING[42] res_pjsip.c: "
        "endpoint=1001 trunk=carrier-a uniqueid=u-1 linkedid=l-1 password=hunter2\n"
        "plain line Authorization: Bearer abc.def\n",
        encoding="utf-8",
    )
    adapter = AsteriskFileLogAdapter(str(tmp_path), max_bytes=4096, max_lines=1)
    first = asyncio.run(adapter.read(LogTarget(server_id), 0))
    second = asyncio.run(adapter.read(LogTarget(server_id), first.next_offset))

    assert len(first.records) == 1
    assert first.records[0].level == "WARNING"
    assert first.records[0].module == "res_pjsip.c"
    assert first.records[0].endpoint == "1001"
    assert first.records[0].trunk == "carrier-a"
    assert first.records[0].unique_id == "u-1"
    assert first.records[0].linked_id == "l-1"
    assert "hunter2" not in first.records[0].message
    assert first.records[0].message.endswith("password=[REDACTED]")
    assert second.records[0].message == "plain line Authorization: [REDACTED]"


def test_failing_log_source_is_isolated_and_observable() -> None:
    healthy_id, failed_id = uuid4(), uuid4()

    class Adapter:
        source = "test/source"

        async def read(self, target: LogTarget, offset: int) -> LogBatch:
            del target, offset
            return LogBatch((), 0)

    class Store:
        async def cursor(self, server_id: UUID, source: str) -> int:
            del source
            if server_id == failed_id:
                raise OSError("synthetic source failure")
            return 0

        async def save(self, server_id: UUID, adapter: Adapter, offset: int) -> int:
            del server_id, adapter, offset
            return 0

    async def exercise() -> list[object]:
        read_model = LogSourceReadModel()
        manager = LogIngestionManager(Adapter(), Store(), read_model)  # type: ignore[arg-type]
        await manager.collect(failed_id)
        await manager.collect(healthy_id)
        return await read_model.list()

    statuses = asyncio.run(exercise())
    assert any(not item.available and item.server_id == failed_id for item in statuses)
    assert any(item.available and item.server_id == healthy_id for item in statuses)


def test_log_search_filters_bounds_authorization_and_retention_audit(tmp_path: Path) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    server_id = uuid4()

    async def prepare() -> tuple[str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        environment = InventoryEnvironment(
            id=uuid4(), code="PROD", display_name="Production", enabled=True
        )
        server = AsteriskServer(
            id=server_id,
            name="PBX Lima",
            name_key="pbx lima",
            hostname="pbx.test",
            environment=environment,
            created_at=now,
            updated_at=now,
        )
        permission = Permission(id=uuid4(), code="log.read", description="View logs")
        profile = Profile(id=uuid4(), name="log-reader", permissions=[permission])
        user = User(
            id=uuid4(),
            username="log-reader",
            display_name="Log Reader",
            password_hash=hash_password("not used"),
            profiles=[profile],
            created_at=now,
            updated_at=now,
        )
        token = new_session_token()
        async with factory() as session:
            session.add_all(
                [
                    server,
                    user,
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(token),
                        user=user,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                    LogEvent(
                        id=uuid4(),
                        server_id=server_id,
                        ingested_at=now - timedelta(days=60),
                        **_record("expired", now - timedelta(days=60)).__dict__,
                    ),
                ]
            )
            await session.commit()
        source = tmp_path / str(server_id)
        source.mkdir()
        (source / "messages").write_text(
            f"[{now.isoformat()}] ERROR[42] res_pjsip.c: registration failed "
            "endpoint=1001 trunk=carrier-a uniqueid=unique-1 linkedid=linked-1 "
            "password=collector-secret\n",
            encoding="utf-8",
        )
        store = DatabaseLogStore(factory)
        adapter = AsteriskFileLogAdapter(str(tmp_path), max_bytes=4096, max_lines=100)
        assert await store.save(server_id, adapter, 0) == 1
        denied_token = new_session_token()
        async with factory() as session:
            denied = User(
                id=uuid4(),
                username="denied",
                display_name="Denied",
                password_hash=hash_password("not used"),
                created_at=now,
                updated_at=now,
            )
            session.add_all(
                [
                    denied,
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(denied_token),
                        user=denied,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()
        return token, denied_token

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    token, denied_token = asyncio.run(prepare())
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_log_source_read_model] = LogSourceReadModel
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/logs").status_code == 401
            assert client.get(
                "/api/v1/logs", headers={"Authorization": f"Bearer {denied_token}"}
            ).status_code == 403
            response = client.get(
                "/api/v1/logs",
                params={
                    "environment": "prod",
                    "server_id": str(server_id),
                    "level": "ERROR",
                    "source": "asterisk/messages",
                    "module": "res_pjsip.c",
                    "endpoint": "1001",
                    "trunk": "carrier-a",
                    "unique_id": "unique-1",
                    "linked_id": "linked-1",
                    "q": "registration",
                    "from_time": (now - timedelta(hours=1)).isoformat(),
                    "to_time": (now + timedelta(hours=1)).isoformat(),
                    "limit": 1,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            message = response.json()["items"][0]["message"]
            assert "registration failed" in message
            assert "collector-secret" not in message
            assert "password=[REDACTED]" in message
            too_wide = client.get(
                "/api/v1/logs",
                params={
                    "from_time": (now - timedelta(days=32)).isoformat(),
                    "to_time": now.isoformat(),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert too_wide.status_code == 422

        deleted = asyncio.run(
            LogRetentionService(factory, retention_days=30, batch_size=1).run_once(now=now)
        )
        assert deleted == 1

        async def evidence() -> tuple[int, set[str], list[dict[str, object]]]:
            async with factory() as session:
                remaining = len(list((await session.scalars(select(LogEvent))).all()))
                events = list((await session.scalars(select(AuditEvent))).all())
                return (
                    remaining,
                    {item.action for item in events},
                    [json.loads(item.metadata_json) for item in events],
                )

        remaining, actions, metadata = asyncio.run(evidence())
        assert remaining == 1
        assert actions == {"logs.retention_policy_applied", "logs.retention_executed"}
        assert any(item.get("deleted_count") == 1 for item in metadata)
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
