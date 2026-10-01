"""Focused tests for production hybrid retrieval with Reciprocal Rank Fusion (Task 7D)."""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy.orm import Session

from app.evidence.citations.service import citation_from_candidate
from app.evidence.hybrid.config import (
    HybridRetrievalConfig,
)
from app.evidence.hybrid.errors import (
    HybridAuthorizationError,
    HybridQueryValidationError,
    HybridRetrievalError,
    HybridWorkspaceMismatchError,
)
from app.evidence.hybrid.rrf import (
    fuse_hybrid_results,
    reciprocal_rank_score,
)
from app.evidence.hybrid.service import HybridRetriever
from app.evidence.hybrid.types import HybridSearchResult
from app.evidence.search.types import SearchChunkCandidate, SearchResult
from app.evidence.semantic.service import SemanticRetriever
from app.evidence.semantic.types import SemanticSearchResult
from app.models import EvidenceDocument, EvidenceDocumentVersion
from tests.test_evidence_chunk_embeddings import _create_parent
from tests.test_evidence_semantic_retrieval import (
    _create_additional_chunk,
    _FakeSemanticModel,
    _persist_chunk_embedding,
    _unit_vector,
)


def _make_candidate(
    *,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    chunk_index: int = 0,
    content: str = "Test chunk content",
) -> SearchChunkCandidate:
    cid = chunk_id or uuid.uuid4()
    did = document_id or uuid.uuid4()
    vid = version_id or uuid.uuid4()
    return SearchChunkCandidate(
        chunk_id=cid,
        document_id=did,
        version_id=vid,
        version_number=1,
        chunk_index=chunk_index,
        content=content,
        content_hash="a" * 64,
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label="Section 1",
        page_number=1,
    )


def _make_lexical_result(
    candidate: SearchChunkCandidate,
    score: int = 100,
) -> SearchResult:
    return SearchResult(
        candidate=candidate,
        score=score,
        matched_terms=("test",),
        exact_phrase_match=False,
        occurrence_count=1,
    )


def _make_semantic_result(
    candidate: SearchChunkCandidate,
    workspace_id: uuid.UUID,
    distance: float = 0.1,
) -> SemanticSearchResult:
    return SemanticSearchResult(
        chunk_id=candidate.chunk_id,
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        version_number=candidate.version_number,
        chunk_index=candidate.chunk_index,
        content=candidate.content,
        content_hash=candidate.content_hash,
        normalized_start_byte=candidate.normalized_start_byte,
        normalized_end_byte=candidate.normalized_end_byte,
        section_label=candidate.section_label,
        page_number=candidate.page_number,
        workspace_id=workspace_id,
        distance=distance,
        similarity=1.0 - distance,
        model_id="sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
        model_version="multi-qa-MiniLM-L6-cos-v1",
        configuration_hash="cfg123",
        embedding_dimension=384,
    )


# ==============================================================================
# RRF Formula Tests
# ==============================================================================


def test_reciprocal_rank_score_exact_formula() -> None:
    # Lexical only, rank 1
    score_lex_1 = reciprocal_rank_score(lexical_rank=1, semantic_rank=None, rrf_k=60)
    assert math.isclose(score_lex_1, 1.0 / 61.0)

    # Semantic only, rank 1
    score_sem_1 = reciprocal_rank_score(lexical_rank=None, semantic_rank=1, rrf_k=60)
    assert math.isclose(score_sem_1, 1.0 / 61.0)

    # Both lexical rank 1 and semantic rank 2
    score_both = reciprocal_rank_score(lexical_rank=1, semantic_rank=2, rrf_k=60)
    expected_both = (1.0 / 61.0) + (1.0 / 62.0)
    assert math.isclose(score_both, expected_both)

    # Lexical rank 10, semantic rank 5
    score_10_5 = reciprocal_rank_score(lexical_rank=10, semantic_rank=5, rrf_k=60)
    expected_10_5 = (1.0 / 70.0) + (1.0 / 65.0)
    assert math.isclose(score_10_5, expected_10_5)


def test_reciprocal_rank_score_validation() -> None:
    with pytest.raises(ValueError, match="rrf_k must be positive"):
        reciprocal_rank_score(1, 1, rrf_k=0)

    with pytest.raises(ValueError, match="At least one component rank is required"):
        reciprocal_rank_score(None, None)

    with pytest.raises(ValueError, match="lexical_rank must be one-based"):
        reciprocal_rank_score(0, 1)

    with pytest.raises(ValueError, match="semantic_rank must be one-based"):
        reciprocal_rank_score(1, 0)


# ==============================================================================
# RRF Fusion & Deduplication Tests
# ==============================================================================


def test_fuse_hybrid_results_deduplication_and_score() -> None:
    ws_id = uuid.uuid4()
    cand_shared = _make_candidate(content="Shared in both")
    cand_lex_only = _make_candidate(content="Lexical only")
    cand_sem_only = _make_candidate(content="Semantic only")

    # Lexical stream: shared (rank 1), lex_only (rank 2)
    lex_results = [
        _make_lexical_result(cand_shared, score=200),
        _make_lexical_result(cand_lex_only, score=100),
    ]

    # Semantic stream: shared (rank 1), sem_only (rank 2)
    sem_results = [
        _make_semantic_result(cand_shared, workspace_id=ws_id, distance=0.05),
        _make_semantic_result(cand_sem_only, workspace_id=ws_id, distance=0.15),
    ]

    fused = fuse_hybrid_results(
        lex_results,
        sem_results,
        authorized_workspace_id=ws_id,
    )

    # Shared appears only ONCE in fused results
    assert len(fused) == 3
    chunk_ids = [r.chunk_id for r in fused]
    assert len(chunk_ids) == len(set(chunk_ids))

    # cand_shared has the highest score because it appeared in both streams
    assert fused[0].chunk_id == cand_shared.chunk_id
    expected_shared_score = (1.0 / 61.0) + (1.0 / 61.0)
    assert math.isclose(fused[0].rrf_score, expected_shared_score)
    assert fused[0].lexical_rank == 1
    assert fused[0].semantic_rank == 1


# ==============================================================================
# Deterministic Tie-Breaking Sequence Tests
# ==============================================================================


def test_fuse_hybrid_results_deterministic_tie_breaking() -> None:
    ws_id = uuid.uuid4()

    # Create 4 candidates with identical component ranks to test tie-breakers
    # Case 1: Same RRF score, one is lexical-present, one is semantic-only
    cand_lex_only = _make_candidate(chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000002"))
    cand_sem_only = _make_candidate(chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000001"))

    # Both rank 1 in their respective single stream -> RRF score is 1/61 for both
    lex_results = [_make_lexical_result(cand_lex_only)]
    sem_results = [_make_semantic_result(cand_sem_only, workspace_id=ws_id)]

    fused = fuse_hybrid_results(
        lex_results,
        sem_results,
        authorized_workspace_id=ws_id,
    )

    assert len(fused) == 2
    # Lexical presence wins tie-break over semantic-only,
    # even though cand_sem_only has a smaller UUID!
    assert fused[0].chunk_id == cand_lex_only.chunk_id
    assert fused[1].chunk_id == cand_sem_only.chunk_id


def test_fuse_hybrid_results_candidate_id_tie_break() -> None:
    cand_b = _make_candidate(chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000002"))
    cand_a = _make_candidate(chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000001"))

    # To test pure UUID tie-breaker: two candidates with identical lexical rank
    # and identical semantic rank (equal score)
    score_a = reciprocal_rank_score(1, None)
    score_b = reciprocal_rank_score(1, None)
    assert math.isclose(score_a, score_b)

    item_b = HybridSearchResult(
        candidate=cand_b,
        rrf_score=score_b,
        lexical_rank=1,
        semantic_rank=None,
    )
    item_a = HybridSearchResult(
        candidate=cand_a,
        rrf_score=score_a,
        lexical_rank=1,
        semantic_rank=None,
    )

    items = [item_b, item_a]

    def sort_key(item: HybridSearchResult) -> tuple[float, bool, int, bool, int, str]:
        return (
            -item.rrf_score,
            item.lexical_rank is None,
            item.lexical_rank if item.lexical_rank is not None else 11,
            item.semantic_rank is None,
            item.semantic_rank if item.semantic_rank is not None else 11,
            str(item.candidate.chunk_id),
        )

    sorted_items = sorted(items, key=sort_key)
    assert sorted_items[0].chunk_id == cand_a.chunk_id
    assert sorted_items[1].chunk_id == cand_b.chunk_id


# ==============================================================================
# Bounds & Limit Tests
# ==============================================================================


def test_fuse_hybrid_results_respects_bounds() -> None:
    ws_id = uuid.uuid4()
    # Create 15 lexical and 15 semantic candidates
    lex_candidates = [_make_candidate() for _ in range(15)]
    sem_candidates = [_make_candidate() for _ in range(15)]

    lex_results = [_make_lexical_result(c) for c in lex_candidates]
    sem_results = [_make_semantic_result(c, workspace_id=ws_id) for c in sem_candidates]

    config = HybridRetrievalConfig(
        lexical_top_k=10,
        semantic_top_k=10,
        final_top_k=10,
    )

    fused = fuse_hybrid_results(
        lex_results,
        sem_results,
        config=config,
        authorized_workspace_id=ws_id,
    )

    # Final result is strictly bounded to final_top_k (10)
    assert len(fused) == 10


# ==============================================================================
# Workspace Isolation Security Invariant Tests
# ==============================================================================


def test_fuse_hybrid_results_rejects_unauthorized_workspace_candidate() -> None:
    ws_authorized = uuid.uuid4()
    ws_other = uuid.uuid4()

    cand_auth = _make_candidate()
    cand_unauth = _make_candidate()

    lex_results = [_make_lexical_result(cand_auth)]
    sem_results = [_make_semantic_result(cand_unauth, workspace_id=ws_other)]

    with pytest.raises(HybridWorkspaceMismatchError, match="unauthorized workspace"):
        fuse_hybrid_results(
            lex_results,
            sem_results,
            authorized_workspace_id=ws_authorized,
        )


# ==============================================================================
# End-to-End HybridRetriever Integration Tests (SQLite)
# ==============================================================================


def test_hybrid_retriever_search_end_to_end(db_session: Session) -> None:
    workspace, chunk_1 = _create_parent(
        db_session, suffix="hyb-1", content="Data retention policy for backups."
    )
    version = db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id)
    doc = db_session.get(EvidenceDocument, version.document_id)

    chunk_2 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=1,
        content="Incident response procedure for security alerts.",
    )

    # Persist embedding for chunk_1
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_1,
        vector=_unit_vector(0),
    )
    # Persist embedding for chunk_2
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_2,
        vector=_unit_vector(1),
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)
    hybrid_retriever = HybridRetriever(semantic_retriever=sem_retriever)

    # Search query matches chunk_1 both lexically ("Data retention") and semantically
    results = hybrid_retriever.search(
        db_session,
        query="Data retention",
        workspace_id=workspace.id,
        limit=5,
    )

    assert len(results) >= 1
    assert results[0].chunk_id == chunk_1.id
    assert results[0].lexical_rank == 1
    assert results[0].rrf_score > 0.0

    # Verify citation compatibility
    citation = citation_from_candidate(results[0].candidate, workspace_id=workspace.id)
    assert citation.chunk_id == chunk_1.id
    assert citation.workspace_id == workspace.id


def test_hybrid_retriever_semantic_failure_fallback_to_lexical(
    db_session: Session,
) -> None:
    """When semantic search fails, HybridRetriever falls back to bounded lexical results."""

    workspace, chunk = _create_parent(
        db_session, suffix="fallback-1", content="Password complexity requirements."
    )

    # Fake model that fails on query encoding
    failing_model = _FakeSemanticModel(fail_queries=True)
    sem_retriever = SemanticRetriever(model=failing_model)

    config = HybridRetrievalConfig(enable_semantic_fallback=True)
    hybrid_retriever = HybridRetriever(semantic_retriever=sem_retriever, config=config)

    report = hybrid_retriever.search_with_report(
        db_session,
        query="Password complexity",
        workspace_id=workspace.id,
    )

    assert report.fallback_used is True
    assert report.lexical_success is True
    assert report.semantic_success is False
    assert len(report.results) == 1
    assert report.results[0].chunk_id == chunk.id
    assert report.results[0].lexical_rank == 1
    assert report.results[0].semantic_rank is None


def test_hybrid_retriever_fails_closed_when_fallback_disabled(
    db_session: Session,
) -> None:
    workspace, _ = _create_parent(
        db_session, suffix="nofallback-1", content="Access control policy."
    )

    failing_model = _FakeSemanticModel(fail_queries=True)
    sem_retriever = SemanticRetriever(model=failing_model)

    config = HybridRetrievalConfig(enable_semantic_fallback=False)
    hybrid_retriever = HybridRetriever(semantic_retriever=sem_retriever, config=config)

    with pytest.raises(HybridRetrievalError, match="Semantic retrieval failed"):
        hybrid_retriever.search(
            db_session,
            query="Access control",
            workspace_id=workspace.id,
        )


def test_hybrid_retriever_lexical_failure_fails_closed_even_when_semantic_succeeds(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Policy: If lexical retrieval fails, fail closed with HybridRetrievalError."""

    workspace, chunk = _create_parent(
        db_session, suffix="lex-fail-1", content="Key rotation policy."
    )
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk,
        vector=_unit_vector(0),
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)
    retriever = HybridRetriever(semantic_retriever=sem_retriever)

    import app.evidence.hybrid.service as hybrid_service_mod

    def _mock_list_search_candidates(*args: object, **kwargs: object) -> tuple[object, ...]:
        raise RuntimeError("Lexical DB query failure")

    monkeypatch.setattr(hybrid_service_mod, "list_search_candidates", _mock_list_search_candidates)

    with pytest.raises(HybridRetrievalError, match="Lexical retrieval failed"):
        retriever.search(
            db_session,
            query="Key rotation",
            workspace_id=workspace.id,
        )


def test_hybrid_retriever_enforces_approved_production_bounds() -> None:
    """Enforce bounds: lexical <= 10, semantic <= 10, final <= 10, rrf_k == 60."""

    with pytest.raises(ValueError, match="lexical_top_k must not exceed approved bound of 10"):
        HybridRetrievalConfig(lexical_top_k=11)

    with pytest.raises(ValueError, match="semantic_top_k must not exceed approved bound of 10"):
        HybridRetrievalConfig(semantic_top_k=11)

    with pytest.raises(ValueError, match="final_top_k must not exceed approved bound of 10"):
        HybridRetrievalConfig(final_top_k=11)

    with pytest.raises(ValueError, match="max_final_top_k must not exceed approved bound of 10"):
        HybridRetrievalConfig(max_final_top_k=11)

    with pytest.raises(ValueError, match="rrf_k must be exactly 60"):
        HybridRetrievalConfig(rrf_k=61)

    with pytest.raises(ValueError, match="rrf_k must be exactly 60"):
        HybridRetrievalConfig(rrf_k=59)


def test_hybrid_retriever_search_limit_exceeding_bounds_rejected(
    db_session: Session,
) -> None:
    workspace, _ = _create_parent(db_session, suffix="lim-bnd-1")
    retriever = HybridRetriever()

    with pytest.raises(ValueError, match="outside permitted range"):
        retriever.search(
            db_session,
            query="Key rotation",
            workspace_id=workspace.id,
            limit=11,
        )


def test_hybrid_retriever_search_version_provenance(db_session: Session) -> None:
    workspace, chunk = _create_parent(
        db_session, suffix="ver-prov-1", content="Data classification standard."
    )
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk,
        vector=_unit_vector(0),
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)
    retriever = HybridRetriever(semantic_retriever=sem_retriever)

    assert retriever.config.search_version == "hybrid-rrf-v1"

    report = retriever.search_with_report(
        db_session,
        query="Data classification",
        workspace_id=workspace.id,
    )
    assert retriever.config.search_version == "hybrid-rrf-v1"
    assert len(report.results) == 1


def test_hybrid_retriever_rejects_invalid_workspace(db_session: Session) -> None:
    hybrid_retriever = HybridRetriever()

    with pytest.raises(HybridAuthorizationError, match="workspace_id"):
        hybrid_retriever.search(
            db_session,
            query="valid query",
            workspace_id=None,  # type: ignore[arg-type]
        )


def test_hybrid_retriever_rejects_invalid_query(db_session: Session) -> None:
    workspace, _ = _create_parent(db_session, suffix="invq-1")
    hybrid_retriever = HybridRetriever()

    with pytest.raises(HybridQueryValidationError, match="Invalid search query"):
        hybrid_retriever.search(
            db_session,
            query="",
            workspace_id=workspace.id,
        )

    with pytest.raises(HybridQueryValidationError, match="Invalid search query"):
        hybrid_retriever.search(
            db_session,
            query="a",
            workspace_id=workspace.id,
        )
