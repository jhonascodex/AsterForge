import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import as_utc, hash_password, new_session_token, token_digest
from app.db.base import Base
from app.db.models import (
    AsteriskServer,
    AuthSession,
    InventoryEnvironment,
    Permission,
    PjsipEndpoint,
    Profile,
    SipTrunk,
    TrunkStateTransition,
    User,
)
from app.db.session import get_session
from app.integrations.ami import (
    AmiContactSnapshot,
    AmiEndpointSnapshot,
    AmiPjsipSnapshot,
    AmiRegistrationSnapshot,
    AmiTransportSnapshot,
    ContactState,
    EndpointState,
    RegistrationState,
    StreamAmiSession,
    normalize_contact_state,
    normalize_endpoint_state,
    normalize_registration_state,
)
from app.main import app
from app.realtime.operational import OperationalEventType
from app.services.pjsip_monitoring import DatabasePjsipSnapshotStore


@pytest.fixture
def pjsip_database() -> tuple[
    async_sessionmaker[AsyncSession], AsteriskServer, InventoryEnvironment
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    environment = InventoryEnvironment(
        id=uuid4(), code="PJSIP", display_name="PJSIP tests", enabled=True
    )
    now = datetime.now(UTC)
    server = AsteriskServer(
        id=uuid4(),
        name="PJSIP PBX",
        name_key=f"pjsip-{uuid4()}",
        hostname="pjsip.test",
        environment=environment,
        created_at=now,
        updated_at=now,
    )

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(server)
            await session.commit()

    asyncio.run(prepare())
    try:
        yield factory, server, environment
    finally:
        asyncio.run(engine.dispose())


def _snapshot(outbound_state: RegistrationState) -> AmiPjsipSnapshot:
    return AmiPjsipSnapshot(
        endpoints=(
            AmiEndpointSnapshot(
                source_id="1001",
                state=EndpointState.AVAILABLE,
                raw_state="NOT_INUSE",
                transport="transport-udp",
                aors="1001",
                active_channels=0,
                contacts_count=1,
            ),
        ),
        contacts=(
            AmiContactSnapshot(
                source_id="1001/sip:1001@10.0.0.10:5060",
                endpoint_name="1001",
                aor="1001",
                uri="sip:1001@10.0.0.10:5060",
                address="10.0.0.10",
                port=5060,
                user_agent="Synthetic phone",
                state=ContactState.AVAILABLE,
                raw_state="Reachable",
                roundtrip_usec=1200,
            ),
        ),
        registrations=(
            AmiRegistrationSnapshot(
                source_id="carrier-lima",
                direction="outbound",
                endpoint_name="carrier-endpoint",
                server_uri="sip:registrar.carrier.test:5060",
                client_uri="sip:pbx@carrier.test",
                transport="transport-udp",
                state=outbound_state,
                raw_state=outbound_state.value.title(),
            ),
            AmiRegistrationSnapshot(
                source_id="1001/contact",
                direction="inbound",
                endpoint_name="1001",
                server_uri=None,
                client_uri="sip:1001@10.0.0.10:5060",
                transport=None,
                state=RegistrationState.REGISTERED,
                raw_state="Reachable",
            ),
        ),
        transports=(AmiTransportSnapshot(source_id="transport-udp"),),
        capabilities=frozenset(
            {
                "endpoints",
                "contacts",
                "inbound_registrations",
                "outbound_registrations",
                "transports",
            }
        ),
    )


def test_unknown_pjsip_states_are_normalized_without_failure() -> None:
    assert normalize_endpoint_state("future-device-state") is EndpointState.UNKNOWN
    assert normalize_contact_state("future-contact-state") is ContactState.UNKNOWN
    assert normalize_registration_state("future-registration-state") is RegistrationState.UNKNOWN


def test_ami_session_collects_supported_lists_and_skips_unsupported_capabilities() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def exercise() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"Response: Success\r\n\r\n"
            b"Event: EndpointList\r\nObjectName: 1001\r\nDeviceState: FUTURE\r\n"
            b"Transport: udp-main\r\nAor: 1001\r\nContacts: one,two\r\n\r\n"
            b"Event: EndpointListComplete\r\nEventList: Complete\r\n\r\n"
            b"Response: Error\r\nMessage: Action not registered\r\n\r\n"
            b"Response: Success\r\n\r\n"
            b"Event: ContactStatusDetail\r\nID: inbound-1001\r\nAOR: 1001\r\n"
            b"URI: sip:1001@10.0.0.10\r\nStatus: Reachable\r\n\r\n"
            b"Event: ContactStatusDetailComplete\r\nEventList: Complete\r\n\r\n"
            b"Response: Success\r\n\r\n"
            b"Event: OutboundRegistrationDetail\r\nObjectName: carrier\r\n"
            b"ServerUri: sip:carrier.test\r\nStatus: Registered\r\n"
            b"Transport: udp-main\r\n\r\n"
            b"Event: OutboundRegistrationDetailComplete\r\nEventList: Complete\r\n\r\n"
        )
        writer = FakeWriter()
        session = StreamAmiSession(
            reader, cast(asyncio.StreamWriter, writer), action_timeout=0.1
        )
        snapshot = await session.pjsip_snapshot()
        assert snapshot.capabilities == frozenset(
            {
                "endpoints",
                "inbound_registrations",
                "outbound_registrations",
                "transports",
            }
        )
        assert snapshot.endpoints[0].state is EndpointState.UNKNOWN
        assert snapshot.endpoints[0].contacts_count == 2
        assert [item.direction for item in snapshot.registrations] == [
            "inbound",
            "outbound",
        ]
        assert snapshot.transports[0].source_id == "udp-main"
        actions = b"".join(writer.writes)
        assert b"Action: PJSIPShowEndpoints" in actions
        assert b"Action: PJSIPShowContacts" in actions
        assert b"Action: PJSIPShowRegistrationInboundContactStatuses" in actions
        assert b"Action: PJSIPShowRegistrationsOutbound" in actions

    asyncio.run(exercise())


def test_reconciliation_persists_resources_transitions_and_stale_state(
    pjsip_database: tuple[
        async_sessionmaker[AsyncSession], AsteriskServer, InventoryEnvironment
    ],
) -> None:
    factory, server, _environment = pjsip_database
    published: list[tuple[OperationalEventType, UUID, datetime]] = []

    async def publish(
        event_type: OperationalEventType, server_id: UUID, observed_at: datetime
    ) -> bool:
        published.append((event_type, server_id, observed_at))
        return True

    store = DatabasePjsipSnapshotStore(factory, operational_publisher=publish)
    first_at = datetime.now(UTC)
    second_at = first_at + timedelta(seconds=30)

    async def exercise() -> None:
        await store.reconcile(server.id, _snapshot(RegistrationState.UNREGISTERED), first_at)
        await store.reconcile(server.id, _snapshot(RegistrationState.REGISTERED), second_at)
        async with factory() as session:
            trunk = await session.scalar(select(SipTrunk))
            assert trunk is not None
            assert trunk.state == RegistrationState.REGISTERED.value
            assert as_utc(trunk.last_state_change_at) == second_at
            transitions = list(
                (
                    await session.scalars(
                        select(TrunkStateTransition).order_by(
                            TrunkStateTransition.occurred_at
                        )
                    )
                ).all()
            )
            assert [(item.from_state, item.to_state) for item in transitions] == [
                (None, "unregistered"),
                ("unregistered", "registered"),
            ]
        empty = AmiPjsipSnapshot(
            (), (), (), (), frozenset({"endpoints", "contacts", "transports"})
        )
        await store.reconcile(server.id, empty, second_at + timedelta(seconds=30))
        async with factory() as session:
            endpoint = await session.scalar(select(PjsipEndpoint))
            trunk = await session.scalar(select(SipTrunk))
            assert endpoint is not None and endpoint.stale
            # A capability that disappears cannot leave its prior state looking current.
            assert trunk is not None and trunk.stale
        await store.mark_stale(server.id)
        async with factory() as session:
            trunk = await session.scalar(select(SipTrunk))
            assert trunk is not None and trunk.stale
        assert len(published) == 4
        assert all(item[0] == "pjsip.snapshot.changed" for item in published)
        assert all(item[1] == server.id for item in published)

    asyncio.run(exercise())


def test_reconciliation_evaluates_endpoint_only_when_capability_is_available(
    pjsip_database: tuple[
        async_sessionmaker[AsyncSession], AsteriskServer, InventoryEnvironment
    ],
) -> None:
    factory, server, _environment = pjsip_database
    endpoints: list[tuple[str, str]] = []

    class AlertSink:
        async def evaluate_trunk(
            self, server_id: UUID, resource_key: str, state: str, observed_at: datetime
        ) -> None:
            del server_id, resource_key, state, observed_at

        async def evaluate_endpoint(
            self, server_id: UUID, resource_key: str, state: str, observed_at: datetime
        ) -> None:
            del observed_at
            assert server_id == server.id
            endpoints.append((resource_key, state))

    async def exercise() -> None:
        store = DatabasePjsipSnapshotStore(factory, AlertSink())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        await store.reconcile(server.id, _snapshot(RegistrationState.REGISTERED), now)
        assert endpoints == [("1001", "available")]
        endpoints.clear()
        unsupported = AmiPjsipSnapshot((), (), (), (), frozenset())
        await store.reconcile(server.id, unsupported, now + timedelta(seconds=30))
        assert endpoints == []

    asyncio.run(exercise())


def test_pjsip_apis_are_authorized_filterable_and_paginated(
    pjsip_database: tuple[
        async_sessionmaker[AsyncSession], AsteriskServer, InventoryEnvironment
    ],
) -> None:
    factory, server, _environment = pjsip_database
    store = DatabasePjsipSnapshotStore(factory)
    asyncio.run(
        store.reconcile(
            server.id, _snapshot(RegistrationState.REGISTERED), datetime.now(UTC)
        )
    )

    async def seed_actor(permission_codes: set[str]) -> str:
        now = datetime.now(UTC)
        token = new_session_token()
        permissions = [
            Permission(id=uuid4(), code=code, description=code) for code in permission_codes
        ]
        profile = Profile(id=uuid4(), name=f"profile-{uuid4()}", permissions=permissions)
        user = User(
            id=uuid4(),
            username=f"user-{uuid4()}",
            display_name="PJSIP Tester",
            password_hash=hash_password("not used"),
            profiles=[profile],
            created_at=now,
            updated_at=now,
        )
        async with factory() as session:
            session.add_all(
                [
                    user,
                    AuthSession(
                        id=uuid4(),
                        token_hash=token_digest(token),
                        user=user,
                        created_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()
        return token

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    denied_token = asyncio.run(seed_actor({"server.read"}))
    allowed_token = asyncio.run(seed_actor({"asterisk.read"}))
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/asterisk/endpoints").status_code == 401
            denied = client.get(
                "/api/v1/asterisk/endpoints",
                headers={"Authorization": f"Bearer {denied_token}"},
            )
            assert denied.status_code == 403
            headers = {"Authorization": f"Bearer {allowed_token}"}
            endpoints = client.get(
                f"/api/v1/asterisk/endpoints?server_id={server.id}&state=available&limit=1",
                headers=headers,
            )
            assert endpoints.status_code == 200
            assert endpoints.json()["total"] == 1
            assert endpoints.json()["limit"] == 1
            trunks = client.get(
                f"/api/v1/asterisk/trunks?server_id={server.id}&state=registered",
                headers=headers,
            )
            assert trunks.status_code == 200
            trunk = trunks.json()["items"][0]
            assert trunk["provider"] == "registrar.carrier.test:5060"
            transitions = client.get(
                f"/api/v1/asterisk/trunks/{trunk['id']}/transitions?limit=1",
                headers=headers,
            )
            assert transitions.status_code == 200
            assert transitions.json()["total"] == 1
            assert transitions.json()["items"][0]["to_state"] == "registered"
    finally:
        app.dependency_overrides.clear()
