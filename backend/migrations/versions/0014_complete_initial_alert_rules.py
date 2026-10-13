"""Complete the FR-051 initial evaluated alert rule set."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0014_initial_alert_rules"
down_revision: str | None = "0013_telemetry_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES = (
    (
        "10000000-0000-0000-0000-000000000005",
        "endpoint.availability.unavailable",
        "categorical_unavailable",
        "WARNING",
        None,
        None,
        30,
        30,
    ),
    (
        "10000000-0000-0000-0000-000000000006",
        "host.memory.high",
        "numeric_high",
        "WARNING",
        90.0,
        80.0,
        300,
        60,
    ),
    (
        "10000000-0000-0000-0000-000000000007",
        "host.filesystem.low",
        "numeric_low",
        "WARNING",
        10.0,
        15.0,
        300,
        60,
    ),
    (
        "10000000-0000-0000-0000-000000000008",
        "calls.failed_rate.high",
        "numeric_high",
        "WARNING",
        20.0,
        10.0,
        300,
        60,
    ),
    (
        "10000000-0000-0000-0000-000000000009",
        "recording.generation.failures",
        "numeric_high",
        "CRITICAL",
        1.0,
        0.0,
        0,
        60,
    ),
)


def upgrade() -> None:
    rules = sa.table(
        "alert_rules",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("condition_kind", sa.String()),
        sa.column("severity", sa.String()),
        sa.column("threshold", sa.Float()),
        sa.column("clear_threshold", sa.Float()),
        sa.column("duration_seconds", sa.Integer()),
        sa.column("debounce_seconds", sa.Integer()),
        sa.column("enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        rules,
        [
            dict(
                zip(
                    (
                        "id",
                        "code",
                        "condition_kind",
                        "severity",
                        "threshold",
                        "clear_threshold",
                        "duration_seconds",
                        "debounce_seconds",
                    ),
                    row,
                    strict=True,
                ),
                enabled=True,
                created_at=now,
            )
            for row in RULES
        ],
    )


def downgrade() -> None:
    rules = sa.table("alert_rules", sa.column("id", sa.Uuid()))
    op.execute(sa.delete(rules).where(rules.c.id.in_([UUID(row[0]) for row in RULES])))
