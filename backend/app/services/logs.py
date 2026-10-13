import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AsteriskServer,
    AuditEvent,
    LogEvent,
    LogSourceCursor,
    RetentionPolicy,
)
from app.integrations.logs import LogSourceAdapter, LogTarget

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class LogSourceStatus:
    server_id: UUID
    source: str
    available: bool
    checked_at: datetime
    error_code: str | None = None


class LogSourceReadModel:
    def __init__(self) -> None:
        self._values: dict[tuple[UUID, str], LogSourceStatus] = {}
        self._lock = asyncio.Lock()

    async def update(self, status: LogSourceStatus) -> None:
        async with self._lock:
            self._values[(status.server_id, status.source)] = status

    async def list(self) -> list[LogSourceStatus]:
        async with self._lock:
            return list(self._values.values())


class DatabaseLogStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def cursor(self, server_id: UUID, source: str) -> int:
        async with self._session_factory() as session:
            value = await session.scalar(
                select(LogSourceCursor.byte_offset).where(
                    LogSourceCursor.server_id == server_id,
                    LogSourceCursor.source == source,
                )
            )
            return value or 0

    async def save(self, server_id: UUID, adapter: LogSourceAdapter, offset: int) -> int:
        batch = await adapter.read(LogTarget(server_id), offset)
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            cursor = await session.scalar(
                select(LogSourceCursor).where(
                    LogSourceCursor.server_id == server_id,
                    LogSourceCursor.source == adapter.source,
                )
            )
            if cursor is None:
                cursor = LogSourceCursor(
                    id=uuid4(), server_id=server_id, source=adapter.source, updated_at=now
                )
                session.add(cursor)
            for record in batch.records:
                session.add(
                    LogEvent(
                        id=uuid4(), server_id=server_id, ingested_at=now, **record.__dict__
                    )
                )
            cursor.byte_offset = batch.next_offset
            cursor.updated_at = now
            await session.commit()
        return len(batch.records)


class LogIngestionManager:
    def __init__(
        self,
        adapter: LogSourceAdapter,
        store: DatabaseLogStore,
        read_model: LogSourceReadModel,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self.read_model = read_model

    async def collect(self, server_id: UUID) -> None:
        checked_at = datetime.now(UTC)
        try:
            offset = await self._store.cursor(server_id, self._adapter.source)
            await self._store.save(server_id, self._adapter, offset)
            status = LogSourceStatus(server_id, self._adapter.source, True, checked_at)
        except asyncio.CancelledError:
            raise
        except (OSError, UnicodeError, ValueError, SQLAlchemyError):
            status = LogSourceStatus(
                server_id, self._adapter.source, False, checked_at, "log_source_unavailable"
            )
            logger.warning(
                "Log source unavailable",
                extra={"server_id": str(server_id), "source": self._adapter.source},
            )
        await self.read_model.update(status)


class LogRetentionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention_days: int,
        batch_size: int,
    ) -> None:
        self._session_factory = session_factory
        self._retention_days = retention_days
        self._batch_size = batch_size

    async def run_once(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        async with self._session_factory() as session:
            policy = await session.get(RetentionPolicy, "operational_logs")
            retention_days = policy.retention_days if policy is not None else self._retention_days
            batch_size = policy.batch_size if policy is not None else self._batch_size
            cutoff = current - timedelta(days=retention_days)
            ids = list(
                (
                    await session.scalars(
                        select(LogEvent.id)
                        .where(LogEvent.occurred_at < cutoff)
                        .order_by(LogEvent.occurred_at)
                        .limit(batch_size)
                    )
                ).all()
            )
            if ids:
                await session.execute(delete(LogEvent).where(LogEvent.id.in_(ids)))
            metadata = {
                "retention_days": retention_days,
                "batch_size": batch_size,
                "cutoff": cutoff.isoformat(),
            }
            session.add_all(
                [
                    AuditEvent(
                        id=uuid4(),
                        occurred_at=current,
                        action="logs.retention_policy_applied",
                        target_type="retention_policy",
                        target_id="operational_logs",
                        metadata_json=json.dumps(metadata, separators=(",", ":")),
                        source="retention_worker",
                    ),
                    AuditEvent(
                        id=uuid4(),
                        occurred_at=current,
                        action="logs.retention_executed",
                        target_type="log_events",
                        target_id="operational_logs",
                        metadata_json=json.dumps(
                            {**metadata, "deleted_count": len(ids)}, separators=(",", ":")
                        ),
                        source="retention_worker",
                    ),
                ]
            )
            await session.commit()
        return len(ids)


class LogSupervisor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        manager: LogIngestionManager,
        retention: LogRetentionService,
        *,
        poll_interval: float,
        inventory_refresh: float,
        retention_interval: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._manager = manager
        self._retention = retention
        self._poll_interval = poll_interval
        self._inventory_refresh = inventory_refresh
        self._retention_interval = retention_interval
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._targets: Sequence[UUID] = ()
        self._last_inventory: datetime | None = None

    async def _server_ids(self) -> Sequence[UUID]:
        async with self._session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(AsteriskServer.id).where(
                            AsteriskServer.monitoring_enabled.is_(True),
                            AsteriskServer.archived_at.is_(None),
                        )
                    )
                ).all()
            )

    async def run(self) -> None:
        last_retention: datetime | None = None
        while True:
            started = datetime.now(UTC)
            try:
                inventory_due = self._last_inventory is None or (
                    started - self._last_inventory
                ).total_seconds() >= self._inventory_refresh
                if inventory_due:
                    self._targets = await self._server_ids()
                    self._last_inventory = started
                for server_id in self._targets:
                    await self._manager.collect(server_id)
                retention_due = last_retention is None or (
                    started - last_retention
                ).total_seconds() >= self._retention_interval
                if retention_due:
                    await self._retention.run_once(now=started)
                    last_retention = started
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, SQLAlchemyError):
                logger.warning("Log ingestion inventory or retention cycle failed")
            await self._sleep(self._poll_interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="log-ingestion-supervisor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
