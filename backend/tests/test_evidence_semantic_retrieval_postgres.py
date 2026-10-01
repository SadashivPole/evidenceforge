"""PostgreSQL 16 + pgvector 0.8.6 integration tests for semantic retrieval (Task 7C)."""

from __future__ import annotations

import math
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

from app.evidence.embeddings.config import EmbeddingConfig
from app.evidence.semantic.config import (
    SemanticRetrievalConfig,
)
from app.evidence.semantic.service import SemanticRetriever
from tests.test_evidence_chunk_embeddings import _create_parent
from tests.test_evidence_semantic_retrieval import (
    _create_additional_chunk,
    _mixed_vector,
    _persist_chunk_embedding,
    _unit_vector,
)

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


def test_postgres_pgvector_exact_semantic_search_and_distance() -> None:
    """Verify real PostgreSQL 16 + pgvector 0.8.6 cosine distance query execution."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with engine.connect() as connection:
            server_version = str(connection.scalar(text("SHOW server_version_num")))
            assert server_version.startswith("16"), server_version
            ext_version = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            assert ext_version == "0.8.6", ext_version

        with Session(engine, expire_on_commit=False) as session:
            workspace, chunk_1 = _create_parent(session, suffix=f"pg-sem-{uuid.uuid4().hex[:12]}")
            from app.models import EvidenceDocument, EvidenceDocumentVersion

            version = session.get(EvidenceDocumentVersion, chunk_1.document_version_id)
            doc = session.get(EvidenceDocument, version.document_id)

            chunk_2 = _create_additional_chunk(
                session,
                document=doc,
                version=version,
                chunk_index=1,
                content="Postgres chunk 2",
            )
            chunk_3 = _create_additional_chunk(
                session,
                document=doc,
                version=version,
                chunk_index=2,
                content="Postgres chunk 3",
            )

            # Persist 384-dimensional test vectors
            _persist_chunk_embedding(
                session, workspace_id=workspace.id, chunk=chunk_1, vector=_unit_vector(0)
            )
            _persist_chunk_embedding(
                session, workspace_id=workspace.id, chunk=chunk_2, vector=_mixed_vector(0, 1)
            )
            _persist_chunk_embedding(
                session, workspace_id=workspace.id, chunk=chunk_3, vector=_unit_vector(1)
            )

            retriever = SemanticRetriever()
            query_vec = _unit_vector(0)

            results = retriever.search_vector(
                session,
                query_vector=query_vec,
                workspace_id=workspace.id,
                top_k=10,
            )

            assert len(results) == 3
            # Closest: chunk_1 (distance 0.0)
            assert results[0].chunk_id == chunk_1.id
            assert math.isclose(results[0].distance, 0.0, abs_tol=1e-4)
            assert math.isclose(results[0].similarity, 1.0, abs_tol=1e-4)

            # Middle: chunk_2 (distance ~0.2929)
            assert results[1].chunk_id == chunk_2.id
            assert 0.28 < results[1].distance < 0.30
            assert 0.70 < results[1].similarity < 0.72

            # Furthest: chunk_3 (distance 1.0)
            assert results[2].chunk_id == chunk_3.id
            assert math.isclose(results[2].distance, 1.0, abs_tol=1e-4)
            assert math.isclose(results[2].similarity, 0.0, abs_tol=1e-4)
    finally:
        engine.dispose()


def test_postgres_semantic_retrieval_workspace_isolation() -> None:
    """Security Invariant: Unauthorized candidate is excluded at the database boundary."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with Session(engine, expire_on_commit=False) as session:
            workspace_a, chunk_a = _create_parent(
                session, suffix=f"pg-iso-a-{uuid.uuid4().hex[:12]}"
            )
            workspace_b, chunk_b = _create_parent(
                session, suffix=f"pg-iso-b-{uuid.uuid4().hex[:12]}"
            )

            target_vec = _unit_vector(42)

            # Workspace B has a candidate that matches query perfectly (distance 0.0)
            _persist_chunk_embedding(
                session,
                workspace_id=workspace_b.id,
                chunk=chunk_b,
                vector=target_vec,
            )

            # Workspace A has a candidate that is orthogonal (distance 1.0)
            _persist_chunk_embedding(
                session,
                workspace_id=workspace_a.id,
                chunk=chunk_a,
                vector=_unit_vector(100),
            )

            retriever = SemanticRetriever()

            # Search in Workspace A
            results = retriever.search_vector(
                session,
                query_vector=target_vec,
                workspace_id=workspace_a.id,
                top_k=10,
            )

            # Must return ONLY Workspace A's candidate
            assert len(results) == 1
            assert results[0].chunk_id == chunk_a.id
            assert results[0].workspace_id == workspace_a.id
            assert all(r.workspace_id == workspace_a.id for r in results)
            assert chunk_b.id not in {r.chunk_id for r in results}
    finally:
        engine.dispose()


def test_postgres_semantic_retrieval_deterministic_tie_breaking() -> None:
    """Verify deterministic tie-breaking on identical distance vectors in PostgreSQL."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with Session(engine, expire_on_commit=False) as session:
            workspace, chunk_1 = _create_parent(session, suffix=f"pg-tie-{uuid.uuid4().hex[:12]}")
            from app.models import EvidenceDocument, EvidenceDocumentVersion

            version = session.get(EvidenceDocumentVersion, chunk_1.document_version_id)
            doc = session.get(EvidenceDocument, version.document_id)

            chunk_2 = _create_additional_chunk(
                session,
                document=doc,
                version=version,
                chunk_index=1,
                content="Tie chunk 2",
            )
            chunk_3 = _create_additional_chunk(
                session,
                document=doc,
                version=version,
                chunk_index=2,
                content="Tie chunk 3",
            )

            identical_vec = _unit_vector(7)
            _persist_chunk_embedding(
                session, workspace_id=workspace.id, chunk=chunk_1, vector=identical_vec
            )
            _persist_chunk_embedding(
                session, workspace_id=workspace.id, chunk=chunk_2, vector=identical_vec
            )
            _persist_chunk_embedding(
                session, workspace_id=workspace.id, chunk=chunk_3, vector=identical_vec
            )

            retriever = SemanticRetriever()
            results = retriever.search_vector(
                session,
                query_vector=identical_vec,
                workspace_id=workspace.id,
                top_k=10,
            )

            assert len(results) == 3
            expected_ids = sorted([chunk_1.id, chunk_2.id, chunk_3.id])
            actual_ids = [r.chunk_id for r in results]
            assert actual_ids == expected_ids
    finally:
        engine.dispose()


def test_postgres_semantic_retrieval_configuration_hash_isolation() -> None:
    """Verify that embeddings from a different configuration are not returned."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with Session(engine, expire_on_commit=False) as session:
            workspace, chunk = _create_parent(session, suffix=f"pg-cfg-{uuid.uuid4().hex[:12]}")

            other_config = SemanticRetrievalConfig(
                embedding_config=EmbeddingConfig(model_version="other-v2")
            )
            _persist_chunk_embedding(
                session,
                workspace_id=workspace.id,
                chunk=chunk,
                vector=_unit_vector(0),
                config=other_config,
            )

            retriever = SemanticRetriever()
            results = retriever.search_vector(
                session,
                query_vector=_unit_vector(0),
                workspace_id=workspace.id,
                top_k=10,
            )
            assert results == ()
    finally:
        engine.dispose()
