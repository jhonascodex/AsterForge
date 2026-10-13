"""Add normalized operational logs and durable source cursors."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_centralized_logs"
down_revision: str | None = "0008_host_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "log_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(),
            sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("module", sa.String(255)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.String(255)),
        sa.Column("trunk", sa.String(255)),
        sa.Column("unique_id", sa.String(255)),
        sa.Column("linked_id", sa.String(255)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "level IN ('DEBUG','NOTICE','INFO','WARNING','ERROR','CRITICAL')"
        ),
    )
    op.create_index("ix_log_events_server_time", "log_events", ["server_id", "occurred_at"])
    op.create_index("ix_log_events_level_time", "log_events", ["level", "occurred_at"])
    op.create_index("ix_log_events_source_time", "log_events", ["source", "occurred_at"])
    op.create_table(
        "log_source_cursors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(),
            sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("byte_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("server_id", "source"),
        sa.CheckConstraint("byte_offset >= 0"),
    )


def downgrade() -> None:
    op.drop_table("log_source_cursors")
    op.drop_table("log_events")
