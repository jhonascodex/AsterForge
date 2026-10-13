"""Add normalized CDR and CEL records for configured history adapters."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_call_history"
down_revision: str | None = "0006_pjsip_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(),
            sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("unique_id", sa.String(255)),
        sa.Column("linked_id", sa.String(255)),
    ]


def upgrade() -> None:
    op.create_table(
        "call_detail_records",
        *_identity_columns(),
        sa.Column("caller_number", sa.String(255)),
        sa.Column("connected_number", sa.String(255)),
        sa.Column("disposition", sa.String(100)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("billable_seconds", sa.Integer()),
        sa.CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0"),
        sa.CheckConstraint("billable_seconds IS NULL OR billable_seconds >= 0"),
        sa.UniqueConstraint("server_id", "source_id"),
    )
    op.create_index(
        "ix_cdr_server_started", "call_detail_records", ["server_id", "started_at"]
    )
    op.create_index(
        "ix_cdr_server_linked_started",
        "call_detail_records",
        ["server_id", "linked_id", "started_at"],
    )
    op.create_index(
        "ix_cdr_server_unique_started",
        "call_detail_records",
        ["server_id", "unique_id", "started_at"],
    )

    op.create_table(
        "channel_event_records",
        *_identity_columns(),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(500)),
        sa.Column("peer", sa.String(500)),
        sa.Column("caller_number", sa.String(255)),
        sa.Column("connected_number", sa.String(255)),
        sa.UniqueConstraint("server_id", "source_id"),
    )
    op.create_index(
        "ix_cel_server_event_time", "channel_event_records", ["server_id", "event_at"]
    )
    op.create_index(
        "ix_cel_server_linked_time",
        "channel_event_records",
        ["server_id", "linked_id", "event_at"],
    )
    op.create_index(
        "ix_cel_server_unique_time",
        "channel_event_records",
        ["server_id", "unique_id", "event_at"],
    )


def downgrade() -> None:
    op.drop_table("channel_event_records")
    op.drop_table("call_detail_records")
