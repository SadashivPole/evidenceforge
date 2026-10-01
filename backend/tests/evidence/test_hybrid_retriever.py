"""Focused service and telemetry tests for HybridRetriever."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.evidence.hybrid.config import (
    DEFAULT_HYBRID_RETRIEVAL_CONFIG,
    HybridRetrievalConfig,
)
from app.evidence.hybrid.service import HybridRetriever
from app.evidence.semantic.errors import SemanticWorkspaceMismatchError
from app.evidence.semantic.service import SemanticRetriever
from app.models import EvidenceDocument, EvidenceDocumentVersion
from tests.test_evidence_chunk_embeddings import _create_parent
from tests.test_evidence_semantic_retrieval import (
    _create_additional_chunk,
    _FakeSemanticModel,
    _persist_chunk_embedding,
    _unit_vector,
)


def test_hybrid_retriever_initialization_defaults() -> None:
    retriever = HybridRetriever()
    assert retriever.config == DEFAULT_HYBRID_RETRIEVAL_CONFIG
    assert retriever.semantic_retriever is not None


def test_hybrid_retriever_document_and_version_filtering(
    db_session: Session,
) -> None:
    workspace, chunk_1 = _create_parent(
        db_session, suffix="hyb-filt-1", content="First document encryption rules."
    )
    version_1 = db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id)
    doc_1 = db_session.get(EvidenceDocument, version_1.document_id)

    chunk_2 = _create_additional_chunk(
        db_session,
        document=doc_1,
        version=version_1,
        chunk_index=1,
        content="First document key management rules.",
    )

    # Persist embeddings
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_1, vector=_unit_vector(0)
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_2, vector=_unit_vector(1)
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)
    retriever = HybridRetriever(semantic_retriever=sem_retriever)

    # Filter to specific document_id
    results_doc = retriever.search(
        db_session,
        query="First document",
        workspace_id=workspace.id,
        document_id=doc_1.id,
    )
    assert len(results_doc) == 2
    assert all(r.document_id == doc_1.id for r in results_doc)

    # Filter to non-existent document_id -> returns empty
    results_none = retriever.search(
        db_session,
        query="First document",
        workspace_id=workspace.id,
        document_id=uuid.uuid4(),
    )
    assert results_none == ()


def test_hybrid_retriever_telemetry_reporting(db_session: Session) -> None:
    workspace, chunk = _create_parent(
        db_session, suffix="hyb-rep-1", content="Access control policy."
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk, vector=_unit_vector(0)
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)
    retriever = HybridRetriever(semantic_retriever=sem_retriever)

    report = retriever.search_with_report(
        db_session,
        query="Access control policy",
        workspace_id=workspace.id,
        limit=5,
    )

    assert report.workspace_id == workspace.id
    assert report.normalized_query == "Access control policy"
    assert report.lexical_success is True
    assert report.semantic_success is True
    assert report.fallback_used is False
    assert report.fallback_reason is None
    assert report.rrf_k == 60
    assert report.final_count == 1
    assert len(report.results) == 1


def test_hybrid_retriever_security_error_fails_closed_without_fallback(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security invariant: SemanticWorkspaceMismatchError MUST NOT be converted to fallback."""

    workspace, chunk = _create_parent(
        db_session, suffix="sec-fail-1", content="Sensitive compliance data."
    )

    sem_retriever = SemanticRetriever()

    def _mock_search(*args: object, **kwargs: object) -> tuple[object, ...]:
        raise SemanticWorkspaceMismatchError("Unauthorized workspace candidate detected")

    monkeypatch.setattr(sem_retriever, "search", _mock_search)

    retriever = HybridRetriever(
        semantic_retriever=sem_retriever,
        config=HybridRetrievalConfig(enable_semantic_fallback=True),
    )

    # Must raise SemanticWorkspaceMismatchError directly and NOT fall back to lexical
    with pytest.raises(SemanticWorkspaceMismatchError, match="Unauthorized workspace"):
        retriever.search(
            db_session,
            query="Sensitive compliance",
            workspace_id=workspace.id,
        )
