import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.live_calls import get_call_read_model, get_websocket_authenticator
from app.core.security import AuthContext, get_auth_context
from app.integrations.ami import (
    AmiCallEvent,
    AmiChannelSnapshot,
    StreamAmiSession,
    normalize_call_event,
)
from app.main import app
from app.services.call_observability import CallFreshness, CallReadModel


def _event(
    event_type: str,
    unique_id: str,
    *,
    linked_id: str | None = None,
    state: str | None = None,
) -> AmiCallEvent:
    return AmiCallEvent(
        event_type=event_type,
        unique_id=unique_id,
        linked_id=linked_id,
        channel=f"PJSIP/{unique_id}",
        state=state,
        caller_number="1001",
        connected_number="2002",
    )


def test_call_events_correlate_channels_and_ignore_duplicates() -> None:
    async def scenario() -> None:
        model = CallReadModel()
        server_id = uuid4()
        now = datetime.now(UTC)
        events = (
            _event("newchannel", "channel-a", linked_id="call-1", state="Ring"),
            _event("newchannel", "channel-b", linked_id="call-1", state="Up"),
        )
        result = await model.apply_events(server_id, events, now)
        assert result is not None
        assert len(result.calls) == 1
        assert result.calls[0].logical_id == "call-1"
        assert result.calls[0].channel_ids == ("channel-a", "channel-b")
        assert result.calls[0].state == "up"
        assert await model.apply_events(server_id, events, now) is None

    asyncio.run(scenario())


def test_out_of_order_hangup_does_not_resurrect_until_authoritative_reconciliation() -> None:
    async def scenario() -> None:
        model = CallReadModel()
        server_id = uuid4()
        now = datetime.now(UTC)
        await model.apply_events(server_id, (_event("hangup", "late-channel"),), now)
        assert (
            await model.apply_events(
                server_id,
                (_event("newchannel", "late-channel", linked_id="call-2"),),
                now,
            )
            is None
        )
        assert (await model.get(server_id)).calls == ()

        stale = await model.mark_stale(server_id, now)
        assert stale.freshness is CallFreshness.STALE
        reconciled = await model.reconcile(
            server_id,
            (
                AmiChannelSnapshot(
                    unique_id="late-channel",
                    linked_id="call-2",
                    channel="PJSIP/1001-0001",
                    state="Up",
                    caller_number="1001",
                    connected_number="2002",
                ),
            ),
            now,
        )
        assert reconciled.freshness is CallFreshness.FRESH
        assert reconciled.calls[0].logical_id == "call-2"

    asyncio.run(scenario())


def test_call_event_normalization_rejects_irrelevant_or_unidentified_input() -> None:
    assert normalize_call_event({"event": "FullyBooted"}) is None
    assert normalize_call_event({"event": "Newchannel"}) is None
    event = normalize_call_event(
        {
            "event": "DialBegin",
            "uniqueid": "a",
            "linkedid": "root",
            "destuniqueid": "b",
            "destlinkedid": "root",
        }
    )
    assert event is not None
    assert event.destination_unique_id == "b"


def test_stream_session_delivers_unsolicited_call_events() -> None:
    class Writer:
        def write(self, _data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def scenario() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"Event: Newchannel\r\nUniqueid: channel-a\r\nLinkedid: call-1\r\n"
            b"ChannelStateDesc: Ring\r\n\r\n"
        )
        session = StreamAmiSession(reader, Writer(), action_timeout=1)  # type: ignore[arg-type]
        events = await session.wait_call_events(0.1)
        assert len(events) == 1
        assert events[0].unique_id == "channel-a"
        assert events[0].linked_id == "call-1"

    asyncio.run(scenario())


def test_live_call_rest_and_websocket_boundaries() -> None:
    model = CallReadModel()
    server_id = uuid4()
    actor = AuthContext(
        user_id=uuid4(),
        username="operator",
        permissions=frozenset({"call.read"}),
        session_id=uuid4(),
        must_change_password=False,
    )

    async def authorize() -> AuthContext:
        return actor

    async def websocket_authorize(_token: str) -> AuthContext:
        return actor

    async def websocket_deny(_token: str) -> AuthContext:
        raise HTTPException(status_code=401)

    asyncio.run(
        model.reconcile(
            server_id,
            (
                AmiChannelSnapshot(
                    unique_id="channel-a",
                    linked_id="call-1",
                    channel="PJSIP/1001-0001",
                    state="Up",
                    caller_number="1001",
                    connected_number="2002",
                ),
            ),
            datetime.now(UTC),
        )
    )
    app.dependency_overrides[get_auth_context] = authorize
    app.dependency_overrides[get_call_read_model] = lambda: model
    app.dependency_overrides[get_websocket_authenticator] = lambda: websocket_authorize
    client = TestClient(app)
    try:
        assert client.get("/api/v1/calls/live").json()["servers"][0]["calls"][0][
            "logical_id"
        ] == "call-1"
        with client.websocket_connect("/api/v1/ws/calls") as websocket:
            websocket.send_json({"type": "authenticate", "token": "memory-only"})
            assert websocket.receive_json()["type"] == "calls.initial"

        app.dependency_overrides[get_websocket_authenticator] = lambda: websocket_deny
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/api/v1/ws/calls") as websocket:
                websocket.send_json({"type": "authenticate", "token": "invalid"})
                websocket.receive_json()
        assert denied.value.code == 4401
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_live_call_rest_requires_call_read_permission() -> None:
    actor = AuthContext(
        user_id=uuid4(),
        username="server-reader",
        permissions=frozenset({"server.read"}),
        session_id=uuid4(),
        must_change_password=False,
    )

    async def authorize() -> AuthContext:
        return actor

    app.dependency_overrides[get_auth_context] = authorize
    client = TestClient(app)
    try:
        assert client.get("/api/v1/calls/live").status_code == 403
    finally:
        client.close()
        app.dependency_overrides.clear()
