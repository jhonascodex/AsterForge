import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.credentials import CredentialConfigurationError, decrypt_credential
from app.db.models import AsteriskServer
from app.integrations.ami import AmiConnector, AmiCoreSnapshot, AmiSession, AmiTarget
from app.realtime.operational import OperationalPublisher
from app.services.call_observability import CallReadModel, ServerCalls
from app.services.pjsip_monitoring import PjsipSnapshotStore

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]
CallPublisher = Callable[[ServerCalls], Awaitable[None]]


class AsteriskAlertSink(Protocol):
    async def evaluate_asterisk(
        self,
        server_id: UUID,
        available: bool,
        observed_at: datetime,
        *,
        error_code: str | None = None,
    ) -> UUID | None: ...


class ConnectionState(StrEnum):
    UNKNOWN = "unknown"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    MISCONFIGURED = "misconfigured"


class FreshnessState(StrEnum):
    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True)
class ServerHealth:
    server_id: UUID
    connection_state: ConnectionState = ConnectionState.UNKNOWN
    freshness: FreshnessState = FreshnessState.UNKNOWN
    observed_at: datetime | None = None
    stale_since: datetime | None = None
    connected_at: datetime | None = None
    last_attempt_at: datetime | None = None
    consecutive_failures: int = 0
    reconnect_in_seconds: float | None = None
    error_code: str | None = None
    core: AmiCoreSnapshot | None = None


class HealthReadModel:
    def __init__(self) -> None:
        self._values: dict[UUID, ServerHealth] = {}
        self._lock = asyncio.Lock()

    async def get(self, server_id: UUID) -> ServerHealth:
        async with self._lock:
            return self._values.get(server_id, ServerHealth(server_id=server_id))

    async def list(self, server_ids: Sequence[UUID]) -> list[ServerHealth]:
        async with self._lock:
            return [self._values.get(item, ServerHealth(server_id=item)) for item in server_ids]

    async def connecting(self, server_id: UUID, now: datetime) -> None:
        async with self._lock:
            current = self._values.get(server_id, ServerHealth(server_id=server_id))
            freshness = FreshnessState.STALE if current.observed_at else FreshnessState.UNKNOWN
            self._values[server_id] = replace(
                current,
                connection_state=ConnectionState.CONNECTING,
                freshness=freshness,
                stale_since=current.stale_since or (now if current.observed_at else None),
                last_attempt_at=now,
                reconnect_in_seconds=None,
                error_code=None,
            )

    async def connected(self, server_id: UUID, now: datetime) -> None:
        async with self._lock:
            current = self._values.get(server_id, ServerHealth(server_id=server_id))
            self._values[server_id] = replace(
                current,
                connection_state=ConnectionState.CONNECTED,
                connected_at=now,
                reconnect_in_seconds=None,
                error_code=None,
            )

    async def observed(self, server_id: UUID, core: AmiCoreSnapshot, now: datetime) -> None:
        async with self._lock:
            current = self._values.get(server_id, ServerHealth(server_id=server_id))
            self._values[server_id] = replace(
                current,
                connection_state=ConnectionState.CONNECTED,
                freshness=FreshnessState.FRESH,
                observed_at=now,
                stale_since=None,
                consecutive_failures=0,
                reconnect_in_seconds=None,
                error_code=None,
                core=core,
            )

    async def disconnected(
        self, server_id: UUID, now: datetime, *, failures: int, delay: float, error_code: str
    ) -> None:
        async with self._lock:
            current = self._values.get(server_id, ServerHealth(server_id=server_id))
            self._values[server_id] = replace(
                current,
                connection_state=ConnectionState.DISCONNECTED,
                freshness=(
                    FreshnessState.STALE if current.observed_at else FreshnessState.UNKNOWN
                ),
                stale_since=current.stale_since or (now if current.observed_at else None),
                consecutive_failures=failures,
                reconnect_in_seconds=delay,
                error_code=error_code,
            )

    async def misconfigured(self, server_id: UUID, error_code: str) -> None:
        async with self._lock:
            current = self._values.get(server_id, ServerHealth(server_id=server_id))
            now = datetime.now(UTC)
            self._values[server_id] = replace(
                current,
                connection_state=ConnectionState.MISCONFIGURED,
                freshness=(
                    FreshnessState.STALE if current.observed_at else FreshnessState.UNKNOWN
                ),
                stale_since=current.stale_since or (now if current.observed_at else None),
                reconnect_in_seconds=None,
                error_code=error_code,
            )

    async def remove(self, server_id: UUID) -> None:
        async with self._lock:
            self._values.pop(server_id, None)


@dataclass(frozen=True)
class BackoffPolicy:
    minimum: float
    maximum: float
    jitter_ratio: float = 0.2

    def delay(self, failures: int, *, random_value: float | None = None) -> float:
        base = min(self.maximum, self.minimum * (2 ** max(0, failures - 1)))
        value = random.random() if random_value is None else random_value
        factor = (1 - self.jitter_ratio) + (2 * self.jitter_ratio * value)
        return float(min(self.maximum, max(0, base * factor)))


class AmiConnectionManager:
    def __init__(
        self,
        connector: AmiConnector,
        read_model: HealthReadModel,
        *,
        poll_interval: float,
        backoff: BackoffPolicy,
        snapshot_store: PjsipSnapshotStore | None = None,
        call_read_model: CallReadModel | None = None,
        call_publisher: CallPublisher | None = None,
        alert_sink: AsteriskAlertSink | None = None,
        operational_publisher: OperationalPublisher | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._connector = connector
        self.read_model = read_model
        self._poll_interval = poll_interval
        self._backoff = backoff
        self._snapshot_store = snapshot_store
        self._call_read_model = call_read_model
        self._call_publisher = call_publisher
        self._alert_sink = alert_sink
        self._operational_publisher = operational_publisher
        self._sleep = sleep
        self._workers: dict[UUID, tuple[AmiTarget, asyncio.Task[None]]] = {}

    async def update_targets(self, targets: Sequence[AmiTarget]) -> None:
        desired = {target.server_id: target for target in targets}
        for server_id, (current, task) in list(self._workers.items()):
            if desired.get(server_id) != current or task.done():
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._workers.pop(server_id)
                if server_id not in desired:
                    await self.read_model.remove(server_id)
                    if self._call_read_model is not None:
                        await self._call_read_model.remove(server_id)
        for server_id, target in desired.items():
            if server_id not in self._workers:
                task = asyncio.create_task(
                    self._run_target(target), name=f"ami-monitor-{server_id}"
                )
                self._workers[server_id] = (target, task)

    async def _run_target(self, target: AmiTarget) -> None:
        failures = 0
        while True:
            session: AmiSession | None = None
            reconnect_delay: float | None = None
            try:
                connecting_at = datetime.now(UTC)
                await self.read_model.connecting(target.server_id, connecting_at)
                await self._publish_health(target.server_id, connecting_at)
                session = await self._connector.connect(target)
                connected_at = datetime.now(UTC)
                await self.read_model.connected(target.server_id, connected_at)
                await self._publish_health(target.server_id, connected_at)
                while True:
                    core = await session.core_snapshot()
                    pjsip = await session.pjsip_snapshot()
                    live_channels = (
                        await session.live_channels_snapshot()
                        if self._call_read_model is not None
                        else ()
                    )
                    observed_at = datetime.now(UTC)
                    if self._snapshot_store is not None:
                        await self._snapshot_store.reconcile(
                            target.server_id, pjsip, observed_at
                        )
                    if self._call_read_model is not None:
                        calls = await self._call_read_model.reconcile(
                            target.server_id, live_channels, observed_at
                        )
                        events = await session.drain_call_events()
                        event_calls = await self._call_read_model.apply_events(
                            target.server_id, events, observed_at
                        )
                        if event_calls is not None:
                            calls = event_calls
                        if self._call_publisher is not None:
                            await self._call_publisher(calls)
                    await self.read_model.observed(target.server_id, core, observed_at)
                    await self._publish_health(target.server_id, observed_at)
                    await self._evaluate_alert(target.server_id, True, observed_at)
                    failures = 0
                    if self._call_read_model is None:
                        await self._sleep(self._poll_interval)
                    else:
                        deadline = asyncio.get_running_loop().time() + self._poll_interval
                        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
                            events = await session.wait_call_events(min(1.0, remaining))
                            event_calls = await self._call_read_model.apply_events(
                                target.server_id, events, datetime.now(UTC)
                            )
                            if event_calls is not None and self._call_publisher is not None:
                                await self._call_publisher(event_calls)
                    await session.ping()
            except asyncio.CancelledError:
                raise
            except (OSError, TimeoutError, RuntimeError, SQLAlchemyError):
                failures += 1
                reconnect_delay = self._backoff.delay(failures)
                failed_at = datetime.now(UTC)
                await self.read_model.disconnected(
                    target.server_id,
                    failed_at,
                    failures=failures,
                    delay=reconnect_delay,
                    error_code="ami_unavailable",
                )
                await self._publish_health(target.server_id, failed_at)
                await self._evaluate_alert(
                    target.server_id, False, failed_at, error_code="ami_unavailable"
                )
                if self._snapshot_store is not None:
                    try:
                        await self._snapshot_store.mark_stale(target.server_id)
                    except SQLAlchemyError:
                        logger.warning(
                            "PJSIP stale-state persistence failed",
                            extra={"server_id": str(target.server_id)},
                        )
                if self._call_read_model is not None:
                    calls = await self._call_read_model.mark_stale(
                        target.server_id, datetime.now(UTC)
                    )
                    if self._call_publisher is not None:
                        await self._call_publisher(calls)
                logger.warning("AMI target unavailable", extra={"server_id": str(target.server_id)})
            finally:
                if session is not None:
                    await session.close()
            if reconnect_delay is not None:
                await self._sleep(reconnect_delay)

    async def _evaluate_alert(
        self,
        server_id: UUID,
        available: bool,
        observed_at: datetime,
        *,
        error_code: str | None = None,
    ) -> None:
        if self._alert_sink is None:
            return
        try:
            await self._alert_sink.evaluate_asterisk(
                server_id, available, observed_at, error_code=error_code
            )
        except SQLAlchemyError:
            logger.warning(
                "Asterisk alert evaluation failed", extra={"server_id": str(server_id)}
            )

    async def _publish_health(self, server_id: UUID, observed_at: datetime) -> None:
        if self._operational_publisher is not None:
            await self._operational_publisher(
                "asterisk.health.changed", server_id, observed_at
            )

    async def report_misconfigured(self, server_id: UUID, error_code: str) -> None:
        now = datetime.now(UTC)
        await self.read_model.misconfigured(server_id, error_code)
        await self._publish_health(server_id, now)
        await self._evaluate_alert(server_id, False, now, error_code=error_code)

    async def stop(self) -> None:
        tasks = [task for _, task in self._workers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()


class MonitoringSupervisor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        manager: AmiConnectionManager,
        *,
        refresh_interval: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._manager = manager
        self._refresh_interval = refresh_interval
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    async def _load_targets(self) -> tuple[list[AmiTarget], list[tuple[UUID, str]]]:
        async with self._session_factory() as session:
            servers = list(
                (
                    await session.scalars(
                        select(AsteriskServer).where(
                            AsteriskServer.monitoring_enabled.is_(True),
                            AsteriskServer.archived_at.is_(None),
                        )
                    )
                ).all()
            )
        targets: list[AmiTarget] = []
        unavailable: list[tuple[UUID, str]] = []
        for server in servers:
            if not server.ami_username_encrypted or not server.ami_secret_encrypted:
                unavailable.append((server.id, "credentials_missing"))
                continue
            try:
                username = decrypt_credential(server.ami_username_encrypted)
                secret = decrypt_credential(server.ami_secret_encrypted)
            except CredentialConfigurationError:
                unavailable.append((server.id, "credentials_unavailable"))
                continue
            targets.append(
                AmiTarget(
                    server_id=server.id,
                    hostname=server.hostname,
                    port=server.ami_port,
                    tls=server.ami_tls,
                    username=username,
                    secret=secret,
                )
            )
        return targets, unavailable

    async def run(self) -> None:
        while True:
            try:
                targets, unavailable = await self._load_targets()
                await self._manager.update_targets(targets)
                for server_id, code in unavailable:
                    await self._manager.report_misconfigured(server_id, code)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, SQLAlchemyError):
                logger.warning("AMI inventory refresh failed")
            await self._sleep(self._refresh_interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="ami-monitoring-supervisor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._manager.stop()
