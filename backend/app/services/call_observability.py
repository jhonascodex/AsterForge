import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.integrations.ami import AmiCallEvent, AmiChannelSnapshot


class CallFreshness(StrEnum):
    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True)
class LiveChannel:
    unique_id: str
    linked_id: str | None
    channel: str | None
    state: str | None
    caller_number: str | None
    connected_number: str | None
    started_at: datetime
    observed_at: datetime


@dataclass(frozen=True)
class LiveCall:
    server_id: UUID
    logical_id: str
    state: str
    caller_number: str | None
    connected_number: str | None
    channel_count: int
    channel_ids: tuple[str, ...]
    started_at: datetime
    observed_at: datetime
    freshness: CallFreshness


@dataclass(frozen=True)
class ServerCalls:
    server_id: UUID
    freshness: CallFreshness
    observed_at: datetime | None
    calls: tuple[LiveCall, ...]


def _call_state(channels: Sequence[LiveChannel]) -> str:
    states = [item.state for item in channels if item.state]
    for preferred in ("up", "ringing", "ring", "dialing"):
        if any(state.casefold() == preferred for state in states):
            return preferred
    return states[0].casefold() if states else "unknown"


class CallReadModel:
    """Concurrency-safe, process-local active-call projection repaired by AMI snapshots."""

    def __init__(self) -> None:
        self._channels: dict[UUID, dict[str, LiveChannel]] = {}
        self._freshness: dict[UUID, CallFreshness] = {}
        self._observed_at: dict[UUID, datetime] = {}
        self._tombstones: dict[UUID, set[str]] = {}
        self._seen_order: dict[UUID, deque[AmiCallEvent]] = {}
        self._seen: dict[UUID, set[AmiCallEvent]] = {}
        self._lock = asyncio.Lock()

    def _remember(self, server_id: UUID, event: AmiCallEvent) -> bool:
        seen = self._seen.setdefault(server_id, set())
        if event in seen:
            return False
        order = self._seen_order.setdefault(server_id, deque())
        seen.add(event)
        order.append(event)
        if len(order) > 10_000:
            seen.discard(order.popleft())
        return True

    def _snapshot_unlocked(self, server_id: UUID) -> ServerCalls:
        freshness = self._freshness.get(server_id, CallFreshness.UNKNOWN)
        grouped: dict[str, list[LiveChannel]] = {}
        for channel in self._channels.get(server_id, {}).values():
            grouped.setdefault(channel.linked_id or channel.unique_id, []).append(channel)
        calls: list[LiveCall] = []
        for logical_id, channels in grouped.items():
            channels.sort(key=lambda item: (item.started_at, item.unique_id))
            calls.append(
                LiveCall(
                    server_id=server_id,
                    logical_id=logical_id,
                    state=_call_state(channels),
                    caller_number=next(
                        (item.caller_number for item in channels if item.caller_number), None
                    ),
                    connected_number=next(
                        (item.connected_number for item in channels if item.connected_number),
                        None,
                    ),
                    channel_count=len(channels),
                    channel_ids=tuple(sorted(item.unique_id for item in channels)),
                    started_at=min(item.started_at for item in channels),
                    observed_at=max(item.observed_at for item in channels),
                    freshness=freshness,
                )
            )
        return ServerCalls(
            server_id=server_id,
            freshness=freshness,
            observed_at=self._observed_at.get(server_id),
            calls=tuple(sorted(calls, key=lambda item: (item.started_at, item.logical_id))),
        )

    async def list(self, server_ids: Sequence[UUID] | None = None) -> list[ServerCalls]:
        async with self._lock:
            ids = (
                sorted(server_ids, key=str)
                if server_ids is not None
                else sorted(self._channels.keys() | self._freshness.keys(), key=str)
            )
            return [self._snapshot_unlocked(server_id) for server_id in ids]

    async def get(self, server_id: UUID) -> ServerCalls:
        async with self._lock:
            return self._snapshot_unlocked(server_id)

    async def reconcile(
        self,
        server_id: UUID,
        snapshots: Sequence[AmiChannelSnapshot],
        observed_at: datetime,
    ) -> ServerCalls:
        async with self._lock:
            previous = self._channels.get(server_id, {})
            channels: dict[str, LiveChannel] = {}
            for item in snapshots:
                old = previous.get(item.unique_id)
                channels[item.unique_id] = LiveChannel(
                    unique_id=item.unique_id,
                    linked_id=item.linked_id,
                    channel=item.channel,
                    state=item.state,
                    caller_number=item.caller_number,
                    connected_number=item.connected_number,
                    started_at=old.started_at if old else observed_at,
                    observed_at=observed_at,
                )
            self._channels[server_id] = channels
            self._tombstones.setdefault(server_id, set()).difference_update(channels)
            self._freshness[server_id] = CallFreshness.FRESH
            self._observed_at[server_id] = observed_at
            return self._snapshot_unlocked(server_id)

    async def apply_events(
        self,
        server_id: UUID,
        events: Sequence[AmiCallEvent],
        observed_at: datetime,
    ) -> ServerCalls | None:
        async with self._lock:
            channels = self._channels.setdefault(server_id, {})
            tombstones = self._tombstones.setdefault(server_id, set())
            changed = False
            for event in events:
                if not self._remember(server_id, event):
                    continue
                if event.event_type == "hangup":
                    tombstones.add(event.unique_id)
                    changed = channels.pop(event.unique_id, None) is not None or changed
                    continue
                if event.unique_id in tombstones:
                    continue
                current = channels.get(event.unique_id)
                channels[event.unique_id] = LiveChannel(
                    unique_id=event.unique_id,
                    linked_id=event.linked_id or (current.linked_id if current else None),
                    channel=event.channel or (current.channel if current else None),
                    state=event.state or (current.state if current else None),
                    caller_number=event.caller_number
                    or (current.caller_number if current else None),
                    connected_number=event.connected_number
                    or (current.connected_number if current else None),
                    started_at=current.started_at if current else observed_at,
                    observed_at=observed_at,
                )
                changed = channels[event.unique_id] != current or changed
                destination_id = event.destination_unique_id
                if destination_id and destination_id not in tombstones:
                    destination = channels.get(destination_id)
                    channels[destination_id] = LiveChannel(
                        unique_id=destination_id,
                        linked_id=event.destination_linked_id
                        or event.linked_id
                        or (destination.linked_id if destination else None),
                        channel=destination.channel if destination else None,
                        state=destination.state if destination else None,
                        caller_number=destination.caller_number if destination else None,
                        connected_number=(
                            destination.connected_number if destination else event.connected_number
                        ),
                        started_at=destination.started_at if destination else observed_at,
                        observed_at=observed_at,
                    )
                    changed = channels[destination_id] != destination or changed
            if not changed:
                return None
            self._freshness[server_id] = CallFreshness.FRESH
            self._observed_at[server_id] = observed_at
            return self._snapshot_unlocked(server_id)

    async def mark_stale(self, server_id: UUID, observed_at: datetime) -> ServerCalls:
        async with self._lock:
            self._freshness[server_id] = CallFreshness.STALE
            self._observed_at[server_id] = observed_at
            return self._snapshot_unlocked(server_id)

    async def remove(self, server_id: UUID) -> None:
        async with self._lock:
            self._channels.pop(server_id, None)
            self._freshness.pop(server_id, None)
            self._observed_at.pop(server_id, None)
            self._tombstones.pop(server_id, None)
            self._seen.pop(server_id, None)
            self._seen_order.pop(server_id, None)


def utc_now() -> datetime:
    return datetime.now(UTC)
