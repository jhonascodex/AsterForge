import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.credentials import CredentialConfigurationError, encrypt_credential
from app.core.security import AuthContext, require_permissions
from app.db.models import (
    AsteriskServer,
    AuditEvent,
    InventoryEnvironment,
    ServerTag,
    asterisk_server_tags,
)
from app.db.session import get_session

router = APIRouter(tags=["server-inventory"])


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _name_key(value: str) -> str:
    return _clean_name(value).casefold()


def _not_found(resource: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": f"{resource}_not_found", "message": f"{resource.title()} not found"},
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


def _audit(
    actor: AuthContext,
    action: str,
    target_type: str,
    target_id: UUID,
    *,
    changed_fields: set[str] | None = None,
) -> AuditEvent:
    metadata = {"changed_fields": sorted(changed_fields)} if changed_fields else {}
    return AuditEvent(
        occurred_at=datetime.now(UTC),
        actor_user_id=actor.user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata_json=json.dumps(metadata, separators=(",", ":")),
    )


async def _commit(session: AsyncSession, *, code: str, message: str) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict(code, message) from exc


class EnvironmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        return _clean_name(value)


class EnvironmentUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)


class EnvironmentResponse(BaseModel):
    id: UUID
    code: str
    display_name: str
    enabled: bool


class EnvironmentCatalogResponse(EnvironmentResponse):
    server_count: int


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean_name(value)


class TagResponse(BaseModel):
    id: UUID
    name: str


class TagCatalogResponse(TagResponse):
    server_count: int


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    hostname: str = Field(min_length=1, max_length=253)
    ami_port: int = Field(default=5038, ge=1, le=65535)
    ami_tls: bool = False
    environment_id: UUID
    location: str | None = Field(default=None, max_length=150)
    monitoring_enabled: bool = True
    tag_ids: set[UUID] = Field(default_factory=set)
    ami_username: SecretStr | None = Field(default=None, min_length=1, max_length=200)
    ami_secret: SecretStr | None = Field(default=None, min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean_name(value)

    @field_validator("hostname")
    @classmethod
    def clean_hostname(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("hostname must not be blank or contain whitespace")
        return cleaned

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def complete_credentials(self) -> ServerCreate:
        if (self.ami_username is None) != (self.ami_secret is None):
            raise ValueError("ami_username and ami_secret must be supplied together")
        return self


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    hostname: str | None = Field(default=None, min_length=1, max_length=253)
    ami_port: int | None = Field(default=None, ge=1, le=65535)
    ami_tls: bool | None = None
    environment_id: UUID | None = None
    location: str | None = Field(default=None, max_length=150)
    monitoring_enabled: bool | None = None
    tag_ids: set[UUID] | None = None
    ami_username: SecretStr | None = Field(default=None, min_length=1, max_length=200)
    ami_secret: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    clear_ami_credentials: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)

    @field_validator("hostname")
    @classmethod
    def clean_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("hostname must not be blank or contain whitespace")
        return cleaned

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def credential_action_is_unambiguous(self) -> ServerUpdate:
        if self.clear_ami_credentials and (
            self.ami_username is not None or self.ami_secret is not None
        ):
            raise ValueError("cannot clear and update AMI credentials in the same request")
        return self


class ServerResponse(BaseModel):
    id: UUID
    name: str
    hostname: str
    ami_port: int
    ami_tls: bool
    environment: EnvironmentResponse
    location: str | None
    monitoring_enabled: bool
    tags: list[TagResponse]
    ami_username_configured: bool
    ami_secret_configured: bool
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ServerListResponse(BaseModel):
    items: list[ServerResponse]
    total: int
    limit: int
    offset: int


def _environment_response(item: InventoryEnvironment) -> EnvironmentResponse:
    return EnvironmentResponse(
        id=item.id, code=item.code, display_name=item.display_name, enabled=item.enabled
    )


def _tag_response(item: ServerTag) -> TagResponse:
    return TagResponse(id=item.id, name=item.name)


def _server_response(item: AsteriskServer) -> ServerResponse:
    return ServerResponse(
        id=item.id,
        name=item.name,
        hostname=item.hostname,
        ami_port=item.ami_port,
        ami_tls=item.ami_tls,
        environment=_environment_response(item.environment),
        location=item.location,
        monitoring_enabled=item.monitoring_enabled,
        tags=sorted((_tag_response(tag) for tag in item.tags), key=lambda tag: tag.name.casefold()),
        ami_username_configured=item.ami_username_encrypted is not None,
        ami_secret_configured=item.ami_secret_encrypted is not None,
        created_at=item.created_at,
        updated_at=item.updated_at,
        archived_at=item.archived_at,
    )


async def _get_environment(
    session: AsyncSession, environment_id: UUID, *, require_enabled: bool = False
) -> InventoryEnvironment:
    environment = await session.get(InventoryEnvironment, environment_id)
    if environment is None:
        raise _not_found("environment")
    if require_enabled and not environment.enabled:
        raise _conflict("environment_disabled", "Environment is disabled")
    return environment


async def _get_tags(session: AsyncSession, tag_ids: set[UUID]) -> list[ServerTag]:
    if not tag_ids:
        return []
    tags = list((await session.scalars(select(ServerTag).where(ServerTag.id.in_(tag_ids)))).all())
    if len(tags) != len(tag_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "unknown_tag", "message": "One or more tags do not exist"},
        )
    return tags


@router.get("/environments", response_model=list[EnvironmentCatalogResponse])
async def list_environments(
    _actor: Annotated[AuthContext, require_permissions("server.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    include_disabled: bool = False,
) -> list[EnvironmentCatalogResponse]:
    statement = (
        select(InventoryEnvironment, func.count(AsteriskServer.id))
        .outerjoin(AsteriskServer, AsteriskServer.environment_id == InventoryEnvironment.id)
        .group_by(
            InventoryEnvironment.id,
            InventoryEnvironment.code,
            InventoryEnvironment.display_name,
            InventoryEnvironment.enabled,
        )
        .order_by(InventoryEnvironment.code)
    )
    if not include_disabled:
        statement = statement.where(InventoryEnvironment.enabled.is_(True))
    return [
        EnvironmentCatalogResponse(
            id=item.id,
            code=item.code,
            display_name=item.display_name,
            enabled=item.enabled,
            server_count=server_count,
        )
        for item, server_count in (await session.execute(statement)).all()
    ]


@router.post(
    "/environments",
    response_model=EnvironmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_environment(
    payload: EnvironmentCreate,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EnvironmentResponse:
    item = InventoryEnvironment(id=uuid4(), code=payload.code, display_name=payload.display_name)
    session.add_all([item, _audit(actor, "inventory.environment_created", "environment", item.id)])
    await _commit(
        session,
        code="environment_code_exists",
        message="Environment code already exists",
    )
    return _environment_response(item)


@router.patch("/environments/{environment_id}", response_model=EnvironmentResponse)
async def update_environment(
    environment_id: UUID,
    payload: EnvironmentUpdate,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EnvironmentResponse:
    item = await _get_environment(session, environment_id)
    changed = payload.model_fields_set
    if payload.display_name is not None:
        item.display_name = payload.display_name
    if payload.enabled is not None:
        item.enabled = payload.enabled
    if changed:
        session.add(
            _audit(
                actor,
                "inventory.environment_updated",
                "environment",
                item.id,
                changed_fields=changed,
            )
        )
        await session.commit()
    return _environment_response(item)


@router.delete("/environments/{environment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_environment(
    environment_id: UUID,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    item = await _get_environment(session, environment_id)
    session.add(_audit(actor, "inventory.environment_deleted", "environment", item.id))
    await session.delete(item)
    await _commit(session, code="environment_in_use", message="Environment is assigned to a server")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/server-tags", response_model=list[TagCatalogResponse])
async def list_tags(
    _actor: Annotated[AuthContext, require_permissions("server.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TagCatalogResponse]:
    statement = (
        select(ServerTag, func.count(asterisk_server_tags.c.server_id))
        .outerjoin(asterisk_server_tags, asterisk_server_tags.c.tag_id == ServerTag.id)
        .group_by(ServerTag.id, ServerTag.name, ServerTag.name_key)
        .order_by(ServerTag.name_key)
    )
    return [
        TagCatalogResponse(id=item.id, name=item.name, server_count=server_count)
        for item, server_count in (await session.execute(statement)).all()
    ]


@router.post("/server-tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: TagCreate,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagResponse:
    item = ServerTag(id=uuid4(), name=payload.name, name_key=_name_key(payload.name))
    session.add_all([item, _audit(actor, "inventory.tag_created", "server_tag", item.id)])
    await _commit(session, code="tag_name_exists", message="Tag name already exists")
    return _tag_response(item)


@router.patch("/server-tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: UUID,
    payload: TagCreate,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagResponse:
    item = await session.get(ServerTag, tag_id)
    if item is None:
        raise _not_found("tag")
    item.name = payload.name
    item.name_key = _name_key(payload.name)
    session.add(
        _audit(
            actor,
            "inventory.tag_updated",
            "server_tag",
            item.id,
            changed_fields={"name"},
        )
    )
    await _commit(session, code="tag_name_exists", message="Tag name already exists")
    return _tag_response(item)


@router.delete("/server-tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    item = await session.get(ServerTag, tag_id)
    if item is None:
        raise _not_found("tag")
    session.add(_audit(actor, "inventory.tag_deleted", "server_tag", item.id))
    await session.delete(item)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/servers", response_model=ServerListResponse)
async def list_servers(
    _actor: Annotated[AuthContext, require_permissions("server.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    environment: str | None = Query(default=None, max_length=50),
    tag: str | None = Query(default=None, max_length=50),
    monitoring_enabled: bool | None = None,
    include_archived: bool = False,
    search: str | None = Query(default=None, min_length=1, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ServerListResponse:
    filters: list[ColumnElement[bool]] = []
    if not include_archived:
        filters.append(AsteriskServer.archived_at.is_(None))
    if environment is not None:
        filters.append(AsteriskServer.environment.has(code=environment.strip().upper()))
    if tag is not None:
        filters.append(AsteriskServer.tags.any(ServerTag.name_key == _name_key(tag)))
    if monitoring_enabled is not None:
        filters.append(AsteriskServer.monitoring_enabled.is_(monitoring_enabled))
    if search is not None:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                AsteriskServer.name.ilike(pattern),
                AsteriskServer.hostname.ilike(pattern),
                AsteriskServer.location.ilike(pattern),
            )
        )
    statement = select(AsteriskServer).where(*filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        await session.scalars(
            statement.order_by(AsteriskServer.name_key).limit(limit).offset(offset)
        )
    ).all()
    return ServerListResponse(
        items=[_server_response(item) for item in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/servers/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: UUID,
    _actor: Annotated[AuthContext, require_permissions("server.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ServerResponse:
    item = await session.get(AsteriskServer, server_id)
    if item is None:
        raise _not_found("server")
    return _server_response(item)


@router.post("/servers", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: ServerCreate,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ServerResponse:
    environment = await _get_environment(session, payload.environment_id, require_enabled=True)
    tags = await _get_tags(session, payload.tag_ids)
    try:
        encrypted_username = (
            encrypt_credential(payload.ami_username.get_secret_value())
            if payload.ami_username
            else None
        )
        encrypted_secret = (
            encrypt_credential(payload.ami_secret.get_secret_value())
            if payload.ami_secret
            else None
        )
    except CredentialConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_encryption_unavailable",
                "message": "Credential encryption is unavailable",
            },
        ) from exc
    now = datetime.now(UTC)
    item = AsteriskServer(
        id=uuid4(),
        name=payload.name,
        name_key=_name_key(payload.name),
        hostname=payload.hostname,
        ami_port=payload.ami_port,
        ami_tls=payload.ami_tls,
        environment=environment,
        location=payload.location,
        monitoring_enabled=payload.monitoring_enabled,
        tags=tags,
        ami_username_encrypted=encrypted_username,
        ami_secret_encrypted=encrypted_secret,
        created_at=now,
        updated_at=now,
    )
    session.add_all([item, _audit(actor, "inventory.server_created", "asterisk_server", item.id)])
    await _commit(session, code="server_name_exists", message="Server name already exists")
    return _server_response(item)


@router.patch("/servers/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: UUID,
    payload: ServerUpdate,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ServerResponse:
    item = await session.get(AsteriskServer, server_id)
    if item is None:
        raise _not_found("server")
    if item.archived_at is not None:
        raise _conflict("server_archived", "Archived servers cannot be modified")
    changed = set(payload.model_fields_set)
    if payload.name is not None:
        item.name = payload.name
        item.name_key = _name_key(payload.name)
    if payload.hostname is not None:
        item.hostname = payload.hostname
    if payload.ami_port is not None:
        item.ami_port = payload.ami_port
    if payload.ami_tls is not None:
        item.ami_tls = payload.ami_tls
    if payload.environment_id is not None:
        item.environment = await _get_environment(
            session, payload.environment_id, require_enabled=True
        )
    if "location" in payload.model_fields_set:
        item.location = payload.location
    if payload.monitoring_enabled is not None:
        item.monitoring_enabled = payload.monitoring_enabled
    if payload.tag_ids is not None:
        item.tags = await _get_tags(session, payload.tag_ids)
    try:
        if payload.clear_ami_credentials:
            item.ami_username_encrypted = None
            item.ami_secret_encrypted = None
        else:
            if payload.ami_username is not None:
                item.ami_username_encrypted = encrypt_credential(
                    payload.ami_username.get_secret_value()
                )
            if payload.ami_secret is not None:
                item.ami_secret_encrypted = encrypt_credential(
                    payload.ami_secret.get_secret_value()
                )
    except CredentialConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_encryption_unavailable",
                "message": "Credential encryption is unavailable",
            },
        ) from exc
    if (item.ami_username_encrypted is None) != (item.ami_secret_encrypted is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "incomplete_ami_credentials",
                "message": "AMI username and secret must both be configured",
            },
        )
    if changed:
        audit_fields = changed - {"ami_username", "ami_secret"}
        if changed & {"ami_username", "ami_secret", "clear_ami_credentials"}:
            audit_fields.add("ami_credentials")
        item.updated_at = datetime.now(UTC)
        session.add(
            _audit(
                actor,
                "inventory.server_updated",
                "asterisk_server",
                item.id,
                changed_fields=audit_fields,
            )
        )
        await _commit(session, code="server_name_exists", message="Server name already exists")
    return _server_response(item)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_server(
    server_id: UUID,
    actor: Annotated[AuthContext, require_permissions("server.manage")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    item = await session.get(AsteriskServer, server_id)
    if item is None:
        raise _not_found("server")
    if item.archived_at is None:
        now = datetime.now(UTC)
        item.monitoring_enabled = False
        item.archived_at = now
        item.updated_at = now
        session.add(_audit(actor, "inventory.server_archived", "asterisk_server", item.id))
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
