import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import CONTINUATION_COOKIE
from app.core.config import get_settings
from app.core.security import (
    hash_password,
    login_identifier_digest,
    new_session_token,
    token_digest,
    verify_password,
)
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    AuthSession,
    AuthSessionAccessToken,
    LoginAttempt,
    Permission,
    Profile,
    User,
)
from app.db.session import get_session
from app.main import app
from app.services.bootstrap import ensure_initial_administrator


@pytest.fixture
def identity_client() -> tuple[TestClient, async_sessionmaker[AsyncSession]]:
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


async def _seed_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    username: str = "admin",
    permissions: set[str],
    user_enabled: bool = True,
    profile_enabled: bool = True,
    must_change_password: bool = False,
    password: str = "correct horse battery",
) -> tuple[User, str, Profile]:
    now = datetime.now(UTC)
    raw_token = new_session_token()
    async with factory() as session:
        permission_models: list[Permission] = []
        for code in permissions:
            existing = await session.scalar(select(Permission).where(Permission.code == code))
            permission_models.append(
                existing or Permission(id=uuid4(), code=code, description=code)
            )
        profile = Profile(
            id=uuid4(),
            name=f"{username}-profile",
            enabled=profile_enabled,
            permissions=permission_models,
        )
        user = User(
            id=uuid4(),
            username=username,
            display_name=username.title(),
            password_hash=hash_password(password),
            enabled=user_enabled,
            must_change_password=must_change_password,
            profiles=[profile],
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.add(
            AuthSession(
                id=uuid4(),
                token_hash=token_digest(raw_token),
                user=user,
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()
        return user, raw_token, profile


def test_login_logout_and_password_is_never_returned(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    asyncio.run(_seed_user(factory, permissions={"user.manage", "profile.manage"}))

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "ADMIN", "password": "correct horse battery"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert "password_hash" not in login.json()
    assert "correct horse battery" not in login.text
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
    assert (
        client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code
        == 204
    )
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


def test_browser_session_restoration_uses_http_only_revocable_continuation(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    asyncio.run(_seed_user(factory, permissions={"user.manage"}))

    login_csrf = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://attacker.invalid"},
        json={"username": "admin", "password": "correct horse battery"},
    )
    assert login_csrf.status_code == 403

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct horse battery"},
    )
    assert login.status_code == 200
    cookie_header = login.headers["set-cookie"].lower()
    assert f"{CONTINUATION_COOKIE}=" in cookie_header
    assert "httponly" in cookie_header
    assert "samesite=strict" in cookie_header
    assert "path=/api/v1/auth" in cookie_header
    continuation_token = client.cookies.get(CONTINUATION_COOKIE)
    assert continuation_token
    assert continuation_token not in login.text

    denied = client.post(
        "/api/v1/auth/restore", headers={"Origin": "https://attacker.invalid"}
    )
    assert denied.status_code == 403
    preflight = client.options(
        "/api/v1/auth/restore",
        headers={
            "Origin": "http://localhost:4173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:4173"
    assert preflight.headers["access-control-allow-credentials"] == "true"
    restored = client.post(
        "/api/v1/auth/restore", headers={"Origin": "http://localhost:4173"}
    )
    assert restored.status_code == 200
    restored_token = restored.json()["access_token"]
    assert restored_token != login.json()["access_token"]
    assert continuation_token not in restored.text
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {restored_token}"}
    ).status_code == 200

    latest_restored_token = restored_token
    for _ in range(10):
        additional_restore = client.post("/api/v1/auth/restore")
        assert additional_restore.status_code == 200
        latest_restored_token = additional_restore.json()["access_token"]

    async def verify_digests_only() -> None:
        async with factory() as session:
            auth_session = await session.scalar(
                select(AuthSession).where(
                    AuthSession.continuation_token_hash == token_digest(continuation_token)
                )
            )
            assert auth_session is not None
            restored_access = await session.scalar(
                select(AuthSessionAccessToken).where(
                    AuthSessionAccessToken.token_hash == token_digest(latest_restored_token)
                )
            )
            assert restored_access is not None
            assert continuation_token not in auth_session.continuation_token_hash
            assert latest_restored_token not in restored_access.token_hash
            restored_count = await session.scalar(
                select(func.count(AuthSessionAccessToken.id)).where(
                    AuthSessionAccessToken.session_id == auth_session.id
                )
            )
            assert restored_count == 8

    asyncio.run(verify_digests_only())
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {restored_token}"}
    ).status_code == 401
    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {latest_restored_token}"},
    )
    assert logout.status_code == 204
    assert client.cookies.get(CONTINUATION_COOKIE) is None
    assert client.post("/api/v1/auth/restore").status_code == 204


def test_secure_cookie_attribute_follows_deployment_setting(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory = identity_client
    asyncio.run(_seed_user(factory, permissions=set()))
    monkeypatch.setenv("ASTERFORGE_SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct horse battery"},
        )
        assert login.status_code == 200
        assert "secure" in login.headers["set-cookie"].lower()
    finally:
        get_settings.cache_clear()


def test_expired_or_disabled_browser_session_restoration_fails_closed(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    user, _seed_token, _profile = asyncio.run(
        _seed_user(factory, permissions={"dashboard.read"})
    )

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct horse battery"},
    )
    assert login.status_code == 200

    async def expire_session() -> None:
        continuation = client.cookies.get(CONTINUATION_COOKIE)
        assert continuation
        async with factory() as session:
            stored = await session.scalar(
                select(AuthSession).where(
                    AuthSession.continuation_token_hash == token_digest(continuation)
                )
            )
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire_session())
    expired = client.post("/api/v1/auth/restore")
    assert expired.status_code == 401
    assert client.cookies.get(CONTINUATION_COOKIE) is None

    fresh = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct horse battery"},
    )
    assert fresh.status_code == 200

    async def disable_user() -> None:
        async with factory() as session:
            stored = await session.get(User, user.id)
            assert stored is not None
            stored.enabled = False
            await session.commit()

    asyncio.run(disable_user())
    disabled = client.post("/api/v1/auth/restore")
    assert disabled.status_code == 401
    assert client.cookies.get(CONTINUATION_COOKIE) is None


def test_authentication_failure_is_generic_and_does_not_log_secret(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, factory = identity_client
    asyncio.run(_seed_user(factory, permissions=set(), user_enabled=False))
    secret = "do-not-log-this-password"
    disabled = client.post("/api/v1/auth/login", json={"username": "admin", "password": secret})
    missing = client.post("/api/v1/auth/login", json={"username": "missing", "password": secret})
    assert disabled.status_code == missing.status_code == 401
    assert disabled.json() == missing.json()
    assert "admin" not in disabled.text
    assert secret not in caplog.text


def test_private_routes_default_deny(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _factory = identity_client
    response = client.get("/api/v1/users")
    assert response.status_code == 401


def test_disabled_user_and_profile_remove_access_immediately(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    user, token, _profile = asyncio.run(_seed_user(factory, permissions={"user.manage"}))
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/users", headers=headers).status_code == 200

    async def disable_user() -> None:
        async with factory() as session:
            stored = await session.get(User, user.id)
            assert stored is not None
            stored.enabled = False
            await session.commit()

    asyncio.run(disable_user())
    assert client.get("/api/v1/users", headers=headers).status_code == 401

    _user2, token2, _profile2 = asyncio.run(
        _seed_user(factory, username="operator", permissions={"user.manage"}, profile_enabled=False)
    )
    assert (
        client.get("/api/v1/users", headers={"Authorization": f"Bearer {token2}"}).status_code
        == 403
    )


def test_user_manager_cannot_assign_privileges_they_do_not_have(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    _actor, token, _profile = asyncio.run(_seed_user(factory, permissions={"user.manage"}))

    async def create_privileged_profile() -> Profile:
        async with factory() as session:
            permission = Permission(
                id=uuid4(), code="profile.manage", description="Manage profiles"
            )
            profile = Profile(id=uuid4(), name="privileged", permissions=[permission])
            session.add(profile)
            await session.commit()
            return profile

    privileged = asyncio.run(create_privileged_profile())
    response = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": "new-user",
            "display_name": "New User",
            "password": "another secure password",
            "profile_ids": [str(privileged.id)],
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "privilege_escalation_denied"


def test_self_access_changes_are_rejected(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    actor, token, _profile = asyncio.run(
        _seed_user(factory, permissions={"user.manage", "profile.manage"})
    )
    response = client.patch(
        f"/api/v1/users/{actor.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "self_lockout"


def test_expired_sessions_are_rejected(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    _user, token, _profile = asyncio.run(_seed_user(factory, permissions=set()))

    async def expire_session() -> None:
        async with factory() as session:
            stored = await session.scalar(
                select(AuthSession).where(AuthSession.token_hash == token_digest(token))
            )
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire_session())
    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_repeated_failures_are_throttled(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _factory = identity_client
    payload = {"username": "unknown", "password": "invalid password"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "login_throttled"


def test_environment_bootstrap_is_idempotent_and_keeps_existing_password(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    _client, factory = identity_client
    first_password = "first temporary password"
    replacement_password = "must never replace first"

    async def bootstrap_twice() -> tuple[int, User, Profile, list[AuditEvent]]:
        async with factory() as session:
            session.add_all(
                [
                    Permission(id=uuid4(), code="user.manage", description="Manage users"),
                    Permission(id=uuid4(), code="profile.manage", description="Manage profiles"),
                ]
            )
            await session.commit()
            assert await ensure_initial_administrator(
                session, username="Initial.Admin", temporary_password=first_password
            )
            assert not await ensure_initial_administrator(
                session, username="initial.admin", temporary_password=replacement_password
            )
            users = list((await session.scalars(select(User))).unique().all())
            profile = await session.scalar(
                select(Profile).where(Profile.name == "Super Administrator")
            )
            events = list((await session.scalars(select(AuditEvent))).all())
            assert profile is not None
            return len(users), users[0], profile, events

    count, user, profile, events = asyncio.run(bootstrap_twice())
    assert count == 1
    assert user.username == "initial.admin"
    assert user.must_change_password
    assert verify_password(first_password, user.password_hash)
    assert not verify_password(replacement_password, user.password_hash)
    assert {permission.code for permission in profile.permissions} == {
        "user.manage",
        "profile.manage",
    }
    assert [event.action for event in events] == ["user.bootstrap_created"]
    assert first_password not in events[0].metadata_json
    assert replacement_password not in events[0].metadata_json


def test_forced_change_allows_only_identity_logout_and_password_change(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    _user, _token, _profile = asyncio.run(
        _seed_user(
            factory,
            permissions={"user.manage", "profile.manage"},
            must_change_password=True,
        )
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct horse battery"},
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True

    restored = client.post("/api/v1/auth/restore")
    assert restored.status_code == 200
    assert restored.json()["must_change_password"] is True
    restored_headers = {
        "Authorization": f"Bearer {restored.json()['access_token']}"
    }
    assert client.get("/api/v1/users", headers=restored_headers).status_code == 403

    denied = client.get("/api/v1/users", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "password_change_required"
    assert client.get("/api/v1/profiles", headers=headers).status_code == 403
    assert client.get("/api/v1/permissions", headers=headers).status_code == 403
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204


def test_password_change_clears_forced_state_and_revokes_other_sessions(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    _user, older_token, _profile = asyncio.run(
        _seed_user(
            factory,
            permissions={"user.manage"},
            must_change_password=True,
        )
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct horse battery"},
    )
    current_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {current_token}"}
    changed = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": "correct horse battery",
            "new_password": "a new strong password",
        },
    )
    assert changed.status_code == 204
    assert client.get("/api/v1/users", headers=headers).status_code == 200
    restored = client.post("/api/v1/auth/restore")
    assert restored.status_code == 200
    assert restored.json()["must_change_password"] is False
    assert client.get(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {restored.json()['access_token']}"},
    ).status_code == 200
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {older_token}"}
    ).status_code == 401
    new_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "a new strong password"},
    )
    assert new_login.status_code == 200
    assert new_login.json()["must_change_password"] is False


def test_administrator_reset_forces_change_and_revokes_target_sessions(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = identity_client
    _admin, admin_token, _admin_profile = asyncio.run(
        _seed_user(factory, username="admin", permissions={"user.manage"})
    )
    target, target_token, _target_profile = asyncio.run(
        _seed_user(factory, username="target", permissions={"user.manage"})
    )
    target_browser_login = client.post(
        "/api/v1/auth/login",
        json={"username": "target", "password": "correct horse battery"},
    )
    assert target_browser_login.status_code == 200
    assert client.cookies.get(CONTINUATION_COOKIE)

    async def block_target_login() -> None:
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(
                LoginAttempt(
                    identifier_hash=login_identifier_digest("target"),
                    failed_count=5,
                    window_started_at=now,
                    blocked_until=now + timedelta(minutes=15),
                )
            )
            await session.commit()

    asyncio.run(block_target_login())
    reset_password = "temporary reset password"
    response = client.post(
        f"/api/v1/users/{target.id}/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"password": reset_password},
    )
    assert response.status_code == 204
    assert reset_password not in response.text
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {target_token}"}
    ).status_code == 401
    assert client.post("/api/v1/auth/restore").status_code == 401
    assert client.cookies.get(CONTINUATION_COOKIE) is None

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "target", "password": reset_password},
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": reset_password, "new_password": "target final password"},
    ).status_code == 204
    assert client.get("/api/v1/users", headers=headers).status_code == 200

    async def read_security_events() -> list[AuditEvent]:
        async with factory() as session:
            return list((await session.scalars(select(AuditEvent))).all())

    events = asyncio.run(read_security_events())
    assert "user.password_reset" in {event.action for event in events}
    assert "user.password_changed" in {event.action for event in events}
    assert all(reset_password not in event.metadata_json for event in events)


def test_password_change_rejects_wrong_current_and_reuse_without_leaking_secrets(
    identity_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, factory = identity_client
    _user, token, _profile = asyncio.run(_seed_user(factory, permissions=set()))
    headers = {"Authorization": f"Bearer {token}"}
    wrong = "wrong current password"
    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": wrong, "new_password": "unused strong password"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_current_password"
    assert wrong not in response.text
    assert wrong not in caplog.text

    reused = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": "correct horse battery",
            "new_password": "correct horse battery",
        },
    )
    assert reused.status_code == 422
    assert reused.json()["detail"]["code"] == "password_reuse"
