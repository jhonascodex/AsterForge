from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.administration import router as administration_router
from app.api.alerts import alert_service
from app.api.alerts import router as alerts_router
from app.api.asterisk_health import health_read_model
from app.api.asterisk_health import router as asterisk_health_router
from app.api.auth import router as auth_router
from app.api.call_history import router as call_history_router
from app.api.health import router as health_router
from app.api.host_telemetry import router as host_telemetry_router
from app.api.host_telemetry import telemetry_read_model
from app.api.identity import router as identity_router
from app.api.inventory import router as inventory_router
from app.api.live_calls import call_read_model, call_realtime_hub
from app.api.live_calls import router as live_calls_router
from app.api.logs import log_source_read_model
from app.api.logs import router as logs_router
from app.api.pjsip_monitoring import router as pjsip_monitoring_router
from app.api.realtime import router as realtime_router
from app.api.reporting import router as reporting_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory, engine
from app.integrations.ami import StreamAmiConnector
from app.integrations.host_telemetry import NodeExporterHostTelemetryProvider
from app.integrations.logs import AsteriskFileLogAdapter
from app.realtime.operational import operational_realtime_hub
from app.services.ami_monitoring import (
    AmiConnectionManager,
    BackoffPolicy,
    MonitoringSupervisor,
)
from app.services.bootstrap import ensure_initial_administrator
from app.services.host_telemetry import (
    DatabaseTelemetrySampleStore,
    HostTelemetryManager,
    HostTelemetrySupervisor,
)
from app.services.logs import (
    DatabaseLogStore,
    LogIngestionManager,
    LogRetentionService,
    LogSupervisor,
)
from app.services.pjsip_monitoring import DatabasePjsipSnapshotStore

settings = get_settings()
configure_logging(settings.log_level)

ami_manager = AmiConnectionManager(
    StreamAmiConnector(
        connect_timeout=settings.ami_connect_timeout_seconds,
        action_timeout=settings.ami_action_timeout_seconds,
    ),
    health_read_model,
    poll_interval=settings.ami_poll_interval_seconds,
    backoff=BackoffPolicy(
        minimum=settings.ami_reconnect_min_seconds,
        maximum=settings.ami_reconnect_max_seconds,
    ),
    snapshot_store=DatabasePjsipSnapshotStore(
        SessionFactory,
        alert_service,
        operational_publisher=operational_realtime_hub.publish,
    ),
    call_read_model=call_read_model,
    call_publisher=call_realtime_hub.publish,
    alert_sink=alert_service,
    operational_publisher=operational_realtime_hub.publish,
)
ami_supervisor = MonitoringSupervisor(
    SessionFactory,
    ami_manager,
    refresh_interval=settings.ami_inventory_refresh_seconds,
)
host_telemetry_manager = HostTelemetryManager(
    NodeExporterHostTelemetryProvider(timeout=settings.host_telemetry_timeout_seconds),
    DatabaseTelemetrySampleStore(SessionFactory, alert_service),
    telemetry_read_model,
    poll_interval=settings.host_telemetry_poll_interval_seconds,
    operational_publisher=operational_realtime_hub.publish,
)
host_telemetry_supervisor = HostTelemetrySupervisor(
    SessionFactory,
    host_telemetry_manager,
    port=settings.host_telemetry_port,
    recording_mountpoint=settings.host_telemetry_recording_mountpoint,
    refresh_interval=settings.host_telemetry_inventory_refresh_seconds,
)
log_manager = LogIngestionManager(
    AsteriskFileLogAdapter(
        settings.log_source_root,
        max_bytes=settings.log_read_max_bytes,
        max_lines=settings.log_read_max_lines,
    ),
    DatabaseLogStore(SessionFactory),
    log_source_read_model,
)
log_supervisor = LogSupervisor(
    SessionFactory,
    log_manager,
    LogRetentionService(
        SessionFactory,
        retention_days=settings.log_retention_days,
        batch_size=settings.log_retention_batch_size,
    ),
    poll_interval=settings.log_poll_interval_seconds,
    inventory_refresh=settings.log_inventory_refresh_seconds,
    retention_interval=settings.log_retention_interval_seconds,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if (
        settings.bootstrap_admin_username is not None
        and settings.bootstrap_admin_password is not None
    ):
        async with SessionFactory() as session:
            await ensure_initial_administrator(
                session,
                username=settings.bootstrap_admin_username,
                temporary_password=settings.bootstrap_admin_password.get_secret_value(),
            )
    if settings.ami_monitoring_enabled:
        ami_supervisor.start()
    if settings.host_telemetry_enabled:
        host_telemetry_supervisor.start()
    if settings.log_ingestion_enabled:
        log_supervisor.start()
    try:
        yield
    finally:
        await ami_supervisor.stop()
        await host_telemetry_supervisor.stop()
        await log_supervisor.stop()
        await engine.dispose()


app = FastAPI(title="AsterForge API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin).rstrip("/") for origin in settings.cors_origins],
    allow_credentials=True,
    allow_methods=["DELETE", "GET", "POST", "PATCH"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)
app.include_router(health_router, prefix="/api/v1")
app.include_router(host_telemetry_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(administration_router, prefix="/api/v1")
app.include_router(identity_router, prefix="/api/v1")
app.include_router(inventory_router, prefix="/api/v1")
app.include_router(asterisk_health_router, prefix="/api/v1")
app.include_router(pjsip_monitoring_router, prefix="/api/v1")
app.include_router(live_calls_router, prefix="/api/v1")
app.include_router(realtime_router, prefix="/api/v1")
app.include_router(call_history_router, prefix="/api/v1")
app.include_router(logs_router, prefix="/api/v1")
app.include_router(alerts_router, prefix="/api/v1")
app.include_router(reporting_router, prefix="/api/v1")
