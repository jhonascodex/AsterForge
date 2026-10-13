# ruff: noqa: E501
"""Add deterministic alerts, incidents, and maintenance windows."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0010_alerts_incidents"
down_revision: str | None = "0009_centralized_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES = (
    ("10000000-0000-0000-0000-000000000001", "asterisk.connection.unavailable", "categorical_unavailable", "CRITICAL", None, None, 30, 30),
    ("10000000-0000-0000-0000-000000000002", "trunk.registration.unavailable", "categorical_unavailable", "CRITICAL", None, None, 30, 30),
    ("10000000-0000-0000-0000-000000000003", "host.cpu.high", "numeric_high", "WARNING", 90.0, 80.0, 300, 60),
    ("10000000-0000-0000-0000-000000000004", "recording.storage.low", "numeric_low", "CRITICAL", 10.0, 15.0, 300, 60),
)


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("condition_kind", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("threshold", sa.Float()),
        sa.Column("clear_threshold", sa.Float()),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("debounce_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')"),
        sa.CheckConstraint("duration_seconds >= 0"),
        sa.CheckConstraint("debounce_seconds >= 0"),
    )
    rules = sa.table(
        "alert_rules",
        sa.column("id", sa.Uuid()), sa.column("code", sa.String()),
        sa.column("condition_kind", sa.String()), sa.column("severity", sa.String()),
        sa.column("threshold", sa.Float()), sa.column("clear_threshold", sa.Float()),
        sa.column("duration_seconds", sa.Integer()), sa.column("debounce_seconds", sa.Integer()),
        sa.column("enabled", sa.Boolean()), sa.column("created_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    op.bulk_insert(rules, [dict(zip(("id", "code", "condition_kind", "severity", "threshold", "clear_threshold", "duration_seconds", "debounce_seconds"), row, strict=True), enabled=True, created_at=now) for row in RULES])
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_type", sa.String(50)), sa.Column("resource_key", sa.String(255)),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ends_at > starts_at"),
        sa.CheckConstraint("(resource_type IS NULL AND resource_key IS NULL) OR (resource_type IS NOT NULL AND resource_key IS NOT NULL)"),
    )
    op.create_index("ix_maintenance_server_time", "maintenance_windows", ["server_id", "starts_at", "ends_at"])
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("alert_rules.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False), sa.Column("resource_key", sa.String(255), nullable=False),
        sa.Column("condition_scope", sa.String(100), nullable=False), sa.Column("dedupe_key", sa.String(500), nullable=False),
        sa.Column("active_key", sa.String(500), unique=True), sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False), sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("acknowledgement_comment", sa.String(500)), sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("condition_metadata_json", sa.Text(), nullable=False), sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("maintenance_window_id", sa.Uuid(), sa.ForeignKey("maintenance_windows.id", ondelete="SET NULL")),
        sa.CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')"),
        sa.CheckConstraint("state IN ('OPEN','ACKNOWLEDGED','RESOLVED')"),
    )
    op.create_index("ix_alerts_server_state_seen", "alerts", ["server_id", "state", "last_seen"])
    op.create_index("ix_alerts_resource", "alerts", ["resource_type", "resource_key"])
    op.create_table(
        "alert_condition_states",
        sa.Column("dedupe_key", sa.String(500), primary_key=True), sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False), sa.Column("resource_key", sa.String(255), nullable=False),
        sa.Column("active_since", sa.DateTime(timezone=True)), sa.Column("clear_since", sa.DateTime(timezone=True)),
        sa.Column("last_is_firing", sa.Boolean(), nullable=False), sa.Column("last_value_json", sa.Text(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("server_id", sa.Uuid(), sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("correlation_key", sa.String(500), nullable=False), sa.Column("active_key", sa.String(500), unique=True),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("state", sa.String(20), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False), sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('OPEN','RESOLVED')"),
    )
    op.create_index("ix_incidents_server_state_opened", "incidents", ["server_id", "state", "opened_at"])
    op.create_table(
        "incident_alerts",
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("alert_id", sa.Uuid(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_id", sa.Uuid(), sa.ForeignKey("alerts.id", ondelete="SET NULL")), sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False), sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_incident_events_incident_time", "incident_events", ["incident_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("incident_events")
    op.drop_table("incident_alerts")
    op.drop_table("incidents")
    op.drop_table("alert_condition_states")
    op.drop_table("alerts")
    op.drop_table("maintenance_windows")
    op.drop_table("alert_rules")
