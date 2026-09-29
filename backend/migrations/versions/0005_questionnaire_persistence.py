"""Add immutable questionnaire persistence and import provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_questionnaire_persistence"
down_revision: str | None = "0004_evidence_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "questionnaires",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
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
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaires_workspace_id_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_questionnaires_workspace_name",
        ),
    )
    op.create_index(
        "ix_questionnaires_workspace_id",
        "questionnaires",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaires_created_by_user_id",
        "questionnaires",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "questionnaire_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("questionnaire_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
        sa.Column("question_identity_version", sa.String(length=64), nullable=False),
        sa.Column("canonical_hash_version", sa.String(length=64), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_questionnaire_versions_positive_version",
        ),
        sa.CheckConstraint(
            "question_count >= 0",
            name="ck_questionnaire_versions_nonnegative_question_count",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id"],
            ["questionnaires.workspace_id", "questionnaires.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_versions_questionnaire_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_versions_workspace_id_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "id",
            name="uq_questionnaire_versions_workspace_questionnaire_id_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "version_number",
            name="uq_questionnaire_versions_questionnaire_version",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "canonical_hash_version",
            "canonical_sha256",
            name="uq_questionnaire_versions_representation",
        ),
    )
    op.create_index(
        "ix_questionnaire_versions_questionnaire_id",
        "questionnaire_versions",
        ["questionnaire_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_versions_raw_sha256",
        "questionnaire_versions",
        ["raw_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_versions_canonical_sha256",
        "questionnaire_versions",
        ["canonical_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_versions_created_by_user_id",
        "questionnaire_versions",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "questionnaire_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("questionnaire_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("identity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("identity_kind", sa.String(length=16), nullable=False),
        sa.Column("normalized_question_text", sa.Text(), nullable=False),
        sa.Column("normalized_sheet_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_section_path", sa.JSON(), nullable=False),
        sa.Column("source_question_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "identity_kind IN ('explicit', 'fallback')",
            name="ck_questionnaire_questions_identity_kind",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id"],
            ["questionnaires.workspace_id", "questionnaires.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_questions_questionnaire_workspace",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_questions_workspace_id_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "id",
            name="uq_questionnaire_questions_workspace_questionnaire_id_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "question_id",
            name="uq_questionnaire_questions_identity",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "identity_fingerprint",
            name="uq_questionnaire_questions_fingerprint",
        ),
    )
    op.create_index(
        "ix_questionnaire_questions_questionnaire_id",
        "questionnaire_questions",
        ["questionnaire_id"],
        unique=False,
    )

    op.create_table(
        "questionnaire_version_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("questionnaire_id", sa.Uuid(), nullable=False),
        sa.Column("questionnaire_version_id", sa.Uuid(), nullable=False),
        sa.Column("questionnaire_question_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("section_path", sa.JSON(), nullable=False),
        sa.Column("normalized_question_text", sa.Text(), nullable=False),
        sa.Column("source_question_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_questionnaire_version_questions_positive_ordinal",
        ),
        sa.CheckConstraint(
            "source_row > 0",
            name="ck_questionnaire_version_questions_positive_source_row",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id", "questionnaire_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.questionnaire_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_version_questions_version_scope",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id", "questionnaire_question_id"],
            [
                "questionnaire_questions.workspace_id",
                "questionnaire_questions.questionnaire_id",
                "questionnaire_questions.id",
            ],
            ondelete="RESTRICT",
            name="fk_questionnaire_version_questions_question_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_version_questions_workspace_id_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "questionnaire_version_id",
            "ordinal",
            name="uq_questionnaire_version_questions_ordinal",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "questionnaire_version_id",
            "questionnaire_question_id",
            name="uq_questionnaire_version_questions_question",
        ),
    )
    op.create_index(
        "ix_questionnaire_version_questions_questionnaire_version_id",
        "questionnaire_version_questions",
        ["questionnaire_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_version_questions_questionnaire_question_id",
        "questionnaire_version_questions",
        ["questionnaire_question_id"],
        unique=False,
    )

    op.create_table(
        "questionnaire_import_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("questionnaire_id", sa.Uuid(), nullable=True),
        sa.Column("resulting_version_id", sa.Uuid(), nullable=True),
        sa.Column("duplicate_of_version_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_sha256", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
        sa.Column("question_identity_version", sa.String(length=64), nullable=False),
        sa.Column("canonical_hash_version", sa.String(length=64), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('CREATED', 'IDENTICAL_DUPLICATE', 'CANONICAL_DUPLICATE', 'FAILED')",
            name="ck_questionnaire_import_attempts_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id"],
            ["questionnaires.workspace_id", "questionnaires.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_import_attempts_questionnaire_scope",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "resulting_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_import_attempts_resulting_version_scope",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "duplicate_of_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_import_attempts_duplicate_version_scope",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_import_attempts_workspace_id_id",
        ),
    )
    op.create_index(
        "ix_questionnaire_import_attempts_workspace_id",
        "questionnaire_import_attempts",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_questionnaire_id",
        "questionnaire_import_attempts",
        ["questionnaire_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_resulting_version_id",
        "questionnaire_import_attempts",
        ["resulting_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_duplicate_of_version_id",
        "questionnaire_import_attempts",
        ["duplicate_of_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_actor_user_id",
        "questionnaire_import_attempts",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_outcome",
        "questionnaire_import_attempts",
        ["outcome"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_raw_sha256",
        "questionnaire_import_attempts",
        ["raw_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_canonical_sha256",
        "questionnaire_import_attempts",
        ["canonical_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_questionnaire_import_attempts_created_at",
        "questionnaire_import_attempts",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_questionnaire_import_attempts_created_at",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_canonical_sha256",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_raw_sha256",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_outcome",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_actor_user_id",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_duplicate_of_version_id",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_resulting_version_id",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_questionnaire_id",
        table_name="questionnaire_import_attempts",
    )
    op.drop_index(
        "ix_questionnaire_import_attempts_workspace_id",
        table_name="questionnaire_import_attempts",
    )
    op.drop_table("questionnaire_import_attempts")

    op.drop_index(
        "ix_questionnaire_version_questions_questionnaire_question_id",
        table_name="questionnaire_version_questions",
    )
    op.drop_index(
        "ix_questionnaire_version_questions_questionnaire_version_id",
        table_name="questionnaire_version_questions",
    )
    op.drop_table("questionnaire_version_questions")

    op.drop_index(
        "ix_questionnaire_questions_questionnaire_id",
        table_name="questionnaire_questions",
    )
    op.drop_table("questionnaire_questions")

    op.drop_index(
        "ix_questionnaire_versions_created_by_user_id",
        table_name="questionnaire_versions",
    )
    op.drop_index(
        "ix_questionnaire_versions_canonical_sha256",
        table_name="questionnaire_versions",
    )
    op.drop_index(
        "ix_questionnaire_versions_raw_sha256",
        table_name="questionnaire_versions",
    )
    op.drop_index(
        "ix_questionnaire_versions_questionnaire_id",
        table_name="questionnaire_versions",
    )
    op.drop_table("questionnaire_versions")

    op.drop_index(
        "ix_questionnaires_created_by_user_id",
        table_name="questionnaires",
    )
    op.drop_index(
        "ix_questionnaires_workspace_id",
        table_name="questionnaires",
    )
    op.drop_table("questionnaires")
