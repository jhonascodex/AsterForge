import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.security import AuthContext, require_permissions, resolve_auth_context
from app.db.session import SessionFactory
from app.services.call_observability import CallReadModel, LiveCall, ServerCalls

router = APIRouter(tags=["live-calls"])


class LiveCallResponse(BaseModel):
    server_id: UUID
    logical_id: str
    state: str
    caller_number: str | None
    connected_number: str | None
    channel_count: int
    channel_ids: tuple[str, ...]
    started_at: datetime
    observed_at: datetime
    freshness: Literal["unknown", "fresh", "stale"]


class ServerCallsResponse(BaseModel):
    server_id: UUID
    freshness: Literal["unknown", "fresh", "stale"]
    observed_at: datetime | None
    calls: list[LiveCallResponse]


class LiveCallsResponse(BaseModel):
    servers: list[ServerCallsResponse]


def _call_response(call: LiveCall) -> LiveCallResponse:
    return LiveCallResponse(
        server_id=call.server_id,
        logical_id=call.logical_id,
        state=call.state,
        caller_number=call.caller_number,
        connected_number=call.connected_number,
        channel_count=call.channel_count,
        channel_ids=call.channel_ids,
        started_at=call.started_at,
        observed_at=call.observed_at,
        freshness=call.freshness.value,
    )


def _server_response(value: ServerCalls) -> ServerCallsResponse:
    return ServerCallsResponse(
        server_id=value.server_id,
        freshness=value.freshness.value,
        observed_at=value.observed_at,
        calls=[_call_response(call) for call in value.calls],
    )


call_read_model = CallReadModel()


def get_call_read_model() -> CallReadModel:
    return call_read_model


class CallRealtimeHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=20)
        async with self._lock:
            self._queues.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._queues.discard(queue)

    async def publish(self, value: ServerCalls) -> None:
        response = _server_response(value)
        message = {
            "type": "calls.snapshot",
            "version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "resource": "live_calls",
            "server_id": str(value.server_id),
            "payload": response.model_dump(mode="json"),
        }
        async with self._lock:
            queues = tuple(self._queues)
        for queue in queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)


call_realtime_hub = CallRealtimeHub()


@router.get("/calls/live", response_model=LiveCallsResponse)
async def list_live_calls(
    _actor: Annotated[AuthContext, require_permissions("call.read")],
    read_model: Annotated[CallReadModel, Depends(get_call_read_model)],
) -> LiveCallsResponse:
    return LiveCallsResponse(
        servers=[_server_response(value) for value in await read_model.list()]
    )


WebSocketAuthenticator = Callable[[str], Awaitable[AuthContext]]


def get_websocket_authenticator() -> WebSocketAuthenticator:
    async def authenticate(token: str) -> AuthContext:
        async with SessionFactory() as session:
            return await resolve_auth_context(token, session)

    return authenticate


@router.websocket("/ws/calls")
async def live_call_updates(
    websocket: WebSocket,
    read_model: Annotated[CallReadModel, Depends(get_call_read_model)],
    authenticate: Annotated[WebSocketAuthenticator, Depends(get_websocket_authenticator)],
) -> None:
    await websocket.accept()
    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        if (
            not isinstance(message, dict)
            or message.get("type") != "authenticate"
            or not isinstance(message.get("token"), str)
        ):
            await websocket.close(code=4401, reason="Authentication required")
            return
        actor = await authenticate(message["token"])
        if actor.must_change_password or "call.read" not in actor.permissions:
            await websocket.close(code=4403, reason="Permission denied")
            return
    except WebSocketDisconnect:
        return
    except (TimeoutError, ValueError, HTTPException):
        await websocket.close(code=4401, reason="Authentication required")
        return

    queue = await call_realtime_hub.subscribe()
    try:
        initial = LiveCallsResponse(
            servers=[_server_response(value) for value in await read_model.list()]
        )
        await websocket.send_json(
            {
                "type": "calls.initial",
                "version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "resource": "live_calls",
                "server_id": None,
                "payload": initial.model_dump(mode="json"),
            }
        )
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        await call_realtime_hub.unsubscribe(queue)
