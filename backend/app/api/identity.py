import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AuthContext,
    hash_password,
    login_identifier_digest,
    normalize_username,
    require_permissions,
)
from app.db.models import AuditEvent, AuthSession, LoginAttempt, Permission, Profile, User
from app.db.session import get_session

router = APIRouter(tags=["identity-access"])
MANAGER_PERMISSIONS = {"user.manage", "profile.manage"}


class PermissionResponse(BaseModel):
    code: str
    description: str


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
    description: str = Field(default="", max_length=255)
    permission_codes: set[str] = Field(default_factory=set)


class ProfileUpdate(BaseModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$"
    )
    description: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    permission_codes: set[str] | None = None


class ProfileResponse(BaseModel):
    id: UUID
    name: str
    description: str
    enabled: bool
    is_system: bool
    permission_codes: list[str]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=12, max_length=256)
    profile_ids: set[UUID] = Field(default_factory=set)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=150)
    enabled: bool | None = None
    profile_ids: set[UUID] | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class UserResponse(BaseModel):
    id: UUID
    username: str
    display_name: str
    enabled: bool
    profile_ids: list[UUID]


def _profile_response(profile: Profile) -> ProfileResponse:
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        enabled=profile.enabled,
        is_system=profile.is_system,
        permission_codes=sorted(permission.code for permission in profile.permissions),
    )


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        enabled=user.enabled,
        profile_ids=sorted((profile.id for profile in user.profiles), key=str),
    )


async def _permissions_by_code(
    session: AsyncSession, codes: set[str], actor: AuthContext
) -> list[Permission]:
    if not codes.issubset(actor.permissions):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "privilege_escalation_denied", "message": "Permission denied"},
        )
    permissions = list(
        (await session.scalars(select(Permission).where(Permission.code.in_(codes)))).all()
    )
    if len(permissions) != len(codes):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_permission", "message": "Unknown permission code"},
        )
    return permissions


async def _profiles_by_id(
    session: AsyncSession, profile_ids: set[UUID], actor: AuthContext
) -> list[Profile]:
    profiles = list(
        (await session.scalars(select(Profile).where(Profile.id.in_(profile_ids)))).all()
    )
    if len(profiles) != len(profile_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_profile", "message": "Unknown profile"},
        )
    granted = {
        permission.code
        for profile in profiles
        if profile.enabled
        for permission in profile.permissions
    }
    if not granted.issubset(actor.permissions):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "privilege_escalation_denied", "message": "Permission denied"},
        )
    return profiles


async def _ensure_effective_manager_remains(session: AsyncSession) -> None:
    users = list((await session.scalars(select(User))).unique().all())
    for user in users:
        effective = {
            permission.code
            for profile in user.profiles
            if user.enabled and profile.enabled
            for permission in profile.permissions
        }
        if MANAGER_PERMISSIONS.issubset(effective):
            return
    await session.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "last_administrator", "message": "At least one administrator is required"},
    )


def _audit(actor: AuthContext, action: str, target_type: str, target_id: UUID) -> AuditEvent:
    return AuditEvent(
        occurred_at=datetime.now(UTC),
        actor_user_id=actor.user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata_json=json.dumps({}, separators=(",", ":")),
    )


@router.get("/permissions", response_model=list[PermissionResponse])
async def list_permissions(
    _actor: Annotated[AuthContext, require_permissions("profile.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PermissionResponse]:
    permissions = (await session.scalars(select(Permission).order_by(Permission.code))).all()
    return [
        PermissionResponse(code=item.code, description=item.description) for item in permissions
    ]


@router.get("/profiles", response_model=list[ProfileResponse])
async def list_profiles(
    _actor: Annotated[AuthContext, require_permissions("profile.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProfileResponse]:
    profiles = (await session.scalars(select(Profile).order_by(Profile.name))).unique().all()
    return [_profile_response(profile) for profile in profiles]


@router.post("/profiles", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: ProfileCreate,
    actor: Annotated[AuthContext, require_permissions("profile.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileResponse:
    if await session.scalar(select(Profile.id).where(Profile.name == payload.name.strip())):
        raise HTTPException(
            status_code=409, detail={"code": "profile_exists", "message": "Profile exists"}
        )
    profile = Profile(
        name=payload.name.strip(),
        description=payload.description.strip(),
        permissions=await _permissions_by_code(session, payload.permission_codes, actor),
    )
    session.add(profile)
    await session.flush()
    session.add(_audit(actor, "profile.created", "profile", profile.id))
    await session.commit()
    return _profile_response(profile)


@router.patch("/profiles/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: UUID,
    payload: ProfileUpdate,
    actor: Annotated[AuthContext, require_permissions("profile.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileResponse:
    profile = await session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Profile not found"}
        )
    if profile.is_system:
        raise HTTPException(
            status_code=409,
            detail={"code": "system_profile", "message": "System profile is immutable"},
        )
    if payload.name is not None:
        profile.name = payload.name.strip()
    if payload.description is not None:
        profile.description = payload.description.strip()
    if payload.enabled is not None:
        profile.enabled = payload.enabled
    if payload.permission_codes is not None:
        profile.permissions = await _permissions_by_code(session, payload.permission_codes, actor)
    await session.flush()
    await _ensure_effective_manager_remains(session)
    session.add(_audit(actor, "profile.updated", "profile", profile.id))
    await session.commit()
    return _profile_response(profile)


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _actor: Annotated[AuthContext, require_permissions("user.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[UserResponse]:
    users = (await session.scalars(select(User).order_by(User.username))).unique().all()
    return [_user_response(user) for user in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    actor: Annotated[AuthContext, require_permissions("user.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    username = normalize_username(payload.username)
    if await session.scalar(select(User.id).where(User.username == username)):
        raise HTTPException(
            status_code=409, detail={"code": "user_exists", "message": "User exists"}
        )
    now = datetime.now(UTC)
    user = User(
        username=username,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        must_change_password=False,
        profiles=await _profiles_by_id(session, payload.profile_ids, actor),
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    session.add(_audit(actor, "user.created", "user", user.id))
    await session.commit()
    return _user_response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    actor: Annotated[AuthContext, require_permissions("user.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "User not found"}
        )
    if actor.user_id == user_id and (payload.enabled is False or payload.profile_ids is not None):
        raise HTTPException(
            status_code=409,
            detail={"code": "self_lockout", "message": "Self access changes are not allowed"},
        )
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.enabled is not None:
        user.enabled = payload.enabled
    if payload.profile_ids is not None:
        user.profiles = await _profiles_by_id(session, payload.profile_ids, actor)
    user.updated_at = datetime.now(UTC)
    await session.flush()
    await _ensure_effective_manager_remains(session)
    if not user.enabled:
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    session.add(_audit(actor, "user.updated", "user", user.id))
    await session.commit()
    return _user_response(user)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: UUID,
    payload: PasswordReset,
    actor: Annotated[AuthContext, require_permissions("user.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "User not found"}
        )
    user.password_hash = hash_password(payload.password)
    user.must_change_password = True
    user.updated_at = datetime.now(UTC)
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.execute(
        delete(LoginAttempt).where(
            LoginAttempt.identifier_hash == login_identifier_digest(user.username)
        )
    )
    session.add(_audit(actor, "user.password_reset", "user", user.id))
    await session.commit()
