"""Deterministic unit and service tests for evidence context selection and budgeting."""

from __future__ import annotations

import uuid

import pytest

from app.evidence.context.config import (
    ContextBudgetConfig,
)
from app.evidence.context.errors import (
    ContextSelectionAuthorizationError,
    ContextSelectionWorkspaceMismatchError,
)
from app.evidence.context.selector import (
    EvidenceContextSelector,
    select_evidence_context,
)
from app.evidence.context.token_counter import (
    ExactModelTokenCounter,
    HeuristicWhitespacePunctuationTokenCounter,
)
from app.evidence.hybrid.types import HybridSearchResult
from app.evidence.search.types import SearchChunkCandidate, SearchResult


def _make_candidate(
    *,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    version_number: int = 1,
    latest_document_version_number: int = 1,
    is_latest_document_version: bool = True,
    document_status: str = "active",
    conflict_group_id: str | None = None,
    content: str = "Default evidence content for testing.",
    section_label: str | None = "Section 1",
    page_number: int | None = 1,
) -> SearchChunkCandidate:
    cid = chunk_id or uuid.uuid4()
    did = document_id or uuid.uuid4()
    vid = version_id or uuid.uuid4()
    return SearchChunkCandidate(
        chunk_id=cid,
        document_id=did,
        version_id=vid,
        version_number=version_number,
        chunk_index=0,
        content=content,
        content_hash="mock_hash",
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label=section_label,
        page_number=page_number,
        document_status=document_status,
        latest_document_version_number=latest_document_version_number,
        is_latest_document_version=is_latest_document_version,
        conflict_group_id=conflict_group_id,
    )


def _make_hybrid_result(
    candidate: SearchChunkCandidate,
    *,
    rrf_score: float = 0.03,
    lexical_rank: int = 1,
    semantic_rank: int = 1,
    workspace_id: uuid.UUID | None = None,
) -> HybridSearchResult:
    lex_res = SearchResult(
        candidate=candidate,
        score=100,
        matched_terms=("term",),
        exact_phrase_match=True,
        occurrence_count=1,
    )
    return HybridSearchResult(
        candidate=candidate,
        rrf_score=rrf_score,
        lexical_rank=lexical_rank,
        semantic_rank=semantic_rank,
        lexical_result=lex_res,
        workspace_id=workspace_id,
    )


class _FixedTokenCounter:
    """Mock token counter returning a pre-configured token count per call."""

    def __init__(self, token_map: dict[str, int], default_count: int = 100) -> None:
        self.token_map = token_map
        self.default_count = default_count
        self.tokenizer_version = "fixed-token-counter-v1"
        self.is_exact_model_tokenizer = True

    def count_tokens(self, text: str) -> int:
        return self.token_map.get(text, self.default_count)


def test_context_selection_five_candidates_all_fit() -> None:
    """Test 1: Five candidates that all fit within token budget -> exactly five selected."""

    ws_id = uuid.uuid4()
    candidates = [_make_candidate(content=f"Chunk {i}") for i in range(5)]
    counter = _FixedTokenCounter({}, default_count=100)

    result = select_evidence_context(
        candidates,
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    assert result.total_selected_chunks == 5
    assert len(result.selected_candidates) == 5
    assert result.total_input_tokens == 500
    assert result.truncated_candidate_count == 0
    assert result.skipped_candidate_count == 0


def test_context_selection_six_candidates_first_five_selected() -> None:
    """Test 2: Six candidates that fit within token budget -> first five selected (max 5 bound)."""

    ws_id = uuid.uuid4()
    candidates = [_make_candidate(content=f"Chunk {i}") for i in range(6)]
    counter = _FixedTokenCounter({}, default_count=100)

    result = select_evidence_context(
        candidates,
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    assert result.total_selected_chunks == 5
    assert len(result.selected_candidates) == 5
    assert result.total_input_tokens == 500
    # First 5 chunk IDs match the first 5 input candidate chunk IDs
    for idx in range(5):
        assert result.selected_candidates[idx].chunk_id == candidates[idx].chunk_id
        assert result.selected_candidates[idx].selection_rank == idx + 1


def test_context_selection_preserves_upstream_rrf_ordering() -> None:
    """Test 3: Candidate ordering remains strictly identical to upstream RRF ordering."""

    ws_id = uuid.uuid4()
    cands = [_make_candidate(content=f"Ordered chunk {i}") for i in range(4)]
    hybrid_results = [
        _make_hybrid_result(
            cands[i],
            rrf_score=0.1 - (i * 0.01),
            lexical_rank=i + 1,
            workspace_id=ws_id,
        )
        for i in range(4)
    ]
    counter = _FixedTokenCounter({}, default_count=200)

    result = select_evidence_context(
        hybrid_results,
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    assert result.total_selected_chunks == 4
    for i in range(4):
        assert result.selected_candidates[i].chunk_id == cands[i].chunk_id
        assert result.selected_candidates[i].rrf_score == hybrid_results[i].rrf_score
        assert result.selected_candidates[i].selection_rank == i + 1


def test_context_selection_stops_at_token_budget_4000() -> None:
    """Test 4: Token budget stops selection when total tokens would exceed 4000."""

    ws_id = uuid.uuid4()
    # 5 candidates with 1,000 tokens each (total 5,000 tokens)
    candidates = [_make_candidate(content=f"Large Chunk {i}") for i in range(5)]
    counter = _FixedTokenCounter({}, default_count=1000)

    result = select_evidence_context(
        candidates,
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    # Only 4 candidates fit (4 * 1000 = 4000 <= 4000; 5th candidate would exceed budget)
    assert result.total_selected_chunks == 4
    assert result.total_input_tokens == 4000
    assert result.skipped_candidate_count == 1
    assert result.diagnostics.budget_exceeded_candidates_skipped == 1


def test_context_selection_candidate_exceeding_remaining_budget_is_skipped() -> None:
    """Test 5: Candidate that would exceed remaining budget is skipped, later smaller fit."""

    ws_id = uuid.uuid4()
    c1 = _make_candidate(content="Chunk 1 (3500 tokens)")
    c2 = _make_candidate(content="Chunk 2 (800 tokens)")  # Exceeds remaining 500 budget -> skipped
    c3 = _make_candidate(content="Chunk 3 (300 tokens)")  # Fits in remaining 500 budget -> selected

    token_map = {
        c1.content: 3500,
        c2.content: 800,
        c3.content: 300,
    }
    counter = _FixedTokenCounter(token_map)

    result = select_evidence_context(
        [c1, c2, c3],
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    # c1 and c3 selected; c2 skipped
    assert result.total_selected_chunks == 2
    assert result.total_input_tokens == 3800
    assert result.selected_candidates[0].chunk_id == c1.chunk_id
    assert result.selected_candidates[1].chunk_id == c3.chunk_id
    assert result.skipped_candidate_count == 1
    assert result.diagnostics.budget_exceeded_candidates_skipped == 1


def test_context_selection_single_oversized_candidate_skipped_safely() -> None:
    """Test 6: A single candidate exceeding 4000 tokens on its own is skipped safely."""

    ws_id = uuid.uuid4()
    c_huge = _make_candidate(content="Huge chunk (4500 tokens)")
    c_normal = _make_candidate(content="Normal chunk (500 tokens)")

    token_map = {
        c_huge.content: 4500,
        c_normal.content: 500,
    }
    counter = _FixedTokenCounter(token_map)

    result = select_evidence_context(
        [c_huge, c_normal],
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    # c_huge is skipped as oversized; c_normal is selected
    assert result.total_selected_chunks == 1
    assert result.total_input_tokens == 500
    assert result.selected_candidates[0].chunk_id == c_normal.chunk_id
    assert result.diagnostics.oversized_candidates_skipped == 1


def test_context_selection_exactly_4000_tokens_accepted() -> None:
    """Test 8: Exactly 4000 tokens is accepted."""

    ws_id = uuid.uuid4()
    c_exact = _make_candidate(content="Exact 4000 token chunk")
    token_map = {c_exact.content: 4000}
    counter = _FixedTokenCounter(token_map)

    result = select_evidence_context(
        [c_exact],
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    assert result.total_selected_chunks == 1
    assert result.total_input_tokens == 4000
    assert result.selected_candidates[0].chunk_id == c_exact.chunk_id
    assert result.diagnostics.remaining_token_budget == 0


def test_context_selection_4001_tokens_skipped() -> None:
    """Test 9: 4001 tokens is rejected/skipped according to documented policy."""

    ws_id = uuid.uuid4()
    c_over = _make_candidate(content="4001 token chunk")
    token_map = {c_over.content: 4001}
    counter = _FixedTokenCounter(token_map)

    result = select_evidence_context(
        [c_over],
        authorized_workspace_id=ws_id,
        token_counter=counter,
    )

    assert result.total_selected_chunks == 0
    assert result.total_input_tokens == 0
    assert result.diagnostics.oversized_candidates_skipped == 1


def test_context_selection_preserves_freshness_and_conflict_metadata() -> None:
    """Tests 10 & 11: Freshness and conflict metadata survive selection unchanged."""

    ws_id = uuid.uuid4()
    c_stale = _make_candidate(
        version_number=1,
        latest_document_version_number=2,
        is_latest_document_version=False,
        document_status="active",
        conflict_group_id=None,
    )
    c_current = _make_candidate(
        version_number=2,
        latest_document_version_number=2,
        is_latest_document_version=True,
        document_status="active",
        conflict_group_id=None,
    )

    result = select_evidence_context(
        [c_stale, c_current],
        authorized_workspace_id=ws_id,
    )

    assert result.total_selected_chunks == 2

    sel_stale = result.selected_candidates[0]
    assert sel_stale.document_version_number == 1
    assert sel_stale.latest_document_version_number == 2
    assert sel_stale.is_latest_document_version is False
    assert sel_stale.document_status == "active"
    assert sel_stale.conflict_group_id is None

    sel_current = result.selected_candidates[1]
    assert sel_current.document_version_number == 2
    assert sel_current.latest_document_version_number == 2
    assert sel_current.is_latest_document_version is True
    assert sel_current.document_status == "active"
    assert sel_current.conflict_group_id is None


def test_context_selection_preserves_provenance() -> None:
    """Test 12: Provenance (chunk_id, document_id, version_id, byte offsets) survives selection."""

    ws_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    c = _make_candidate(
        chunk_id=chunk_id,
        document_id=doc_id,
        version_id=ver_id,
        version_number=3,
        section_label="Section 4.1",
        page_number=12,
        content="Provenance verification content.",
    )

    result = select_evidence_context(
        [c],
        authorized_workspace_id=ws_id,
    )

    assert result.total_selected_chunks == 1
    sel = result.selected_candidates[0]
    assert sel.chunk_id == chunk_id
    assert sel.document_id == doc_id
    assert sel.version_id == ver_id
    assert sel.version_number == 3
    assert sel.section_label == "Section 4.1"
    assert sel.page_number == 12
    assert sel.content == "Provenance verification content."
    assert sel.content_hash == "mock_hash"


def test_context_selection_workspace_mismatch_fails_closed() -> None:
    """Test 13: Candidate belonging to unauthorized workspace fails closed with error."""

    ws_auth = uuid.uuid4()
    ws_other = uuid.uuid4()

    c_other = _make_candidate()
    hybrid_other = _make_hybrid_result(c_other, workspace_id=ws_other)

    with pytest.raises(ContextSelectionWorkspaceMismatchError, match="unauthorized workspace"):
        select_evidence_context(
            [hybrid_other],
            authorized_workspace_id=ws_auth,
        )


def test_context_selection_deterministic_across_repeated_calls() -> None:
    """Test 14: Selection output is bit-identical across multiple invocations."""

    ws_id = uuid.uuid4()
    candidates = [_make_candidate(content=f"Repeatable chunk {i}") for i in range(10)]

    result_1 = select_evidence_context(candidates, authorized_workspace_id=ws_id)
    result_2 = select_evidence_context(candidates, authorized_workspace_id=ws_id)

    assert result_1.total_selected_chunks == result_2.total_selected_chunks == 5
    assert result_1.total_input_tokens == result_2.total_input_tokens
    assert [c.chunk_id for c in result_1.selected_candidates] == [
        c.chunk_id for c in result_2.selected_candidates
    ]


def test_context_selection_invalid_workspace_id_fails_closed() -> None:
    """Test: None or non-UUID workspace raises ContextSelectionAuthorizationError."""

    with pytest.raises(ContextSelectionAuthorizationError):
        select_evidence_context([], authorized_workspace_id=None)  # type: ignore[arg-type]


def test_context_budget_config_bounds_enforcement() -> None:
    """Test: ContextBudgetConfig enforces max_chunks <= 5 and max_tokens <= 4000."""

    with pytest.raises(ValueError, match="max_chunks must not exceed approved bound of 5"):
        ContextBudgetConfig(max_chunks=6)

    with pytest.raises(ValueError, match="max_tokens must not exceed approved bound of 4000"):
        ContextBudgetConfig(max_tokens=4001)

    with pytest.raises(ValueError, match="max_chunks must be at least 1"):
        ContextBudgetConfig(max_chunks=0)

    with pytest.raises(ValueError, match="max_tokens must be at least 1"):
        ContextBudgetConfig(max_tokens=0)


def test_evidence_context_selector_service_with_overrides() -> None:
    """Test: EvidenceContextSelector service respects custom valid bounds overrides."""

    ws_id = uuid.uuid4()
    candidates = [_make_candidate(content=f"Service Chunk {i}") for i in range(5)]
    selector = EvidenceContextSelector()

    # Override to max 3 chunks
    res = selector.select(candidates, authorized_workspace_id=ws_id, max_chunks=3)
    assert res.total_selected_chunks == 3


def test_deterministic_token_counters() -> None:
    """Test: ExactModelTokenCounter and HeuristicWhitespacePunctuationTokenCounter behavior."""

    # Heuristic Counter
    heuristic_counter = HeuristicWhitespacePunctuationTokenCounter()
    assert heuristic_counter.tokenizer_version == "heuristic-whitespace-punctuation-v1"
    assert heuristic_counter.is_exact_model_tokenizer is False
    assert heuristic_counter.count_tokens("") == 0
    assert heuristic_counter.count_tokens("   ") == 0
    tokens_phrase = heuristic_counter.count_tokens("Access control requires MFA.")
    assert tokens_phrase == 5  # Access, control, requires, MFA, .

    # Exact Model Token Counter with mock tokenizer
    class _MockTokenizer:
        def encode(self, text: str, **_: object) -> list[int]:
            return [1, 2, 3]

    exact_counter = ExactModelTokenCounter(tokenizer=_MockTokenizer())
    assert exact_counter.tokenizer_version == "exact-model-tokenizer-v1"
    assert exact_counter.is_exact_model_tokenizer is True
    assert exact_counter.count_tokens("test text") == 3
