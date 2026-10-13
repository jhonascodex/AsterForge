import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.realtime import (
    get_operational_realtime_hub,
    get_operational_websocket_authenticator,
)
from app.core.security import AuthContext
from app.main import app
from app.realtime.operational import OperationalRealtimeHub, OperationalSubscription


def _actor(*permissions: str) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        username="operator",
        permissions=frozenset(permissions),
        session_id=uuid4(),
        must_change_password=False,
    )


def test_operational_hub_filters_permissions_and_server_scope() -> None:
    async def scenario() -> None:
        hub = OperationalRealtimeHub()
        first_server = uuid4()
        second_server = uuid4()
        now = datetime.now(UTC)
        asterisk = await hub.subscribe(_actor("asterisk.read"))
        host = await hub.subscribe(_actor("server.read"))
        scoped = await hub.subscribe(
            _actor("asterisk.read"), server_ids=frozenset({second_server})
        )

        assert await hub.publish("asterisk.health.changed", first_server, now)
        health_message = asterisk.queue.get_nowait()
        assert health_message["type"] == "asterisk.health.changed"
        assert health_message["resource"] == "asterisk_health"
        assert health_message["server_id"] == str(first_server)
        assert health_message["payload"] == {"resync": "rest"}
        assert hub.can_receive(_actor("asterisk.read"), health_message)
        assert not hub.can_receive(_actor("server.read"), health_message)
        assert host.queue.empty()
        assert scoped.queue.empty()

        assert await hub.publish("host.telemetry.changed", second_server, now)
        assert host.queue.get_nowait()["type"] == "host.telemetry.changed"
        assert asterisk.queue.empty()

        assert await hub.publish(
            "pjsip.snapshot.changed", second_server, now + timedelta(seconds=1)
        )
        assert scoped.queue.get_nowait()["server_id"] == str(second_server)

    asyncio.run(scenario())


def test_operational_hub_rejects_duplicate_and_out_of_order_updates() -> None:
    async def scenario() -> None:
        hub = OperationalRealtimeHub()
        server_id = uuid4()
        now = datetime.now(UTC)
        subscription = await hub.subscribe(_actor("alert.read", "incident.read"))

        assert await hub.publish("alerts.changed", server_id, now)
        assert not await hub.publish("alerts.changed", server_id, now)
        assert not await hub.publish(
            "alerts.changed", server_id, now - timedelta(seconds=1)
        )
        assert subscription.queue.qsize() == 1
        assert subscription.queue.get_nowait()["sequence"] == 1

        assert await hub.publish(
            "incidents.changed", server_id, now + timedelta(seconds=1)
        )
        assert subscription.queue.get_nowait()["sequence"] == 2

    asyncio.run(scenario())


def test_operational_hub_requires_resync_after_buffer_overflow() -> None:
    async def scenario() -> None:
        hub = OperationalRealtimeHub(queue_size=1)
        server_id = uuid4()
        now = datetime.now(UTC)
        actor = _actor("asterisk.read")
        subscription = await hub.subscribe(actor)

        await hub.publish("asterisk.health.changed", server_id, now)
        await hub.publish(
            "pjsip.snapshot.changed", server_id, now + timedelta(seconds=1)
        )
        message = subscription.queue.get_nowait()
        assert message["type"] == "operations.resync_required"
        assert message["payload"]["reason"] == "buffer_overflow"
        assert message["sequence"] == 2
        assert subscription.queue.empty()

    asyncio.run(scenario())


def test_operational_websocket_starts_with_authorized_resync_boundary() -> None:
    hub = OperationalRealtimeHub()
    actor = _actor("asterisk.read")

    async def authorize(_token: str) -> AuthContext:
        return actor

    app.dependency_overrides[get_operational_realtime_hub] = lambda: hub
    app.dependency_overrides[get_operational_websocket_authenticator] = lambda: authorize
    client = TestClient(app)
    try:
        with client.websocket_connect("/api/v1/ws/operations") as websocket:
            websocket.send_json(
                {
                    "type": "authenticate",
                    "token": "memory-only",
                    "server_ids": [str(uuid4())],
                }
            )
            message = websocket.receive_json()
            assert message["type"] == "operations.resync_required"
            assert message["version"] == 1
            assert message["payload"] == {
                "reason": "initial_subscription",
                "resources": ["asterisk_health", "pjsip"],
            }
            assert isinstance(message["stream_id"], str)
            assert message["sequence"] == 0
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_operational_websocket_denies_unauthorized_clients() -> None:
    async def no_permission(_token: str) -> AuthContext:
        return _actor("call.read")

    async def invalid_token(_token: str) -> AuthContext:
        raise HTTPException(status_code=401)

    hub = OperationalRealtimeHub()
    app.dependency_overrides[get_operational_realtime_hub] = lambda: hub
    app.dependency_overrides[get_operational_websocket_authenticator] = lambda: no_permission
    client = TestClient(app)
    try:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/api/v1/ws/operations") as websocket:
                websocket.send_json({"type": "authenticate", "token": "memory-only"})
                websocket.receive_json()
        assert denied.value.code == 4403

        app.dependency_overrides[get_operational_websocket_authenticator] = (
            lambda: invalid_token
        )
        with pytest.raises(WebSocketDisconnect) as unauthenticated:
            with client.websocket_connect("/api/v1/ws/operations") as websocket:
                websocket.send_json({"type": "authenticate", "token": "invalid"})
                websocket.receive_json()
        assert unauthenticated.value.code == 4401
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_operational_websocket_revalidates_permissions_before_delivery() -> None:
    server_id = uuid4()
    calls = 0

    class PreloadedHub(OperationalRealtimeHub):
        async def subscribe(
            self,
            actor: AuthContext,
            *,
            server_ids: frozenset[UUID] | None = None,
        ) -> OperationalSubscription:
            subscription = await super().subscribe(actor, server_ids=server_ids)
            await self.publish(
                "asterisk.health.changed", server_id, datetime.now(UTC)
            )
            return subscription

    async def authorize(_token: str) -> AuthContext:
        nonlocal calls
        calls += 1
        return _actor("asterisk.read") if calls == 1 else _actor("call.read")

    hub = PreloadedHub()
    app.dependency_overrides[get_operational_realtime_hub] = lambda: hub
    app.dependency_overrides[get_operational_websocket_authenticator] = lambda: authorize
    client = TestClient(app)
    try:
        with pytest.raises(WebSocketDisconnect) as revoked:
            with client.websocket_connect("/api/v1/ws/operations") as websocket:
                websocket.send_json({"type": "authenticate", "token": "memory-only"})
                assert websocket.receive_json()["type"] == "operations.resync_required"
                websocket.receive_json()
        assert revoked.value.code == 4403
        assert calls == 2
    finally:
        client.close()
        app.dependency_overrides.clear()
