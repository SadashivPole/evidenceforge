"""Persist provenance-aware 384-dimensional evidence chunk embeddings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0007_evidence_chunk_embeddings"
down_revision: str | None = "0006_questionnaire_responses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VECTOR_DIMENSION = 384


def upgrade() -> None:
    """Add the additive PostgreSQL/pgvector embedding persistence foundation."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "evidence_chunk_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_chunk_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_content_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(_VECTOR_DIMENSION), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"embedding_dimension = {_VECTOR_DIMENSION}",
            name="ck_evidence_chunk_embeddings_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_chunk_id"],
            ["evidence_chunks.id"],
            ondelete="CASCADE",
            name="fk_evidence_chunk_embeddings_chunk",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_evidence_chunk_embeddings_workspace",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_chunk_id",
            "evidence_content_hash",
            "workspace_id",
            "model_id",
            "model_version",
            "configuration_hash",
            name="uq_evidence_chunk_embeddings_generation",
        ),
    )

    op.create_index(
        "ix_evidence_chunk_embeddings_evidence_chunk_id",
        "evidence_chunk_embeddings",
        ["evidence_chunk_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_chunk_embeddings_evidence_content_hash",
        "evidence_chunk_embeddings",
        ["evidence_content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_chunk_embeddings_workspace_id",
        "evidence_chunk_embeddings",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_chunk_embeddings_model_id",
        "evidence_chunk_embeddings",
        ["model_id"],
        unique=False,
    )

    # Existing evidence chunks do not carry workspace_id directly. Keep the
    # duplicated embedding workspace and content hash bound to the immutable
    # parent row at the database boundary until a larger composite-key schema
    # redesign is justified.
    op.execute(
        """
        CREATE FUNCTION enforce_evidence_chunk_embedding_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_workspace_id uuid;
            parent_content_hash text;
        BEGIN
            SELECT document.workspace_id, chunk.content_hash
              INTO parent_workspace_id, parent_content_hash
              FROM evidence_chunks AS chunk
              JOIN evidence_document_versions AS version
                ON version.id = chunk.document_version_id
              JOIN evidence_documents AS document
                ON document.id = version.document_id
             WHERE chunk.id = NEW.evidence_chunk_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Evidence chunk % does not exist for embedding provenance',
                    NEW.evidence_chunk_id
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.workspace_id IS DISTINCT FROM parent_workspace_id THEN
                RAISE EXCEPTION
                    'Embedding workspace does not match evidence chunk workspace'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.evidence_content_hash IS DISTINCT FROM parent_content_hash THEN
                RAISE EXCEPTION
                    'Embedding content hash does not match evidence chunk content hash'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidence_chunk_embeddings_provenance
        BEFORE INSERT OR UPDATE OF evidence_chunk_id, evidence_content_hash, workspace_id
        ON evidence_chunk_embeddings
        FOR EACH ROW
        EXECUTE FUNCTION enforce_evidence_chunk_embedding_provenance()
        """
    )


def downgrade() -> None:
    """Remove only the embedding persistence objects introduced by this revision."""

    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_evidence_chunk_embeddings_provenance
        ON evidence_chunk_embeddings
        """
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_evidence_chunk_embedding_provenance()")

    op.drop_index(
        "ix_evidence_chunk_embeddings_model_id",
        table_name="evidence_chunk_embeddings",
    )
    op.drop_index(
        "ix_evidence_chunk_embeddings_workspace_id",
        table_name="evidence_chunk_embeddings",
    )
    op.drop_index(
        "ix_evidence_chunk_embeddings_evidence_content_hash",
        table_name="evidence_chunk_embeddings",
    )
    op.drop_index(
        "ix_evidence_chunk_embeddings_evidence_chunk_id",
        table_name="evidence_chunk_embeddings",
    )
    op.drop_table("evidence_chunk_embeddings")

    # Keep the shared vector extension installed. Other migrations or services
    # may use it, and extension lifecycle is an environment concern.
