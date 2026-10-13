"""Add revocable browser-session continuation credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_session_continuation"
down_revision: str | None = "0010_alerts_incidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column("continuation_token_hash", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_auth_sessions_continuation_token_hash",
        "auth_sessions",
        ["continuation_token_hash"],
    )
    op.create_table(
        "auth_session_access_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_auth_session_access_tokens_session_id",
        "auth_session_access_tokens",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_session_access_tokens_session_id",
        table_name="auth_session_access_tokens",
    )
    op.drop_table("auth_session_access_tokens")
    op.drop_constraint(
        "uq_auth_sessions_continuation_token_hash",
        "auth_sessions",
        type_="unique",
    )
    op.drop_column("auth_sessions", "continuation_token_hash")
