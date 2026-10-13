"""Persist reconciled PJSIP resources and trunk state transitions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_pjsip_monitoring"
down_revision: str | None = "0005_ami_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _resource_columns(source_length: int = 255) -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(),
            sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(source_length), nullable=False),
    ]


def _observation_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]


def upgrade() -> None:
    op.create_table(
        "pjsip_endpoints",
        *_resource_columns(),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("raw_state", sa.String(100)),
        sa.Column("transport", sa.String(255)),
        sa.Column("aors", sa.Text()),
        sa.Column("active_channels", sa.Integer()),
        sa.Column("contacts_count", sa.Integer()),
        *_observation_columns(),
        sa.UniqueConstraint("server_id", "source_id"),
    )
    op.create_index("ix_pjsip_endpoints_server_id", "pjsip_endpoints", ["server_id"])
    op.create_index(
        "ix_pjsip_endpoints_server_state", "pjsip_endpoints", ["server_id", "state"]
    )
    op.create_table(
        "pjsip_contacts",
        *_resource_columns(500),
        sa.Column("endpoint_name", sa.String(255)),
        sa.Column("aor", sa.String(255)),
        sa.Column("uri", sa.Text()),
        sa.Column("address", sa.String(255)),
        sa.Column("port", sa.Integer()),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("raw_state", sa.String(100)),
        sa.Column("roundtrip_usec", sa.Integer()),
        *_observation_columns(),
        sa.UniqueConstraint("server_id", "source_id"),
    )
    op.create_index("ix_pjsip_contacts_server_id", "pjsip_contacts", ["server_id"])
    op.create_index("ix_pjsip_contacts_endpoint_name", "pjsip_contacts", ["endpoint_name"])
    op.create_index(
        "ix_pjsip_contacts_server_state", "pjsip_contacts", ["server_id", "state"]
    )
    op.create_table(
        "pjsip_registrations",
        *_resource_columns(500),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("endpoint_name", sa.String(255)),
        sa.Column("server_uri", sa.Text()),
        sa.Column("client_uri", sa.Text()),
        sa.Column("transport", sa.String(255)),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("raw_state", sa.String(100)),
        *_observation_columns(),
        sa.UniqueConstraint("server_id", "direction", "source_id"),
    )
    op.create_index("ix_pjsip_registrations_server_id", "pjsip_registrations", ["server_id"])
    op.create_index(
        "ix_pjsip_registrations_endpoint_name", "pjsip_registrations", ["endpoint_name"]
    )
    op.create_index(
        "ix_pjsip_registrations_server_state",
        "pjsip_registrations",
        ["server_id", "state"],
    )
    op.create_table(
        "pjsip_transports",
        *_resource_columns(),
        sa.Column("protocol", sa.String(30)),
        sa.Column("bind", sa.String(255)),
        *_observation_columns(),
        sa.UniqueConstraint("server_id", "source_id"),
    )
    op.create_index("ix_pjsip_transports_server_id", "pjsip_transports", ["server_id"])
    op.create_table(
        "sip_trunks",
        *_resource_columns(),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(255)),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("raw_state", sa.String(100)),
        sa.Column("endpoint_name", sa.String(255)),
        sa.Column("server_uri", sa.Text()),
        sa.Column("last_state_change_at", sa.DateTime(timezone=True), nullable=False),
        *_observation_columns(),
        sa.UniqueConstraint("server_id", "source_id"),
    )
    op.create_index("ix_sip_trunks_server_id", "sip_trunks", ["server_id"])
    op.create_index("ix_sip_trunks_server_state", "sip_trunks", ["server_id", "state"])
    op.create_table(
        "trunk_state_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "trunk_id",
            sa.Uuid(),
            sa.ForeignKey("sip_trunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(30)),
        sa.Column("to_state", sa.String(30), nullable=False),
        sa.Column("raw_state", sa.String(100)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_trunk_transitions_trunk_time",
        "trunk_state_transitions",
        ["trunk_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("trunk_state_transitions")
    op.drop_table("sip_trunks")
    op.drop_table("pjsip_transports")
    op.drop_table("pjsip_registrations")
    op.drop_table("pjsip_contacts")
    op.drop_table("pjsip_endpoints")
