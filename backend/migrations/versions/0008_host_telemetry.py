"""Add capability-aware host and recording-storage telemetry samples."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_host_telemetry"
down_revision: str | None = "0007_call_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "host_telemetry_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(),
            sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("cpu_usage_percent", sa.Float()),
        sa.Column("memory_total_bytes", sa.BigInteger()),
        sa.Column("memory_available_bytes", sa.BigInteger()),
        sa.Column("swap_total_bytes", sa.BigInteger()),
        sa.Column("swap_free_bytes", sa.BigInteger()),
        sa.Column("load_1", sa.Float()),
        sa.Column("load_5", sa.Float()),
        sa.Column("load_15", sa.Float()),
        sa.Column("uptime_seconds", sa.Float()),
        sa.Column("asterisk_service_state", sa.String(30)),
        sa.Column("filesystems_json", sa.Text(), nullable=False),
        sa.Column("recording_mountpoint", sa.String(500), nullable=False),
        sa.Column("recording_total_bytes", sa.BigInteger()),
        sa.Column("recording_available_bytes", sa.BigInteger()),
        sa.Column("recording_files", sa.BigInteger()),
        sa.Column("recording_files_free", sa.BigInteger()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cpu_usage_percent IS NULL OR "
            "(cpu_usage_percent >= 0 AND cpu_usage_percent <= 100)"
        ),
    )
    op.create_index(
        "ix_host_telemetry_server_time",
        "host_telemetry_samples",
        ["server_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_table("host_telemetry_samples")
