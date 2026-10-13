import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    PjsipContact,
    PjsipEndpoint,
    PjsipRegistration,
    PjsipTransport,
    SipTrunk,
    TrunkStateTransition,
)
from app.integrations.ami import AmiPjsipSnapshot, AmiRegistrationSnapshot
from app.realtime.operational import OperationalPublisher

logger = logging.getLogger(__name__)


class PjsipSnapshotStore(Protocol):
    async def reconcile(
        self, server_id: UUID, snapshot: AmiPjsipSnapshot, observed_at: datetime
    ) -> None: ...

    async def mark_stale(self, server_id: UUID) -> None: ...


class TrunkAlertSink(Protocol):
    async def evaluate_trunk(
        self, server_id: UUID, resource_key: str, state: str, observed_at: datetime
    ) -> UUID | None: ...

    async def evaluate_endpoint(
        self, server_id: UUID, resource_key: str, state: str, observed_at: datetime
    ) -> UUID | None: ...


def _provider(registration: AmiRegistrationSnapshot) -> str | None:
    value = registration.server_uri
    if not value:
        return None
    authority = value.split("@", maxsplit=1)[-1]
    authority = authority.removeprefix("sip:").removeprefix("sips:")
    return authority.split(";", maxsplit=1)[0][:255] or None


class DatabasePjsipSnapshotStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        alert_sink: TrunkAlertSink | None = None,
        operational_publisher: OperationalPublisher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._alert_sink = alert_sink
        self._operational_publisher = operational_publisher

    async def reconcile(
        self, server_id: UUID, snapshot: AmiPjsipSnapshot, observed_at: datetime
    ) -> None:
        async with self._session_factory() as session:
            if "endpoints" in snapshot.capabilities:
                existing = {
                    item.source_id: item
                    for item in (
                        await session.scalars(
                            select(PjsipEndpoint).where(PjsipEndpoint.server_id == server_id)
                        )
                    ).all()
                }
                seen: set[str] = set()
                for endpoint in snapshot.endpoints:
                    seen.add(endpoint.source_id)
                    endpoint_model = existing.get(endpoint.source_id)
                    if endpoint_model is None:
                        endpoint_model = PjsipEndpoint(
                            id=uuid4(), server_id=server_id, source_id=endpoint.source_id
                        )
                        session.add(endpoint_model)
                    endpoint_model.state = endpoint.state.value
                    endpoint_model.raw_state = endpoint.raw_state
                    endpoint_model.transport = endpoint.transport
                    endpoint_model.aors = endpoint.aors
                    endpoint_model.active_channels = endpoint.active_channels
                    endpoint_model.contacts_count = endpoint.contacts_count
                    endpoint_model.observed_at = observed_at
                    endpoint_model.stale = False
                for source_id, endpoint_model in existing.items():
                    if source_id not in seen:
                        endpoint_model.stale = True
            else:
                await session.execute(
                    update(PjsipEndpoint)
                    .where(PjsipEndpoint.server_id == server_id)
                    .values(stale=True)
                )

            if "contacts" in snapshot.capabilities:
                existing_contacts = {
                    item.source_id: item
                    for item in (
                        await session.scalars(
                            select(PjsipContact).where(PjsipContact.server_id == server_id)
                        )
                    ).all()
                }
                seen_contacts: set[str] = set()
                for contact in snapshot.contacts:
                    seen_contacts.add(contact.source_id)
                    contact_model = existing_contacts.get(contact.source_id)
                    if contact_model is None:
                        contact_model = PjsipContact(
                            id=uuid4(), server_id=server_id, source_id=contact.source_id
                        )
                        session.add(contact_model)
                    contact_model.endpoint_name = contact.endpoint_name
                    contact_model.aor = contact.aor
                    contact_model.uri = contact.uri
                    contact_model.address = contact.address
                    contact_model.port = contact.port
                    contact_model.user_agent = contact.user_agent
                    contact_model.state = contact.state.value
                    contact_model.raw_state = contact.raw_state
                    contact_model.roundtrip_usec = contact.roundtrip_usec
                    contact_model.observed_at = observed_at
                    contact_model.stale = False
                for source_id, contact_model in existing_contacts.items():
                    if source_id not in seen_contacts:
                        contact_model.stale = True
            else:
                await session.execute(
                    update(PjsipContact)
                    .where(PjsipContact.server_id == server_id)
                    .values(stale=True)
                )

            await self._reconcile_registrations(session, server_id, snapshot, observed_at)
            if "transports" in snapshot.capabilities:
                existing_transports = {
                    item.source_id: item
                    for item in (
                        await session.scalars(
                            select(PjsipTransport).where(PjsipTransport.server_id == server_id)
                        )
                    ).all()
                }
                seen_transports: set[str] = set()
                for transport in snapshot.transports:
                    seen_transports.add(transport.source_id)
                    transport_model = existing_transports.get(transport.source_id)
                    if transport_model is None:
                        transport_model = PjsipTransport(
                            id=uuid4(), server_id=server_id, source_id=transport.source_id
                        )
                        session.add(transport_model)
                    transport_model.protocol = transport.protocol
                    transport_model.bind = transport.bind
                    transport_model.observed_at = observed_at
                    transport_model.stale = False
                for source_id, transport_model in existing_transports.items():
                    if source_id not in seen_transports:
                        transport_model.stale = True
            else:
                await session.execute(
                    update(PjsipTransport)
                    .where(PjsipTransport.server_id == server_id)
                    .values(stale=True)
                )

            if "outbound_registrations" in snapshot.capabilities:
                await self._reconcile_trunks(session, server_id, snapshot, observed_at)
            else:
                await session.execute(
                    update(SipTrunk)
                    .where(SipTrunk.server_id == server_id)
                    .values(stale=True)
                )
            await session.commit()
        if self._operational_publisher is not None:
            await self._operational_publisher(
                "pjsip.snapshot.changed", server_id, observed_at
            )
        if self._alert_sink is not None and "outbound_registrations" in snapshot.capabilities:
            for registration in snapshot.registrations:
                if registration.direction == "outbound":
                    try:
                        await self._alert_sink.evaluate_trunk(
                            server_id,
                            registration.source_id,
                            registration.state.value,
                            observed_at,
                        )
                    except SQLAlchemyError:
                        logger.warning(
                            "Trunk alert evaluation failed",
                            extra={"server_id": str(server_id)},
                        )
        if self._alert_sink is not None and "endpoints" in snapshot.capabilities:
            for endpoint in snapshot.endpoints:
                try:
                    await self._alert_sink.evaluate_endpoint(
                        server_id,
                        endpoint.source_id,
                        endpoint.state.value,
                        observed_at,
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "Endpoint alert evaluation failed",
                        extra={"server_id": str(server_id)},
                    )

    async def _reconcile_registrations(
        self,
        session: AsyncSession,
        server_id: UUID,
        snapshot: AmiPjsipSnapshot,
        observed_at: datetime,
    ) -> None:
        directions = {
            direction
            for direction, capability in (
                ("inbound", "inbound_registrations"),
                ("outbound", "outbound_registrations"),
            )
            if capability in snapshot.capabilities
        }
        for direction, capability in (
            ("inbound", "inbound_registrations"),
            ("outbound", "outbound_registrations"),
        ):
            if capability not in snapshot.capabilities:
                await session.execute(
                    update(PjsipRegistration)
                    .where(
                        PjsipRegistration.server_id == server_id,
                        PjsipRegistration.direction == direction,
                    )
                    .values(stale=True)
                )
        if not directions:
            return
        existing = {
            (item.direction, item.source_id): item
            for item in (
                await session.scalars(
                    select(PjsipRegistration).where(
                        PjsipRegistration.server_id == server_id,
                        PjsipRegistration.direction.in_(directions),
                    )
                )
            ).all()
        }
        seen: set[tuple[str, str]] = set()
        for value in snapshot.registrations:
            if value.direction not in directions:
                continue
            key = (value.direction, value.source_id)
            seen.add(key)
            item = existing.get(key)
            if item is None:
                item = PjsipRegistration(
                    id=uuid4(),
                    server_id=server_id,
                    direction=value.direction,
                    source_id=value.source_id,
                )
                session.add(item)
            item.endpoint_name = value.endpoint_name
            item.server_uri = value.server_uri
            item.client_uri = value.client_uri
            item.transport = value.transport
            item.state = value.state.value
            item.raw_state = value.raw_state
            item.observed_at = observed_at
            item.stale = False
        for key, item in existing.items():
            if key not in seen:
                item.stale = True

    async def _reconcile_trunks(
        self,
        session: AsyncSession,
        server_id: UUID,
        snapshot: AmiPjsipSnapshot,
        observed_at: datetime,
    ) -> None:
        existing = {
            item.source_id: item
            for item in (
                await session.scalars(select(SipTrunk).where(SipTrunk.server_id == server_id))
            ).all()
        }
        seen: set[str] = set()
        for value in snapshot.registrations:
            if value.direction != "outbound":
                continue
            seen.add(value.source_id)
            item = existing.get(value.source_id)
            previous_state = item.state if item is not None else None
            if item is None:
                item = SipTrunk(
                    id=uuid4(),
                    server_id=server_id,
                    source_id=value.source_id,
                    label=value.source_id,
                    state=value.state.value,
                    last_state_change_at=observed_at,
                    observed_at=observed_at,
                )
                session.add(item)
            if previous_state != value.state.value:
                item.last_state_change_at = observed_at
                session.add(
                    TrunkStateTransition(
                        id=uuid4(),
                        trunk_id=item.id,
                        from_state=previous_state,
                        to_state=value.state.value,
                        raw_state=value.raw_state,
                        occurred_at=observed_at,
                    )
                )
            item.provider = _provider(value)
            item.state = value.state.value
            item.raw_state = value.raw_state
            item.endpoint_name = value.endpoint_name
            item.server_uri = value.server_uri
            item.observed_at = observed_at
            item.stale = False
        for source_id, item in existing.items():
            if source_id not in seen:
                item.stale = True

    async def mark_stale(self, server_id: UUID) -> None:
        models: Sequence[
            type[
                PjsipEndpoint
                | PjsipContact
                | PjsipRegistration
                | PjsipTransport
                | SipTrunk
            ]
        ] = (
            PjsipEndpoint,
            PjsipContact,
            PjsipRegistration,
            PjsipTransport,
            SipTrunk,
        )
        async with self._session_factory() as session:
            for model in models:
                await session.execute(
                    update(model).where(model.server_id == server_id).values(stale=True)
                )
            await session.commit()
        if self._operational_publisher is not None:
            await self._operational_publisher(
                "pjsip.snapshot.changed", server_id, datetime.now(UTC)
            )
