import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.integrations.ami import (
    MAX_AMI_MESSAGE_BYTES,
    AmiCallEvent,
    AmiChannelSnapshot,
    AmiCoreSnapshot,
    AmiPjsipSnapshot,
    AmiProtocolError,
    AmiSession,
    AmiTarget,
    normalize_core_snapshot,
    parse_ami_message,
)
from app.realtime.operational import OperationalEventType
from app.services.ami_monitoring import (
    AmiConnectionManager,
    BackoffPolicy,
    ConnectionState,
    FreshnessState,
    HealthReadModel,
)
from app.services.call_observability import CallFreshness, CallReadModel, ServerCalls


class FakeSession:
    def __init__(
        self,
        snapshot: AmiCoreSnapshot,
        *,
        fail_ping: bool = False,
        observed: asyncio.Event | None = None,
        channels: tuple[AmiChannelSnapshot, ...] = (),
        events: tuple[AmiCallEvent, ...] = (),
    ) -> None:
        self.snapshot = snapshot
        self.fail_ping = fail_ping
        self.observed = observed
        self.closed = False
        self.channels = channels
        self.events = events

    async def core_snapshot(self) -> AmiCoreSnapshot:
        return self.snapshot

    async def pjsip_snapshot(self) -> AmiPjsipSnapshot:
        if self.observed:
            self.observed.set()
        return AmiPjsipSnapshot((), (), (), (), frozenset())

    async def live_channels_snapshot(self) -> tuple[AmiChannelSnapshot, ...]:
        return self.channels

    async def drain_call_events(self) -> tuple[AmiCallEvent, ...]:
        events = self.events
        self.events = ()
        return events

    async def wait_call_events(self, wait_seconds: float) -> tuple[AmiCallEvent, ...]:
        if self.events:
            return await self.drain_call_events()
        await asyncio.sleep(wait_seconds)
        return ()

    async def ping(self) -> None:
        if self.fail_ping:
            raise OSError("synthetic disconnect")

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, outcomes: dict[str, list[AmiSession | OSError]]) -> None:
        self.outcomes = outcomes
        self.attempts: dict[str, int] = {}

    async def connect(self, target: AmiTarget) -> AmiSession:
        attempt = self.attempts.get(target.hostname, 0)
        self.attempts[target.hostname] = attempt + 1
        outcomes = self.outcomes[target.hostname]
        outcome = outcomes[min(attempt, len(outcomes) - 1)]
        if isinstance(outcome, OSError):
            raise outcome
        return outcome


def _target(hostname: str) -> AmiTarget:
    return AmiTarget(
        server_id=uuid4(),
        hostname=hostname,
        port=5038,
        tls=False,
        username="reader",
        secret="test-only",
    )


def _snapshot(calls: int = 1) -> AmiCoreSnapshot:
    return AmiCoreSnapshot(
        system_name="pbx",
        asterisk_version="22.0.0",
        started_at_source="2026-09-12 10:00:00",
        reloaded_at_source="2026-09-12 10:01:00",
        current_calls=calls,
        max_calls=100,
    )


def test_parser_rejects_malformed_and_oversized_untrusted_messages() -> None:
    with pytest.raises(AmiProtocolError, match="malformed"):
        parse_ami_message(b"Response: Success\r\nnot-a-header\r\n")
    with pytest.raises(AmiProtocolError, match="size"):
        parse_ami_message(b"x" * (MAX_AMI_MESSAGE_BYTES + 1))


def test_core_normalization_maps_invalid_or_unknown_values_safely() -> None:
    normalized = normalize_core_snapshot(
        {
            "response": "Success",
            "asteriskversion": "future-version",
            "corecurrentcalls": "not-a-number",
            "coremaxcalls": "-1",
            "newfuturefield": "unexpected",
        }
    )
    assert normalized.asterisk_version == "future-version"
    assert normalized.current_calls is None
    assert normalized.max_calls is None
    assert normalized.system_name is None


def test_backoff_is_exponential_jittered_and_bounded() -> None:
    policy = BackoffPolicy(minimum=1, maximum=10, jitter_ratio=0.2)
    assert policy.delay(1, random_value=0) == pytest.approx(0.8)
    assert policy.delay(2, random_value=0.5) == pytest.approx(2)
    assert policy.delay(20, random_value=1) == pytest.approx(10)


def test_disconnect_marks_an_existing_observation_stale() -> None:
    async def scenario() -> None:
        read_model = HealthReadModel()
        server_id = uuid4()
        now = datetime.now(UTC)
        await read_model.observed(server_id, _snapshot(), now)
        await read_model.disconnected(
            server_id,
            now,
            failures=1,
            delay=1.25,
            error_code="ami_unavailable",
        )
        value = await read_model.get(server_id)
        assert value.connection_state is ConnectionState.DISCONNECTED
        assert value.freshness is FreshnessState.STALE
        assert value.stale_since == now
        assert value.reconnect_in_seconds == 1.25

    asyncio.run(scenario())


def test_manager_reconnects_and_reconciles_after_disconnect() -> None:
    async def scenario() -> None:
        target = _target("reconnect.test")
        reconciled = asyncio.Event()
        first = FakeSession(_snapshot(1), fail_ping=True)
        second = FakeSession(_snapshot(2), observed=reconciled)
        connector = FakeConnector({target.hostname: [first, second]})
        read_model = HealthReadModel()
        published: list[tuple[OperationalEventType, UUID, datetime]] = []

        async def publish(
            event_type: OperationalEventType, server_id: UUID, observed_at: datetime
        ) -> bool:
            published.append((event_type, server_id, observed_at))
            return True

        manager = AmiConnectionManager(
            connector,
            read_model,
            poll_interval=0.01,
            backoff=BackoffPolicy(0.01, 0.02, jitter_ratio=0),
            operational_publisher=publish,
        )
        await manager.update_targets([target])
        await asyncio.wait_for(reconciled.wait(), timeout=1)
        value = await read_model.get(target.server_id)
        assert connector.attempts[target.hostname] >= 2
        assert value.connection_state is ConnectionState.CONNECTED
        assert value.freshness is FreshnessState.FRESH
        assert value.core is not None and value.core.current_calls == 2
        assert len(published) >= 7
        assert all(item[0] == "asterisk.health.changed" for item in published)
        assert all(item[1] == target.server_id for item in published)
        await manager.stop()
        assert first.closed and second.closed

    asyncio.run(scenario())


def test_manager_marks_calls_stale_then_repairs_them_from_reconnect_snapshot() -> None:
    async def scenario() -> None:
        target = _target("call-reconnect.test")
        repaired = asyncio.Event()
        first = FakeSession(
            _snapshot(),
            fail_ping=True,
            channels=(
                AmiChannelSnapshot("old", "old-call", None, "Up", "1001", "2002"),
            ),
        )
        second = FakeSession(
            _snapshot(),
            channels=(
                AmiChannelSnapshot("new", "new-call", None, "Ring", "3003", "4004"),
            ),
        )
        published: list[ServerCalls] = []

        async def publish(value: ServerCalls) -> None:
            published.append(value)
            if value.calls and value.calls[0].logical_id == "new-call":
                repaired.set()

        model = CallReadModel()
        manager = AmiConnectionManager(
            FakeConnector({target.hostname: [first, second]}),
            HealthReadModel(),
            poll_interval=0.01,
            backoff=BackoffPolicy(0.01, 0.02, jitter_ratio=0),
            call_read_model=model,
            call_publisher=publish,
        )
        await manager.update_targets([target])
        await asyncio.wait_for(repaired.wait(), timeout=1)
        assert any(value.freshness is CallFreshness.STALE for value in published)
        current = await model.get(target.server_id)
        assert current.freshness is CallFreshness.FRESH
        assert current.calls[0].logical_id == "new-call"
        await manager.stop()

    asyncio.run(scenario())


def test_failing_target_is_isolated_from_healthy_target() -> None:
    async def scenario() -> None:
        bad = _target("bad.test")
        good = _target("good.test")
        healthy_observed = asyncio.Event()
        connector = FakeConnector(
            {
                bad.hostname: [OSError("unreachable")],
                good.hostname: [FakeSession(_snapshot(), observed=healthy_observed)],
            }
        )
        read_model = HealthReadModel()
        manager = AmiConnectionManager(
            connector,
            read_model,
            poll_interval=60,
            backoff=BackoffPolicy(0.05, 0.1, jitter_ratio=0),
        )
        await manager.update_targets([bad, good])
        await asyncio.wait_for(healthy_observed.wait(), timeout=1)
        await asyncio.sleep(0)
        assert (await read_model.get(good.server_id)).freshness is FreshnessState.FRESH
        assert (
            await read_model.get(bad.server_id)
        ).connection_state is ConnectionState.DISCONNECTED
        await manager.stop()

    asyncio.run(scenario())


def test_persistence_failure_reconnects_even_when_stale_marking_also_fails() -> None:
    class RecoveringStore:
        def __init__(self) -> None:
            self.attempts = 0
            self.recovered = asyncio.Event()

        async def reconcile(
            self, server_id: object, snapshot: AmiPjsipSnapshot, observed_at: datetime
        ) -> None:
            del server_id, snapshot, observed_at
            self.attempts += 1
            if self.attempts == 1:
                raise SQLAlchemyError("synthetic persistence outage")
            self.recovered.set()

        async def mark_stale(self, server_id: object) -> None:
            del server_id
            raise SQLAlchemyError("synthetic stale write outage")

    async def scenario() -> None:
        target = _target("database-recovery.test")
        first = FakeSession(_snapshot())
        second = FakeSession(_snapshot())
        connector = FakeConnector({target.hostname: [first, second]})
        store = RecoveringStore()
        manager = AmiConnectionManager(
            connector,
            HealthReadModel(),
            poll_interval=60,
            backoff=BackoffPolicy(0.01, 0.02, jitter_ratio=0),
            snapshot_store=store,
        )
        await manager.update_targets([target])
        await asyncio.wait_for(store.recovered.wait(), timeout=1)
        assert connector.attempts[target.hostname] >= 2
        await manager.stop()
        assert first.closed and second.closed

    asyncio.run(scenario())
