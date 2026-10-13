import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.host_telemetry import get_telemetry_read_model
from app.core.security import hash_password, new_session_token, token_digest
from app.db.base import Base
from app.db.models import (
    AsteriskServer,
    AuthSession,
    InventoryEnvironment,
    Permission,
    Profile,
    User,
)
from app.db.session import get_session
from app.integrations.host_telemetry import (
    FilesystemObservation,
    HostTelemetryObservation,
    HostTelemetryTarget,
    NetworkObservation,
    NodeExporterHostTelemetryProvider,
    RecordingGenerationObservation,
    VoipQualityObservation,
    fetch_node_exporter_metrics,
)
from app.main import app
from app.services.host_telemetry import (
    DatabaseTelemetrySampleStore,
    HostTelemetryManager,
    TelemetryFreshness,
    TelemetryReadModel,
)


def _metrics(
    cpu_idle: int,
    cpu_user: int,
    *,
    source_time: int = 1200,
    receive_bytes: int = 1000,
    transmit_bytes: int = 2000,
    recording_failures: int = 2,
) -> str:
    return f"""
node_cpu_seconds_total{{cpu="0",mode="idle"}} {cpu_idle}
node_cpu_seconds_total{{cpu="0",mode="user"}} {cpu_user}
node_memory_MemTotal_bytes 1000
node_memory_MemAvailable_bytes 400
node_memory_SwapTotal_bytes 200
node_memory_SwapFree_bytes 50
node_load1 1.5
node_load5 1.0
node_load15 0.5
node_time_seconds {source_time}
node_boot_time_seconds 200
node_filesystem_size_bytes{{device="/dev/sda",fstype="ext4",mountpoint="/recordings"}} 10000
node_filesystem_avail_bytes{{device="/dev/sda",fstype="ext4",mountpoint="/recordings"}} 2500
node_filesystem_files{{device="/dev/sda",fstype="ext4",mountpoint="/recordings"}} 100
node_filesystem_files_free{{device="/dev/sda",fstype="ext4",mountpoint="/recordings"}} 25
node_systemd_unit_state{{name="asterisk.service",state="active",type="simple"}} 1
node_systemd_unit_state{{name="asterisk.service",state="failed",type="simple"}} 0
node_network_receive_bytes_total{{device="eth0"}} {receive_bytes}
node_network_transmit_bytes_total{{device="eth0"}} {transmit_bytes}
node_network_receive_errs_total{{device="eth0"}} 3
node_network_transmit_errs_total{{device="eth0"}} 4
node_network_receive_drop_total{{device="eth0"}} 5
node_network_transmit_drop_total{{device="eth0"}} 6
node_network_receive_bytes_total{{device="lo"}} 999999
asterforge_recording_generation_failures_total {recording_failures}
asterforge_recording_generation_last_failure_timestamp_seconds 1100
"""


def _target(hostname: str = "pbx.test") -> HostTelemetryTarget:
    return HostTelemetryTarget(uuid4(), hostname, 9100, "/recordings")


def _observation(*, recording_available: int = 2500) -> HostTelemetryObservation:
    filesystem = FilesystemObservation(
        "/dev/sda", "/recordings", "ext4", 10000, recording_available, 100, 25
    )
    return HostTelemetryObservation(
        capabilities=frozenset(
            {
                "asterisk_service",
                "cpu",
                "filesystem",
                "load",
                "memory",
                "recording_storage",
                "swap",
                "uptime",
            }
        ),
        cpu_usage_percent=25,
        memory_total_bytes=1000,
        memory_available_bytes=400,
        swap_total_bytes=200,
        swap_free_bytes=50,
        load_1=1.5,
        load_5=1.0,
        load_15=0.5,
        uptime_seconds=1000,
        asterisk_service_state="active",
        filesystems=(filesystem,),
        recording_mountpoint="/recordings",
        recording_total_bytes=10000,
        recording_available_bytes=recording_available,
        recording_files=100,
        recording_files_free=25,
        network=NetworkObservation(
            interface_count=1,
            receive_bytes_total=1600,
            transmit_bytes_total=2600,
            receive_errors_total=3,
            transmit_errors_total=4,
            receive_dropped_total=5,
            transmit_dropped_total=6,
            receive_bytes_per_second=10,
            transmit_bytes_per_second=10,
            source="node_exporter:network",
            source_observed_at=datetime(2026, 9, 13, tzinfo=UTC),
        ),
        recording_generation=RecordingGenerationObservation(
            failures_total=3,
            failures_since_previous_sample=1,
            last_failure_at=datetime(2026, 9, 13, tzinfo=UTC),
            source=(
                "node_exporter:textfile:"
                "asterforge_recording_generation_failures_total"
            ),
            source_observed_at=datetime(2026, 9, 13, tzinfo=UTC),
        ),
        voip_quality=VoipQualityObservation(
            source="rtcp_probe:pbx-edge",
            source_observed_at=datetime(2026, 9, 13, tzinfo=UTC),
            jitter_ms=4.5,
            packet_loss_percent=0.2,
            rtt_ms=18,
            mos=4.3,
            codec="ulaw",
            rtp_receive_packets=400,
            rtp_transmit_packets=395,
        ),
    )


def test_node_exporter_provider_collects_supported_metrics_and_cpu_delta() -> None:
    responses = iter(
        (
            _metrics(100, 100),
            _metrics(
                110,
                130,
                source_time=1260,
                receive_bytes=1600,
                transmit_bytes=2600,
                recording_failures=3,
            ),
            _metrics(
                120,
                140,
                source_time=1320,
                receive_bytes=10,
                transmit_bytes=20,
                recording_failures=0,
            ),
        )
    )

    async def fetch(hostname: str, port: int, timeout_seconds: float) -> str:
        assert (hostname, port, timeout_seconds) == ("pbx.test", 9100, 2)
        return next(responses)

    provider = NodeExporterHostTelemetryProvider(timeout=2, fetcher=fetch)
    target = _target()

    async def exercise() -> None:
        first = await provider.collect(target)
        second = await provider.collect(target)
        after_counter_reset = await provider.collect(target)
        assert first.cpu_usage_percent is None
        assert first.network is not None
        assert first.network.receive_bytes_per_second is None
        assert first.recording_generation is not None
        assert first.recording_generation.failures_since_previous_sample is None
        assert second.cpu_usage_percent == 75
        assert second.memory_total_bytes == 1000
        assert second.swap_free_bytes == 50
        assert (second.load_1, second.load_5, second.load_15) == (1.5, 1.0, 0.5)
        assert second.uptime_seconds == 1060
        assert second.asterisk_service_state == "active"
        assert second.recording_total_bytes == 10000
        assert second.recording_available_bytes == 2500
        assert second.recording_files_free == 25
        assert second.network is not None
        assert second.network.interface_count == 1
        assert second.network.receive_bytes_total == 1600
        assert second.network.receive_errors_total == 3
        assert second.network.receive_bytes_per_second == 10
        assert second.network.transmit_bytes_per_second == 10
        assert second.network.source_observed_at == datetime.fromtimestamp(1260, UTC)
        assert second.recording_generation is not None
        assert second.recording_generation.failures_total == 3
        assert second.recording_generation.failures_since_previous_sample == 1
        assert second.recording_generation.last_failure_at == datetime.fromtimestamp(1100, UTC)
        assert second.voip_quality is None
        assert after_counter_reset.network is not None
        assert after_counter_reset.network.receive_bytes_per_second is None
        assert after_counter_reset.network.transmit_bytes_per_second is None
        assert after_counter_reset.recording_generation is not None
        assert after_counter_reset.recording_generation.failures_since_previous_sample is None

    asyncio.run(exercise())


def test_node_exporter_fetcher_reads_a_fragmented_response_to_eof() -> None:
    async def exercise() -> None:
        body = _metrics(100, 100).encode()

        async def respond(
            _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await _reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n")
            await writer.drain()
            for offset in range(0, len(body), 17):
                writer.write(body[offset : offset + 17])
                await writer.drain()
                await asyncio.sleep(0)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            received = await fetch_node_exporter_metrics("127.0.0.1", port, 2)
            assert received == body.decode()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_sample_store_forwards_only_explicit_alert_inputs() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    target = _target()
    received: list[dict[str, object]] = []

    class AlertSink:
        async def evaluate_host(
            self, server_id: object, observed_at: datetime, **values: object
        ) -> None:
            del observed_at
            assert server_id == target.server_id
            received.append(values)

    async def exercise() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        environment = InventoryEnvironment(
            id=uuid4(), code="ALERT-INPUT", display_name="Alert input", enabled=True
        )
        async with factory() as session:
            session.add(
                AsteriskServer(
                    id=target.server_id,
                    name="Alert input PBX",
                    name_key=f"alert-input-{uuid4()}",
                    hostname="alert-input.test",
                    environment=environment,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
        store = DatabaseTelemetrySampleStore(factory, AlertSink())  # type: ignore[arg-type]
        await store.save(target.server_id, _observation(), datetime.now(UTC))
        assert received == [
            {
                "cpu_usage_percent": 25,
                "memory_total_bytes": 1000,
                "memory_available_bytes": 400,
                "filesystems": (("/recordings", 10000, 2500),),
                "recording_total_bytes": 10000,
                "recording_available_bytes": 2500,
                "recording_failures_since_previous_sample": 1,
                "recording_failure_source": (
                    "node_exporter:textfile:"
                    "asterforge_recording_generation_failures_total"
                ),
            }
        ]

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(engine.dispose())


def test_node_exporter_provider_represents_unsupported_metrics_as_unavailable() -> None:
    async def fetch(_hostname: str, _port: int, _timeout: float) -> str:
        return 'node_load1 0\nnode_network_info{device="eth0"} 1\n'

    observation = asyncio.run(
        NodeExporterHostTelemetryProvider(timeout=1, fetcher=fetch).collect(_target())
    )
    assert observation.capabilities == frozenset({"load"})
    assert observation.cpu_usage_percent is None
    assert observation.memory_total_bytes is None
    assert observation.asterisk_service_state is None
    assert observation.recording_total_bytes is None
    assert observation.network is None
    assert observation.recording_generation is None
    assert observation.voip_quality is None


def test_recording_failure_signal_is_unavailable_without_reliable_counter() -> None:
    async def fetch(_hostname: str, _port: int, _timeout: float) -> str:
        return "\n".join(
            line
            for line in _metrics(100, 100).splitlines()
            if not line.startswith("asterforge_recording_generation_")
        )

    observation = asyncio.run(
        NodeExporterHostTelemetryProvider(timeout=1, fetcher=fetch).collect(_target())
    )
    assert "recording_storage" in observation.capabilities
    assert "recording_generation_failures" not in observation.capabilities
    assert observation.recording_generation is None


def test_failing_host_is_isolated_from_healthy_host() -> None:
    healthy_saved = asyncio.Event()
    published: list[tuple[str, object]] = []

    class Provider:
        async def collect(self, target: HostTelemetryTarget) -> HostTelemetryObservation:
            if target.hostname == "bad.test":
                raise OSError("synthetic failure")
            return _observation()

    class Store:
        async def save(
            self,
            server_id: object,
            observation: HostTelemetryObservation,
            observed_at: datetime,
        ) -> None:
            del server_id, observation, observed_at
            healthy_saved.set()

    async def exercise() -> None:
        async def publish(event_type: str, server_id: object, _observed_at: datetime) -> bool:
            published.append((event_type, server_id))
            return True

        model = TelemetryReadModel()
        manager = HostTelemetryManager(
            Provider(),
            Store(),
            model,
            poll_interval=60,
            operational_publisher=publish,  # type: ignore[arg-type]
        )
        bad = _target("bad.test")
        good = _target("good.test")
        await manager.update_targets([bad, good])
        await asyncio.wait_for(healthy_saved.wait(), timeout=1)
        await asyncio.sleep(0)
        assert (await model.get(good.server_id)).freshness is TelemetryFreshness.FRESH
        assert (await model.get(bad.server_id)).error_code == "host_telemetry_unavailable"
        assert ("host.telemetry.changed", good.server_id) in published
        assert ("host.telemetry.changed", bad.server_id) in published
        await manager.stop()

    asyncio.run(exercise())


def test_persisted_telemetry_api_is_authorized_and_exposes_recording_growth() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    server_id = uuid4()
    unavailable_server_id = uuid4()

    async def prepare() -> str:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        environment = InventoryEnvironment(
            id=uuid4(), code="DEV", display_name="Development", enabled=True
        )
        server = AsteriskServer(
            id=server_id,
            name="PBX",
            name_key="pbx",
            hostname="pbx.test",
            environment=environment,
            created_at=now,
            updated_at=now,
        )
        unavailable_server = AsteriskServer(
            id=unavailable_server_id,
            name="PBX without optional sources",
            name_key="pbx-without-optional-sources",
            hostname="pbx-unavailable.test",
            environment=environment,
            created_at=now,
            updated_at=now,
        )
        permission = Permission(id=uuid4(), code="server.read", description="read")
        profile = Profile(id=uuid4(), name="telemetry-reader", permissions=[permission])
        user = User(
            id=uuid4(),
            username="telemetry-reader",
            display_name="Telemetry Reader",
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
                    unavailable_server,
                    user,
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(token),
                        user=user,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()
        store = DatabaseTelemetrySampleStore(factory)
        await store.save(server_id, _observation(recording_available=3000), now)
        await store.save(
            server_id,
            _observation(recording_available=2500),
            now + timedelta(seconds=60),
        )
        await store.save(
            unavailable_server_id,
            replace(
                _observation(),
                network=None,
                recording_generation=None,
                voip_quality=None,
            ),
            now,
        )
        return token

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    token = asyncio.run(prepare())
    read_model = TelemetryReadModel()
    asyncio.run(read_model.observed(server_id, now + timedelta(seconds=60)))
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_telemetry_read_model] = lambda: read_model
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/v1/host/servers/{server_id}/telemetry").status_code == 401
            response = client.get(
                f"/api/v1/host/servers/{server_id}/telemetry",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["freshness"] == "fresh"
            assert body["sample"]["recording_storage"]["used_bytes"] == 7500
            assert body["sample"]["recording_storage"]["growth_bytes"] == 500
            assert body["sample"]["recording_storage"]["growth_period_seconds"] == 60
            assert body["sample"]["network"]["interface_count"] == 1
            assert body["sample"]["network"]["receive_bytes_per_second"] == 10
            assert body["sample"]["network"]["source"] == "node_exporter:network"
            assert body["sample"]["recording_generation"]["failure_detected"] is True
            assert body["sample"]["recording_generation"]["failures_total"] == 3
            assert body["sample"]["voip_quality"] == {
                "jitter_ms": 4.5,
                "packet_loss_percent": 0.2,
                "rtt_ms": 18.0,
                "mos": 4.3,
                "codec": "ulaw",
                "rtp_receive_packets": 400,
                "rtp_transmit_packets": 395,
                "source": "rtcp_probe:pbx-edge",
                "source_observed_at": "2026-09-13T00:00:00Z",
            }
            unavailable_response = client.get(
                f"/api/v1/host/servers/{unavailable_server_id}/telemetry",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert unavailable_response.status_code == 200
            unavailable_sample = unavailable_response.json()["sample"]
            assert unavailable_sample["network"] is None
            assert unavailable_sample["recording_generation"] is None
            assert unavailable_sample["voip_quality"] is None
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
