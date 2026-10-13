import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import as_utc
from app.db.models import (
    Alert,
    AlertConditionState,
    AlertRule,
    AuditEvent,
    CallDetailRecord,
    Incident,
    IncidentEvent,
    MaintenanceWindow,
    incident_alerts,
)
from app.realtime.operational import OperationalPublisher


@dataclass(frozen=True)
class ConditionObservation:
    rule_code: str
    server_id: UUID
    resource_type: str
    resource_key: str
    condition_scope: str
    value: str | float | None
    observed_at: datetime
    metadata: dict[str, Any]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class AlertService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        operational_publisher: OperationalPublisher | None = None,
        *,
        cdr_enabled: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._operational_publisher = operational_publisher
        self._cdr_enabled = cdr_enabled

    async def _publish_updates(self, server_id: UUID, observed_at: datetime) -> None:
        if self._operational_publisher is None:
            return
        await self._operational_publisher("alerts.changed", server_id, observed_at)
        await self._operational_publisher("incidents.changed", server_id, observed_at)

    async def evaluate(self, observation: ConditionObservation) -> UUID | None:
        observed_at = as_utc(observation.observed_at)
        async with self._session_factory() as session, session.begin():
            rule = await session.scalar(
                select(AlertRule).where(
                    AlertRule.code == observation.rule_code,
                    AlertRule.enabled.is_(True),
                )
            )
            if rule is None or observation.value is None:
                return None
            dedupe_key = (
                f"{rule.code}:{observation.server_id}:"
                f"{observation.resource_type}:{observation.resource_key}"
            )
            state = await session.scalar(
                select(AlertConditionState)
                .where(AlertConditionState.dedupe_key == dedupe_key)
                .with_for_update()
            )
            if state is not None and observed_at < as_utc(state.last_evaluated_at):
                current = await session.scalar(
                    select(Alert.id).where(Alert.active_key == dedupe_key)
                )
                return current
            firing = self._is_firing(rule, observation.value, state)
            if state is None:
                state = AlertConditionState(
                    dedupe_key=dedupe_key,
                    rule_id=rule.id,
                    server_id=observation.server_id,
                    resource_type=observation.resource_type,
                    resource_key=observation.resource_key,
                    active_since=observed_at if firing else None,
                    clear_since=None,
                    last_is_firing=firing,
                    last_value_json=_json(observation.value),
                    last_evaluated_at=observed_at,
                )
                session.add(state)
            else:
                if firing:
                    if not state.last_is_firing:
                        state.active_since = observed_at
                    state.clear_since = None
                else:
                    if state.last_is_firing:
                        state.clear_since = observed_at
                    state.active_since = None
                state.last_is_firing = firing
                state.last_value_json = _json(observation.value)
                state.last_evaluated_at = observed_at

            active = await session.scalar(
                select(Alert).where(Alert.active_key == dedupe_key).with_for_update()
            )
            if firing:
                active_since = as_utc(state.active_since or observed_at)
                if observed_at - active_since < timedelta(seconds=rule.duration_seconds):
                    return active.id if active is not None else None
                maintenance = await self._maintenance_for(session, observation, observed_at)
                suppressed = maintenance is not None
                if active is None:
                    active = Alert(
                        id=uuid4(),
                        rule_id=rule.id,
                        server_id=observation.server_id,
                        resource_type=observation.resource_type,
                        resource_key=observation.resource_key,
                        condition_scope=observation.condition_scope,
                        dedupe_key=dedupe_key,
                        active_key=dedupe_key,
                        severity=rule.severity,
                        state="OPEN",
                        first_seen=active_since,
                        last_seen=observed_at,
                        condition_metadata_json=_json(observation.metadata),
                        suppressed=suppressed,
                        maintenance_window_id=maintenance.id if maintenance else None,
                    )
                    session.add(active)
                    await session.flush()
                    session.add(
                        AuditEvent(
                            occurred_at=observed_at,
                            action="alert.opened",
                            target_type="alert",
                            target_id=str(active.id),
                            metadata_json=_json(
                                {"rule": rule.code, "suppressed": suppressed}
                            ),
                        )
                    )
                    await self._attach_to_incident(session, active, observed_at)
                else:
                    active.last_seen = observed_at
                    active.condition_metadata_json = _json(observation.metadata)
                    if (
                        active.suppressed != suppressed
                        or active.maintenance_window_id
                        != (maintenance.id if maintenance else None)
                    ):
                        active.suppressed = suppressed
                        active.maintenance_window_id = maintenance.id if maintenance else None
                        await self._record_alert_event(
                            session,
                            active,
                            "SUPPRESSION_CHANGED",
                            observed_at,
                            metadata={"suppressed": suppressed},
                        )
                return active.id

            if active is None:
                return None
            clear_since = as_utc(state.clear_since or observed_at)
            if observed_at - clear_since < timedelta(seconds=rule.debounce_seconds):
                return active.id
            await self._resolve(session, active, observed_at)
            return active.id

    @staticmethod
    def _is_firing(
        rule: AlertRule,
        value: str | float,
        state: AlertConditionState | None,
    ) -> bool:
        if rule.condition_kind == "categorical_unavailable":
            healthy = {
                "asterisk.connection.unavailable": "AVAILABLE",
                "endpoint.availability.unavailable": "AVAILABLE",
                "trunk.registration.unavailable": "REGISTERED",
            }.get(rule.code)
            if healthy is None:
                return False
            return str(value).upper() != healthy
        numeric = float(value)
        if rule.threshold is None or rule.clear_threshold is None:
            return False
        if rule.condition_kind == "numeric_high":
            return (
                numeric > rule.clear_threshold
                if state is not None and state.last_is_firing
                else numeric >= rule.threshold
            )
        if rule.condition_kind == "numeric_low":
            return (
                numeric < rule.clear_threshold
                if state is not None and state.last_is_firing
                else numeric <= rule.threshold
            )
        return False

    @staticmethod
    async def _maintenance_for(
        session: AsyncSession,
        observation: ConditionObservation,
        observed_at: datetime,
    ) -> MaintenanceWindow | None:
        return cast(
            MaintenanceWindow | None,
            await session.scalar(
                select(MaintenanceWindow)
                .where(
                    MaintenanceWindow.server_id == observation.server_id,
                    MaintenanceWindow.starts_at <= observed_at,
                    MaintenanceWindow.ends_at > observed_at,
                    or_(
                        MaintenanceWindow.resource_type.is_(None),
                        and_(
                            MaintenanceWindow.resource_type == observation.resource_type,
                            MaintenanceWindow.resource_key == observation.resource_key,
                        ),
                    ),
                )
                .order_by(MaintenanceWindow.starts_at.desc())
                .limit(1)
            ),
        )

    async def _attach_to_incident(
        self, session: AsyncSession, alert: Alert, occurred_at: datetime
    ) -> None:
        correlation_key = f"{alert.server_id}:{alert.condition_scope}"
        incident = await session.scalar(
            select(Incident).where(Incident.active_key == correlation_key).with_for_update()
        )
        if incident is None:
            incident = Incident(
                id=uuid4(),
                server_id=alert.server_id,
                correlation_key=correlation_key,
                active_key=correlation_key,
                title=f"{alert.condition_scope.replace('_', ' ').title()} incident",
                state="OPEN",
                opened_at=occurred_at,
            )
            session.add(incident)
            await session.flush()
            session.add(
                IncidentEvent(
                    id=uuid4(),
                    incident_id=incident.id,
                    event_type="INCIDENT_OPENED",
                    occurred_at=occurred_at,
                    metadata_json="{}",
                )
            )
        await session.execute(
            insert(incident_alerts).values(incident_id=incident.id, alert_id=alert.id)
        )
        session.add(
            IncidentEvent(
                id=uuid4(),
                incident_id=incident.id,
                alert_id=alert.id,
                event_type="ALERT_ATTACHED",
                occurred_at=occurred_at,
                metadata_json=_json({"dedupe_key": alert.dedupe_key}),
            )
        )

    async def _record_alert_event(
        self,
        session: AsyncSession,
        alert: Alert,
        event_type: str,
        occurred_at: datetime,
        *,
        actor_user_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        incident_id = await session.scalar(
            select(incident_alerts.c.incident_id).where(
                incident_alerts.c.alert_id == alert.id
            )
        )
        if incident_id is not None:
            session.add(
                IncidentEvent(
                    id=uuid4(),
                    incident_id=incident_id,
                    alert_id=alert.id,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    actor_user_id=actor_user_id,
                    metadata_json=_json(metadata or {}),
                )
            )

    async def _resolve(
        self, session: AsyncSession, alert: Alert, occurred_at: datetime
    ) -> None:
        alert.state = "RESOLVED"
        alert.resolved_at = occurred_at
        alert.last_seen = occurred_at
        alert.active_key = None
        alert.suppressed = False
        alert.maintenance_window_id = None
        session.add(
            AuditEvent(
                occurred_at=occurred_at,
                action="alert.resolved",
                target_type="alert",
                target_id=str(alert.id),
            )
        )
        await self._record_alert_event(session, alert, "ALERT_RESOLVED", occurred_at)
        await session.flush()
        incident_id = await session.scalar(
            select(incident_alerts.c.incident_id).where(
                incident_alerts.c.alert_id == alert.id
            )
        )
        if incident_id is None:
            return
        remaining = await session.scalar(
            select(Alert.id)
            .join(incident_alerts, incident_alerts.c.alert_id == Alert.id)
            .where(
                incident_alerts.c.incident_id == incident_id,
                Alert.state != "RESOLVED",
            )
            .limit(1)
        )
        if remaining is None:
            incident = await session.get(Incident, incident_id)
            if incident is not None:
                incident.state = "RESOLVED"
                incident.resolved_at = occurred_at
                incident.active_key = None
                session.add(
                    IncidentEvent(
                        id=uuid4(),
                        incident_id=incident.id,
                        event_type="INCIDENT_RESOLVED",
                        occurred_at=occurred_at,
                        metadata_json="{}",
                    )
                )

    async def acknowledge(
        self, alert_id: UUID, actor_user_id: UUID, comment: str, *, now: datetime | None = None
    ) -> Alert | None:
        occurred_at = as_utc(now or datetime.now(UTC))
        async with self._session_factory() as session, session.begin():
            alert = await session.scalar(
                select(Alert).where(Alert.id == alert_id).with_for_update()
            )
            if alert is None:
                return None
            if alert.state != "OPEN":
                raise ValueError("alert_not_open")
            alert.state = "ACKNOWLEDGED"
            alert.acknowledged_at = occurred_at
            alert.acknowledged_by_user_id = actor_user_id
            alert.acknowledgement_comment = comment
            session.add(
                AuditEvent(
                    occurred_at=occurred_at,
                    actor_user_id=actor_user_id,
                    action="alert.acknowledged",
                    target_type="alert",
                    target_id=str(alert.id),
                    metadata_json=_json({"comment_present": bool(comment)}),
                )
            )
            await self._record_alert_event(
                session,
                alert,
                "ALERT_ACKNOWLEDGED",
                occurred_at,
                actor_user_id=actor_user_id,
                metadata={"comment": comment},
            )
            await session.flush()
        await self._publish_updates(alert.server_id, occurred_at)
        return alert

    async def evaluate_asterisk(
        self,
        server_id: UUID,
        available: bool,
        observed_at: datetime,
        *,
        error_code: str | None = None,
    ) -> UUID | None:
        alert_id = await self.evaluate(
            ConditionObservation(
                rule_code="asterisk.connection.unavailable",
                server_id=server_id,
                resource_type="asterisk",
                resource_key="core",
                condition_scope="availability",
                value="AVAILABLE" if available else "UNAVAILABLE",
                observed_at=observed_at,
                metadata={"error_code": error_code},
            )
        )
        call_alert_id = await self.evaluate_failed_call_rate(server_id, observed_at)
        if alert_id is not None or call_alert_id is not None:
            await self._publish_updates(server_id, observed_at)
        return alert_id

    async def evaluate_trunk(
        self, server_id: UUID, resource_key: str, state: str, observed_at: datetime
    ) -> UUID | None:
        alert_id = await self.evaluate(
            ConditionObservation(
                rule_code="trunk.registration.unavailable",
                server_id=server_id,
                resource_type="trunk",
                resource_key=resource_key,
                condition_scope="availability",
                value=state,
                observed_at=observed_at,
                metadata={"state": state},
            )
        )
        if alert_id is not None:
            await self._publish_updates(server_id, observed_at)
        return alert_id

    async def evaluate_endpoint(
        self, server_id: UUID, resource_key: str, state: str, observed_at: datetime
    ) -> UUID | None:
        alert_id = await self.evaluate(
            ConditionObservation(
                rule_code="endpoint.availability.unavailable",
                server_id=server_id,
                resource_type="endpoint",
                resource_key=resource_key,
                condition_scope="availability",
                value=state,
                observed_at=observed_at,
                metadata={"state": state},
            )
        )
        if alert_id is not None:
            await self._publish_updates(server_id, observed_at)
        return alert_id

    async def evaluate_host(
        self,
        server_id: UUID,
        observed_at: datetime,
        *,
        cpu_usage_percent: float | None,
        recording_total_bytes: int | None,
        recording_available_bytes: int | None,
        memory_total_bytes: int | None = None,
        memory_available_bytes: int | None = None,
        filesystems: tuple[tuple[str, int, int], ...] = (),
        recording_failures_since_previous_sample: int | None = None,
        recording_failure_source: str | None = None,
    ) -> None:
        changed = False
        if cpu_usage_percent is not None:
            alert_id = await self.evaluate(
                ConditionObservation(
                    rule_code="host.cpu.high",
                    server_id=server_id,
                    resource_type="host",
                    resource_key="cpu",
                    condition_scope="capacity",
                    value=cpu_usage_percent,
                    observed_at=observed_at,
                    metadata={"cpu_usage_percent": cpu_usage_percent},
                )
            )
            changed = alert_id is not None or changed
        if memory_total_bytes and memory_available_bytes is not None:
            used_percent = (memory_total_bytes - memory_available_bytes) * 100 / memory_total_bytes
            alert_id = await self.evaluate(
                ConditionObservation(
                    rule_code="host.memory.high",
                    server_id=server_id,
                    resource_type="host",
                    resource_key="memory",
                    condition_scope="capacity",
                    value=used_percent,
                    observed_at=observed_at,
                    metadata={"used_percent": round(used_percent, 3)},
                )
            )
            changed = alert_id is not None or changed
        for mountpoint, total_bytes, available_bytes in filesystems:
            if total_bytes <= 0:
                continue
            available_percent = available_bytes * 100 / total_bytes
            alert_id = await self.evaluate(
                ConditionObservation(
                    rule_code="host.filesystem.low",
                    server_id=server_id,
                    resource_type="filesystem",
                    resource_key=mountpoint,
                    condition_scope="capacity",
                    value=available_percent,
                    observed_at=observed_at,
                    metadata={
                        "mountpoint": mountpoint,
                        "available_percent": round(available_percent, 3),
                    },
                )
            )
            changed = alert_id is not None or changed
        if recording_total_bytes and recording_available_bytes is not None:
            available_percent = recording_available_bytes * 100 / recording_total_bytes
            alert_id = await self.evaluate(
                ConditionObservation(
                    rule_code="recording.storage.low",
                    server_id=server_id,
                    resource_type="storage",
                    resource_key="recordings",
                    condition_scope="capacity",
                    value=available_percent,
                    observed_at=observed_at,
                    metadata={"available_percent": round(available_percent, 3)},
                )
            )
            changed = alert_id is not None or changed
        if (
            recording_failures_since_previous_sample is not None
            and recording_failure_source is not None
        ):
            alert_id = await self.evaluate(
                ConditionObservation(
                    rule_code="recording.generation.failures",
                    server_id=server_id,
                    resource_type="storage",
                    resource_key="recordings",
                    condition_scope="recording_generation",
                    value=float(recording_failures_since_previous_sample),
                    observed_at=observed_at,
                    metadata={
                        "failures_since_previous_sample": (
                            recording_failures_since_previous_sample
                        ),
                        "source": recording_failure_source,
                    },
                )
            )
            changed = alert_id is not None or changed
        call_alert_id = await self.evaluate_failed_call_rate(server_id, observed_at)
        changed = call_alert_id is not None or changed
        if changed:
            await self._publish_updates(server_id, observed_at)

    async def evaluate_failed_call_rate(
        self, server_id: UUID, observed_at: datetime
    ) -> UUID | None:
        """Evaluate a bounded CDR window only when its configured input is trustworthy."""
        if not self._cdr_enabled:
            return None
        observed_at = as_utc(observed_at)
        window_start = observed_at - timedelta(minutes=5)
        known_failures = ("BUSY", "FAILED", "NO ANSWER", "CONGESTION", "CHANUNAVAIL")
        async with self._session_factory() as session:
            disposition = func.upper(CallDetailRecord.disposition)
            total = int(
                await session.scalar(
                    select(func.count(CallDetailRecord.id)).where(
                        CallDetailRecord.server_id == server_id,
                        CallDetailRecord.started_at >= window_start,
                        CallDetailRecord.started_at < observed_at,
                        disposition.in_(("ANSWERED", *known_failures)),
                    )
                )
                or 0
            )
            if total < 10:
                return None
            failures = int(
                await session.scalar(
                    select(func.count(CallDetailRecord.id)).where(
                        CallDetailRecord.server_id == server_id,
                        CallDetailRecord.started_at >= window_start,
                        CallDetailRecord.started_at < observed_at,
                        disposition.in_(known_failures),
                    )
                )
                or 0
            )
        failure_rate = failures * 100 / total
        return await self.evaluate(
            ConditionObservation(
                rule_code="calls.failed_rate.high",
                server_id=server_id,
                resource_type="calls",
                resource_key="cdr:5m",
                condition_scope="call_completion",
                value=failure_rate,
                observed_at=observed_at,
                metadata={
                    "failure_rate_percent": round(failure_rate, 3),
                    "failed_calls": failures,
                    "known_outcomes": total,
                    "window_seconds": 300,
                    "source": "postgres_cdr",
                },
            )
        )
