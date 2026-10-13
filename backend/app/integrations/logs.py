import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

SOURCE_NAME = "asterisk/messages"
LEVELS = frozenset({"DEBUG", "NOTICE", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ASTERISK_LINE = re.compile(
    r"^\[(?P<timestamp>[^]]+)]\s+(?P<level>[A-Za-z]+)(?:\[[^]]*])?\s*"
    r"(?:(?P<module>[^:]+):\s*)?(?P<message>.*)$"
)
_FIELD = re.compile(
    r"(?i)\b(?P<name>endpoint|trunk|uniqueid|unique_id|linkedid|linked_id)="
    r"(?P<value>[^\s,;]+)"
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b((?:proxy-)?authorization\s*:\s*)[^\r\n]+"),
    re.compile(
        r"(?i)\b((?:password|passwd|(?:ami[_-]?)?secret|api[_-]?key|token)"
        r"\s*[:=]\s*)[^\s,;]+"
    ),
)


@dataclass(frozen=True)
class LogTarget:
    server_id: UUID


@dataclass(frozen=True)
class NormalizedLogRecord:
    source: str
    level: str
    module: str | None
    message: str
    endpoint: str | None
    trunk: str | None
    unique_id: str | None
    linked_id: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class LogBatch:
    records: tuple[NormalizedLogRecord, ...]
    next_offset: int


class LogSourceAdapter(Protocol):
    source: str

    async def read(self, target: LogTarget, offset: int) -> LogBatch: ...


def redact_log_message(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return redacted


def parse_asterisk_log_line(line: str, *, fallback_time: datetime) -> NormalizedLogRecord:
    match = _ASTERISK_LINE.match(line.strip())
    if match is None:
        timestamp = fallback_time
        level = "INFO"
        module = None
        message = line.rstrip("\r\n")
    else:
        raw_timestamp = match.group("timestamp")
        try:
            timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            timestamp = (
                timestamp.replace(tzinfo=UTC)
                if timestamp.tzinfo is None
                else timestamp.astimezone(UTC)
            )
        except ValueError:
            timestamp = fallback_time
        raw_level = match.group("level").upper()
        level = raw_level if raw_level in LEVELS else "INFO"
        module = match.group("module")
        message = match.group("message")
    fields = {
        item.group("name").replace("_", "").casefold(): item.group("value")
        for item in _FIELD.finditer(message)
    }
    return NormalizedLogRecord(
        source=SOURCE_NAME,
        level=level,
        module=module.strip() if module else None,
        message=redact_log_message(message),
        endpoint=fields.get("endpoint"),
        trunk=fields.get("trunk"),
        unique_id=fields.get("uniqueid"),
        linked_id=fields.get("linkedid"),
        occurred_at=timestamp,
    )


class AsteriskFileLogAdapter:
    """Reads only the fixed `<root>/<server UUID>/messages` deployment mount."""

    source = SOURCE_NAME

    def __init__(self, root: str, *, max_bytes: int, max_lines: int) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes
        self._max_lines = max_lines

    def _read_sync(self, target: LogTarget, offset: int) -> LogBatch:
        path = self._root / str(target.server_id) / "messages"
        root = self._root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or path.is_symlink():
            raise OSError("log source must be a regular file inside the configured root")
        if not resolved.is_file():
            raise OSError("log source is not a regular file")
        size = resolved.stat().st_size
        safe_offset = 0 if size < offset else offset
        with resolved.open("rb") as stream:
            stream.seek(safe_offset)
            payload = stream.read(self._max_bytes)
        lines = payload.splitlines(keepends=True)
        selected = lines[: self._max_lines]
        consumed = sum(len(line) for line in selected)
        now = datetime.now(UTC)
        records = tuple(
            parse_asterisk_log_line(line.decode("utf-8", errors="replace"), fallback_time=now)
            for line in selected
            if line.strip()
        )
        return LogBatch(records, safe_offset + consumed)

    async def read(self, target: LogTarget, offset: int) -> LogBatch:
        return await asyncio.to_thread(self._read_sync, target, offset)
