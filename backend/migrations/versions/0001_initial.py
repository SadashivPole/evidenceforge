"""Establish the initial application schema revision.

The Phase 1A skeleton intentionally has no domain tables. This revision creates
Alembic's version marker and gives later domain migrations a stable parent.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the initial, intentionally empty application revision."""


def downgrade() -> None:
    """Revert the initial, intentionally empty application revision."""
