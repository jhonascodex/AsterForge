import csv
import io
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_auth_context, require_permissions
from app.db.models import (
    Alert,
    AlertRule,
    AsteriskServer,
    CallDetailRecord,
    HostTelemetrySample,
    Incident,
    PjsipEndpoint,
    SipTrunk,
    TrunkStateTransition,
)
from app.db.session import get_session

router = APIRouter(tags=["reporting-search"])

MAX_REPORT_WINDOW = timedelta(days=31)
MAX_REPORT_ROWS = 1_000
MAX_SEARCH_RESULTS_PER_TYPE = 20
MAX_CSV_BYTES = 2_000_000

ReportFamily = Literal[
    "server-availability",
    "calls",
    "failures",
    "trunk-availability",
    "endpoint-availability",
    "incidents",
    "host-storage",
]

REPORT_PERMISSION: dict[str, str] = {
    "server-availability": "asterisk.read",
    "calls": "call.read",
    "failures": "call.read",
    "trunk-availability": "asterisk.read",
    "endpoint-availability": "asterisk.read",
    "incidents": "incident.read",
    "host-storage": "server.read",
}


class SearchResult(BaseModel):
    entity_type: Literal["server", "endpoint", "trunk", "phone_number", "unique_id", "linked_id"]
    identifier: str
    label: str
    server_id: UUID
    observed_at: datetime | None = None


class SearchResponse(BaseModel):
    query: str
    items: list[SearchResult]
    truncated: bool


class ReportRow(BaseModel):
    occurred_at: datetime
    server_id: UUID
    server_name: str
    values: dict[str, str | int | float | bool | None]


class ReportResponse(BaseModel):
    family: ReportFamily
    from_time: datetime
    to_time: datetime
    limit: int
    offset: int
    has_more: bool
    items: list[ReportRow]


def _active_actor(
    actor: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    if actor.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "password_change_required", "message": "Password change required"},
        )
    return actor


def _window(from_time: datetime, to_time: datetime) -> tuple[datetime, datetime]:
    if from_time.tzinfo is None or to_time.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "timezone_required",
                "message": "Report timestamps must include a timezone",
            },
        )
    start = from_time.astimezone(UTC)
    end = to_time.astimezone(UTC)
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_time_window",
                "message": "to_time must be later than from_time",
            },
        )
    if end - start > MAX_REPORT_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "report_window_too_large",
                "message": "Report time windows cannot exceed 31 days",
            },
        )
    return start, end


def _require_family_permission(actor: AuthContext, family: str) -> None:
    required = REPORT_PERMISSION[family]
    if required not in actor.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "permission_denied", "message": "Permission denied"},
        )


async def _search_servers(session: AsyncSession, term: str) -> list[SearchResult]:
    rows = (
        await session.scalars(
            select(AsteriskServer)
            .where(
                AsteriskServer.archived_at.is_(None),
                or_(
                    AsteriskServer.name.contains(term, autoescape=True),
                    AsteriskServer.hostname.contains(term, autoescape=True),
                ),
            )
            .order_by(AsteriskServer.name)
            .limit(MAX_SEARCH_RESULTS_PER_TYPE + 1)
        )
    ).all()
    return [
        SearchResult(
            entity_type="server",
            identifier=str(item.id),
            label=f"{item.name} ({item.hostname})",
            server_id=item.id,
            observed_at=item.updated_at,
        )
        for item in rows
    ]


async def _search_pjsip(session: AsyncSession, term: str) -> list[SearchResult]:
    endpoints = (
        await session.execute(
            select(PjsipEndpoint, AsteriskServer.name)
            .join(AsteriskServer, AsteriskServer.id == PjsipEndpoint.server_id)
            .where(
                AsteriskServer.archived_at.is_(None),
                PjsipEndpoint.source_id.contains(term, autoescape=True),
            )
            .order_by(PjsipEndpoint.source_id)
            .limit(MAX_SEARCH_RESULTS_PER_TYPE + 1)
        )
    ).all()
    trunks = (
        await session.execute(
            select(SipTrunk, AsteriskServer.name)
            .join(AsteriskServer, AsteriskServer.id == SipTrunk.server_id)
            .where(
                AsteriskServer.archived_at.is_(None),
                or_(
                    SipTrunk.source_id.contains(term, autoescape=True),
                    SipTrunk.label.contains(term, autoescape=True),
                ),
            )
            .order_by(SipTrunk.label)
            .limit(MAX_SEARCH_RESULTS_PER_TYPE + 1)
        )
    ).all()
    return [
        SearchResult(
            entity_type="endpoint",
            identifier=item.source_id,
            label=f"{item.source_id} · {server_name}",
            server_id=item.server_id,
            observed_at=item.observed_at,
        )
        for item, server_name in endpoints
    ] + [
        SearchResult(
            entity_type="trunk",
            identifier=item.source_id,
            label=f"{item.label} · {server_name}",
            server_id=item.server_id,
            observed_at=item.observed_at,
        )
        for item, server_name in trunks
    ]


async def _search_calls(session: AsyncSession, term: str) -> list[SearchResult]:
    records = (
        await session.execute(
            select(CallDetailRecord, AsteriskServer.name)
            .join(AsteriskServer, AsteriskServer.id == CallDetailRecord.server_id)
            .where(
                AsteriskServer.archived_at.is_(None),
                or_(
                    CallDetailRecord.caller_number.contains(term, autoescape=True),
                    CallDetailRecord.connected_number.contains(term, autoescape=True),
                    CallDetailRecord.unique_id.contains(term, autoescape=True),
                    CallDetailRecord.linked_id.contains(term, autoescape=True),
                ),
            )
            .order_by(CallDetailRecord.started_at.desc())
            .limit(MAX_SEARCH_RESULTS_PER_TYPE + 1)
        )
    ).all()
    results: list[SearchResult] = []
    seen: set[tuple[str, str, UUID]] = set()
    folded = term.casefold()
    for item, server_name in records:
        candidates = (
            ("phone_number", item.caller_number),
            ("phone_number", item.connected_number),
            ("unique_id", item.unique_id),
            ("linked_id", item.linked_id),
        )
        for entity_type, identifier in candidates:
            key = (entity_type, identifier or "", item.server_id)
            if identifier and folded in identifier.casefold() and key not in seen:
                seen.add(key)
                results.append(
                    SearchResult(
                        entity_type=entity_type,  # type: ignore[arg-type]
                        identifier=identifier,
                        label=f"{identifier} · {server_name}",
                        server_id=item.server_id,
                        observed_at=item.started_at,
                    )
                )
                if len(results) >= MAX_SEARCH_RESULTS_PER_TYPE + 1:
                    return results
    return results


@router.get("/search", response_model=SearchResponse)
async def global_search(
    actor: Annotated[AuthContext, Depends(_active_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = Query(min_length=2, max_length=100),
) -> SearchResponse:
    term = q.strip()
    if len(term) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "search_term_too_short",
                "message": "Search terms require two characters",
            },
        )
    items: list[SearchResult] = []
    if "server.read" in actor.permissions:
        items.extend(await _search_servers(session, term))
    if "asterisk.read" in actor.permissions:
        items.extend(await _search_pjsip(session, term))
    if "call.read" in actor.permissions:
        items.extend(await _search_calls(session, term))
    truncated = any(
        sum(item.entity_type in entity_types for item in items) > MAX_SEARCH_RESULTS_PER_TYPE
        for entity_types in (
            {"server"},
            {"endpoint"},
            {"trunk"},
            {"phone_number", "unique_id", "linked_id"},
        )
    )
    counts: dict[str, int] = {}
    bounded: list[SearchResult] = []
    for item in items:
        group = (
            "call"
            if item.entity_type in {"phone_number", "unique_id", "linked_id"}
            else item.entity_type
        )
        if counts.get(group, 0) < MAX_SEARCH_RESULTS_PER_TYPE:
            bounded.append(item)
            counts[group] = counts.get(group, 0) + 1
    return SearchResponse(query=term, items=bounded, truncated=truncated)


def _report_query(
    family: str, start: datetime, end: datetime, server_id: UUID | None
) -> Select[Any]:
    query: Select[Any]
    if family == "server-availability":
        query = (
            select(Alert, AlertRule.code, AsteriskServer.name)
            .join(AlertRule, AlertRule.id == Alert.rule_id)
            .join(AsteriskServer, AsteriskServer.id == Alert.server_id)
            .where(
                AlertRule.code == "asterisk.connection.unavailable",
                Alert.first_seen >= start,
                Alert.first_seen < end,
            )
            .order_by(Alert.first_seen.desc())
        )
        if server_id is not None:
            query = query.where(Alert.server_id == server_id)
    elif family in {"calls", "failures"}:
        query = select(CallDetailRecord, AsteriskServer.name).join(AsteriskServer).where(
            CallDetailRecord.started_at >= start, CallDetailRecord.started_at < end
        )
        if family == "failures":
            query = query.where(
                or_(
                    CallDetailRecord.disposition.is_(None),
                    func.upper(CallDetailRecord.disposition) != "ANSWERED",
                )
            )
        query = query.order_by(CallDetailRecord.started_at.desc())
        if server_id is not None:
            query = query.where(CallDetailRecord.server_id == server_id)
    elif family == "trunk-availability":
        query = (
            select(TrunkStateTransition, SipTrunk, AsteriskServer.name)
            .select_from(TrunkStateTransition)
            .join(SipTrunk, SipTrunk.id == TrunkStateTransition.trunk_id)
            .join(AsteriskServer, AsteriskServer.id == SipTrunk.server_id)
            .where(
                TrunkStateTransition.occurred_at >= start,
                TrunkStateTransition.occurred_at < end,
            )
            .order_by(TrunkStateTransition.occurred_at.desc())
        )
        if server_id is not None:
            query = query.where(SipTrunk.server_id == server_id)
    elif family == "endpoint-availability":
        query = select(PjsipEndpoint, AsteriskServer.name).join(AsteriskServer).where(
            PjsipEndpoint.observed_at >= start, PjsipEndpoint.observed_at < end
        ).order_by(PjsipEndpoint.observed_at.desc())
        if server_id is not None:
            query = query.where(PjsipEndpoint.server_id == server_id)
    elif family == "incidents":
        query = select(Incident, AsteriskServer.name).join(AsteriskServer).where(
            Incident.opened_at >= start, Incident.opened_at < end
        ).order_by(Incident.opened_at.desc())
        if server_id is not None:
            query = query.where(Incident.server_id == server_id)
    else:
        query = select(HostTelemetrySample, AsteriskServer.name).join(AsteriskServer).where(
            HostTelemetrySample.observed_at >= start, HostTelemetrySample.observed_at < end
        ).order_by(HostTelemetrySample.observed_at.desc())
        if server_id is not None:
            query = query.where(HostTelemetrySample.server_id == server_id)
    return query


def _report_row(family: str, row: Sequence[object]) -> ReportRow:
    item = row[0]
    if family == "server-availability":
        assert isinstance(item, Alert)
        return ReportRow(
            occurred_at=item.first_seen,
            server_id=item.server_id,
            server_name=str(row[2]),
            values={
                "event": str(row[1]),
                "state": item.state,
                "last_seen": item.last_seen.isoformat(),
                "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
            },
        )
    if family in {"calls", "failures"}:
        assert isinstance(item, CallDetailRecord)
        return ReportRow(
            occurred_at=item.started_at,
            server_id=item.server_id,
            server_name=str(row[1]),
            values={
                "source_id": item.source_id,
                "unique_id": item.unique_id,
                "linked_id": item.linked_id,
                "caller_number": item.caller_number,
                "connected_number": item.connected_number,
                "disposition": item.disposition,
                "duration_seconds": item.duration_seconds,
            },
        )
    if family == "trunk-availability":
        assert isinstance(item, TrunkStateTransition)
        trunk = row[1]
        assert isinstance(trunk, SipTrunk)
        return ReportRow(
            occurred_at=item.occurred_at,
            server_id=trunk.server_id,
            server_name=str(row[2]),
            values={
                "trunk": trunk.label,
                "source_id": trunk.source_id,
                "from_state": item.from_state,
                "to_state": item.to_state,
            },
        )
    if family == "endpoint-availability":
        assert isinstance(item, PjsipEndpoint)
        return ReportRow(
            occurred_at=item.observed_at,
            server_id=item.server_id,
            server_name=str(row[1]),
            values={
                "endpoint": item.source_id,
                "state": item.state,
                "contacts_count": item.contacts_count,
                "stale": item.stale,
            },
        )
    if family == "incidents":
        assert isinstance(item, Incident)
        return ReportRow(
            occurred_at=item.opened_at,
            server_id=item.server_id,
            server_name=str(row[1]),
            values={
                "incident_id": str(item.id),
                "title": item.title,
                "state": item.state,
                "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
            },
        )
    assert isinstance(item, HostTelemetrySample)
    used = (
        item.recording_total_bytes - item.recording_available_bytes
        if item.recording_total_bytes is not None
        and item.recording_available_bytes is not None
        else None
    )
    return ReportRow(
        occurred_at=item.observed_at,
        server_id=item.server_id,
        server_name=str(row[1]),
        values={
            "cpu_usage_percent": item.cpu_usage_percent,
            "memory_total_bytes": item.memory_total_bytes,
            "memory_available_bytes": item.memory_available_bytes,
            "load_1": item.load_1,
            "recording_total_bytes": item.recording_total_bytes,
            "recording_used_bytes": used,
            "recording_available_bytes": item.recording_available_bytes,
        },
    )


async def _load_report(
    session: AsyncSession,
    family: str,
    start: datetime,
    end: datetime,
    server_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[ReportRow], bool]:
    query = _report_query(family, start, end, server_id).offset(offset).limit(limit + 1)
    rows = (await session.execute(query)).all()
    return [_report_row(family, row) for row in rows[:limit]], len(rows) > limit


@router.get("/reports/{family}", response_model=ReportResponse)
async def get_report(
    family: ReportFamily,
    actor: Annotated[AuthContext, require_permissions("report.read")],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_time: datetime,
    to_time: datetime,
    server_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=MAX_REPORT_ROWS),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> ReportResponse:
    _require_family_permission(actor, family)
    start, end = _window(from_time, to_time)
    items, has_more = await _load_report(session, family, start, end, server_id, limit, offset)
    return ReportResponse(
        family=family,
        from_time=start,
        to_time=end,
        limit=limit,
        offset=offset,
        has_more=has_more,
        items=items,
    )


def _safe_csv_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def _csv_response(family: str, rows: list[ReportRow]) -> Response:
    value_keys = sorted({key for row in rows for key in row.values})
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["occurred_at", "server_id", "server_name", *value_keys])
    for row in rows:
        writer.writerow(
            [
                _safe_csv_cell(row.occurred_at.isoformat()),
                _safe_csv_cell(row.server_id),
                _safe_csv_cell(row.server_name),
            ]
            + [_safe_csv_cell(row.values.get(key)) for key in value_keys]
        )
        if output.tell() > MAX_CSV_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "export_too_large", "message": "CSV export exceeds the size limit"},
            )
    body = output.getvalue().encode("utf-8-sig")
    if len(body) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "export_too_large", "message": "CSV export exceeds the size limit"},
        )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="asterforge-{family}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/reports/{family}/export")
async def export_report(
    family: ReportFamily,
    actor: Annotated[AuthContext, require_permissions("report.read", "report.export")],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_time: datetime,
    to_time: datetime,
    server_id: UUID | None = None,
    limit: int = Query(default=MAX_REPORT_ROWS, ge=1, le=MAX_REPORT_ROWS),
) -> Response:
    _require_family_permission(actor, family)
    start, end = _window(from_time, to_time)
    rows, _has_more = await _load_report(session, family, start, end, server_id, limit, 0)
    return _csv_response(family, rows)
