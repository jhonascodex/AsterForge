from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, as_utc, require_permissions
from app.db.models import AsteriskServer, InventoryEnvironment, LogEvent
from app.db.session import get_session
from app.services.logs import LogSourceReadModel

router = APIRouter(prefix="/logs", tags=["logs"])
log_source_read_model = LogSourceReadModel()
MAX_RANGE = timedelta(days=31)


class LogEventResponse(BaseModel):
    id: UUID
    server_id: UUID
    server_name: str
    environment: str
    source: str
    level: str
    module: str | None
    message: str
    endpoint: str | None
    trunk: str | None
    unique_id: str | None
    linked_id: str | None
    occurred_at: datetime


class LogSearchResponse(BaseModel):
    items: list[LogEventResponse]
    offset: int
    limit: int
    has_more: bool
    from_time: datetime
    to_time: datetime


class LogSourceStatusResponse(BaseModel):
    server_id: UUID
    source: str
    available: bool
    checked_at: datetime
    error_code: str | None


def get_log_source_read_model() -> LogSourceReadModel:
    return log_source_read_model


def _bounded_range(
    from_time: datetime | None, to_time: datetime | None
) -> tuple[datetime, datetime]:
    end = as_utc(to_time) if to_time is not None else datetime.now(UTC)
    start = as_utc(from_time) if from_time is not None else end - timedelta(hours=24)
    if start >= end or end - start > MAX_RANGE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_log_time_range",
                "message": "Log time range must be positive and no longer than 31 days",
            },
        )
    return start, end


@router.get("", response_model=LogSearchResponse)
async def search_logs(
    _actor: Annotated[AuthContext, require_permissions("log.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    environment: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    server_id: UUID | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    level: Annotated[
        str | None, Query(pattern="^(DEBUG|NOTICE|INFO|WARNING|ERROR|CRITICAL)$")
    ] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    module: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    endpoint: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    trunk: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    unique_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    linked_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> LogSearchResponse:
    start, end = _bounded_range(from_time, to_time)
    statement: Select[tuple[LogEvent, str, str]] = (
        select(LogEvent, AsteriskServer.name, InventoryEnvironment.code)
        .join(AsteriskServer, AsteriskServer.id == LogEvent.server_id)
        .join(InventoryEnvironment, InventoryEnvironment.id == AsteriskServer.environment_id)
        .where(LogEvent.occurred_at >= start, LogEvent.occurred_at < end)
    )
    exact_filters = {
        LogEvent.server_id: server_id,
        LogEvent.level: level,
        LogEvent.source: source,
        LogEvent.module: module,
        LogEvent.endpoint: endpoint,
        LogEvent.trunk: trunk,
        LogEvent.unique_id: unique_id,
        LogEvent.linked_id: linked_id,
    }
    for column, value in exact_filters.items():
        if value is not None:
            statement = statement.where(column == value)
    if environment is not None:
        statement = statement.where(InventoryEnvironment.code == environment.upper())
    if q is not None:
        statement = statement.where(LogEvent.message.icontains(q, autoescape=True))
    rows = (
        await session.execute(
            statement.order_by(LogEvent.occurred_at.desc(), LogEvent.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    items = [
        LogEventResponse(
            id=item.id,
            server_id=item.server_id,
            server_name=server_name,
            environment=environment_code,
            source=item.source,
            level=item.level,
            module=item.module,
            message=item.message,
            endpoint=item.endpoint,
            trunk=item.trunk,
            unique_id=item.unique_id,
            linked_id=item.linked_id,
            occurred_at=item.occurred_at,
        )
        for item, server_name, environment_code in rows[:limit]
    ]
    return LogSearchResponse(
        items=items,
        offset=offset,
        limit=limit,
        has_more=len(rows) > limit,
        from_time=start,
        to_time=end,
    )


@router.get("/sources", response_model=list[LogSourceStatusResponse])
async def list_log_sources(
    _actor: Annotated[AuthContext, require_permissions("log.read")],
    read_model: Annotated[LogSourceReadModel, Depends(get_log_source_read_model)],
) -> list[LogSourceStatusResponse]:
    return [
        LogSourceStatusResponse.model_validate(item.__dict__)
        for item in await read_model.list()
    ]
