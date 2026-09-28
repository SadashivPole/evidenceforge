"""Add evidence provenance, chunk offsets, and ingestion attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_evidence_persistence"
down_revision: str | None = "0003_evidence_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rename the Phase 1C normalized-content fields to their explicit
    # provenance names.
    op.alter_column(
        "evidence_document_versions",
        "content_hash",
        new_column_name="normalized_sha256",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )

    op.alter_column(
        "evidence_document_versions",
        "byte_size",
        new_column_name="raw_size_bytes",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )

    # Match the new SQLAlchemy model's BIGINT representation.
    op.alter_column(
        "evidence_document_versions",
        "raw_size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )

    op.drop_index(
        "ix_evidence_document_versions_content_hash",
        table_name="evidence_document_versions",
    )

    op.create_index(
        "ix_evidence_document_versions_normalized_sha256",
        "evidence_document_versions",
        ["normalized_sha256"],
        unique=False,
    )

    # The current database contains zero evidence rows, so these required
    # provenance fields can be introduced directly as NOT NULL.
    op.add_column(
        "evidence_document_versions",
        sa.Column(
            "raw_sha256",
            sa.String(length=64),
            nullable=False,
        ),
    )

    op.add_column(
        "evidence_document_versions",
        sa.Column(
            "normalization_version",
            sa.String(length=64),
            nullable=False,
        ),
    )

    op.add_column(
        "evidence_document_versions",
        sa.Column(
            "normalized_size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
    )

    op.add_column(
        "evidence_document_versions",
        sa.Column(
            "chunking_version",
            sa.String(length=64),
            nullable=False,
        ),
    )

    op.add_column(
        "evidence_document_versions",
        sa.Column(
            "chunk_target_bytes",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "evidence_document_versions",
        sa.Column(
            "chunk_overlap_bytes",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_evidence_document_versions_raw_sha256",
        "evidence_document_versions",
        ["raw_sha256"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_evidence_document_versions_representation",
        "evidence_document_versions",
        [
            "document_id",
            "normalized_sha256",
            "normalization_version",
            "chunking_version",
        ],
    )

    # Persist exact normalized-text byte ranges for deterministic retrieval
    # and provenance.
    op.add_column(
        "evidence_chunks",
        sa.Column(
            "normalized_start_byte",
            sa.BigInteger(),
            nullable=False,
        ),
    )

    op.add_column(
        "evidence_chunks",
        sa.Column(
            "normalized_end_byte",
            sa.BigInteger(),
            nullable=False,
        ),
    )

    op.create_check_constraint(
        "ck_evidence_chunks_valid_byte_range",
        "evidence_chunks",
        "normalized_end_byte > normalized_start_byte",
    )

    # Immutable ingestion-attempt provenance. No uploaded content is stored.
    op.create_table(
        "evidence_ingestion_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "outcome",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "error_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "original_filename",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "media_type",
            sa.String(length=127),
            nullable=False,
        ),
        sa.Column(
            "raw_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "normalized_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "normalization_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "raw_size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "normalized_size_bytes",
            sa.BigInteger(),
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
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["evidence_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["evidence_document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_workspace_id",
        "evidence_ingestion_attempts",
        ["workspace_id"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_document_id",
        "evidence_ingestion_attempts",
        ["document_id"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_version_id",
        "evidence_ingestion_attempts",
        ["version_id"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_actor_user_id",
        "evidence_ingestion_attempts",
        ["actor_user_id"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_outcome",
        "evidence_ingestion_attempts",
        ["outcome"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_raw_sha256",
        "evidence_ingestion_attempts",
        ["raw_sha256"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_normalized_sha256",
        "evidence_ingestion_attempts",
        ["normalized_sha256"],
        unique=False,
    )

    op.create_index(
        "ix_evidence_ingestion_attempts_created_at",
        "evidence_ingestion_attempts",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_ingestion_attempts_created_at",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_normalized_sha256",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_raw_sha256",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_outcome",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_actor_user_id",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_version_id",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_document_id",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_index(
        "ix_evidence_ingestion_attempts_workspace_id",
        table_name="evidence_ingestion_attempts",
    )

    op.drop_table("evidence_ingestion_attempts")

    op.drop_constraint(
        "ck_evidence_chunks_valid_byte_range",
        "evidence_chunks",
        type_="check",
    )

    op.drop_column(
        "evidence_chunks",
        "normalized_end_byte",
    )

    op.drop_column(
        "evidence_chunks",
        "normalized_start_byte",
    )

    op.drop_constraint(
        "uq_evidence_document_versions_representation",
        "evidence_document_versions",
        type_="unique",
    )

    op.drop_index(
        "ix_evidence_document_versions_raw_sha256",
        table_name="evidence_document_versions",
    )

    op.drop_column(
        "evidence_document_versions",
        "chunk_overlap_bytes",
    )

    op.drop_column(
        "evidence_document_versions",
        "chunk_target_bytes",
    )

    op.drop_column(
        "evidence_document_versions",
        "chunking_version",
    )

    op.drop_column(
        "evidence_document_versions",
        "normalized_size_bytes",
    )

    op.drop_column(
        "evidence_document_versions",
        "normalization_version",
    )

    op.drop_column(
        "evidence_document_versions",
        "raw_sha256",
    )

    op.drop_index(
        "ix_evidence_document_versions_normalized_sha256",
        table_name="evidence_document_versions",
    )

    op.create_index(
        "ix_evidence_document_versions_content_hash",
        "evidence_document_versions",
        ["normalized_sha256"],
        unique=False,
    )

    op.alter_column(
        "evidence_document_versions",
        "raw_size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        new_column_name="byte_size",
        existing_nullable=False,
    )

    op.alter_column(
        "evidence_document_versions",
        "normalized_sha256",
        new_column_name="content_hash",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
