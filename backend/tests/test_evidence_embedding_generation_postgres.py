"""PostgreSQL integration tests for deterministic embedding generation/backfill."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.evidence.embeddings.config import DEFAULT_EMBEDDING_CONFIG
from app.evidence.embeddings.service import (
    EmbeddingGenerator,
    backfill_workspace,
    list_workspace_embedding_targets,
    persist_embedding,
)
from app.evidence.embeddings.types import EmbeddingPersistenceStatus
from tests.test_evidence_chunk_embeddings import _create_parent, _embedding
from tests.test_evidence_embedding_generation import _FakeModel

POSTGRES_URL = os.getenv("EVIDENCEFORGE_TEST_DATABASE_URL", "").strip()
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _postgres_configuration_reason(database_url: str) -> str | None:
    """Return an explicit skip reason for an unavailable PostgreSQL test DB."""

    if not database_url:
        return "requires EVIDENCEFORGE_TEST_DATABASE_URL for PostgreSQL 16/pgvector 0.8.6"
    try:
        parsed_url = make_url(database_url)
    except ArgumentError as exc:
        return f"invalid PostgreSQL test URL: {exc}"
    if parsed_url.get_backend_name() != "postgresql":
        return "EVIDENCEFORGE_TEST_DATABASE_URL must use PostgreSQL"
    if not parsed_url.host:
        return "EVIDENCEFORGE_TEST_DATABASE_URL must include a PostgreSQL host"
    return None


POSTGRES_CONFIGURATION_REASON = _postgres_configuration_reason(POSTGRES_URL)

pytestmark = pytest.mark.skipif(
    POSTGRES_CONFIGURATION_REASON is not None,
    reason=POSTGRES_CONFIGURATION_REASON or "invalid PostgreSQL test configuration",
)


def _upgrade_head() -> None:
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


def test_workspace_backfill_persists_384_vectors_idempotently() -> None:
    """Exercise real pgvector persistence without adding a retrieval path."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            assert str(connection.scalar(text("SHOW server_version_num"))).startswith("16")
            assert (
                connection.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                == "0.8.6"
            )
            assert (
                connection.scalar(
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
                == "vector(384)"
            )

        with Session(engine, expire_on_commit=False) as session:
            workspace, chunk = _create_parent(
                session,
                suffix=f"generation-{uuid.uuid4().hex[:12]}",
            )
            targets = list_workspace_embedding_targets(
                session,
                workspace_id=workspace.id,
                config=DEFAULT_EMBEDDING_CONFIG,
                limit=10,
            )
            assert len(targets) == 1

            generator = EmbeddingGenerator(_FakeModel())
            report = backfill_workspace(
                session,
                workspace_id=workspace.id,
                generator=generator,
                config=DEFAULT_EMBEDDING_CONFIG,
                batch_size=1,
                max_chunks=10,
                max_retries=1,
            )
            assert report.generated_count == 1
            assert report.failed_count == 0

            repeated_status = persist_embedding(
                session,
                target=targets[0],
                vector=generator.generate(targets)[0],
                config=DEFAULT_EMBEDDING_CONFIG,
            )
            session.commit()
            assert repeated_status is EmbeddingPersistenceStatus.SKIPPED_EXISTING

            stored_dimension = session.scalar(
                text(
                    "SELECT vector_dims(embedding) "
                    "FROM evidence_chunk_embeddings WHERE evidence_chunk_id = :chunk_id"
                ),
                {"chunk_id": str(chunk.id)},
            )
            assert stored_dimension == 384

            other_workspace, other_chunk = _create_parent(
                session,
                suffix=f"generation-other-{uuid.uuid4().hex[:12]}",
            )
            assert other_workspace.id != workspace.id
            session.add(
                _embedding(
                    workspace_id=other_workspace.id,
                    chunk_id=other_chunk.id,
                    content_hash=other_chunk.content_hash,
                    model_version="other-generation",
                )
            )
            session.commit()
            assert (
                list_workspace_embedding_targets(
                    session,
                    workspace_id=workspace.id,
                    config=DEFAULT_EMBEDDING_CONFIG,
                    limit=10,
                )
                == ()
            )
    finally:
        engine.dispose()
