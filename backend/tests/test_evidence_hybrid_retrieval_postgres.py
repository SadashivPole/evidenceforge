"""PostgreSQL 16 + pgvector 0.8.6 integration tests for production hybrid retrieval (Task 7D)."""

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

from app.evidence.hybrid.config import HybridRetrievalConfig
from app.evidence.hybrid.service import HybridRetriever
from app.evidence.semantic.service import SemanticRetriever
from tests.test_evidence_chunk_embeddings import _create_parent
from tests.test_evidence_semantic_retrieval import (
    _create_additional_chunk,
    _FakeSemanticModel,
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


def test_postgres_hybrid_retrieval_fusion_and_ranking() -> None:
    """Verify real PostgreSQL 16 + pgvector 0.8.6 lexical + semantic RRF fusion."""

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
            workspace, chunk_1 = _create_parent(
                session,
                suffix=f"pg-hyb-{uuid.uuid4().hex[:12]}",
                content="Data encryption standard AES-256 for databases.",
            )
            from app.models import EvidenceDocument, EvidenceDocumentVersion

            version = session.get(EvidenceDocumentVersion, chunk_1.document_version_id)
            doc = session.get(EvidenceDocument, version.document_id)

            # Chunk 2 matches query keywords
            chunk_2 = _create_additional_chunk(
                session,
                document=doc,
                version=version,
                chunk_index=1,
                content="Backup retention policy data storage.",
            )

            # Persist pgvector embeddings
            # chunk_1: unit vector 0
            _persist_chunk_embedding(
                session,
                workspace_id=workspace.id,
                chunk=chunk_1,
                vector=_unit_vector(0),
            )
            # chunk_2: unit vector 1
            _persist_chunk_embedding(
                session,
                workspace_id=workspace.id,
                chunk=chunk_2,
                vector=_unit_vector(1),
            )

            model = _FakeSemanticModel()
            sem_retriever = SemanticRetriever(model=model)
            retriever = HybridRetriever(semantic_retriever=sem_retriever)

            results = retriever.search(
                session,
                query="Data encryption",
                workspace_id=workspace.id,
                limit=10,
            )

            assert len(results) >= 1
            # Top candidate should be chunk_1 (matched in both lexical and semantic)
            assert results[0].chunk_id == chunk_1.id
            assert results[0].rrf_score > 0.0
    finally:
        engine.dispose()


def test_postgres_hybrid_retrieval_workspace_isolation() -> None:
    """Security Invariant: Unauthorized candidate is excluded in both streams in PostgreSQL."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with Session(engine, expire_on_commit=False) as session:
            workspace_a, chunk_a = _create_parent(
                session,
                suffix=f"pg-hiso-a-{uuid.uuid4().hex[:12]}",
                content="Authentication and password policy.",
            )
            workspace_b, chunk_b = _create_parent(
                session,
                suffix=f"pg-hiso-b-{uuid.uuid4().hex[:12]}",
                content="Authentication and password policy with perfect match.",
            )

            target_vec = _unit_vector(5)

            _persist_chunk_embedding(
                session,
                workspace_id=workspace_b.id,
                chunk=chunk_b,
                vector=target_vec,
            )
            _persist_chunk_embedding(
                session,
                workspace_id=workspace_a.id,
                chunk=chunk_a,
                vector=_unit_vector(10),
            )

            model = _FakeSemanticModel()
            sem_retriever = SemanticRetriever(model=model)
            retriever = HybridRetriever(semantic_retriever=sem_retriever)

            # Search in Workspace A
            results = retriever.search(
                session,
                query="Authentication password",
                workspace_id=workspace_a.id,
                limit=10,
            )

            assert len(results) == 1
            assert results[0].chunk_id == chunk_a.id
            assert results[0].workspace_id == workspace_a.id
            assert chunk_b.id not in {r.chunk_id for r in results}
    finally:
        engine.dispose()


def test_postgres_hybrid_retrieval_semantic_fallback() -> None:
    """Verify lexical fallback on PostgreSQL when semantic retrieval fails."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    try:
        with Session(engine, expire_on_commit=False) as session:
            workspace, chunk = _create_parent(
                session,
                suffix=f"pg-hfb-{uuid.uuid4().hex[:12]}",
                content="Vulnerability management and scanning policy.",
            )

            failing_model = _FakeSemanticModel(fail_queries=True)
            sem_retriever = SemanticRetriever(model=failing_model)
            retriever = HybridRetriever(
                semantic_retriever=sem_retriever,
                config=HybridRetrievalConfig(enable_semantic_fallback=True),
            )

            report = retriever.search_with_report(
                session,
                query="Vulnerability management",
                workspace_id=workspace.id,
                limit=10,
            )

            assert report.fallback_used is True
            assert report.lexical_success is True
            assert report.semantic_success is False
            assert len(report.results) == 1
            assert report.results[0].chunk_id == chunk.id
    finally:
        engine.dispose()
