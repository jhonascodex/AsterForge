from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import AuthContext, require_permissions
from app.db.models import CallDetailRecord, ChannelEventRecord
from app.db.session import get_session
from app.integrations.call_history import (
    CallHistoryAdapter,
    CdrSearch,
    PostgresCallHistoryAdapter,
)

router = APIRouter(prefix="/calls", tags=["call-history"])
MAX_HISTORY_WINDOW = timedelta(days=31)


class SourceCapability(BaseModel):
    status: Literal["available", "unconfigured"]
    source: Literal["postgres"] | None


class HistoryCapabilitiesResponse(BaseModel):
    cdr: SourceCapability
    cel: SourceCapability


class HistoryPage(BaseModel):
    availability: Literal["available", "unconfigured"]
    source: Literal["postgres"] | None
    total: int
    limit: int
    offset: int


class CdrResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    unique_id: str | None
    linked_id: str | None
    caller_number: str | None
    connected_number: str | None
    disposition: str | None
    started_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    billable_seconds: int | None


class CdrListResponse(HistoryPage):
    items: list[CdrResponse]


class CelEventResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    event_type: str
    event_at: datetime
    unique_id: str | None
    linked_id: str | None
    channel: str | None
    peer: str | None
    caller_number: str | None
    connected_number: str | None


class TimelineResponse(HistoryPage):
    server_id: UUID
    correlation_id: str
    items: list[CelEventResponse]


def get_call_history_adapter(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CallHistoryAdapter:
    return PostgresCallHistoryAdapter(
        session,
        cdr_enabled=settings.cdr_source == "postgres",
        cel_enabled=settings.cel_source == "postgres",
    )


def _capability(source: str | None) -> SourceCapability:
    return SourceCapability(
        status="available" if source is not None else "unconfigured",
        source="postgres" if source is not None else None,
    )


def _validate_window(started_from: datetime, started_to: datetime) -> tuple[datetime, datetime]:
    if started_from.tzinfo is None or started_to.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "timezone_required",
                "message": "History timestamps must include a timezone",
            },
        )
    start = started_from.astimezone(UTC)
    end = started_to.astimezone(UTC)
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_time_window",
                "message": "started_to must be later than started_from",
            },
        )
    if end - start > MAX_HISTORY_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "history_window_too_large",
                "message": "History time windows cannot exceed 31 days",
            },
        )
    return start, end


def _cdr_response(item: CallDetailRecord) -> CdrResponse:
    return CdrResponse.model_validate(item, from_attributes=True)


def _cel_response(item: ChannelEventRecord) -> CelEventResponse:
    return CelEventResponse.model_validate(item, from_attributes=True)


@router.get("/history/capabilities", response_model=HistoryCapabilitiesResponse)
async def get_history_capabilities(
    _actor: Annotated[AuthContext, require_permissions("call.read")],
    adapter: Annotated[CallHistoryAdapter, Depends(get_call_history_adapter)],
) -> HistoryCapabilitiesResponse:
    return HistoryCapabilitiesResponse(
        cdr=_capability(adapter.capabilities.cdr_source),
        cel=_capability(adapter.capabilities.cel_source),
    )


@router.get("/history", response_model=CdrListResponse)
async def search_call_history(
    _actor: Annotated[AuthContext, require_permissions("call.read")],
    adapter: Annotated[CallHistoryAdapter, Depends(get_call_history_adapter)],
    started_from: datetime,
    started_to: datetime,
    server_id: UUID | None = None,
    caller_number: str | None = Query(default=None, min_length=1, max_length=255),
    connected_number: str | None = Query(default=None, min_length=1, max_length=255),
    disposition: str | None = Query(default=None, min_length=1, max_length=100),
    correlation_id: str | None = Query(default=None, min_length=1, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> CdrListResponse:
    start, end = _validate_window(started_from, started_to)
    source = adapter.capabilities.cdr_source
    if source is None:
        return CdrListResponse(
            availability="unconfigured", source=None, items=[], total=0, limit=limit, offset=offset
        )
    result = await adapter.search_cdr(
        CdrSearch(
            started_from=start,
            started_to=end,
            server_id=server_id,
            caller_number=caller_number,
            connected_number=connected_number,
            disposition=disposition,
            correlation_id=correlation_id,
            limit=limit,
            offset=offset,
        )
    )
    return CdrListResponse(
        availability="available",
        source="postgres",
        items=[_cdr_response(item) for item in result.items],
        total=result.total,
        limit=limit,
        offset=offset,
    )


@router.get("/timeline", response_model=TimelineResponse)
async def get_call_timeline(
    _actor: Annotated[AuthContext, require_permissions("call.read")],
    adapter: Annotated[CallHistoryAdapter, Depends(get_call_history_adapter)],
    server_id: UUID,
    correlation_id: Annotated[str, Query(min_length=1, max_length=255)],
    started_from: Annotated[datetime, Query()],
    started_to: Annotated[datetime, Query()],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> TimelineResponse:
    start, end = _validate_window(started_from, started_to)
    source = adapter.capabilities.cel_source
    if source is None:
        return TimelineResponse(
            availability="unconfigured",
            source=None,
            server_id=server_id,
            correlation_id=correlation_id,
            items=[],
            total=0,
            limit=limit,
            offset=offset,
        )
    result = await adapter.list_cel(
        server_id=server_id,
        correlation_id=correlation_id,
        started_from=start,
        started_to=end,
        limit=limit,
        offset=offset,
    )
    return TimelineResponse(
        availability="available",
        source="postgres",
        server_id=server_id,
        correlation_id=correlation_id,
        items=[_cel_response(item) for item in result.items],
        total=result.total,
        limit=limit,
        offset=offset,
    )
