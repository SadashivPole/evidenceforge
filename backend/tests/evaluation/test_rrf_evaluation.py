"""Evaluation-only lexical, semantic, and RRF comparison tests."""

from __future__ import annotations

import uuid

import pytest

from app.evaluation.retrieval import EvaluationCategory, evaluate_cases
from app.evaluation.rrf import (
    RRFConfig,
    fuse_ranked_candidate_ids,
    reciprocal_rank_score,
    run_rrf_comparison,
)
from tests.evaluation.retrieval_cases import (
    MAIN_WORKSPACE_ID,
    OTHER_WORKSPACE_ID,
    load_evaluation_cases,
)


def _id(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"rrf-test:{label}")


@pytest.fixture(scope="module")
def rrf_report():
    try:
        return run_rrf_comparison(load_evaluation_cases())
    except RuntimeError as exc:
        pytest.skip(f"Real semantic model unavailable: {exc}")


def test_hand_calculated_rrf_scores_use_one_based_ranks() -> None:
    candidate_a = _id("a")
    candidate_b = _id("b")
    lexical = (candidate_a, candidate_b)
    semantic = (candidate_b, candidate_a)

    fused = fuse_ranked_candidate_ids(lexical, semantic)
    by_id = {item.candidate_id: item for item in fused}

    expected_a = (1 / (60 + 1)) + (1 / (60 + 2))
    expected_b = (1 / (60 + 2)) + (1 / (60 + 1))
    assert by_id[candidate_a].rrf_score == expected_a
    assert by_id[candidate_b].rrf_score == expected_b
    assert by_id[candidate_a].lexical_rank == 1
    assert by_id[candidate_a].semantic_rank == 2


def test_rrf_one_list_and_two_list_contributions() -> None:
    assert reciprocal_rank_score(1, None) == 1 / 61
    assert reciprocal_rank_score(None, 3) == 1 / 63
    assert reciprocal_rank_score(1, 3) == (1 / 61) + (1 / 63)


def test_rrf_candidate_identity_is_candidate_id_and_top_k_is_bounded() -> None:
    lexical = tuple(_id(f"lexical-{index}") for index in range(15))
    semantic = tuple(_id(f"semantic-{index}") for index in range(15))

    fused = fuse_ranked_candidate_ids(
        lexical,
        semantic,
        config=RRFConfig(final_top_k=10),
    )

    assert len(fused) == 10
    assert len({item.candidate_id for item in fused}) == 10
    assert all(item.candidate_id in set(lexical) | set(semantic) for item in fused)


def test_rrf_tie_breaking_is_deterministic() -> None:
    lexical_only = _id("lexical-only")
    semantic_only = _id("semantic-only")
    lower_id = min(lexical_only, semantic_only)

    first = fuse_ranked_candidate_ids((lexical_only,), (semantic_only,))
    second = fuse_ranked_candidate_ids((lexical_only,), (semantic_only,))

    assert first == second
    assert first[0].candidate_id == lexical_only
    assert first[0].candidate_id != lower_id or lexical_only == lower_id


def test_rrf_output_is_repeatable() -> None:
    cases = load_evaluation_cases()
    lexical = tuple(item.candidate_id for item in cases[0].candidates)
    semantic = tuple(reversed(lexical))

    first = fuse_ranked_candidate_ids(lexical, semantic)
    second = fuse_ranked_candidate_ids(lexical, semantic)

    assert first == second


def test_fixed_corpus_and_lexical_baseline_are_unchanged() -> None:
    cases = load_evaluation_cases()
    assert len(cases) == 60
    assert {case.category for case in cases} == set(EvaluationCategory)

    lexical_report = evaluate_cases(cases)
    assert lexical_report.macro_recall_at_5 == 0.8333333333333334
    assert lexical_report.macro_ndcg_at_5 == 0.9219567263864729
    assert lexical_report.macro_ndcg_at_10 == 0.9219567263864729


def test_three_way_report_preserves_lexical_and_semantic_baselines(rrf_report) -> None:
    assert rrf_report.corpus_case_count == 60
    assert rrf_report.lexical.macro_recall_at_5 == 0.8333333333333334
    assert rrf_report.lexical.macro_ndcg_at_5 == 0.9219567263864729
    assert rrf_report.lexical.macro_ndcg_at_10 == 0.9219567263864729
    assert rrf_report.semantic.macro_recall_at_5 == 0.8333333333333334
    assert rrf_report.semantic.macro_ndcg_at_5 == 0.953101098880283
    assert rrf_report.semantic.macro_ndcg_at_10 == 0.953101098880283
    assert rrf_report.hybrid_rrf.case_count == 60


def test_three_way_report_contains_all_categories_and_candidate_scores(rrf_report) -> None:
    categories = [metrics.category for metrics in rrf_report.hybrid_rrf.category_breakdown]
    assert categories == list(EvaluationCategory)
    assert all(metrics.case_count > 0 for metrics in rrf_report.hybrid_rrf.category_breakdown)
    assert rrf_report.hybrid_rrf.total_candidates > 0
    assert rrf_report.hybrid_rrf.average_candidates <= 10
    assert any(
        score is not None
        for result in rrf_report.hybrid_rrf.case_results
        for score in result.rank_scores
    )


def test_rrf_workspace_isolation_and_injection_metrics_remain_safe(rrf_report) -> None:
    for configuration in (
        rrf_report.lexical,
        rrf_report.semantic,
        rrf_report.hybrid_rrf,
    ):
        assert configuration.workspace_leakage_count == 0
        assert configuration.workspace_leakage_rate == 0.0
        assert configuration.injection_content_inert_rate == 1.0
        assert configuration.injection_candidates_surfaced == 10


def test_rrf_conflicting_stale_visibility_remains_complete(rrf_report) -> None:
    metrics = next(
        metrics
        for metrics in rrf_report.hybrid_rrf.category_breakdown
        if metrics.category is EvaluationCategory.CONFLICTING_STALE
    )
    assert metrics.conflict_stale_complete_rate == 1.0
    assert metrics.evidence_state_match_rate == 1.0


def test_workspace_fixture_constants_remain_distinct() -> None:
    assert MAIN_WORKSPACE_ID != OTHER_WORKSPACE_ID
