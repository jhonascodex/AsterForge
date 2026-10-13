"""Establish the initial migration baseline."""

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No domain tables are introduced during bootstrap."""


def downgrade() -> None:
    """The bootstrap baseline has no schema objects to remove."""
