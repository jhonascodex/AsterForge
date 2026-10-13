from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, require_permissions
from app.db.models import AsteriskServer
from app.db.session import get_session
from app.integrations.ami import AmiCoreSnapshot
from app.services.ami_monitoring import (
    ConnectionState,
    FreshnessState,
    HealthReadModel,
    ServerHealth,
)

router = APIRouter(prefix="/asterisk", tags=["asterisk-monitoring"])
health_read_model = HealthReadModel()


class CoreHealthResponse(BaseModel):
    system_name: str | None
    asterisk_version: str | None
    started_at_source: str | None
    reloaded_at_source: str | None
    current_calls: int | None
    max_calls: int | None


class AsteriskHealthResponse(BaseModel):
    server_id: UUID
    connection_state: ConnectionState
    freshness: FreshnessState
    observed_at: datetime | None
    stale_since: datetime | None
    connected_at: datetime | None
    last_attempt_at: datetime | None
    consecutive_failures: int
    reconnect_in_seconds: float | None
    error_code: str | None
    core: CoreHealthResponse | None


def get_health_read_model() -> HealthReadModel:
    return health_read_model


def _response(value: ServerHealth) -> AsteriskHealthResponse:
    core = (
        CoreHealthResponse(
            system_name=value.core.system_name,
            asterisk_version=value.core.asterisk_version,
            started_at_source=value.core.started_at_source,
            reloaded_at_source=value.core.reloaded_at_source,
            current_calls=value.core.current_calls,
            max_calls=value.core.max_calls,
        )
        if isinstance(value.core, AmiCoreSnapshot)
        else None
    )
    return AsteriskHealthResponse(
        server_id=value.server_id,
        connection_state=value.connection_state,
        freshness=value.freshness,
        observed_at=value.observed_at,
        stale_since=value.stale_since,
        connected_at=value.connected_at,
        last_attempt_at=value.last_attempt_at,
        consecutive_failures=value.consecutive_failures,
        reconnect_in_seconds=value.reconnect_in_seconds,
        error_code=value.error_code,
        core=core,
    )


@router.get("/health", response_model=list[AsteriskHealthResponse])
async def list_asterisk_health(
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    read_model: Annotated[HealthReadModel, Depends(get_health_read_model)],
) -> list[AsteriskHealthResponse]:
    server_ids = list(
        (
            await session.scalars(
                select(AsteriskServer.id).where(AsteriskServer.archived_at.is_(None))
            )
        ).all()
    )
    return [_response(value) for value in await read_model.list(server_ids)]


@router.get("/servers/{server_id}/health", response_model=AsteriskHealthResponse)
async def get_asterisk_health(
    server_id: UUID,
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    read_model: Annotated[HealthReadModel, Depends(get_health_read_model)],
) -> AsteriskHealthResponse:
    server = await session.get(AsteriskServer, server_id)
    if server is None or server.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "server_not_found", "message": "Server not found"},
        )
    return _response(await read_model.get(server_id))
