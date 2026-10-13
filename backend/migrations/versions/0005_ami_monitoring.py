"""Add AMI transport mode to monitored server inventory."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ami_monitoring"
down_revision: str | None = "0004_asterisk_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "asterisk_servers",
        sa.Column("ami_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("asterisk_servers", "ami_tls")
