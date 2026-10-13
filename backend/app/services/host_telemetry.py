import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AsteriskServer, HostTelemetrySample
from app.integrations.host_telemetry import (
    HostTelemetryObservation,
    HostTelemetryProvider,
    HostTelemetryTarget,
)
from app.realtime.operational import OperationalPublisher

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


class TelemetryFreshness(StrEnum):
    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True)
class TelemetryStatus:
    server_id: UUID
    freshness: TelemetryFreshness = TelemetryFreshness.UNKNOWN
    observed_at: datetime | None = None
    stale_since: datetime | None = None
    error_code: str | None = None


class TelemetryReadModel:
    def __init__(self) -> None:
        self._values: dict[UUID, TelemetryStatus] = {}
        self._lock = asyncio.Lock()

    async def get(self, server_id: UUID) -> TelemetryStatus:
        async with self._lock:
            return self._values.get(server_id, TelemetryStatus(server_id))

    async def observed(self, server_id: UUID, observed_at: datetime) -> None:
        async with self._lock:
            self._values[server_id] = TelemetryStatus(
                server_id=server_id,
                freshness=TelemetryFreshness.FRESH,
                observed_at=observed_at,
            )

    async def failed(self, server_id: UUID, error_code: str) -> None:
        async with self._lock:
            current = self._values.get(server_id, TelemetryStatus(server_id))
            self._values[server_id] = replace(
                current,
                freshness=(
                    TelemetryFreshness.STALE
                    if current.observed_at is not None
                    else TelemetryFreshness.UNKNOWN
                ),
                stale_since=current.stale_since or datetime.now(UTC),
                error_code=error_code,
            )

    async def remove(self, server_id: UUID) -> None:
        async with self._lock:
            self._values.pop(server_id, None)


class TelemetrySampleStore(Protocol):
    async def save(
        self,
        server_id: UUID,
        observation: HostTelemetryObservation,
        observed_at: datetime,
    ) -> None: ...


class HostAlertSink(Protocol):
    async def evaluate_host(
        self,
        server_id: UUID,
        observed_at: datetime,
        *,
        cpu_usage_percent: float | None,
        memory_total_bytes: int | None,
        memory_available_bytes: int | None,
        filesystems: tuple[tuple[str, int, int], ...],
        recording_total_bytes: int | None,
        recording_available_bytes: int | None,
        recording_failures_since_previous_sample: int | None,
        recording_failure_source: str | None,
    ) -> None: ...


class DatabaseTelemetrySampleStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        alert_sink: HostAlertSink | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._alert_sink = alert_sink

    async def save(
        self,
        server_id: UUID,
        observation: HostTelemetryObservation,
        observed_at: datetime,
    ) -> None:
        filesystems = [
            {
                "device": item.device,
                "mountpoint": item.mountpoint,
                "filesystem_type": item.filesystem_type,
                "size_bytes": item.size_bytes,
                "available_bytes": item.available_bytes,
                "files": item.files,
                "files_free": item.files_free,
            }
            for item in observation.filesystems
        ]
        network = observation.network
        recording_generation = observation.recording_generation
        voip_quality = observation.voip_quality
        capabilities = set(observation.capabilities)
        if network is not None:
            capabilities.add("network")
        if recording_generation is not None:
            capabilities.add("recording_generation_failures")
        if voip_quality is not None:
            capabilities.add("voip_quality")
        async with self._session_factory() as session:
            session.add(
                HostTelemetrySample(
                    id=uuid4(),
                    server_id=server_id,
                    provider="node_exporter",
                    capabilities_json=json.dumps(sorted(capabilities)),
                    cpu_usage_percent=observation.cpu_usage_percent,
                    memory_total_bytes=observation.memory_total_bytes,
                    memory_available_bytes=observation.memory_available_bytes,
                    swap_total_bytes=observation.swap_total_bytes,
                    swap_free_bytes=observation.swap_free_bytes,
                    load_1=observation.load_1,
                    load_5=observation.load_5,
                    load_15=observation.load_15,
                    uptime_seconds=observation.uptime_seconds,
                    asterisk_service_state=observation.asterisk_service_state,
                    filesystems_json=json.dumps(filesystems, separators=(",", ":")),
                    recording_mountpoint=observation.recording_mountpoint,
                    recording_total_bytes=observation.recording_total_bytes,
                    recording_available_bytes=observation.recording_available_bytes,
                    recording_files=observation.recording_files,
                    recording_files_free=observation.recording_files_free,
                    network_interface_count=(network.interface_count if network else None),
                    network_receive_bytes_total=(
                        network.receive_bytes_total if network else None
                    ),
                    network_transmit_bytes_total=(
                        network.transmit_bytes_total if network else None
                    ),
                    network_receive_errors_total=(
                        network.receive_errors_total if network else None
                    ),
                    network_transmit_errors_total=(
                        network.transmit_errors_total if network else None
                    ),
                    network_receive_dropped_total=(
                        network.receive_dropped_total if network else None
                    ),
                    network_transmit_dropped_total=(
                        network.transmit_dropped_total if network else None
                    ),
                    network_receive_bytes_per_second=(
                        network.receive_bytes_per_second if network else None
                    ),
                    network_transmit_bytes_per_second=(
                        network.transmit_bytes_per_second if network else None
                    ),
                    network_source=network.source if network else None,
                    network_source_observed_at=(
                        network.source_observed_at if network else None
                    ),
                    recording_failures_total=(
                        recording_generation.failures_total
                        if recording_generation
                        else None
                    ),
                    recording_failures_since_previous_sample=(
                        recording_generation.failures_since_previous_sample
                        if recording_generation
                        else None
                    ),
                    recording_last_failure_at=(
                        recording_generation.last_failure_at
                        if recording_generation
                        else None
                    ),
                    recording_failure_source=(
                        recording_generation.source if recording_generation else None
                    ),
                    recording_failure_source_observed_at=(
                        recording_generation.source_observed_at
                        if recording_generation
                        else None
                    ),
                    voip_jitter_ms=voip_quality.jitter_ms if voip_quality else None,
                    voip_packet_loss_percent=(
                        voip_quality.packet_loss_percent if voip_quality else None
                    ),
                    voip_rtt_ms=voip_quality.rtt_ms if voip_quality else None,
                    voip_mos=voip_quality.mos if voip_quality else None,
                    voip_codec=voip_quality.codec if voip_quality else None,
                    voip_rtp_receive_packets=(
                        voip_quality.rtp_receive_packets if voip_quality else None
                    ),
                    voip_rtp_transmit_packets=(
                        voip_quality.rtp_transmit_packets if voip_quality else None
                    ),
                    voip_source=voip_quality.source if voip_quality else None,
                    voip_source_observed_at=(
                        voip_quality.source_observed_at if voip_quality else None
                    ),
                    observed_at=observed_at,
                )
            )
            await session.commit()
        if self._alert_sink is not None:
            try:
                await self._alert_sink.evaluate_host(
                    server_id,
                    observed_at,
                    cpu_usage_percent=observation.cpu_usage_percent,
                    memory_total_bytes=observation.memory_total_bytes,
                    memory_available_bytes=observation.memory_available_bytes,
                    filesystems=tuple(
                        (item.mountpoint, item.size_bytes, item.available_bytes)
                        for item in observation.filesystems
                        if item.size_bytes is not None
                        and item.available_bytes is not None
                    ),
                    recording_total_bytes=observation.recording_total_bytes,
                    recording_available_bytes=observation.recording_available_bytes,
                    recording_failures_since_previous_sample=(
                        recording_generation.failures_since_previous_sample
                        if recording_generation
                        else None
                    ),
                    recording_failure_source=(
                        recording_generation.source if recording_generation else None
                    ),
                )
            except SQLAlchemyError:
                logger.warning(
                    "Host alert evaluation failed", extra={"server_id": str(server_id)}
                )


class HostTelemetryManager:
    def __init__(
        self,
        provider: HostTelemetryProvider,
        store: TelemetrySampleStore,
        read_model: TelemetryReadModel,
        *,
        poll_interval: float,
        operational_publisher: OperationalPublisher | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._provider = provider
        self._store = store
        self.read_model = read_model
        self._poll_interval = poll_interval
        self._operational_publisher = operational_publisher
        self._sleep = sleep
        self._workers: dict[UUID, tuple[HostTelemetryTarget, asyncio.Task[None]]] = {}

    async def update_targets(self, targets: Sequence[HostTelemetryTarget]) -> None:
        desired = {target.server_id: target for target in targets}
        for server_id, (current, task) in list(self._workers.items()):
            if desired.get(server_id) != current or task.done():
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._workers.pop(server_id)
                if server_id not in desired:
                    await self.read_model.remove(server_id)
        for server_id, target in desired.items():
            if server_id not in self._workers:
                task = asyncio.create_task(
                    self._run_target(target), name=f"host-telemetry-{server_id}"
                )
                self._workers[server_id] = (target, task)

    async def _run_target(self, target: HostTelemetryTarget) -> None:
        while True:
            try:
                observation = await self._provider.collect(target)
                observed_at = datetime.now(UTC)
                await self._store.save(target.server_id, observation, observed_at)
                await self.read_model.observed(target.server_id, observed_at)
                if self._operational_publisher is not None:
                    await self._operational_publisher(
                        "host.telemetry.changed", target.server_id, observed_at
                    )
            except asyncio.CancelledError:
                raise
            except (OSError, TimeoutError, ValueError, SQLAlchemyError):
                failed_at = datetime.now(UTC)
                await self.read_model.failed(target.server_id, "host_telemetry_unavailable")
                if self._operational_publisher is not None:
                    await self._operational_publisher(
                        "host.telemetry.changed", target.server_id, failed_at
                    )
                logger.warning(
                    "Host telemetry target unavailable",
                    extra={"server_id": str(target.server_id)},
                )
            await self._sleep(self._poll_interval)

    async def stop(self) -> None:
        tasks = [task for _, task in self._workers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()


class HostTelemetrySupervisor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        manager: HostTelemetryManager,
        *,
        port: int,
        recording_mountpoint: str,
        refresh_interval: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._manager = manager
        self._port = port
        self._recording_mountpoint = recording_mountpoint
        self._refresh_interval = refresh_interval
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    async def _load_targets(self) -> list[HostTelemetryTarget]:
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
        return [
            HostTelemetryTarget(
                server_id=server.id,
                hostname=server.hostname,
                port=self._port,
                recording_mountpoint=self._recording_mountpoint,
            )
            for server in servers
        ]

    async def run(self) -> None:
        while True:
            try:
                await self._manager.update_targets(await self._load_targets())
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, SQLAlchemyError):
                logger.warning("Host telemetry inventory refresh failed")
            await self._sleep(self._refresh_interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="host-telemetry-supervisor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._manager.stop()
