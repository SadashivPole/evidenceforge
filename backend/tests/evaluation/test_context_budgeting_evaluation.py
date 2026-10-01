"""Evaluation-level tests and metrics report for evidence context budgeting and window coverage.

MEASUREMENT CATEGORIES:
- MEASURED: Fixed 60-case benchmark corpus.
- NOT MEASURED: Real enterprise PDF corpus, NIST/SOC2 production-like document corpus,
  or long-tail token distribution in production evidence (no real document dataset
  is currently stored in the repository).
"""

from __future__ import annotations

from app.evidence.context.config import MAX_CONTEXT_CHUNKS, MAX_CONTEXT_TOKENS
from app.evidence.context.selector import select_evidence_context
from app.evidence.context.token_counter import HeuristicWhitespacePunctuationTokenCounter
from tests.evaluation.retrieval_cases import MAIN_WORKSPACE_ID, load_evaluation_cases


def test_context_budgeting_evaluation_report_across_60_cases() -> None:
    """Evaluate context budgeting over the fixed 60-case corpus and report bounds accounting.

    Note: This measures evidence context bounds and provenance preservation, NOT answer accuracy.
    """

    cases = load_evaluation_cases()
    assert len(cases) == 60

    counter = HeuristicWhitespacePunctuationTokenCounter()

    total_chunks_selected_all = 0
    total_tokens_selected_all = 0
    max_chunks_seen = 0
    max_tokens_seen = 0
    total_oversized_skipped = 0
    total_budget_skipped = 0
    provenance_preserved_count = 0
    freshness_preserved_count = 0

    for case in cases:
        # Extract workspace-eligible candidates
        eligible = [ec.candidate for ec in case.candidates if ec.workspace_id == MAIN_WORKSPACE_ID]

        result = select_evidence_context(
            eligible,
            authorized_workspace_id=MAIN_WORKSPACE_ID,
            token_counter=counter,
        )

        # Verify hard bounds for every single case
        assert result.total_selected_chunks <= MAX_CONTEXT_CHUNKS
        assert result.total_input_tokens <= MAX_CONTEXT_TOKENS
        assert result.truncated_candidate_count == 0

        total_chunks_selected_all += result.total_selected_chunks
        total_tokens_selected_all += result.total_input_tokens
        max_chunks_seen = max(max_chunks_seen, result.total_selected_chunks)
        max_tokens_seen = max(max_tokens_seen, result.total_input_tokens)
        total_oversized_skipped += result.diagnostics.oversized_candidates_skipped
        total_budget_skipped += result.diagnostics.budget_exceeded_candidates_skipped

        # Verify provenance and freshness preservation on all selected candidates
        for sel in result.selected_candidates:
            orig = next(c for c in eligible if c.chunk_id == sel.chunk_id)
            # Provenance check
            if (
                sel.chunk_id == orig.chunk_id
                and sel.document_id == orig.document_id
                and sel.version_id == orig.version_id
                and sel.version_number == orig.version_number
                and sel.content == orig.content
                and sel.content_hash == orig.content_hash
            ):
                provenance_preserved_count += 1

            # Freshness metadata check
            if (
                sel.document_version_number == orig.document_version_number
                and sel.document_status == orig.document_status
                and sel.is_latest_document_version == orig.is_latest_document_version
            ):
                freshness_preserved_count += 1

    avg_chunks = total_chunks_selected_all / len(cases)
    avg_tokens = total_tokens_selected_all / len(cases)

    # Telemetry assertions
    assert max_chunks_seen <= 5
    assert max_tokens_seen <= 4000
    assert provenance_preserved_count == total_chunks_selected_all
    assert freshness_preserved_count == total_chunks_selected_all
    assert avg_chunks >= 1.0
    assert avg_tokens >= 0.0


def test_chunk_length_and_embedding_window_coverage_analysis() -> None:
    """Analyze chunk token distribution and 250-word-piece embedding coverage on the 60-case corpus.

    Categorization:
    - MEASURED: Fixed 60-case benchmark corpus chunk distribution.
    - NOT MEASURED: Real enterprise PDF corpus / production long-tail evidence.
    """

    cases = load_evaluation_cases()
    counter = HeuristicWhitespacePunctuationTokenCounter()

    all_chunks = [ec.candidate for case in cases for ec in case.candidates]
    unique_chunks = {c.chunk_id: c for c in all_chunks}.values()

    token_counts = [counter.count_tokens(c.content) for c in unique_chunks]
    byte_counts = [len(c.content.encode("utf-8")) for c in unique_chunks]

    total_unique = len(unique_chunks)
    assert total_unique > 0

    exceeding_250_tokens = sum(1 for tc in token_counts if tc > 250)
    fraction_exceeding_250 = exceeding_250_tokens / total_unique

    # In the fixed 60-case benchmark corpus, chunks are short synthetic passages (<250 tokens)
    coverage_percentages = [min(1.0, 250.0 / tc) if tc > 0 else 1.0 for tc in token_counts]
    mean_coverage = sum(coverage_percentages) / total_unique

    assert fraction_exceeding_250 == 0.0
    assert mean_coverage == 1.0
    assert all(tc >= 0 for tc in token_counts)
    assert all(bc >= 0 for bc in byte_counts)
