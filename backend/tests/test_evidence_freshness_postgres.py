"""PostgreSQL 16 + pgvector 0.8.6 integration tests for evidence freshness metadata enrichment."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.evidence.hybrid.service import HybridRetriever
from app.evidence.persistence.search_repository import list_search_candidates
from app.evidence.semantic.service import SemanticRetriever
from tests.test_evidence_chunk_embeddings import _create_parent
from tests.test_evidence_freshness import _create_versioned_evidence
from tests.test_evidence_semantic_retrieval import (
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


def test_postgres_freshness_metadata_enrichment_and_workspace_isolation() -> None:
    """Verify real PostgreSQL 16 lexical and semantic freshness metadata enrichment."""

    _upgrade_head()
    engine = create_engine(POSTGRES_URL)

    with Session(engine) as session:
        ws_a, _ = _create_parent(session, suffix="pg-fresh-a")
        ws_b, _ = _create_parent(session, suffix="pg-fresh-b")

        # Workspace A: Document with 2 versions
        doc_a, ver_chunks_a = _create_versioned_evidence(
            session,
            workspace_id=ws_a.id,
            actor_user_id=ws_a.created_by_user_id,
            document_name="Postgres Policy A",
            versions=[
                (1, "Postgres Access Control v1."),
                (2, "Postgres Access Control v2 with MFA."),
            ],
        )

        # Workspace B: Document with 1 version
        doc_b, ver_chunks_b = _create_versioned_evidence(
            session,
            workspace_id=ws_b.id,
            actor_user_id=ws_b.created_by_user_id,
            document_name="Postgres Policy B",
            versions=[(1, "Postgres Access Control v1 for Workspace B.")],
        )

        # Persist embeddings for Workspace A
        _, chunk_a1 = ver_chunks_a[0]
        _, chunk_a2 = ver_chunks_a[1]
        _persist_chunk_embedding(
            session, workspace_id=ws_a.id, chunk=chunk_a1, vector=_unit_vector(0)
        )
        _persist_chunk_embedding(
            session, workspace_id=ws_a.id, chunk=chunk_a2, vector=_unit_vector(1)
        )

        # 1. Lexical candidate check in Workspace A
        cands_a = list_search_candidates(session, workspace_id=ws_a.id)
        assert len(cands_a) >= 2
        cand_map_a = {c.version_number: c for c in cands_a if c.document_id == doc_a.id}

        assert cand_map_a[1].latest_document_version_number == 2
        assert cand_map_a[1].is_latest_document_version is False
        assert cand_map_a[2].latest_document_version_number == 2
        assert cand_map_a[2].is_latest_document_version is True

        # 2. Semantic search check in Workspace A
        model = _FakeSemanticModel()
        sem_retriever = SemanticRetriever(model=model)
        sem_results_a = sem_retriever.search(
            session,
            query="Postgres Access Control",
            workspace_id=ws_a.id,
        )
        assert len(sem_results_a) >= 2
        sem_map_a = {r.version_number: r for r in sem_results_a if r.document_id == doc_a.id}
        assert sem_map_a[1].is_latest_document_version is False
        assert sem_map_a[2].is_latest_document_version is True

        # 3. Hybrid search check in Workspace A
        hybrid_retriever = HybridRetriever(semantic_retriever=sem_retriever)
        hybrid_results_a = hybrid_retriever.search(
            session,
            query="Postgres Access Control",
            workspace_id=ws_a.id,
        )
        hyb_map_a = {r.version_number: r for r in hybrid_results_a if r.document_id == doc_a.id}
        assert hyb_map_a[1].is_latest_document_version is False
        assert hyb_map_a[2].is_latest_document_version is True

        # 4. Workspace B check
        cands_b = list_search_candidates(session, workspace_id=ws_b.id)
        cand_map_b = {c.version_number: c for c in cands_b if c.document_id == doc_b.id}
        assert cand_map_b[1].latest_document_version_number == 1
        assert cand_map_b[1].is_latest_document_version is True
