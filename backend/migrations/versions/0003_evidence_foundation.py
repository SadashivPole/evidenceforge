"""Create Phase 1C evidence document, version, and chunk tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_evidence_foundation"
down_revision: str | None = "0002_security_boundary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default="file",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
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
            "name",
            name="uq_evidence_documents_workspace_name",
        ),
    )

    op.create_index(
        "ix_evidence_documents_workspace_id",
        "evidence_documents",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_documents_created_by_user_id",
        "evidence_documents",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "evidence_document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["evidence_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_evidence_document_versions_document_version",
        ),
    )

    op.create_index(
        "ix_evidence_document_versions_document_id",
        "evidence_document_versions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_document_versions_content_hash",
        "evidence_document_versions",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_document_versions_created_by_user_id",
        "evidence_document_versions",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("section_label", sa.String(length=255), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["evidence_document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "chunk_index",
            name="uq_evidence_chunks_version_index",
        ),
    )

    op.create_index(
        "ix_evidence_chunks_document_version_id",
        "evidence_chunks",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_chunks_content_hash",
        "evidence_chunks",
        ["content_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_chunks_content_hash",
        table_name="evidence_chunks",
    )
    op.drop_index(
        "ix_evidence_chunks_document_version_id",
        table_name="evidence_chunks",
    )
    op.drop_table("evidence_chunks")

    op.drop_index(
        "ix_evidence_document_versions_created_by_user_id",
        table_name="evidence_document_versions",
    )
    op.drop_index(
        "ix_evidence_document_versions_content_hash",
        table_name="evidence_document_versions",
    )
    op.drop_index(
        "ix_evidence_document_versions_document_id",
        table_name="evidence_document_versions",
    )
    op.drop_table("evidence_document_versions")

    op.drop_index(
        "ix_evidence_documents_created_by_user_id",
        table_name="evidence_documents",
    )
    op.drop_index(
        "ix_evidence_documents_workspace_id",
        table_name="evidence_documents",
    )
    op.drop_table("evidence_documents")
