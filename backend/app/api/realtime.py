import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core.security import AuthContext, resolve_auth_context
from app.db.session import SessionFactory
from app.realtime.operational import OperationalRealtimeHub, operational_realtime_hub

router = APIRouter(tags=["operational-realtime"])
WebSocketAuthenticator = Callable[[str], Awaitable[AuthContext]]


def get_operational_realtime_hub() -> OperationalRealtimeHub:
    return operational_realtime_hub


def get_operational_websocket_authenticator() -> WebSocketAuthenticator:
    async def authenticate(token: str) -> AuthContext:
        async with SessionFactory() as session:
            return await resolve_auth_context(token, session)

    return authenticate


def _server_scope(message: dict[str, Any]) -> frozenset[UUID] | None:
    raw = message.get("server_ids")
    if raw is None:
        return None
    if (
        not isinstance(raw, list)
        or len(raw) > 200
        or not all(isinstance(item, str) for item in raw)
    ):
        raise ValueError("invalid server scope")
    return frozenset(UUID(item) for item in raw)


@router.websocket("/ws/operations")
async def operational_updates(
    websocket: WebSocket,
    hub: Annotated[OperationalRealtimeHub, Depends(get_operational_realtime_hub)],
    authenticate: Annotated[
        WebSocketAuthenticator, Depends(get_operational_websocket_authenticator)
    ],
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
        server_ids = _server_scope(message)
        if actor.must_change_password or not hub.permitted_event_types(actor):
            await websocket.close(code=4403, reason="Permission denied")
            return
    except WebSocketDisconnect:
        return
    except (TimeoutError, ValueError, HTTPException):
        await websocket.close(code=4401, reason="Authentication required")
        return

    subscription = await hub.subscribe(actor, server_ids=server_ids)
    try:
        await websocket.send_json(
            await hub.resync_message(actor, reason="initial_subscription")
        )
        while True:
            queued = await subscription.queue.get()
            try:
                actor = await authenticate(message["token"])
            except HTTPException:
                await websocket.close(code=4401, reason="Authentication required")
                return
            if actor.must_change_password or not hub.permitted_event_types(actor):
                await websocket.close(code=4403, reason="Permission denied")
                return
            if queued["type"] == "operations.resync_required":
                queued = await hub.resync_message(actor, reason="buffer_overflow")
            elif not hub.can_receive(actor, queued):
                continue
            await websocket.send_json(queued)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(subscription)
