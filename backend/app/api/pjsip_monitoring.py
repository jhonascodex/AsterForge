from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.security import AuthContext, as_utc, require_permissions
from app.db.models import (
    PjsipContact,
    PjsipEndpoint,
    PjsipRegistration,
    PjsipTransport,
    SipTrunk,
    TrunkStateTransition,
)
from app.db.session import get_session
from app.integrations.ami import ContactState, EndpointState, RegistrationState

router = APIRouter(prefix="/asterisk", tags=["pjsip-monitoring"])


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class EndpointResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    state: EndpointState
    raw_state: str | None
    transport: str | None
    aors: str | None
    active_channels: int | None
    contacts_count: int | None
    observed_at: datetime
    stale: bool


class EndpointListResponse(Page):
    items: list[EndpointResponse]


class ContactResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    endpoint_name: str | None
    aor: str | None
    uri: str | None
    address: str | None
    port: int | None
    user_agent: str | None
    state: ContactState
    raw_state: str | None
    roundtrip_usec: int | None
    observed_at: datetime
    stale: bool


class ContactListResponse(Page):
    items: list[ContactResponse]


class RegistrationResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    direction: Literal["inbound", "outbound"]
    endpoint_name: str | None
    server_uri: str | None
    client_uri: str | None
    transport: str | None
    state: RegistrationState
    raw_state: str | None
    observed_at: datetime
    stale: bool


class RegistrationListResponse(Page):
    items: list[RegistrationResponse]


class TransportResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    protocol: str | None
    bind: str | None
    observed_at: datetime
    stale: bool


class TransportListResponse(Page):
    items: list[TransportResponse]


class TrunkResponse(BaseModel):
    id: UUID
    server_id: UUID
    source_id: str
    label: str
    provider: str | None
    state: RegistrationState
    raw_state: str | None
    endpoint_name: str | None
    server_uri: str | None
    last_state_change_at: datetime
    duration_in_state_seconds: int
    active_calls: int | None
    failure_indicator: bool | None
    observed_at: datetime
    stale: bool


class TrunkListResponse(Page):
    items: list[TrunkResponse]


class TrunkTransitionResponse(BaseModel):
    id: UUID
    trunk_id: UUID
    from_state: RegistrationState | None
    to_state: RegistrationState
    raw_state: str | None
    occurred_at: datetime


class TrunkTransitionListResponse(Page):
    items: list[TrunkTransitionResponse]


def _endpoint_response(item: PjsipEndpoint) -> EndpointResponse:
    return EndpointResponse(
        id=item.id,
        server_id=item.server_id,
        source_id=item.source_id,
        state=EndpointState(item.state),
        raw_state=item.raw_state,
        transport=item.transport,
        aors=item.aors,
        active_channels=item.active_channels,
        contacts_count=item.contacts_count,
        observed_at=item.observed_at,
        stale=item.stale,
    )


def _contact_response(item: PjsipContact) -> ContactResponse:
    return ContactResponse(
        id=item.id,
        server_id=item.server_id,
        source_id=item.source_id,
        endpoint_name=item.endpoint_name,
        aor=item.aor,
        uri=item.uri,
        address=item.address,
        port=item.port,
        user_agent=item.user_agent,
        state=ContactState(item.state),
        raw_state=item.raw_state,
        roundtrip_usec=item.roundtrip_usec,
        observed_at=item.observed_at,
        stale=item.stale,
    )


def _registration_response(item: PjsipRegistration) -> RegistrationResponse:
    direction: Literal["inbound", "outbound"] = (
        "inbound" if item.direction == "inbound" else "outbound"
    )
    return RegistrationResponse(
        id=item.id,
        server_id=item.server_id,
        source_id=item.source_id,
        direction=direction,
        endpoint_name=item.endpoint_name,
        server_uri=item.server_uri,
        client_uri=item.client_uri,
        transport=item.transport,
        state=RegistrationState(item.state),
        raw_state=item.raw_state,
        observed_at=item.observed_at,
        stale=item.stale,
    )


def _transport_response(item: PjsipTransport) -> TransportResponse:
    return TransportResponse(
        id=item.id,
        server_id=item.server_id,
        source_id=item.source_id,
        protocol=item.protocol,
        bind=item.bind,
        observed_at=item.observed_at,
        stale=item.stale,
    )


def _trunk_response(item: SipTrunk) -> TrunkResponse:
    now = datetime.now(UTC)
    changed_at = as_utc(item.last_state_change_at)
    return TrunkResponse(
        id=item.id,
        server_id=item.server_id,
        source_id=item.source_id,
        label=item.label,
        provider=item.provider,
        state=RegistrationState(item.state),
        raw_state=item.raw_state,
        endpoint_name=item.endpoint_name,
        server_uri=item.server_uri,
        last_state_change_at=item.last_state_change_at,
        duration_in_state_seconds=max(0, int((now - changed_at).total_seconds())),
        active_calls=None,
        failure_indicator=(
            True
            if item.state == RegistrationState.REJECTED.value
            else False
            if item.state == RegistrationState.REGISTERED.value
            else None
        ),
        observed_at=item.observed_at,
        stale=item.stale,
    )


@router.get("/endpoints", response_model=EndpointListResponse)
async def list_endpoints(
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: UUID | None = None,
    state_filter: Annotated[EndpointState | None, Query(alias="state")] = None,
    stale: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EndpointListResponse:
    filters: list[ColumnElement[bool]] = []
    if server_id is not None:
        filters.append(PjsipEndpoint.server_id == server_id)
    if state_filter is not None:
        filters.append(PjsipEndpoint.state == state_filter.value)
    if stale is not None:
        filters.append(PjsipEndpoint.stale.is_(stale))
    statement = select(PjsipEndpoint).where(*filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(PjsipEndpoint.server_id, PjsipEndpoint.source_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return EndpointListResponse(
        items=[_endpoint_response(item) for item in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/contacts", response_model=ContactListResponse)
async def list_contacts(
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: UUID | None = None,
    endpoint: str | None = Query(default=None, min_length=1, max_length=255),
    state_filter: Annotated[ContactState | None, Query(alias="state")] = None,
    stale: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ContactListResponse:
    filters: list[ColumnElement[bool]] = []
    if server_id is not None:
        filters.append(PjsipContact.server_id == server_id)
    if endpoint is not None:
        filters.append(PjsipContact.endpoint_name == endpoint)
    if state_filter is not None:
        filters.append(PjsipContact.state == state_filter.value)
    if stale is not None:
        filters.append(PjsipContact.stale.is_(stale))
    statement = select(PjsipContact).where(*filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(PjsipContact.server_id, PjsipContact.source_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return ContactListResponse(
        items=[_contact_response(item) for item in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/registrations", response_model=RegistrationListResponse)
async def list_registrations(
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: UUID | None = None,
    direction: Literal["inbound", "outbound"] | None = None,
    state_filter: Annotated[RegistrationState | None, Query(alias="state")] = None,
    stale: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RegistrationListResponse:
    filters: list[ColumnElement[bool]] = []
    if server_id is not None:
        filters.append(PjsipRegistration.server_id == server_id)
    if direction is not None:
        filters.append(PjsipRegistration.direction == direction)
    if state_filter is not None:
        filters.append(PjsipRegistration.state == state_filter.value)
    if stale is not None:
        filters.append(PjsipRegistration.stale.is_(stale))
    statement = select(PjsipRegistration).where(*filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(
                PjsipRegistration.server_id,
                PjsipRegistration.direction,
                PjsipRegistration.source_id,
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return RegistrationListResponse(
        items=[_registration_response(item) for item in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/transports", response_model=TransportListResponse)
async def list_transports(
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: UUID | None = None,
    stale: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TransportListResponse:
    filters: list[ColumnElement[bool]] = []
    if server_id is not None:
        filters.append(PjsipTransport.server_id == server_id)
    if stale is not None:
        filters.append(PjsipTransport.stale.is_(stale))
    statement = select(PjsipTransport).where(*filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(PjsipTransport.server_id, PjsipTransport.source_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return TransportListResponse(
        items=[_transport_response(item) for item in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/trunks", response_model=TrunkListResponse)
async def list_trunks(
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: UUID | None = None,
    state_filter: Annotated[RegistrationState | None, Query(alias="state")] = None,
    stale: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TrunkListResponse:
    filters: list[ColumnElement[bool]] = []
    if server_id is not None:
        filters.append(SipTrunk.server_id == server_id)
    if state_filter is not None:
        filters.append(SipTrunk.state == state_filter.value)
    if stale is not None:
        filters.append(SipTrunk.stale.is_(stale))
    statement = select(SipTrunk).where(*filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(SipTrunk.server_id, SipTrunk.source_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return TrunkListResponse(
        items=[_trunk_response(item) for item in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/trunks/{trunk_id}/transitions", response_model=TrunkTransitionListResponse
)
async def list_trunk_transitions(
    trunk_id: UUID,
    _actor: Annotated[AuthContext, require_permissions("asterisk.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TrunkTransitionListResponse:
    if await session.get(SipTrunk, trunk_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "trunk_not_found", "message": "Trunk not found"},
        )
    statement = select(TrunkStateTransition).where(
        TrunkStateTransition.trunk_id == trunk_id
    )
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(TrunkStateTransition.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return TrunkTransitionListResponse(
        items=[
            TrunkTransitionResponse(
                id=item.id,
                trunk_id=item.trunk_id,
                from_state=(
                    RegistrationState(item.from_state) if item.from_state is not None else None
                ),
                to_state=RegistrationState(item.to_state),
                raw_state=item.raw_state,
                occurred_at=item.occurred_at,
            )
            for item in items
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
