"""PostgreSQL/pgvector migration and integrity tests for embeddings."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import Session

from tests.test_evidence_chunk_embeddings import (
    MODEL_ID,
    _create_parent,
    _embedding,
)

POSTGRES_URL = os.getenv("EVIDENCEFORGE_TEST_DATABASE_URL", "").strip()
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _postgres_configuration_reason(database_url: str) -> str | None:
    """Return a clear reason when the dedicated PostgreSQL test DB is absent."""

    if not database_url:
        return (
            "requires EVIDENCEFORGE_TEST_DATABASE_URL pointing to a dedicated "
            "PostgreSQL 16/pgvector 0.8.6 test database"
        )

    try:
        parsed_url = make_url(database_url)
    except ArgumentError as exc:
        return f"invalid PostgreSQL test URL: {exc}"

    if parsed_url.get_backend_name() != "postgresql":
        return "EVIDENCEFORGE_TEST_DATABASE_URL must use a PostgreSQL backend"
    if not parsed_url.host:
        return "EVIDENCEFORGE_TEST_DATABASE_URL must include a PostgreSQL hostname"

    return None


POSTGRES_CONFIGURATION_REASON = _postgres_configuration_reason(POSTGRES_URL)

pytestmark = pytest.mark.skipif(
    POSTGRES_CONFIGURATION_REASON is not None,
    reason=POSTGRES_CONFIGURATION_REASON or "invalid PostgreSQL test configuration",
)


def _upgrade_head() -> None:
    """Apply all migrations to the dedicated test database."""

    environment = os.environ.copy()
    environment["DATABASE_URL"] = POSTGRES_URL
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_migration_and_embedding_integrity_on_postgres() -> None:
    """Validate the migration, vector type, provenance, and DB invariants."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with engine.connect() as connection:
            server_version = connection.scalar(text("SHOW server_version_num"))
            extension_version = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            current_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            vector_type = connection.scalar(
                text(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute AS a
                    JOIN pg_class AS c ON c.oid = a.attrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relname = 'evidence_chunk_embeddings'
                      AND a.attname = 'embedding'
                    """
                )
            )
            constraint_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = 'evidence_chunk_embeddings'::regclass
                        """
                    )
                )
            )
            index_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'evidence_chunk_embeddings'
                        """
                    )
                )
            )
            hnsw_index_count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'evidence_chunk_embeddings'
                      AND indexdef ILIKE '%hnsw%'
                    """
                )
            )

        assert str(server_version).startswith("16")
        assert extension_version == "0.8.6"
        assert current_revision == "0007_evidence_chunk_embeddings"
        assert vector_type == "vector(384)"
        assert "ck_evidence_chunk_embeddings_dimension" in constraint_names
        assert "uq_evidence_chunk_embeddings_generation" in constraint_names
        assert "fk_evidence_chunk_embeddings_chunk" in constraint_names
        assert "fk_evidence_chunk_embeddings_workspace" in constraint_names
        assert "ix_evidence_chunk_embeddings_workspace_id" in index_names
        assert "ix_evidence_chunk_embeddings_model_id" in index_names
        assert hnsw_index_count == 0

        with Session(engine, expire_on_commit=False) as session:
            workspace, chunk = _create_parent(
                session,
                suffix=f"postgres-{uuid.uuid4().hex[:12]}",
            )
            first = _embedding(
                workspace_id=workspace.id,
                chunk_id=chunk.id,
                content_hash=chunk.content_hash,
            )
            session.add(first)
            session.commit()

            session.add(
                _embedding(
                    workspace_id=workspace.id,
                    chunk_id=chunk.id,
                    content_hash=chunk.content_hash,
                    model_version="baseline-v2",
                )
            )
            session.commit()

            other_workspace, _ = _create_parent(
                session,
                suffix=f"postgres-other-{uuid.uuid4().hex[:12]}",
            )
            session.add(
                _embedding(
                    workspace_id=other_workspace.id,
                    chunk_id=chunk.id,
                    content_hash=chunk.content_hash,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            session.add(
                _embedding(
                    workspace_id=workspace.id,
                    chunk_id=chunk.id,
                    content_hash="f" * 64,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            session.add(
                _embedding(
                    workspace_id=workspace.id,
                    chunk_id=uuid.uuid4(),
                    content_hash=chunk.content_hash,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            assert first.model_id == MODEL_ID
    finally:
        engine.dispose()
