"""Evaluation-only tests for the real local semantic benchmark."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.evaluation.retrieval import EvaluationCategory, evaluate_cases
from app.evaluation.semantic import (
    DEFAULT_MODEL_ID,
    SemanticBenchmarkConfig,
    SemanticRetriever,
    aggregate_injection_content_inert_rate,
    count_unauthorized_candidates_surfaced,
    evaluate_semantic_cases,
    load_local_semantic_model,
    measure_injection_content_inert,
    prepare_embedding_window,
)
from tests.evaluation.retrieval_cases import (
    MAIN_WORKSPACE_ID,
    OTHER_WORKSPACE_ID,
    load_evaluation_cases,
)


class _WordPieceTestTokenizer:
    """Small tokenizer double for testing explicit window mechanics only."""

    def encode(self, text: str, **_: object) -> list[int]:
        return list(range(len(text.split())))

    def decode(self, token_ids: list[int], **_: object) -> str:
        return " ".join(f"token-{token_id}" for token_id in token_ids)


@pytest.fixture(scope="module")
def semantic_retriever() -> SemanticRetriever:
    config = SemanticBenchmarkConfig()
    try:
        model, load_seconds = load_local_semantic_model(config)
    except RuntimeError as exc:
        pytest.skip(f"Real semantic model unavailable: {exc}")
    return SemanticRetriever(model, config, model_load_seconds=load_seconds)


@pytest.fixture(scope="module")
def semantic_report(semantic_retriever: SemanticRetriever):
    return evaluate_semantic_cases(load_evaluation_cases(), semantic_retriever)


def test_fixed_corpus_remains_unchanged() -> None:
    cases = load_evaluation_cases()

    assert len(cases) == 60
    assert {case.category for case in cases} == set(EvaluationCategory)
    category_counts = {
        category: sum(item.category == category for item in cases)
        for category in EvaluationCategory
    }
    assert category_counts == {
        EvaluationCategory.SUPPORTED: 20,
        EvaluationCategory.AMBIGUOUS: 10,
        EvaluationCategory.INSUFFICIENT_EVIDENCE: 10,
        EvaluationCategory.CONFLICTING_STALE: 10,
        EvaluationCategory.MALICIOUS_INJECTED: 10,
    }


def test_explicit_embedding_window_is_deterministic() -> None:
    tokenizer = _WordPieceTestTokenizer()
    text = " ".join(f"word-{index}" for index in range(300))

    first = prepare_embedding_window(text, tokenizer, max_word_pieces=250)
    second = prepare_embedding_window(text, tokenizer, max_word_pieces=250)

    assert first == second
    assert first.was_windowed is True
    assert first.original_word_pieces == 300
    assert first.used_word_pieces == 250
    assert len(first.text.split()) == 250


def test_injection_content_inertness_requires_observed_injected_content() -> None:
    malicious_case = next(
        case
        for case in load_evaluation_cases()
        if case.category is EvaluationCategory.MALICIOUS_INJECTED
    )
    injected_candidates = tuple(item for item in malicious_case.candidates if item.injection_like)
    non_injected_candidates = tuple(
        item for item in malicious_case.candidates if not item.injection_like
    )

    assert injected_candidates
    assert measure_injection_content_inert(injected_candidates) == 1
    assert measure_injection_content_inert(non_injected_candidates) is None
    assert (
        measure_injection_content_inert(
            injected_candidates,
            observed_content_by_candidate={
                item.candidate_id: "mutated after evaluation" for item in injected_candidates
            },
        )
        == 0
    )
    assert (
        measure_injection_content_inert(
            injected_candidates,
            instruction_effect_observed=True,
        )
        == 0
    )


def test_injection_content_inertness_aggregate_ignores_none() -> None:
    assert aggregate_injection_content_inert_rate((None, 1, None, 0)) == 0.5
    assert aggregate_injection_content_inert_rate((None, None)) is None


def test_malicious_case_without_surfaced_injection_reports_none(
    semantic_retriever: SemanticRetriever,
) -> None:
    malicious_case = next(
        case
        for case in load_evaluation_cases()
        if case.category is EvaluationCategory.MALICIOUS_INJECTED
    )
    case_without_injection_labels = replace(
        malicious_case,
        candidates=tuple(replace(item, injection_like=False) for item in malicious_case.candidates),
    )

    result = semantic_retriever.rank_case(case_without_injection_labels)

    assert result.injection_candidates_surfaced == 0
    assert result.injection_content_inert is None


def test_lexical_baseline_remains_unchanged() -> None:
    report = evaluate_cases(load_evaluation_cases())

    assert report.macro_recall_at_5 == 0.8333333333333334
    assert report.macro_ndcg_at_5 == 0.9219567263864729
    assert report.macro_ndcg_at_10 == 0.9219567263864729


def test_real_model_shape_and_asymmetric_encoder_configuration(
    semantic_report,
) -> None:
    assert semantic_report.model == DEFAULT_MODEL_ID
    assert semantic_report.embedding_dimension == 384
    assert semantic_report.query_encoder == "encode_query"
    assert semantic_report.document_encoder == "encode_document"
    assert semantic_report.similarity == "cosine"
    assert semantic_report.top_k == 10


def test_semantic_ranking_is_deterministic_and_bounded(
    semantic_retriever: SemanticRetriever,
) -> None:
    case = load_evaluation_cases()[0]

    first = semantic_retriever.rank_case(case)
    second = semantic_retriever.rank_case(case)

    assert len(first.ranked_candidate_ids) <= 10
    assert first.ranked_candidate_ids == second.ranked_candidate_ids
    assert first.similarity_scores == second.similarity_scores
    assert len(first.similarity_scores) == len(first.ranked_candidate_ids)
    assert all(-1.0 <= score <= 1.0 for score in first.similarity_scores)


def test_semantic_workspace_isolation(
    semantic_retriever: SemanticRetriever,
) -> None:
    case = load_evaluation_cases()[6]
    unauthorized_ids = {
        item.candidate_id for item in case.candidates if item.workspace_id == OTHER_WORKSPACE_ID
    }

    result = semantic_retriever.rank_case(case)

    assert case.workspace_id == MAIN_WORKSPACE_ID
    assert unauthorized_ids
    assert not unauthorized_ids.intersection(result.ranked_candidate_ids)
    assert result.unauthorized_candidates_surfaced == 0
    assert all(metadata.workspace_id == MAIN_WORKSPACE_ID for metadata in result.ranked_metadata)


def test_workspace_leakage_count_comes_from_observed_ranked_ids(
    semantic_retriever: SemanticRetriever,
) -> None:
    case = load_evaluation_cases()[6]
    unauthorized_item = next(
        item for item in case.candidates if item.workspace_id == OTHER_WORKSPACE_ID
    )
    unauthorized_id = uuid.uuid5(uuid.NAMESPACE_URL, "semantic-unauthorized-regression")
    unauthorized_candidate = replace(
        unauthorized_item.candidate,
        chunk_id=unauthorized_id,
        content=case.question,
        content_hash="f" * 64,
    )
    case_with_distractor = replace(
        case,
        candidates=case.candidates
        + (replace(unauthorized_item, candidate=unauthorized_candidate),),
    )

    result = semantic_retriever.rank_case(case_with_distractor)

    assert unauthorized_id not in result.ranked_candidate_ids
    assert result.unauthorized_candidates_surfaced == 0
    assert (
        count_unauthorized_candidates_surfaced(
            (unauthorized_id,),
            case_with_distractor.candidates,
            case.workspace_id,
        )
        == 1
    )
    assert (
        count_unauthorized_candidates_surfaced(
            result.ranked_candidate_ids,
            case_with_distractor.candidates,
            case.workspace_id,
        )
        == result.unauthorized_candidates_surfaced
    )


def test_semantic_long_input_window_is_recorded(
    semantic_retriever: SemanticRetriever,
) -> None:
    text = "long evidence word " * 300
    window = prepare_embedding_window(
        text,
        semantic_retriever.tokenizer,
        max_word_pieces=semantic_retriever.config.max_input_word_pieces,
    )

    assert window.was_windowed is True
    assert window.original_word_pieces > window.used_word_pieces
    assert window.used_word_pieces == semantic_retriever.config.max_input_word_pieces


def test_semantic_report_contains_all_categories_and_runtime(
    semantic_report,
) -> None:
    assert semantic_report.case_count == 60
    assert semantic_report.macro_recall_at_5 >= 0.0
    assert semantic_report.macro_ndcg_at_5 >= 0.0
    assert semantic_report.macro_ndcg_at_10 >= 0.0
    assert semantic_report.workspace_leakage_count == 0
    assert semantic_report.workspace_leakage_rate == 0.0
    assert semantic_report.injection_content_inert_rate == 1.0
    assert semantic_report.injection_candidates_surfaced >= 0
    assert semantic_report.model_load_seconds >= 0.0
    assert semantic_report.query_embedding_seconds >= 0.0
    assert semantic_report.document_embedding_seconds >= 0.0
    assert semantic_report.ranking_seconds >= 0.0
    assert [metrics.category for metrics in semantic_report.category_breakdown] == list(
        EvaluationCategory
    )
    assert all(metrics.case_count > 0 for metrics in semantic_report.category_breakdown)


def test_malicious_evidence_is_not_mutated(
    semantic_retriever: SemanticRetriever,
) -> None:
    case = next(
        case
        for case in load_evaluation_cases()
        if case.category is EvaluationCategory.MALICIOUS_INJECTED
    )
    before = tuple(item.candidate.content for item in case.candidates)

    result = semantic_retriever.rank_case(case)

    after = tuple(item.candidate.content for item in case.candidates)
    assert before == after
    assert result.injection_content_inert == 1


def test_metric_category_breakdown_is_reproducible(semantic_report) -> None:
    category_values = tuple(
        (
            metrics.category,
            metrics.recall_at_5,
            metrics.ndcg_at_5,
            metrics.ndcg_at_10,
            metrics.evidence_state_match_rate,
        )
        for metrics in semantic_report.category_breakdown
    )

    assert category_values == tuple(
        (
            metrics.category,
            metrics.recall_at_5,
            metrics.ndcg_at_5,
            metrics.ndcg_at_10,
            metrics.evidence_state_match_rate,
        )
        for metrics in semantic_report.category_breakdown
    )


@pytest.mark.parametrize("case_index", [0, 8, 18, 28, 38, 48])
def test_original_candidate_metadata_is_preserved(
    semantic_retriever: SemanticRetriever,
    case_index: int,
) -> None:
    case = load_evaluation_cases()[case_index]
    result = semantic_retriever.rank_case(case)

    assert len(result.ranked_metadata) == len(result.ranked_candidate_ids)
    assert all(
        metadata.candidate_id == candidate_id
        for metadata, candidate_id in zip(
            result.ranked_metadata,
            result.ranked_candidate_ids,
            strict=True,
        )
    )
