import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.asterisk_health import get_health_read_model
from app.core.security import hash_password, new_session_token, token_digest
from app.db.base import Base
from app.db.models import (
    AsteriskServer,
    AuditEvent,
    AuthSession,
    InventoryEnvironment,
    Permission,
    Profile,
    User,
)
from app.db.session import get_session
from app.integrations.ami import AmiCoreSnapshot
from app.main import app
from app.services.ami_monitoring import HealthReadModel


@pytest.fixture
def inventory_client() -> tuple[TestClient, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    asyncio.run(prepare())
    app.dependency_overrides[get_session] = test_session
    client = TestClient(app)
    try:
        yield client, factory
    finally:
        client.close()
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


async def _seed_actor(
    factory: async_sessionmaker[AsyncSession], permissions: set[str]
) -> tuple[str, InventoryEnvironment]:
    now = datetime.now(UTC)
    token = new_session_token()
    permission_models = [
        Permission(id=uuid4(), code=code, description=code) for code in permissions
    ]
    profile = Profile(id=uuid4(), name=f"profile-{uuid4()}", permissions=permission_models)
    user = User(
        id=uuid4(),
        username=f"user-{uuid4()}",
        display_name="Inventory Tester",
        password_hash=hash_password("not used in these tests"),
        profiles=[profile],
        created_at=now,
        updated_at=now,
    )
    async with factory() as session:
        environment = await session.scalar(
            select(InventoryEnvironment).where(InventoryEnvironment.code == "DEV")
        )
        if environment is None:
            environment = InventoryEnvironment(
                id=uuid4(), code="DEV", display_name="Development", enabled=True
            )
            session.add(environment)
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
    return token, environment


def test_inventory_routes_enforce_read_and_manage_permissions(
    inventory_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = inventory_client
    assert client.get("/api/v1/servers").status_code == 401
    read_token, environment = asyncio.run(_seed_actor(factory, {"server.read"}))
    headers = {"Authorization": f"Bearer {read_token}"}
    assert client.get("/api/v1/servers", headers=headers).status_code == 200
    denied = client.post(
        "/api/v1/servers",
        headers=headers,
        json={"name": "PBX", "hostname": "pbx.local", "environment_id": str(environment.id)},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"


def test_asterisk_health_api_is_authorized_and_exposes_normalized_state(
    inventory_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = inventory_client
    server_token, environment = asyncio.run(_seed_actor(factory, {"server.read"}))
    health_token, _ = asyncio.run(_seed_actor(factory, {"asterisk.read"}))
    server_id = uuid4()

    async def seed_server_and_health(read_model: HealthReadModel) -> None:
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(
                AsteriskServer(
                    id=server_id,
                    name="Health PBX",
                    name_key="health pbx",
                    hostname="health.test",
                    environment_id=environment.id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        await read_model.observed(
            server_id,
            AmiCoreSnapshot("pbx", "22.0.0", None, None, 3, 100),
            now,
        )

    read_model = HealthReadModel()
    asyncio.run(seed_server_and_health(read_model))
    app.dependency_overrides[get_health_read_model] = lambda: read_model
    try:
        denied = client.get(
            f"/api/v1/asterisk/servers/{server_id}/health",
            headers={"Authorization": f"Bearer {server_token}"},
        )
        assert denied.status_code == 403
        response = client.get(
            f"/api/v1/asterisk/servers/{server_id}/health",
            headers={"Authorization": f"Bearer {health_token}"},
        )
        assert response.status_code == 200
        assert response.json()["connection_state"] == "connected"
        assert response.json()["freshness"] == "fresh"
        assert response.json()["core"]["current_calls"] == 3
    finally:
        app.dependency_overrides.pop(get_health_read_model, None)


def test_inventory_crud_filtering_redaction_encryption_and_audit(
    inventory_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, factory = inventory_client
    token, _default_environment = asyncio.run(
        _seed_actor(factory, {"server.read", "server.manage"})
    )
    headers = {"Authorization": f"Bearer {token}"}
    qa = client.post(
        "/api/v1/environments",
        headers=headers,
        json={"code": "qa", "display_name": "Quality Assurance"},
    )
    assert qa.status_code == 201
    tag = client.post("/api/v1/server-tags", headers=headers, json={"name": " Lima "})
    assert tag.status_code == 201
    username = "asterforge-reader"
    secret = "never-return-or-log-this-secret"
    created = client.post(
        "/api/v1/servers",
        headers=headers,
        json={
            "name": "Lima PBX",
            "hostname": "10.20.30.40",
            "environment_id": qa.json()["id"],
            "ami_tls": True,
            "location": "Lima DC",
            "tag_ids": [tag.json()["id"]],
            "ami_username": username,
            "ami_secret": secret,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["ami_username_configured"] is True
    assert body["ami_secret_configured"] is True
    assert body["ami_tls"] is True
    assert username not in created.text
    assert secret not in created.text
    assert "ami_username" not in body
    assert "ami_secret" not in body

    filtered = client.get(
        "/api/v1/servers?environment=qa&tag=lima&monitoring_enabled=true",
        headers=headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == body["id"]
    assert client.get(
        "/api/v1/servers?search=Lima%20DC", headers=headers
    ).json()["total"] == 1
    assert client.get("/api/v1/servers?environment=PROD", headers=headers).json()["total"] == 0

    async def verify_storage() -> None:
        async with factory() as session:
            stored = await session.get(AsteriskServer, UUID(body["id"]))
            assert stored is not None
            assert stored.ami_username_encrypted not in (None, username)
            assert stored.ami_secret_encrypted not in (None, secret)
            events = list((await session.scalars(select(AuditEvent))).all())
            assert {event.action for event in events} >= {
                "inventory.environment_created",
                "inventory.tag_created",
                "inventory.server_created",
            }
            server_event = next(
                event for event in events if event.action == "inventory.server_created"
            )
            assert server_event.target_id == body["id"]
            assert all(username not in event.metadata_json for event in events)
            assert all(secret not in event.metadata_json for event in events)

    asyncio.run(verify_storage())

    empty_credentials = client.post(
        "/api/v1/servers",
        headers=headers,
        json={
            "name": "Second PBX",
            "hostname": "pbx-2.local",
            "environment_id": qa.json()["id"],
        },
    )
    partial = client.patch(
        f"/api/v1/servers/{empty_credentials.json()['id']}",
        headers=headers,
        json={"ami_username": "incomplete"},
    )
    assert partial.status_code == 422
    assert partial.json()["detail"]["code"] == "incomplete_ami_credentials"
    assert username not in caplog.text
    assert secret not in caplog.text


def test_monitoring_disable_and_archive_preserve_inventory_record(
    inventory_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = inventory_client
    token, environment = asyncio.run(_seed_actor(factory, {"server.read", "server.manage"}))
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/servers",
        headers=headers,
        json={
            "name": "Preserved PBX",
            "hostname": "pbx.example.test",
            "environment_id": str(environment.id),
        },
    )
    assert created.status_code == 201
    server_id = created.json()["id"]
    disabled = client.patch(
        f"/api/v1/servers/{server_id}",
        headers=headers,
        json={"monitoring_enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["monitoring_enabled"] is False
    assert client.delete(f"/api/v1/servers/{server_id}", headers=headers).status_code == 204
    assert client.get("/api/v1/servers", headers=headers).json()["total"] == 0
    archived = client.get("/api/v1/servers?include_archived=true", headers=headers).json()
    assert archived["total"] == 1
    assert archived["items"][0]["archived_at"] is not None

    async def verify_preserved() -> None:
        async with factory() as session:
            stored = await session.get(AsteriskServer, UUID(server_id))
            assert stored is not None
            assert stored.monitoring_enabled is False
            assert stored.archived_at is not None
            actions = {event.action for event in (await session.scalars(select(AuditEvent))).all()}
            assert "inventory.server_updated" in actions
            assert "inventory.server_archived" in actions

    asyncio.run(verify_preserved())


def test_environment_and_tag_validation_and_crud(
    inventory_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = inventory_client
    token, environment = asyncio.run(_seed_actor(factory, {"server.read", "server.manage"}))
    headers = {"Authorization": f"Bearer {token}"}
    updated_environment = client.patch(
        f"/api/v1/environments/{environment.id}",
        headers=headers,
        json={"display_name": "Developer Systems", "enabled": False},
    )
    assert updated_environment.status_code == 200
    assert updated_environment.json()["enabled"] is False
    assert client.get("/api/v1/environments", headers=headers).json() == []
    all_environments = client.get(
        "/api/v1/environments?include_disabled=true", headers=headers
    ).json()
    assert len(all_environments) == 1

    first = client.post("/api/v1/server-tags", headers=headers, json={"name": "Critical"})
    assert first.status_code == 201
    duplicate = client.post("/api/v1/server-tags", headers=headers, json={"name": "critical"})
    assert duplicate.status_code == 409
    renamed = client.patch(
        f"/api/v1/server-tags/{first.json()['id']}", headers=headers, json={"name": "Core"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Core"
    deleted = client.delete(f"/api/v1/server-tags/{first.json()['id']}", headers=headers)
    assert deleted.status_code == 204


def test_classification_lists_include_server_usage_counts(
    inventory_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = inventory_client
    token, environment = asyncio.run(_seed_actor(factory, {"server.read", "server.manage"}))
    headers = {"Authorization": f"Bearer {token}"}
    tag = client.post("/api/v1/server-tags", headers=headers, json={"name": "Critical"})
    assert tag.status_code == 201

    created = client.post(
        "/api/v1/servers",
        headers=headers,
        json={
            "name": "Counted PBX",
            "hostname": "counted.example.test",
            "environment_id": str(environment.id),
            "tag_ids": [tag.json()["id"]],
        },
    )
    assert created.status_code == 201

    environments = client.get(
        "/api/v1/environments?include_disabled=true", headers=headers
    ).json()
    tags = client.get("/api/v1/server-tags", headers=headers).json()
    assert next(item for item in environments if item["id"] == str(environment.id))[
        "server_count"
    ] == 1
    assert next(item for item in tags if item["id"] == tag.json()["id"])[
        "server_count"
    ] == 1

    archived = client.delete(
        f"/api/v1/servers/{created.json()['id']}", headers=headers
    )
    assert archived.status_code == 204
    archived_environment = client.get(
        "/api/v1/environments?include_disabled=true", headers=headers
    ).json()
    archived_tags = client.get("/api/v1/server-tags", headers=headers).json()
    assert next(item for item in archived_environment if item["id"] == str(environment.id))[
        "server_count"
    ] == 1
    assert next(item for item in archived_tags if item["id"] == tag.json()["id"])[
        "server_count"
    ] == 1
