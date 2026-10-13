import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import AuthContext, as_utc, require_permissions
from app.db.models import (
    Alert,
    AlertRule,
    AsteriskServer,
    AuditEvent,
    Incident,
    IncidentEvent,
    MaintenanceWindow,
    incident_alerts,
)
from app.db.session import SessionFactory, get_session
from app.realtime.operational import operational_realtime_hub
from app.services.alerts import AlertService

router = APIRouter(tags=["alerts-incidents-maintenance"])
alert_service = AlertService(
    SessionFactory,
    operational_publisher=operational_realtime_hub.publish,
    cdr_enabled=get_settings().cdr_source == "postgres",
)

AlertState = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
IncidentState = Literal["OPEN", "RESOLVED"]


class AlertResponse(BaseModel):
    id: UUID
    rule_code: str
    server_id: UUID
    resource_type: str
    resource_key: str
    condition_scope: str
    severity: str
    state: str
    first_seen: datetime
    last_seen: datetime
    acknowledged_at: datetime | None
    acknowledged_by_user_id: UUID | None
    acknowledgement_comment: str | None
    resolved_at: datetime | None
    condition_metadata: dict[str, object]
    suppressed: bool
    maintenance_window_id: UUID | None


class AcknowledgeRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=500)


class IncidentResponse(BaseModel):
    id: UUID
    server_id: UUID
    correlation_key: str
    title: str
    state: str
    opened_at: datetime
    resolved_at: datetime | None
    alert_ids: list[UUID]


class IncidentEventResponse(BaseModel):
    id: UUID
    alert_id: UUID | None
    event_type: str
    occurred_at: datetime
    actor_user_id: UUID | None
    metadata: dict[str, object]


class MaintenanceWindowRequest(BaseModel):
    server_id: UUID
    resource_type: Literal[
        "asterisk", "calls", "endpoint", "filesystem", "host", "storage", "trunk"
    ] | None = None
    resource_key: str | None = Field(default=None, min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_window(self) -> MaintenanceWindowRequest:
        if (self.resource_type is None) != (self.resource_key is None):
            raise ValueError("resource_type and resource_key must be supplied together")
        if as_utc(self.ends_at) <= as_utc(self.starts_at):
            raise ValueError("ends_at must be after starts_at")
        return self


class MaintenanceWindowResponse(BaseModel):
    id: UUID
    server_id: UUID
    resource_type: str | None
    resource_key: str | None
    starts_at: datetime
    ends_at: datetime
    reason: str
    created_by_user_id: UUID | None
    created_at: datetime
    active: bool


def get_alert_service() -> AlertService:
    return alert_service


def _alert_response(alert: Alert, rule_code: str) -> AlertResponse:
    return AlertResponse(
        id=alert.id,
        rule_code=rule_code,
        server_id=alert.server_id,
        resource_type=alert.resource_type,
        resource_key=alert.resource_key,
        condition_scope=alert.condition_scope,
        severity=alert.severity,
        state=alert.state,
        first_seen=alert.first_seen,
        last_seen=alert.last_seen,
        acknowledged_at=alert.acknowledged_at,
        acknowledged_by_user_id=alert.acknowledged_by_user_id,
        acknowledgement_comment=alert.acknowledgement_comment,
        resolved_at=alert.resolved_at,
        condition_metadata=json.loads(alert.condition_metadata_json),
        suppressed=alert.suppressed,
        maintenance_window_id=alert.maintenance_window_id,
    )


@router.get("/alerts", response_model=list[AlertResponse])
async def list_alerts(
    _actor: Annotated[AuthContext, require_permissions("alert.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    state_filter: Annotated[AlertState | None, Query(alias="state")] = None,
    server_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AlertResponse]:
    query = select(Alert, AlertRule.code).join(AlertRule, AlertRule.id == Alert.rule_id)
    if state_filter is not None:
        query = query.where(Alert.state == state_filter)
    if server_id is not None:
        query = query.where(Alert.server_id == server_id)
    rows = (await session.execute(query.order_by(Alert.last_seen.desc()).limit(limit))).all()
    return [_alert_response(alert, rule_code) for alert, rule_code in rows]


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertResponse)
async def acknowledge_alert(
    alert_id: UUID,
    payload: AcknowledgeRequest,
    actor: Annotated[AuthContext, require_permissions("alert.acknowledge")],
    service: Annotated[AlertService, Depends(get_alert_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertResponse:
    try:
        alert = await service.acknowledge(alert_id, actor.user_id, payload.comment.strip())
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": str(error), "message": "Alert is not open"},
        ) from error
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "alert_not_found", "message": "Alert not found"},
        )
    rule_code = await session.scalar(select(AlertRule.code).where(AlertRule.id == alert.rule_id))
    if rule_code is None:
        raise RuntimeError("alert rule missing")
    return _alert_response(alert, rule_code)


@router.get("/incidents", response_model=list[IncidentResponse])
async def list_incidents(
    _actor: Annotated[AuthContext, require_permissions("incident.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    state_filter: Annotated[IncidentState | None, Query(alias="state")] = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[IncidentResponse]:
    query = select(Incident)
    if state_filter is not None:
        query = query.where(Incident.state == state_filter)
    incidents = list(
        (await session.scalars(query.order_by(Incident.opened_at.desc()).limit(limit))).all()
    )
    result: list[IncidentResponse] = []
    for incident in incidents:
        alert_ids = list(
            (
                await session.scalars(
                    select(incident_alerts.c.alert_id).where(
                        incident_alerts.c.incident_id == incident.id
                    )
                )
            ).all()
        )
        result.append(
            IncidentResponse(
                id=incident.id,
                server_id=incident.server_id,
                correlation_key=incident.correlation_key,
                title=incident.title,
                state=incident.state,
                opened_at=incident.opened_at,
                resolved_at=incident.resolved_at,
                alert_ids=alert_ids,
            )
        )
    return result


@router.get("/incidents/{incident_id}/timeline", response_model=list[IncidentEventResponse])
async def incident_timeline(
    incident_id: UUID,
    _actor: Annotated[AuthContext, require_permissions("incident.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=200, ge=1, le=500),
) -> list[IncidentEventResponse]:
    if await session.get(Incident, incident_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "incident_not_found", "message": "Incident not found"},
        )
    events = list(
        (
            await session.scalars(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.occurred_at, IncidentEvent.id)
                .limit(limit)
            )
        ).all()
    )
    return [
        IncidentEventResponse(
            id=item.id,
            alert_id=item.alert_id,
            event_type=item.event_type,
            occurred_at=item.occurred_at,
            actor_user_id=item.actor_user_id,
            metadata=json.loads(item.metadata_json),
        )
        for item in events
    ]


def _maintenance_response(item: MaintenanceWindow, now: datetime) -> MaintenanceWindowResponse:
    active = as_utc(item.starts_at) <= now < as_utc(item.ends_at)
    return MaintenanceWindowResponse(
        id=item.id,
        server_id=item.server_id,
        resource_type=item.resource_type,
        resource_key=item.resource_key,
        starts_at=item.starts_at,
        ends_at=item.ends_at,
        reason=item.reason,
        created_by_user_id=item.created_by_user_id,
        created_at=item.created_at,
        active=active,
    )


@router.get("/maintenance-windows", response_model=list[MaintenanceWindowResponse])
async def list_maintenance_windows(
    _actor: Annotated[AuthContext, require_permissions("maintenance.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
    active_only: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[MaintenanceWindowResponse]:
    now = datetime.now(UTC)
    query = select(MaintenanceWindow)
    if active_only:
        query = query.where(
            MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at > now
        )
    items = list(
        (
            await session.scalars(
                query.order_by(MaintenanceWindow.starts_at.desc()).limit(limit)
            )
        ).all()
    )
    return [_maintenance_response(item, now) for item in items]


@router.post(
    "/maintenance-windows",
    response_model=MaintenanceWindowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_maintenance_window(
    payload: MaintenanceWindowRequest,
    actor: Annotated[AuthContext, require_permissions("maintenance.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MaintenanceWindowResponse:
    server = await session.get(AsteriskServer, payload.server_id)
    if server is None or server.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "server_not_found", "message": "Server not found"},
        )
    now = datetime.now(UTC)
    item = MaintenanceWindow(
        id=uuid4(),
        server_id=payload.server_id,
        resource_type=payload.resource_type,
        resource_key=payload.resource_key.strip() if payload.resource_key else None,
        starts_at=as_utc(payload.starts_at),
        ends_at=as_utc(payload.ends_at),
        reason=payload.reason.strip(),
        created_by_user_id=actor.user_id,
        created_at=now,
    )
    session.add(item)
    session.add(
        AuditEvent(
            occurred_at=now,
            actor_user_id=actor.user_id,
            action="maintenance.created",
            target_type="maintenance_window",
            target_id=str(item.id),
            metadata_json=json.dumps(
                {"server_id": str(item.server_id), "ends_at": item.ends_at.isoformat()},
                separators=(",", ":"),
            ),
        )
    )
    await session.commit()
    return _maintenance_response(item, now)


@router.patch("/maintenance-windows/{window_id}/end", response_model=MaintenanceWindowResponse)
async def end_maintenance_window(
    window_id: UUID,
    actor: Annotated[AuthContext, require_permissions("maintenance.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MaintenanceWindowResponse:
    item = await session.scalar(
        select(MaintenanceWindow)
        .where(MaintenanceWindow.id == window_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "maintenance_not_found", "message": "Maintenance window not found"},
        )
    now = datetime.now(UTC)
    if as_utc(item.ends_at) > now:
        if as_utc(item.starts_at) > now:
            item.starts_at = now
            item.ends_at = now + timedelta(microseconds=1)
        else:
            item.ends_at = now
        session.add(
            AuditEvent(
                occurred_at=now,
                actor_user_id=actor.user_id,
                action="maintenance.ended",
                target_type="maintenance_window",
                target_id=str(item.id),
            )
        )
        await session.commit()
    return _maintenance_response(item, now)
