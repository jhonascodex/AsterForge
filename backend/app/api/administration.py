import json
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, as_utc, require_permissions
from app.db.models import AlertRule, AuditEvent, RetentionPolicy
from app.db.session import get_session

router = APIRouter(tags=["audit-monitoring-configuration"])
SENSITIVE_KEYS = ("password", "secret", "token", "credential", "authorization", "cookie")


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(part in str(key).lower() for part in SENSITIVE_KEYS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _metadata(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"invalid_metadata": True}
    if isinstance(parsed, dict):
        return cast(dict[str, object], _redact(parsed))
    return {"value": _redact(parsed)}


class AuditEventResponse(BaseModel):
    id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    action: str
    target_type: str
    target_id: str
    outcome: str
    source: str
    request_id: str | None
    metadata: dict[str, object]


class AuditEventPage(BaseModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


class AlertRuleResponse(BaseModel):
    id: UUID
    code: str
    condition_kind: str
    severity: str
    threshold: float | None
    clear_threshold: float | None
    duration_seconds: int
    debounce_seconds: int
    enabled: bool


class AlertRuleUpdate(BaseModel):
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    threshold: float | None = Field(default=None, ge=0)
    clear_threshold: float | None = Field(default=None, ge=0)
    duration_seconds: int = Field(ge=0, le=86400)
    debounce_seconds: int = Field(ge=0, le=86400)
    enabled: bool

    @model_validator(mode="after")
    def validate_threshold_pair(self) -> AlertRuleUpdate:
        if (self.threshold is None) != (self.clear_threshold is None):
            raise ValueError("threshold and clear_threshold must be supplied together")
        return self


class RetentionPolicyResponse(BaseModel):
    category: str
    retention_days: int
    batch_size: int
    updated_at: datetime
    updated_by_user_id: UUID | None


class RetentionPolicyUpdate(BaseModel):
    retention_days: int = Field(ge=1, le=3650)
    batch_size: int = Field(ge=1, le=10000)


def _rule_response(rule: AlertRule) -> AlertRuleResponse:
    return AlertRuleResponse.model_validate(rule, from_attributes=True)


def _threshold_maximum(rule_code: str) -> float:
    if rule_code == "recording.generation.failures":
        return 10_000
    return 100


@router.get("/audit-events", response_model=AuditEventPage)
async def list_audit_events(
    _actor: Annotated[AuthContext, require_permissions("audit.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    action: str | None = Query(default=None, min_length=1, max_length=100),
    target_type: str | None = Query(default=None, min_length=1, max_length=50),
    outcome: Literal["success", "failure"] | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> AuditEventPage:
    if from_time is not None and to_time is not None and as_utc(from_time) >= as_utc(to_time):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_time_range", "message": "from_time must precede to_time"},
        )
    filters = []
    if action is not None:
        filters.append(AuditEvent.action == action)
    if target_type is not None:
        filters.append(AuditEvent.target_type == target_type)
    if outcome is not None:
        filters.append(AuditEvent.outcome == outcome)
    if from_time is not None:
        filters.append(AuditEvent.occurred_at >= as_utc(from_time))
    if to_time is not None:
        filters.append(AuditEvent.occurred_at < as_utc(to_time))
    total = int(
        await session.scalar(select(func.count()).select_from(AuditEvent).where(*filters))
        or 0
    )
    items = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(*filters)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return AuditEventPage(
        items=[
            AuditEventResponse(
                id=item.id,
                occurred_at=item.occurred_at,
                actor_user_id=item.actor_user_id,
                action=item.action,
                target_type=item.target_type,
                target_id=item.target_id,
                outcome=item.outcome,
                source=item.source,
                request_id=item.request_id,
                metadata=_metadata(item.metadata_json),
            )
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/monitoring-config/alert-rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    _actor: Annotated[AuthContext, require_permissions("monitoring_config.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AlertRuleResponse]:
    rules = list((await session.scalars(select(AlertRule).order_by(AlertRule.code))).all())
    return [_rule_response(rule) for rule in rules]


@router.patch(
    "/monitoring-config/alert-rules/{rule_id}", response_model=AlertRuleResponse
)
async def update_alert_rule(
    rule_id: UUID,
    payload: AlertRuleUpdate,
    actor: Annotated[AuthContext, require_permissions("monitoring_config.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertRuleResponse:
    rule = await session.scalar(
        select(AlertRule).where(AlertRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "rule_not_found", "message": "Rule not found"},
        )
    if rule.condition_kind == "numeric_high" and (
        payload.threshold is None
        or payload.clear_threshold is None
        or payload.threshold <= payload.clear_threshold
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_thresholds",
                "message": "High threshold must exceed clear threshold",
            },
        )
    if rule.condition_kind == "numeric_low" and (
        payload.threshold is None
        or payload.clear_threshold is None
        or payload.threshold >= payload.clear_threshold
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_thresholds",
                "message": "Low threshold must be below clear threshold",
            },
        )
    if not rule.condition_kind.startswith("numeric_") and (
        payload.threshold is not None or payload.clear_threshold is not None
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "thresholds_not_supported",
                "message": "This rule does not accept numeric thresholds",
            },
        )
    if rule.condition_kind.startswith("numeric_") and (
        payload.threshold is not None
        and payload.clear_threshold is not None
        and max(payload.threshold, payload.clear_threshold) > _threshold_maximum(rule.code)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "threshold_out_of_range",
                "message": "Threshold exceeds the safe bound for this rule",
            },
        )
    changed = {
        field: value
        for field, value in payload.model_dump().items()
        if getattr(rule, field) != value
    }
    for field, value in changed.items():
        setattr(rule, field, value)
    if changed:
        now = datetime.now(UTC)
        session.add(
            AuditEvent(
                occurred_at=now,
                actor_user_id=actor.user_id,
                action="monitoring.alert_rule_updated",
                target_type="alert_rule",
                target_id=str(rule.id),
                source="api",
                metadata_json=json.dumps(
                    {"changed_fields": sorted(changed)}, separators=(",", ":")
                ),
            )
        )
        await session.commit()
    return _rule_response(rule)


@router.get("/monitoring-config/retention-policies", response_model=list[RetentionPolicyResponse])
async def list_retention_policies(
    _actor: Annotated[AuthContext, require_permissions("monitoring_config.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[RetentionPolicyResponse]:
    policies = list(
        (
            await session.scalars(
                select(RetentionPolicy).order_by(RetentionPolicy.category)
            )
        ).all()
    )
    return [
        RetentionPolicyResponse.model_validate(item, from_attributes=True)
        for item in policies
    ]


@router.patch(
    "/monitoring-config/retention-policies/{category}",
    response_model=RetentionPolicyResponse,
)
async def update_retention_policy(
    category: Literal["operational_logs"],
    payload: RetentionPolicyUpdate,
    actor: Annotated[AuthContext, require_permissions("monitoring_config.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RetentionPolicyResponse:
    policy = await session.scalar(
        select(RetentionPolicy)
        .where(RetentionPolicy.category == category)
        .with_for_update()
    )
    if policy is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "policy_not_found",
                "message": "Retention policy not found",
            },
        )
    before = {"retention_days": policy.retention_days, "batch_size": policy.batch_size}
    after = payload.model_dump()
    if before != after:
        now = datetime.now(UTC)
        policy.retention_days = payload.retention_days
        policy.batch_size = payload.batch_size
        policy.updated_at = now
        policy.updated_by_user_id = actor.user_id
        session.add(
            AuditEvent(
                occurred_at=now,
                actor_user_id=actor.user_id,
                action="monitoring.retention_policy_updated",
                target_type="retention_policy",
                target_id=category,
                source="api",
                metadata_json=json.dumps(
                    {"before": before, "after": after}, separators=(",", ":")
                ),
            )
        )
        await session.commit()
    return RetentionPolicyResponse.model_validate(policy, from_attributes=True)
