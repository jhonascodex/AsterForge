from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    AuthContext,
    as_utc,
    auth_context_for_session,
    get_auth_context,
    login_identifier_digest,
    new_session_token,
    normalize_username,
    password_hasher,
    perform_dummy_password_check,
    token_digest,
    verify_password,
)
from app.db.models import AuditEvent, AuthSession, AuthSessionAccessToken, LoginAttempt, User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["authentication"])
CONTINUATION_COOKIE = "asterforge_session_continuation"
CONTINUATION_COOKIE_PATH = "/api/v1/auth"
MAX_RESTORED_ACCESS_TOKENS = 8


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    must_change_password: bool


class CurrentUserResponse(BaseModel):
    id: str
    username: str
    permissions: list[str]
    must_change_password: bool


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_credentials", "message": "Invalid credentials"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _set_continuation_cookie(response: Response, token: str, expires_at: datetime) -> None:
    settings = get_settings()
    max_age = max(0, int((as_utc(expires_at) - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=CONTINUATION_COOKIE,
        value=token,
        max_age=max_age,
        expires=expires_at,
        path=CONTINUATION_COOKIE_PATH,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_continuation_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CONTINUATION_COOKIE,
        path=CONTINUATION_COOKIE_PATH,
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _validate_continuation_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed_origins = {str(item).rstrip("/") for item in get_settings().cors_origins}
    if (origin is not None and origin.rstrip("/") not in allowed_origins) or (
        request.headers.get("sec-fetch-site", "").casefold() == "cross-site"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "origin_denied", "message": "Request origin denied"},
        )


async def _record_failure(session: AsyncSession, identifier_hash: str, now: datetime) -> None:
    settings = get_settings()
    attempt = await session.scalar(
        select(LoginAttempt)
        .where(LoginAttempt.identifier_hash == identifier_hash)
        .with_for_update()
    )
    window = timedelta(minutes=settings.login_attempt_window_minutes)
    if attempt is None:
        attempt = LoginAttempt(
            identifier_hash=identifier_hash, failed_count=1, window_started_at=now
        )
        session.add(attempt)
    elif now - as_utc(attempt.window_started_at) >= window:
        attempt.failed_count = 1
        attempt.window_started_at = now
        attempt.blocked_until = None
    else:
        attempt.failed_count += 1
    if attempt.failed_count >= settings.login_attempt_limit:
        attempt.blocked_until = now + timedelta(minutes=settings.login_block_minutes)
    session.add(
        AuditEvent(
            occurred_at=now,
            action="auth.login_failed",
            target_type="authentication",
            target_id=identifier_hash,
            outcome="failure",
            source="authentication",
        )
    )
    await session.commit()


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    _validate_continuation_origin(request)
    now = datetime.now(UTC)
    identifier_hash = login_identifier_digest(payload.username)
    attempt = await session.scalar(
        select(LoginAttempt).where(LoginAttempt.identifier_hash == identifier_hash)
    )
    if (
        attempt is not None
        and attempt.blocked_until is not None
        and as_utc(attempt.blocked_until) > now
    ):
        perform_dummy_password_check(payload.password)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "login_throttled", "message": "Login temporarily unavailable"},
        )

    user = await session.scalar(
        select(User).where(User.username == normalize_username(payload.username))
    )
    if user is None:
        perform_dummy_password_check(payload.password)
        await _record_failure(session, identifier_hash, now)
        raise _invalid_credentials()
    if not verify_password(payload.password, user.password_hash) or not user.enabled:
        await _record_failure(session, identifier_hash, now)
        raise _invalid_credentials()

    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(payload.password)
        user.updated_at = now
    await session.execute(
        delete(LoginAttempt).where(LoginAttempt.identifier_hash == identifier_hash)
    )
    raw_token = new_session_token()
    continuation_token = new_session_token()
    expires_at = now + timedelta(minutes=get_settings().session_ttl_minutes)
    session.add(
        AuthSession(
            token_hash=token_digest(raw_token),
            continuation_token_hash=token_digest(continuation_token),
            user_id=user.id,
            created_at=now,
            expires_at=expires_at,
        )
    )
    session.add(
        AuditEvent(
            occurred_at=now,
            actor_user_id=user.id,
            action="auth.login_succeeded",
            target_type="user",
            target_id=str(user.id),
            source="authentication",
        )
    )
    await session.commit()
    _set_continuation_cookie(response, continuation_token, expires_at)
    return TokenResponse(
        access_token=raw_token,
        expires_at=expires_at,
        must_change_password=user.must_change_password,
    )


@router.post(
    "/restore",
    response_model=TokenResponse,
    responses={204: {"description": "No browser session to restore"}},
)
async def restore(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse | Response:
    _validate_continuation_origin(request)
    continuation_token = request.cookies.get(CONTINUATION_COOKIE)
    if continuation_token is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    auth_session = await session.scalar(
        select(AuthSession).where(
            AuthSession.continuation_token_hash == token_digest(continuation_token)
        )
    )
    try:
        context = auth_context_for_session(auth_session)
    except HTTPException:
        invalid = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": {
                    "code": "authentication_required",
                    "message": "Authentication required",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
        _clear_continuation_cookie(invalid)
        return invalid

    raw_token = new_session_token()
    superseded_token_ids = (
        select(AuthSessionAccessToken.id)
        .where(AuthSessionAccessToken.session_id == context.session_id)
        .order_by(AuthSessionAccessToken.created_at.desc())
        .offset(MAX_RESTORED_ACCESS_TOKENS - 1)
    )
    await session.execute(
        delete(AuthSessionAccessToken).where(
            AuthSessionAccessToken.id.in_(superseded_token_ids)
        )
    )
    session.add(
        AuthSessionAccessToken(
            session_id=context.session_id,
            token_hash=token_digest(raw_token),
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    assert auth_session is not None
    return TokenResponse(
        access_token=raw_token,
        expires_at=auth_session.expires_at,
        must_change_password=context.must_change_password,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    context: Annotated[AuthContext, Depends(get_auth_context)],
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    auth_session = await session.get(AuthSession, context.session_id)
    if auth_session is not None:
        now = datetime.now(UTC)
        auth_session.revoked_at = now
        session.add(
            AuditEvent(
                occurred_at=now,
                actor_user_id=context.user_id,
                action="auth.logout",
                target_type="auth_session",
                target_id=str(context.session_id),
                source="authentication",
            )
        )
        await session.commit()
    _clear_continuation_cookie(response)


@router.get("/me", response_model=CurrentUserResponse)
async def me(context: Annotated[AuthContext, Depends(get_auth_context)]) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=str(context.user_id),
        username=context.username,
        permissions=sorted(context.permissions),
        must_change_password=context.must_change_password,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    user = await session.get(User, context.user_id)
    if user is None or not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_current_password", "message": "Invalid current password"},
        )
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "password_reuse", "message": "New password must be different"},
        )

    now = datetime.now(UTC)
    user.password_hash = password_hasher.hash(payload.new_password)
    user.must_change_password = False
    user.updated_at = now
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.id != context.session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    session.add(
        AuditEvent(
            occurred_at=now,
            actor_user_id=user.id,
            action="user.password_changed",
            target_type="user",
            target_id=str(user.id),
            source="authentication",
        )
    )
    await session.commit()
