import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthSession, AuthSessionAccessToken
from app.db.session import get_session

password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
_dummy_hash = password_hasher.hash("not-a-real-account-password")


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hasher.verify(encoded_hash, password)
    except VerificationError, InvalidHashError:
        return False


def perform_dummy_password_check(password: str) -> None:
    verify_password(password, _dummy_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login_identifier_digest(username: str) -> str:
    return hashlib.sha256(normalize_username(username).encode()).hexdigest()


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    username: str
    permissions: frozenset[str]
    session_id: UUID
    must_change_password: bool


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "authentication_required", "message": "Authentication required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthContext:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise _authentication_error()
    return await resolve_auth_context(credentials.credentials, session)


async def resolve_auth_context(token: str, session: AsyncSession) -> AuthContext:
    auth_session = await session.scalar(
        select(AuthSession)
        .outerjoin(AuthSessionAccessToken)
        .where(
            or_(
                AuthSession.token_hash == token_digest(token),
                AuthSessionAccessToken.token_hash == token_digest(token),
            )
        )
    )
    return auth_context_for_session(auth_session)


def auth_context_for_session(auth_session: AuthSession | None) -> AuthContext:
    now = datetime.now(UTC)
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or as_utc(auth_session.expires_at) <= now
        or not auth_session.user.enabled
    ):
        raise _authentication_error()
    permissions = frozenset(
        permission.code
        for profile in auth_session.user.profiles
        if profile.enabled
        for permission in profile.permissions
    )
    return AuthContext(
        user_id=auth_session.user.id,
        username=auth_session.user.username,
        permissions=permissions,
        session_id=auth_session.id,
        must_change_password=auth_session.user.must_change_password,
    )


def require_permissions(*required: str) -> object:
    async def authorize(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
        if context.must_change_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "password_change_required",
                    "message": "Password change required",
                },
            )
        if not set(required).issubset(context.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "permission_denied", "message": "Permission denied"},
            )
        return context

    return Depends(authorize)
