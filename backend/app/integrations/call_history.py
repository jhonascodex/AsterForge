from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import CallDetailRecord, ChannelEventRecord


@dataclass(frozen=True)
class HistoryCapabilities:
    cdr_source: str | None
    cel_source: str | None


@dataclass(frozen=True)
class CdrSearch:
    started_from: datetime
    started_to: datetime
    server_id: UUID | None = None
    caller_number: str | None = None
    connected_number: str | None = None
    disposition: str | None = None
    correlation_id: str | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class CdrPage:
    items: Sequence[CallDetailRecord]
    total: int


@dataclass(frozen=True)
class CelPage:
    items: Sequence[ChannelEventRecord]
    total: int


class CallHistoryAdapter(Protocol):
    @property
    def capabilities(self) -> HistoryCapabilities: ...

    async def search_cdr(self, filters: CdrSearch) -> CdrPage: ...

    async def list_cel(
        self,
        *,
        server_id: UUID,
        correlation_id: str,
        started_from: datetime,
        started_to: datetime,
        limit: int,
        offset: int,
    ) -> CelPage: ...


class PostgresCallHistoryAdapter:
    """Query normalized history populated by a deployment-owned CDR/CEL pipeline."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        cdr_enabled: bool,
        cel_enabled: bool,
    ) -> None:
        self._session = session
        self._capabilities = HistoryCapabilities(
            cdr_source="postgres" if cdr_enabled else None,
            cel_source="postgres" if cel_enabled else None,
        )

    @property
    def capabilities(self) -> HistoryCapabilities:
        return self._capabilities

    async def search_cdr(self, filters: CdrSearch) -> CdrPage:
        clauses: list[ColumnElement[bool]] = [
            CallDetailRecord.started_at >= filters.started_from,
            CallDetailRecord.started_at < filters.started_to,
        ]
        if filters.server_id is not None:
            clauses.append(CallDetailRecord.server_id == filters.server_id)
        if filters.caller_number is not None:
            clauses.append(CallDetailRecord.caller_number == filters.caller_number)
        if filters.connected_number is not None:
            clauses.append(CallDetailRecord.connected_number == filters.connected_number)
        if filters.disposition is not None:
            clauses.append(CallDetailRecord.disposition == filters.disposition)
        if filters.correlation_id is not None:
            clauses.append(
                or_(
                    CallDetailRecord.linked_id == filters.correlation_id,
                    CallDetailRecord.unique_id == filters.correlation_id,
                )
            )
        statement = select(CallDetailRecord).where(*clauses)
        total = await self._session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        items = (
            await self._session.scalars(
                statement.order_by(
                    CallDetailRecord.started_at.desc(),
                    CallDetailRecord.source_id,
                    CallDetailRecord.id,
                )
                .limit(filters.limit)
                .offset(filters.offset)
            )
        ).all()
        return CdrPage(items=items, total=total or 0)

    async def list_cel(
        self,
        *,
        server_id: UUID,
        correlation_id: str,
        started_from: datetime,
        started_to: datetime,
        limit: int,
        offset: int,
    ) -> CelPage:
        statement = select(ChannelEventRecord).where(
            ChannelEventRecord.server_id == server_id,
            ChannelEventRecord.event_at >= started_from,
            ChannelEventRecord.event_at < started_to,
            or_(
                ChannelEventRecord.linked_id == correlation_id,
                ChannelEventRecord.unique_id == correlation_id,
            ),
        )
        total = await self._session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        items = (
            await self._session.scalars(
                statement.order_by(
                    ChannelEventRecord.event_at,
                    ChannelEventRecord.source_id,
                    ChannelEventRecord.id,
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return CelPage(items=items, total=total or 0)
