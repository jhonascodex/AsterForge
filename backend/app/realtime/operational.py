import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from app.core.security import AuthContext

type OperationalEventType = Literal[
    "asterisk.health.changed",
    "pjsip.snapshot.changed",
    "host.telemetry.changed",
    "alerts.changed",
    "incidents.changed",
]
type OperationalPublisher = Callable[
    [OperationalEventType, UUID, datetime], Awaitable[bool]
]


@dataclass(frozen=True)
class EventPolicy:
    resource: str
    permission: str


EVENT_POLICIES: dict[OperationalEventType, EventPolicy] = {
    "asterisk.health.changed": EventPolicy("asterisk_health", "asterisk.read"),
    "pjsip.snapshot.changed": EventPolicy("pjsip", "asterisk.read"),
    "host.telemetry.changed": EventPolicy("host_telemetry", "server.read"),
    "alerts.changed": EventPolicy("alerts", "alert.read"),
    "incidents.changed": EventPolicy("incidents", "incident.read"),
}


@dataclass(frozen=True)
class OperationalSubscription:
    queue: asyncio.Queue[dict[str, Any]]
    permissions: frozenset[str]
    server_ids: frozenset[UUID] | None


class OperationalRealtimeHub:
    """Bounded process-local change feed; REST remains the authoritative snapshot."""

    def __init__(self, *, queue_size: int = 20) -> None:
        self._queue_size = queue_size
        self._subscriptions: set[OperationalSubscription] = set()
        self._latest: dict[tuple[OperationalEventType, UUID], datetime] = {}
        self._sequence = 0
        self._stream_id = str(uuid4())
        self._lock = asyncio.Lock()

    def permitted_event_types(self, actor: AuthContext) -> tuple[OperationalEventType, ...]:
        return tuple(
            event_type
            for event_type, policy in EVENT_POLICIES.items()
            if policy.permission in actor.permissions
        )

    @staticmethod
    def can_receive(actor: AuthContext, message: dict[str, Any]) -> bool:
        event_type = message.get("type")
        if not isinstance(event_type, str):
            return False
        policy = EVENT_POLICIES.get(cast(OperationalEventType, event_type))
        return policy is not None and policy.permission in actor.permissions

    async def subscribe(
        self,
        actor: AuthContext,
        *,
        server_ids: frozenset[UUID] | None = None,
    ) -> OperationalSubscription:
        subscription = OperationalSubscription(
            queue=asyncio.Queue(maxsize=self._queue_size),
            permissions=actor.permissions,
            server_ids=server_ids,
        )
        async with self._lock:
            self._subscriptions.add(subscription)
        return subscription

    async def unsubscribe(self, subscription: OperationalSubscription) -> None:
        async with self._lock:
            self._subscriptions.discard(subscription)

    async def resync_message(
        self,
        actor: AuthContext,
        *,
        reason: Literal["initial_subscription", "buffer_overflow"],
    ) -> dict[str, Any]:
        async with self._lock:
            return self._resync_message(actor.permissions, reason=reason)

    def _resync_message(
        self,
        permissions: frozenset[str],
        *,
        reason: Literal["initial_subscription", "buffer_overflow"],
    ) -> dict[str, Any]:
        resources = sorted(
            policy.resource
            for policy in EVENT_POLICIES.values()
            if policy.permission in permissions
        )
        return {
            "type": "operations.resync_required",
            "version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "resource": "operations",
            "server_id": None,
            "stream_id": self._stream_id,
            "sequence": self._sequence,
            "payload": {"reason": reason, "resources": resources},
        }

    async def publish(
        self,
        event_type: OperationalEventType,
        server_id: UUID,
        observed_at: datetime,
    ) -> bool:
        timestamp = (
            observed_at.replace(tzinfo=UTC)
            if observed_at.tzinfo is None
            else observed_at.astimezone(UTC)
        )
        policy = EVENT_POLICIES[event_type]
        key = (event_type, server_id)
        async with self._lock:
            previous = self._latest.get(key)
            if previous is not None and timestamp <= previous:
                return False
            self._latest[key] = timestamp
            self._sequence += 1
            message = {
                "type": event_type,
                "version": 1,
                "timestamp": timestamp.isoformat(),
                "resource": policy.resource,
                "server_id": str(server_id),
                "stream_id": self._stream_id,
                "sequence": self._sequence,
                "payload": {"resync": "rest"},
            }
            subscriptions = tuple(self._subscriptions)
            for subscription in subscriptions:
                if policy.permission not in subscription.permissions:
                    continue
                if (
                    subscription.server_ids is not None
                    and server_id not in subscription.server_ids
                ):
                    continue
                if subscription.queue.full():
                    while not subscription.queue.empty():
                        subscription.queue.get_nowait()
                    subscription.queue.put_nowait(
                        self._resync_message(
                            subscription.permissions, reason="buffer_overflow"
                        )
                    )
                    continue
                subscription.queue.put_nowait(message)
        return True


operational_realtime_hub = OperationalRealtimeHub()
