import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, as_utc, require_permissions
from app.db.models import AsteriskServer, HostTelemetrySample
from app.db.session import get_session
from app.services.host_telemetry import TelemetryFreshness, TelemetryReadModel

router = APIRouter(prefix="/host", tags=["host-telemetry"])
telemetry_read_model = TelemetryReadModel()


class FilesystemResponse(BaseModel):
    device: str
    mountpoint: str
    filesystem_type: str
    size_bytes: int | None
    available_bytes: int | None
    files: int | None
    files_free: int | None


class RecordingStorageResponse(BaseModel):
    mountpoint: str
    total_bytes: int | None
    available_bytes: int | None
    used_bytes: int | None
    files: int | None
    files_free: int | None
    growth_bytes: int | None
    growth_period_seconds: float | None


class NetworkResponse(BaseModel):
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


class RecordingGenerationResponse(BaseModel):
    failures_total: int
    failures_since_previous_sample: int | None
    failure_detected: bool | None
    last_failure_at: datetime | None
    source: str
    source_observed_at: datetime | None


class VoipQualityResponse(BaseModel):
    jitter_ms: float | None
    packet_loss_percent: float | None
    rtt_ms: float | None
    mos: float | None
    codec: str | None
    rtp_receive_packets: int | None
    rtp_transmit_packets: int | None
    source: str
    source_observed_at: datetime


class HostTelemetrySampleResponse(BaseModel):
    provider: str
    capabilities: list[str]
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
    filesystems: list[FilesystemResponse]
    recording_storage: RecordingStorageResponse
    network: NetworkResponse | None
    recording_generation: RecordingGenerationResponse | None
    voip_quality: VoipQualityResponse | None
    observed_at: datetime


class HostTelemetryResponse(BaseModel):
    server_id: UUID
    freshness: TelemetryFreshness
    stale_since: datetime | None
    error_code: str | None
    sample: HostTelemetrySampleResponse | None


def get_telemetry_read_model() -> TelemetryReadModel:
    return telemetry_read_model


def _used(sample: HostTelemetrySample) -> int | None:
    if sample.recording_total_bytes is None or sample.recording_available_bytes is None:
        return None
    return max(0, sample.recording_total_bytes - sample.recording_available_bytes)


def _sample_response(
    current: HostTelemetrySample,
    previous: HostTelemetrySample | None,
) -> HostTelemetrySampleResponse:
    current_used = _used(current)
    previous_used = _used(previous) if previous is not None else None
    growth = (
        current_used - previous_used
        if current_used is not None and previous_used is not None
        else None
    )
    period = (
        (as_utc(current.observed_at) - as_utc(previous.observed_at)).total_seconds()
        if previous is not None
        else None
    )
    filesystems = [
        FilesystemResponse.model_validate(value)
        for value in json.loads(current.filesystems_json)
    ]
    return HostTelemetrySampleResponse(
        provider=current.provider,
        capabilities=json.loads(current.capabilities_json),
        cpu_usage_percent=current.cpu_usage_percent,
        memory_total_bytes=current.memory_total_bytes,
        memory_available_bytes=current.memory_available_bytes,
        swap_total_bytes=current.swap_total_bytes,
        swap_free_bytes=current.swap_free_bytes,
        load_1=current.load_1,
        load_5=current.load_5,
        load_15=current.load_15,
        uptime_seconds=current.uptime_seconds,
        asterisk_service_state=current.asterisk_service_state,
        filesystems=filesystems,
        recording_storage=RecordingStorageResponse(
            mountpoint=current.recording_mountpoint,
            total_bytes=current.recording_total_bytes,
            available_bytes=current.recording_available_bytes,
            used_bytes=current_used,
            files=current.recording_files,
            files_free=current.recording_files_free,
            growth_bytes=growth,
            growth_period_seconds=period,
        ),
        network=(
            NetworkResponse(
                interface_count=current.network_interface_count,
                receive_bytes_total=current.network_receive_bytes_total,
                transmit_bytes_total=current.network_transmit_bytes_total,
                receive_errors_total=current.network_receive_errors_total,
                transmit_errors_total=current.network_transmit_errors_total,
                receive_dropped_total=current.network_receive_dropped_total,
                transmit_dropped_total=current.network_transmit_dropped_total,
                receive_bytes_per_second=current.network_receive_bytes_per_second,
                transmit_bytes_per_second=current.network_transmit_bytes_per_second,
                source=current.network_source,
                source_observed_at=(
                    as_utc(current.network_source_observed_at)
                    if current.network_source_observed_at is not None
                    else None
                ),
            )
            if current.network_interface_count is not None
            and current.network_source is not None
            else None
        ),
        recording_generation=(
            RecordingGenerationResponse(
                failures_total=current.recording_failures_total,
                failures_since_previous_sample=(
                    current.recording_failures_since_previous_sample
                ),
                failure_detected=(
                    current.recording_failures_since_previous_sample > 0
                    if current.recording_failures_since_previous_sample is not None
                    else None
                ),
                last_failure_at=(
                    as_utc(current.recording_last_failure_at)
                    if current.recording_last_failure_at is not None
                    else None
                ),
                source=current.recording_failure_source,
                source_observed_at=(
                    as_utc(current.recording_failure_source_observed_at)
                    if current.recording_failure_source_observed_at is not None
                    else None
                ),
            )
            if current.recording_failures_total is not None
            and current.recording_failure_source is not None
            else None
        ),
        voip_quality=(
            VoipQualityResponse(
                jitter_ms=current.voip_jitter_ms,
                packet_loss_percent=current.voip_packet_loss_percent,
                rtt_ms=current.voip_rtt_ms,
                mos=current.voip_mos,
                codec=current.voip_codec,
                rtp_receive_packets=current.voip_rtp_receive_packets,
                rtp_transmit_packets=current.voip_rtp_transmit_packets,
                source=current.voip_source,
                source_observed_at=as_utc(current.voip_source_observed_at),
            )
            if current.voip_source is not None
            and current.voip_source_observed_at is not None
            else None
        ),
        observed_at=current.observed_at,
    )


async def _response(
    session: AsyncSession,
    read_model: TelemetryReadModel,
    server_id: UUID,
) -> HostTelemetryResponse:
    samples = list(
        (
            await session.scalars(
                select(HostTelemetrySample)
                .where(HostTelemetrySample.server_id == server_id)
                .order_by(HostTelemetrySample.observed_at.desc())
                .limit(2)
            )
        ).all()
    )
    current = samples[0] if samples else None
    runtime = await read_model.get(server_id)
    return HostTelemetryResponse(
        server_id=server_id,
        freshness=runtime.freshness,
        stale_since=runtime.stale_since,
        error_code=runtime.error_code,
        sample=_sample_response(current, samples[1] if len(samples) > 1 else None)
        if current is not None
        else None,
    )


@router.get("/telemetry", response_model=list[HostTelemetryResponse])
async def list_host_telemetry(
    _actor: Annotated[AuthContext, require_permissions("server.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    read_model: Annotated[TelemetryReadModel, Depends(get_telemetry_read_model)],
) -> list[HostTelemetryResponse]:
    server_ids = list(
        (
            await session.scalars(
                select(AsteriskServer.id).where(AsteriskServer.archived_at.is_(None))
            )
        ).all()
    )
    return [await _response(session, read_model, server_id) for server_id in server_ids]


@router.get("/servers/{server_id}/telemetry", response_model=HostTelemetryResponse)
async def get_host_telemetry(
    server_id: UUID,
    _actor: Annotated[AuthContext, require_permissions("server.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    read_model: Annotated[TelemetryReadModel, Depends(get_telemetry_read_model)],
) -> HostTelemetryResponse:
    server = await session.get(AsteriskServer, server_id)
    if server is None or server.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "server_not_found", "message": "Server not found"},
        )
    return await _response(session, read_model, server_id)
