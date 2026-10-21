"""Preserve imported question wording for the guided review browser."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_questionnaire_browser"
down_revision: str | None = "0007_evidence_chunk_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add source wording and backfill existing immutable question snapshots."""

    op.add_column(
        "questionnaire_version_questions",
        sa.Column("source_question_text", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE questionnaire_version_questions "
        "SET source_question_text = normalized_question_text "
        "WHERE source_question_text IS NULL"
    )
    op.alter_column(
        "questionnaire_version_questions",
        "source_question_text",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    """Remove the additive imported-wording column."""

    op.drop_column("questionnaire_version_questions", "source_question_text")
