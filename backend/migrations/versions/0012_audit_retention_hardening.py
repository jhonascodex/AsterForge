"""Harden append-only audit evidence and persisted monitoring configuration."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0012_audit_retention"
down_revision: str | None = "0011_session_continuation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("outcome", sa.String(20), nullable=False, server_default="success"),
    )
    op.add_column(
        "audit_events",
        sa.Column("source", sa.String(50), nullable=False, server_default="platform"),
    )
    op.add_column("audit_events", sa.Column("request_id", sa.String(100)))
    op.create_check_constraint(
        "ck_audit_events_outcome", "audit_events", "outcome IN ('success','failure')"
    )
    op.create_table(
        "retention_policies",
        sa.Column("category", sa.String(50), primary_key=True),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("retention_days >= 1 AND retention_days <= 3650"),
        sa.CheckConstraint("batch_size >= 1 AND batch_size <= 10000"),
    )
    policies = sa.table(
        "retention_policies",
        sa.column("category", sa.String()),
        sa.column("retention_days", sa.Integer()),
        sa.column("batch_size", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        policies,
        [
            {
                "category": "operational_logs",
                "retention_days": 30,
                "batch_size": 5000,
                "updated_at": datetime.now(UTC),
            }
        ],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_audit_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_event_mutation()")
    op.drop_table("retention_policies")
    op.drop_constraint("ck_audit_events_outcome", "audit_events", type_="check")
    op.drop_column("audit_events", "request_id")
    op.drop_column("audit_events", "source")
    op.drop_column("audit_events", "outcome")
