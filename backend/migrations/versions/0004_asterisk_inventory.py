"""Add extensible Asterisk server inventory, tags, and encrypted credential storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_asterisk_inventory"
down_revision: str | None = "0003_forced_password_change"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ENVIRONMENTS = [
    ("10000000-0000-0000-0000-000000000001", "DEV", "Development"),
    ("10000000-0000-0000-0000-000000000002", "QA", "Quality Assurance"),
    ("10000000-0000-0000-0000-000000000003", "PROD", "Production"),
]


def upgrade() -> None:
    op.create_table(
        "inventory_environments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    environments = sa.table(
        "inventory_environments",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("display_name", sa.String()),
    )
    op.bulk_insert(
        environments,
        [
            {"id": environment_id, "code": code, "display_name": display_name}
            for environment_id, code, display_name in DEFAULT_ENVIRONMENTS
        ],
    )
    op.create_table(
        "server_tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("name_key", sa.String(50), nullable=False, unique=True),
    )
    op.create_table(
        "asterisk_servers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(100), nullable=False, unique=True),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("ami_port", sa.Integer(), nullable=False, server_default="5038"),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("inventory_environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("location", sa.String(150)),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ami_username_encrypted", sa.Text()),
        sa.Column("ami_secret_encrypted", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("ami_port >= 1 AND ami_port <= 65535"),
    )
    op.create_index("ix_asterisk_servers_environment_id", "asterisk_servers", ["environment_id"])
    op.create_index("ix_asterisk_servers_archived_at", "asterisk_servers", ["archived_at"])
    op.create_table(
        "asterisk_server_tags",
        sa.Column(
            "server_id",
            sa.Uuid(),
            sa.ForeignKey("asterisk_servers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Uuid(),
            sa.ForeignKey("server_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("asterisk_server_tags")
    op.drop_index("ix_asterisk_servers_archived_at", table_name="asterisk_servers")
    op.drop_index("ix_asterisk_servers_environment_id", table_name="asterisk_servers")
    op.drop_table("asterisk_servers")
    op.drop_table("server_tags")
    op.drop_table("inventory_environments")
