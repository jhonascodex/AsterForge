import asyncio
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

MAX_METRICS_BYTES = 2 * 1024 * 1024
_SAMPLE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)(?:\s+\d+)?$'
)
_LABEL = re.compile(r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:\\.|[^"\\])*)"')
MetricsFetcher = Callable[[str, int, float], Awaitable[str]]
_NETWORK_METRICS = frozenset(
    {
        "node_network_receive_bytes_total",
        "node_network_transmit_bytes_total",
        "node_network_receive_errs_total",
        "node_network_transmit_errs_total",
        "node_network_receive_drop_total",
        "node_network_transmit_drop_total",
    }
)


@dataclass(frozen=True)
class HostTelemetryTarget:
    server_id: UUID
    hostname: str
    port: int
    recording_mountpoint: str


@dataclass(frozen=True)
class FilesystemObservation:
    device: str
    mountpoint: str
    filesystem_type: str
    size_bytes: int | None
    available_bytes: int | None
    files: int | None
    files_free: int | None


@dataclass(frozen=True)
class NetworkObservation:
    interface_count: int
    receive_bytes_total: int | None
    transmit_bytes_total: int | None
    receive_errors_total: int | None
    transmit_errors_total: int | None
    receive_dropped_total: int | None
    transmit_dropped_total: int | None
    receive_bytes_per_second: float | None
    transmit_bytes_per_second: float | None
    source: str
    source_observed_at: datetime | None


@dataclass(frozen=True)
class RecordingGenerationObservation:
    failures_total: int
    failures_since_previous_sample: int | None
    last_failure_at: datetime | None
    source: str
    source_observed_at: datetime | None


@dataclass(frozen=True)
class VoipQualityObservation:
    source: str
    source_observed_at: datetime
    jitter_ms: float | None = None
    packet_loss_percent: float | None = None
    rtt_ms: float | None = None
    mos: float | None = None
    codec: str | None = None
    rtp_receive_packets: int | None = None
    rtp_transmit_packets: int | None = None


@dataclass(frozen=True)
class HostTelemetryObservation:
    capabilities: frozenset[str]
    cpu_usage_percent: float | None
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None
    load_1: float | None
    load_5: float | None
    load_15: float | None
    uptime_seconds: float | None
    asterisk_service_state: str | None
    filesystems: tuple[FilesystemObservation, ...]
    recording_mountpoint: str
    recording_total_bytes: int | None
    recording_available_bytes: int | None
    recording_files: int | None
    recording_files_free: int | None
    network: NetworkObservation | None = None
    recording_generation: RecordingGenerationObservation | None = None
    voip_quality: VoipQualityObservation | None = None


class HostTelemetryProvider(Protocol):
    async def collect(self, target: HostTelemetryTarget) -> HostTelemetryObservation: ...


def _labels(raw: str | None) -> dict[str, str]:
    if raw is None:
        return {}
    labels: dict[str, str] = {}
    for match in _LABEL.finditer(raw):
        value = bytes(match.group("value"), "utf-8").decode("unicode_escape")
        labels[match.group("name")] = value
    return labels


def _parse_metrics(text: str) -> list[tuple[str, Mapping[str, str], float]]:
    samples: list[tuple[str, Mapping[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.fullmatch(line.strip())
        if match is None:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if math.isfinite(value):
            samples.append((match.group("name"), _labels(match.group("labels")), value))
    return samples


def _nonnegative_int(value: float | None) -> int | None:
    return int(value) if value is not None and value >= 0 else None


def _timestamp(value: float | None) -> datetime | None:
    if value is None or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _network_total(
    values: Mapping[str, Mapping[str, float]], metric: str
) -> int | None:
    matched = [metrics[metric] for metrics in values.values() if metric in metrics]
    if not matched or any(value < 0 for value in matched):
        return None
    return int(sum(matched))


async def fetch_node_exporter_metrics(
    hostname: str, port: int, timeout_seconds: float
) -> str:
    async def request() -> str:
        reader, writer = await asyncio.open_connection(hostname, port)
        try:
            request_bytes = (
                f"GET /metrics HTTP/1.0\r\nHost: {hostname}:{port}\r\n"
                "Accept: text/plain\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(request_bytes)
            await writer.drain()
            payload = bytearray()
            while chunk := await reader.read(64 * 1024):
                payload.extend(chunk)
                if len(payload) > MAX_METRICS_BYTES:
                    raise ValueError("node_exporter response exceeds size limit")
        finally:
            writer.close()
            await writer.wait_closed()
        header, separator, body = bytes(payload).partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("node_exporter returned a malformed HTTP response")
        status_line = header.split(b"\r\n", 1)[0]
        if not status_line.startswith(b"HTTP/") or b" 200 " not in status_line:
            raise OSError("node_exporter returned a non-success HTTP status")
        return body.decode("utf-8", errors="replace")

    async with asyncio.timeout(timeout_seconds):
        return await request()


class NodeExporterHostTelemetryProvider:
    """Read-only node_exporter provider with a fixed endpoint and bounded response."""

    def __init__(self, *, timeout: float, fetcher: MetricsFetcher = fetch_node_exporter_metrics):
        self._timeout = timeout
        self._fetcher = fetcher
        self._previous_cpu: dict[UUID, tuple[float, float]] = {}
        self._previous_network: dict[UUID, tuple[float, int | None, int | None]] = {}
        self._previous_recording_failures: dict[UUID, int] = {}

    async def collect(self, target: HostTelemetryTarget) -> HostTelemetryObservation:
        samples = _parse_metrics(
            await self._fetcher(target.hostname, target.port, self._timeout)
        )
        scalar: dict[str, float] = {}
        cpu_total = 0.0
        cpu_idle = 0.0
        filesystem_values: dict[tuple[str, str, str], dict[str, float]] = {}
        service_states: dict[str, float] = {}
        network_values: dict[str, dict[str, float]] = {}
        for name, labels, value in samples:
            if not labels and name not in scalar:
                scalar[name] = value
            if name == "node_cpu_seconds_total":
                cpu_total += value
                if labels.get("mode") in {"idle", "iowait"}:
                    cpu_idle += value
            elif name.startswith("node_filesystem_"):
                key = (
                    labels.get("device", ""),
                    labels.get("mountpoint", ""),
                    labels.get("fstype", ""),
                )
                filesystem_values.setdefault(key, {})[name] = value
            elif (
                name == "node_systemd_unit_state"
                and labels.get("name") == "asterisk.service"
            ):
                service_states[labels.get("state", "unknown")] = value
            elif name in _NETWORK_METRICS and labels.get("device") != "lo":
                device = labels.get("device")
                if device:
                    network_values.setdefault(device, {})[name] = value

        capabilities: set[str] = set()
        cpu_usage: float | None = None
        if cpu_total > 0:
            capabilities.add("cpu")
            previous = self._previous_cpu.get(target.server_id)
            self._previous_cpu[target.server_id] = (cpu_total, cpu_idle)
            if previous is not None:
                total_delta = cpu_total - previous[0]
                idle_delta = cpu_idle - previous[1]
                if total_delta > 0 and 0 <= idle_delta <= total_delta:
                    cpu_usage = 100 * (1 - idle_delta / total_delta)

        memory_total = _nonnegative_int(scalar.get("node_memory_MemTotal_bytes"))
        memory_available = _nonnegative_int(scalar.get("node_memory_MemAvailable_bytes"))
        if memory_total is not None or memory_available is not None:
            capabilities.add("memory")
        swap_total = _nonnegative_int(scalar.get("node_memory_SwapTotal_bytes"))
        swap_free = _nonnegative_int(scalar.get("node_memory_SwapFree_bytes"))
        if swap_total is not None or swap_free is not None:
            capabilities.add("swap")
        loads = tuple(scalar.get(f"node_load{period}") for period in (1, 5, 15))
        if any(value is not None for value in loads):
            capabilities.add("load")
        now = scalar.get("node_time_seconds")
        boot = scalar.get("node_boot_time_seconds")
        uptime = now - boot if now is not None and boot is not None and now >= boot else None
        if uptime is not None:
            capabilities.add("uptime")

        filesystems = tuple(
            FilesystemObservation(
                device=device,
                mountpoint=mountpoint,
                filesystem_type=filesystem_type,
                size_bytes=_nonnegative_int(values.get("node_filesystem_size_bytes")),
                available_bytes=_nonnegative_int(values.get("node_filesystem_avail_bytes")),
                files=_nonnegative_int(values.get("node_filesystem_files")),
                files_free=_nonnegative_int(values.get("node_filesystem_files_free")),
            )
            for (device, mountpoint, filesystem_type), values in sorted(filesystem_values.items())
            if mountpoint
        )
        if filesystems:
            capabilities.add("filesystem")
        active_states = [state for state, value in service_states.items() if value == 1]
        service_state = active_states[0] if len(active_states) == 1 else None
        if service_states:
            capabilities.add("asterisk_service")
        recording = next(
            (item for item in filesystems if item.mountpoint == target.recording_mountpoint),
            None,
        )
        if recording is not None:
            capabilities.add("recording_storage")

        source_time = scalar.get("node_time_seconds")
        receive_total = _network_total(
            network_values, "node_network_receive_bytes_total"
        )
        transmit_total = _network_total(
            network_values, "node_network_transmit_bytes_total"
        )
        network: NetworkObservation | None = None
        if network_values:
            capabilities.add("network")
            receive_rate: float | None = None
            transmit_rate: float | None = None
            if source_time is not None:
                previous_network = self._previous_network.get(target.server_id)
                self._previous_network[target.server_id] = (
                    source_time,
                    receive_total,
                    transmit_total,
                )
                if previous_network is not None and source_time > previous_network[0]:
                    interval = source_time - previous_network[0]
                    if (
                        receive_total is not None
                        and previous_network[1] is not None
                        and receive_total >= previous_network[1]
                    ):
                        receive_rate = (receive_total - previous_network[1]) / interval
                    if (
                        transmit_total is not None
                        and previous_network[2] is not None
                        and transmit_total >= previous_network[2]
                    ):
                        transmit_rate = (transmit_total - previous_network[2]) / interval
            network = NetworkObservation(
                interface_count=len(network_values),
                receive_bytes_total=receive_total,
                transmit_bytes_total=transmit_total,
                receive_errors_total=_network_total(
                    network_values, "node_network_receive_errs_total"
                ),
                transmit_errors_total=_network_total(
                    network_values, "node_network_transmit_errs_total"
                ),
                receive_dropped_total=_network_total(
                    network_values, "node_network_receive_drop_total"
                ),
                transmit_dropped_total=_network_total(
                    network_values, "node_network_transmit_drop_total"
                ),
                receive_bytes_per_second=receive_rate,
                transmit_bytes_per_second=transmit_rate,
                source="node_exporter:network",
                source_observed_at=_timestamp(source_time),
            )

        failures_total = _nonnegative_int(
            scalar.get("asterforge_recording_generation_failures_total")
        )
        recording_generation: RecordingGenerationObservation | None = None
        if failures_total is not None:
            capabilities.add("recording_generation_failures")
            previous_failures = self._previous_recording_failures.get(target.server_id)
            self._previous_recording_failures[target.server_id] = failures_total
            failures_delta = (
                failures_total - previous_failures
                if previous_failures is not None and failures_total >= previous_failures
                else None
            )
            recording_generation = RecordingGenerationObservation(
                failures_total=failures_total,
                failures_since_previous_sample=failures_delta,
                last_failure_at=_timestamp(
                    scalar.get(
                        "asterforge_recording_generation_last_failure_timestamp_seconds"
                    )
                ),
                source=(
                    "node_exporter:textfile:"
                    "asterforge_recording_generation_failures_total"
                ),
                source_observed_at=_timestamp(source_time),
            )

        return HostTelemetryObservation(
            capabilities=frozenset(capabilities),
            cpu_usage_percent=cpu_usage,
            memory_total_bytes=memory_total,
            memory_available_bytes=memory_available,
            swap_total_bytes=swap_total,
            swap_free_bytes=swap_free,
            load_1=loads[0],
            load_5=loads[1],
            load_15=loads[2],
            uptime_seconds=uptime,
            asterisk_service_state=service_state,
            filesystems=filesystems,
            recording_mountpoint=target.recording_mountpoint,
            recording_total_bytes=recording.size_bytes if recording else None,
            recording_available_bytes=recording.available_bytes if recording else None,
            recording_files=recording.files if recording else None,
            recording_files_free=recording.files_free if recording else None,
            network=network,
            recording_generation=recording_generation,
        )
