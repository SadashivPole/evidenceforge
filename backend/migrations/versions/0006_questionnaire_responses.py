"""Persist immutable questionnaire response revisions and evidence citations.

Revision ID: 0006_questionnaire_responses
Revises: 0005_questionnaire_persistence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_questionnaire_responses"
down_revision = "0005_questionnaire_persistence"
branch_labels = None
depends_on = None


_RESPONSE_STATUS_VALUES = (
    "'PROPOSED', 'APPROVED', 'NEEDS_REVIEW', 'INSUFFICIENT_EVIDENCE', "
    "'STALE_SOURCE', 'CONFLICTING_SOURCES', 'NOT_APPLICABLE', 'DO_NOT_DISCLOSE'"
)


def upgrade() -> None:
    """Create response, immutable revision, and citation relationship tables."""

    # The composite target makes the response-to-version-question relationship
    # enforce workspace, questionnaire, and questionnaire-version identity.
    op.create_unique_constraint(
        "uq_questionnaire_version_questions_full_scope",
        "questionnaire_version_questions",
        [
            "workspace_id",
            "questionnaire_id",
            "questionnaire_version_id",
            "id",
        ],
    )

    op.create_table(
        "questionnaire_responses",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("questionnaire_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("questionnaire_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "questionnaire_version_question_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_responses_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_questionnaire_responses_created_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id", "questionnaire_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.questionnaire_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_responses_version_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "questionnaire_id",
                "questionnaire_version_id",
                "questionnaire_version_question_id",
            ],
            [
                "questionnaire_version_questions.workspace_id",
                "questionnaire_version_questions.questionnaire_id",
                "questionnaire_version_questions.questionnaire_version_id",
                "questionnaire_version_questions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_responses_version_question_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_version_id",
            "questionnaire_version_question_id",
            name="uq_questionnaire_responses_version_question",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_responses_workspace_id_id",
        ),
    )
    op.create_index(
        "ix_questionnaire_responses_workspace_id",
        "questionnaire_responses",
        ["workspace_id"],
    )
    op.create_index(
        "ix_questionnaire_responses_created_by_user_id",
        "questionnaire_responses",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_questionnaire_responses_questionnaire_version_id",
        "questionnaire_responses",
        ["questionnaire_version_id"],
    )
    op.create_index(
        "ix_questionnaire_responses_questionnaire_version_question_id",
        "questionnaire_responses",
        ["questionnaire_version_question_id"],
    )

    op.create_table(
        "questionnaire_response_revisions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("response_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("author_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_questionnaire_response_revisions_positive_number",
        ),
        sa.CheckConstraint(
            f"status IN ({_RESPONSE_STATUS_VALUES})",
            name="ck_questionnaire_response_revisions_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_response_revisions_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "response_id"],
            [
                "questionnaire_responses.workspace_id",
                "questionnaire_responses.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_response_revisions_response_scope",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_questionnaire_response_revisions_author",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "response_id",
            "revision_number",
            name="uq_questionnaire_response_revisions_number",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_response_revisions_workspace_id_id",
        ),
    )
    op.create_index(
        "ix_questionnaire_response_revisions_workspace_id",
        "questionnaire_response_revisions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_questionnaire_response_revisions_response_id",
        "questionnaire_response_revisions",
        ["response_id"],
    )
    op.create_index(
        "ix_questionnaire_response_revisions_author_user_id",
        "questionnaire_response_revisions",
        ["author_user_id"],
    )
    op.create_index(
        "ix_questionnaire_response_revisions_created_at",
        "questionnaire_response_revisions",
        ["created_at"],
    )

    op.create_table(
        "questionnaire_response_citations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("response_revision_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("citation_order", sa.Integer(), nullable=False),
        sa.Column("evidence_document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evidence_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evidence_chunk_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evidence_version_number", sa.Integer(), nullable=False),
        sa.Column("evidence_chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_start_byte", sa.BigInteger(), nullable=False),
        sa.Column("normalized_end_byte", sa.BigInteger(), nullable=False),
        sa.Column("section_label", sa.String(length=255), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_response_citations_workspace",
        ),
        sa.CheckConstraint(
            "citation_order >= 1",
            name="ck_questionnaire_response_citations_positive_order",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "response_revision_id"],
            [
                "questionnaire_response_revisions.workspace_id",
                "questionnaire_response_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_response_citations_revision_scope",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_document_id"],
            ["evidence_documents.id"],
            ondelete="RESTRICT",
            name="fk_questionnaire_response_citations_document",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_version_id"],
            ["evidence_document_versions.id"],
            ondelete="RESTRICT",
            name="fk_questionnaire_response_citations_version",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_chunk_id"],
            ["evidence_chunks.id"],
            ondelete="RESTRICT",
            name="fk_questionnaire_response_citations_chunk",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "response_revision_id",
            "evidence_chunk_id",
            name="uq_questionnaire_response_citations_revision_chunk",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "response_revision_id",
            "citation_order",
            name="uq_questionnaire_response_citations_revision_order",
        ),
    )
    op.create_index(
        "ix_questionnaire_response_citations_workspace_id",
        "questionnaire_response_citations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_questionnaire_response_citations_response_revision_id",
        "questionnaire_response_citations",
        ["response_revision_id"],
    )
    op.create_index(
        "ix_questionnaire_response_citations_evidence_document_id",
        "questionnaire_response_citations",
        ["evidence_document_id"],
    )
    op.create_index(
        "ix_questionnaire_response_citations_evidence_version_id",
        "questionnaire_response_citations",
        ["evidence_version_id"],
    )
    op.create_index(
        "ix_questionnaire_response_citations_evidence_chunk_id",
        "questionnaire_response_citations",
        ["evidence_chunk_id"],
    )


def downgrade() -> None:
    """Remove Phase 1K response tables in reverse dependency order."""

    op.drop_index(
        "ix_questionnaire_response_citations_evidence_chunk_id",
        table_name="questionnaire_response_citations",
    )
    op.drop_index(
        "ix_questionnaire_response_citations_evidence_version_id",
        table_name="questionnaire_response_citations",
    )
    op.drop_index(
        "ix_questionnaire_response_citations_evidence_document_id",
        table_name="questionnaire_response_citations",
    )
    op.drop_index(
        "ix_questionnaire_response_citations_response_revision_id",
        table_name="questionnaire_response_citations",
    )
    op.drop_index(
        "ix_questionnaire_response_citations_workspace_id",
        table_name="questionnaire_response_citations",
    )
    op.drop_table("questionnaire_response_citations")

    op.drop_index(
        "ix_questionnaire_response_revisions_created_at",
        table_name="questionnaire_response_revisions",
    )
    op.drop_index(
        "ix_questionnaire_response_revisions_author_user_id",
        table_name="questionnaire_response_revisions",
    )
    op.drop_index(
        "ix_questionnaire_response_revisions_response_id",
        table_name="questionnaire_response_revisions",
    )
    op.drop_index(
        "ix_questionnaire_response_revisions_workspace_id",
        table_name="questionnaire_response_revisions",
    )
    op.drop_table("questionnaire_response_revisions")

    op.drop_index(
        "ix_questionnaire_responses_questionnaire_version_question_id",
        table_name="questionnaire_responses",
    )
    op.drop_index(
        "ix_questionnaire_responses_created_by_user_id",
        table_name="questionnaire_responses",
    )
    op.drop_index(
        "ix_questionnaire_responses_questionnaire_version_id",
        table_name="questionnaire_responses",
    )
    op.drop_index(
        "ix_questionnaire_responses_workspace_id",
        table_name="questionnaire_responses",
    )
    op.drop_table("questionnaire_responses")
    op.drop_constraint(
        "uq_questionnaire_version_questions_full_scope",
        "questionnaire_version_questions",
        type_="unique",
    )
