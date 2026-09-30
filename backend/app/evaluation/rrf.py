"""Evaluation-only lexical, semantic, and RRF comparison primitives."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.evaluation.retrieval import (
    EvaluationCase,
    EvaluationCategory,
    evaluate_cases,
    ndcg_at_k,
    support_recall_at_5,
)
from app.evaluation.semantic import (
    SemanticBenchmarkConfig,
    SemanticCaseResult,
    SemanticRetriever,
    _conflict_stale_complete,
    _evidence_state_match,
    aggregate_injection_content_inert_rate,
    count_unauthorized_candidates_surfaced,
    evaluate_semantic_cases,
    load_local_semantic_model,
    measure_injection_content_inert,
)

DEFAULT_RRF_K = 60
DEFAULT_LEXICAL_TOP_K = 10
DEFAULT_SEMANTIC_TOP_K = 10
DEFAULT_FINAL_TOP_K = 10


@dataclass(frozen=True, slots=True)
class RRFConfig:
    """Fixed evaluation-only RRF configuration."""

    lexical_top_k: int = DEFAULT_LEXICAL_TOP_K
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K
    rrf_k: int = DEFAULT_RRF_K
    final_top_k: int = DEFAULT_FINAL_TOP_K

    def __post_init__(self) -> None:
        if self.lexical_top_k < 1 or self.semantic_top_k < 1 or self.final_top_k < 1:
            raise ValueError("RRF candidate limits must be positive")
        if self.rrf_k < 1:
            raise ValueError("RRF k must be positive")


@dataclass(frozen=True, slots=True)
class RRFCandidateScore:
    """One deterministically ranked candidate and its component ranks."""

    candidate_id: uuid.UUID
    rrf_score: float
    lexical_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True, slots=True)
class RankedCaseMetrics:
    """Metrics and observed ranking data for one configuration and case."""

    case_id: str
    category: EvaluationCategory
    ranked_candidate_ids: tuple[uuid.UUID, ...]
    rank_scores: tuple[float | None, ...]
    retrieved_relevance_grades: tuple[int, ...]
    evidence_state_match: int
    conflict_stale_complete: int
    injection_content_inert: int | None
    injection_candidates_surfaced: int
    unauthorized_candidates_surfaced: int


@dataclass(frozen=True, slots=True)
class RRFCategoryMetrics:
    """Metrics for one category under one retrieval configuration."""

    category: EvaluationCategory
    case_count: int
    recall_at_5: float
    ndcg_at_5: float
    ndcg_at_10: float
    evidence_state_match_rate: float
    conflict_stale_complete_rate: float | None
    injection_content_inert_rate: float | None
    injection_candidates_surfaced: int
    workspace_leakage_rate: float


@dataclass(frozen=True, slots=True)
class ConfigurationMetrics:
    """Aggregate and category metrics for lexical, semantic, or hybrid retrieval."""

    name: str
    case_count: int
    macro_recall_at_5: float
    macro_ndcg_at_5: float
    macro_ndcg_at_10: float
    evidence_state_match_rate: float
    conflict_stale_complete_rate: float | None
    injection_content_inert_rate: float | None
    injection_candidates_surfaced: int
    workspace_leakage_count: int
    workspace_leakage_rate: float
    total_candidates: int
    average_candidates: float
    runtime_seconds: float
    category_breakdown: tuple[RRFCategoryMetrics, ...]
    case_results: tuple[RankedCaseMetrics, ...]


@dataclass(frozen=True, slots=True)
class RRFComparisonReport:
    """Complete evaluation-only three-way retrieval comparison."""

    corpus_case_count: int
    lexical_top_k: int
    semantic_top_k: int
    rrf_k: int
    final_top_k: int
    lexical: ConfigurationMetrics
    semantic: ConfigurationMetrics
    hybrid_rrf: ConfigurationMetrics
    total_runtime_seconds: float
    semantic_model_load_seconds: float
    semantic_query_embedding_seconds: float
    semantic_document_embedding_seconds: float
    semantic_ranking_seconds: float
    rrf_fusion_seconds: float


def reciprocal_rank_score(
    lexical_rank: int | None,
    semantic_rank: int | None,
    *,
    rrf_k: int = DEFAULT_RRF_K,
) -> float:
    """Calculate the RRF score using one-based component ranks."""

    if rrf_k < 1:
        raise ValueError("RRF k must be positive")
    if lexical_rank is None and semantic_rank is None:
        raise ValueError("At least one component rank is required")
    if lexical_rank is not None and lexical_rank < 1:
        raise ValueError("Lexical rank must be one-based")
    if semantic_rank is not None and semantic_rank < 1:
        raise ValueError("Semantic rank must be one-based")

    score = 0.0
    if lexical_rank is not None:
        score += 1.0 / (rrf_k + lexical_rank)
    if semantic_rank is not None:
        score += 1.0 / (rrf_k + semantic_rank)
    return score


def _rank_map(candidate_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    ranks: dict[uuid.UUID, int] = {}
    for rank, candidate_id in enumerate(candidate_ids, start=1):
        if candidate_id in ranks:
            raise ValueError(f"Duplicate candidate ID in ranking: {candidate_id}")
        ranks[candidate_id] = rank
    return ranks


def fuse_ranked_candidate_ids(
    lexical_candidate_ids: Sequence[uuid.UUID],
    semantic_candidate_ids: Sequence[uuid.UUID],
    *,
    config: RRFConfig | None = None,
    authorized_candidate_ids: frozenset[uuid.UUID] | None = None,
) -> tuple[RRFCandidateScore, ...]:
    """Fuse two bounded ranked lists without using text content as identity.

    Candidate IDs are filtered to the already authorized set before fusion when
    one is supplied. Ordering is score descending, lexical rank quality, semantic
    rank quality, and stable candidate ID. Lower component rank is better quality.
    """

    effective_config = config or RRFConfig()
    lexical_ranks = _rank_map(lexical_candidate_ids[: effective_config.lexical_top_k])
    semantic_ranks = _rank_map(semantic_candidate_ids[: effective_config.semantic_top_k])
    candidate_ids = set(lexical_ranks) | set(semantic_ranks)
    if authorized_candidate_ids is not None:
        candidate_ids.intersection_update(authorized_candidate_ids)

    scores = tuple(
        RRFCandidateScore(
            candidate_id=candidate_id,
            rrf_score=reciprocal_rank_score(
                lexical_ranks.get(candidate_id),
                semantic_ranks.get(candidate_id),
                rrf_k=effective_config.rrf_k,
            ),
            lexical_rank=lexical_ranks.get(candidate_id),
            semantic_rank=semantic_ranks.get(candidate_id),
        )
        for candidate_id in candidate_ids
    )

    def sort_key(score: RRFCandidateScore) -> tuple[float, bool, int, bool, int, str]:
        return (
            -score.rrf_score,
            score.lexical_rank is None,
            score.lexical_rank
            if score.lexical_rank is not None
            else effective_config.lexical_top_k + 1,
            score.semantic_rank is None,
            score.semantic_rank
            if score.semantic_rank is not None
            else effective_config.semantic_top_k + 1,
            str(score.candidate_id),
        )

    return tuple(sorted(scores, key=sort_key)[: effective_config.final_top_k])


def _case_metrics(
    case: EvaluationCase,
    ranked_candidate_ids: Sequence[uuid.UUID],
    rank_scores: Sequence[float | None],
) -> RankedCaseMetrics:
    grades = case.gold_relevance_by_candidate
    ranked_ids = tuple(ranked_candidate_ids)
    ranked_items = tuple(
        item
        for candidate_id in ranked_ids
        for item in case.candidates
        if item.candidate_id == candidate_id
    )
    injection_content_inert = (
        measure_injection_content_inert(
            ranked_items,
            observed_content_by_candidate={
                item.candidate_id: item.candidate.content for item in case.candidates
            },
        )
        if case.category is EvaluationCategory.MALICIOUS_INJECTED
        else None
    )
    conflict_stale_complete = _conflict_stale_complete(case, ranked_ids)
    return RankedCaseMetrics(
        case_id=case.case_id,
        category=case.category,
        ranked_candidate_ids=ranked_ids,
        rank_scores=tuple(rank_scores),
        retrieved_relevance_grades=tuple(
            grades.get(candidate_id, 0) for candidate_id in ranked_ids
        ),
        evidence_state_match=_evidence_state_match(
            case,
            ranked_ids,
            conflict_stale_complete=conflict_stale_complete,
            injection_content_inert=injection_content_inert,
        ),
        conflict_stale_complete=conflict_stale_complete,
        injection_content_inert=injection_content_inert,
        injection_candidates_surfaced=sum(item.injection_like for item in ranked_items),
        unauthorized_candidates_surfaced=count_unauthorized_candidates_surfaced(
            ranked_ids,
            case.candidates,
            case.workspace_id,
        ),
    )


def _category_metrics(
    category: EvaluationCategory,
    cases_by_id: Mapping[str, EvaluationCase],
    results: Sequence[RankedCaseMetrics],
) -> RRFCategoryMetrics:
    category_results = tuple(result for result in results if result.category is category)
    inert_measurements = tuple(result.injection_content_inert for result in category_results)
    return RRFCategoryMetrics(
        category=category,
        case_count=len(category_results),
        recall_at_5=sum(
            support_recall_at_5(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
            )
            for result in category_results
        )
        / len(category_results),
        ndcg_at_5=sum(
            ndcg_at_k(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
                k=5,
            )
            for result in category_results
        )
        / len(category_results),
        ndcg_at_10=sum(
            ndcg_at_k(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
                k=10,
            )
            for result in category_results
        )
        / len(category_results),
        evidence_state_match_rate=sum(result.evidence_state_match for result in category_results)
        / len(category_results),
        conflict_stale_complete_rate=(
            sum(result.conflict_stale_complete for result in category_results)
            / len(category_results)
            if category is EvaluationCategory.CONFLICTING_STALE
            else None
        ),
        injection_content_inert_rate=(
            aggregate_injection_content_inert_rate(inert_measurements)
            if category is EvaluationCategory.MALICIOUS_INJECTED
            else None
        ),
        injection_candidates_surfaced=sum(
            result.injection_candidates_surfaced for result in category_results
        ),
        workspace_leakage_rate=sum(
            int(result.unauthorized_candidates_surfaced > 0) for result in category_results
        )
        / len(category_results),
    )


def _configuration_metrics(
    name: str,
    cases: Sequence[EvaluationCase],
    ranked_results: Sequence[RankedCaseMetrics],
    runtime_seconds: float,
) -> ConfigurationMetrics:
    cases_by_id = {case.case_id: case for case in cases}
    category_breakdown = tuple(
        _category_metrics(category, cases_by_id, ranked_results)
        for category in EvaluationCategory
        if any(result.category is category for result in ranked_results)
    )
    conflict_results = tuple(
        result
        for result in ranked_results
        if result.category is EvaluationCategory.CONFLICTING_STALE
    )
    inert_measurements = tuple(result.injection_content_inert for result in ranked_results)
    return ConfigurationMetrics(
        name=name,
        case_count=len(ranked_results),
        macro_recall_at_5=sum(
            support_recall_at_5(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
            )
            for result in ranked_results
        )
        / len(ranked_results),
        macro_ndcg_at_5=sum(
            ndcg_at_k(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
                k=5,
            )
            for result in ranked_results
        )
        / len(ranked_results),
        macro_ndcg_at_10=sum(
            ndcg_at_k(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
                k=10,
            )
            for result in ranked_results
        )
        / len(ranked_results),
        evidence_state_match_rate=sum(result.evidence_state_match for result in ranked_results)
        / len(ranked_results),
        conflict_stale_complete_rate=(
            sum(result.conflict_stale_complete for result in conflict_results)
            / len(conflict_results)
            if conflict_results
            else None
        ),
        injection_content_inert_rate=aggregate_injection_content_inert_rate(inert_measurements),
        injection_candidates_surfaced=sum(
            result.injection_candidates_surfaced for result in ranked_results
        ),
        workspace_leakage_count=sum(
            result.unauthorized_candidates_surfaced for result in ranked_results
        ),
        workspace_leakage_rate=sum(
            int(result.unauthorized_candidates_surfaced > 0) for result in ranked_results
        )
        / len(ranked_results),
        total_candidates=sum(len(result.ranked_candidate_ids) for result in ranked_results),
        average_candidates=sum(len(result.ranked_candidate_ids) for result in ranked_results)
        / len(ranked_results),
        runtime_seconds=runtime_seconds,
        category_breakdown=category_breakdown,
        case_results=tuple(ranked_results),
    )


def _semantic_ranked_results(
    semantic_results: Sequence[SemanticCaseResult],
) -> tuple[RankedCaseMetrics, ...]:
    return tuple(
        RankedCaseMetrics(
            case_id=result.case_id,
            category=result.category,
            ranked_candidate_ids=result.ranked_candidate_ids,
            rank_scores=tuple(result.similarity_scores),
            retrieved_relevance_grades=result.retrieved_relevance_grades,
            evidence_state_match=result.evidence_state_match,
            conflict_stale_complete=result.conflict_stale_complete,
            injection_content_inert=result.injection_content_inert,
            injection_candidates_surfaced=result.injection_candidates_surfaced,
            unauthorized_candidates_surfaced=result.unauthorized_candidates_surfaced,
        )
        for result in semantic_results
    )


def run_rrf_comparison(
    cases: Sequence[EvaluationCase],
    *,
    rrf_config: RRFConfig | None = None,
    semantic_config: SemanticBenchmarkConfig | None = None,
) -> RRFComparisonReport:
    """Run lexical-only, semantic-only, and evaluation-only hybrid RRF."""

    if not cases:
        raise ValueError("At least one evaluation case is required")
    config = rrf_config or RRFConfig()
    total_started = time.perf_counter()

    lexical_started = time.perf_counter()
    lexical_report = evaluate_cases(cases, result_limit=config.lexical_top_k)
    lexical_runtime = time.perf_counter() - lexical_started
    lexical_results = tuple(
        _case_metrics(
            case, result.retrieved_candidate_ids, (None,) * len(result.retrieved_candidate_ids)
        )
        for case, result in zip(cases, lexical_report.case_results, strict=True)
    )

    effective_semantic_config = semantic_config or SemanticBenchmarkConfig(
        top_k=config.semantic_top_k
    )
    semantic_started = time.perf_counter()
    model, model_load_seconds = load_local_semantic_model(effective_semantic_config)
    semantic_retriever = SemanticRetriever(
        model,
        effective_semantic_config,
        model_load_seconds=model_load_seconds,
    )
    semantic_report = evaluate_semantic_cases(cases, semantic_retriever)
    semantic_runtime = time.perf_counter() - semantic_started
    semantic_results = _semantic_ranked_results(semantic_report.case_results)

    rrf_started = time.perf_counter()
    hybrid_results: list[RankedCaseMetrics] = []
    for case, lexical_result, semantic_result in zip(
        cases,
        lexical_results,
        semantic_results,
        strict=True,
    ):
        authorized_ids = frozenset(
            item.candidate_id for item in case.candidates if item.workspace_id == case.workspace_id
        )
        fused = fuse_ranked_candidate_ids(
            lexical_result.ranked_candidate_ids,
            semantic_result.ranked_candidate_ids,
            config=config,
            authorized_candidate_ids=authorized_ids,
        )
        hybrid_results.append(
            _case_metrics(
                case,
                tuple(item.candidate_id for item in fused),
                tuple(item.rrf_score for item in fused),
            )
        )
    rrf_runtime = time.perf_counter() - rrf_started

    return RRFComparisonReport(
        corpus_case_count=len(cases),
        lexical_top_k=config.lexical_top_k,
        semantic_top_k=config.semantic_top_k,
        rrf_k=config.rrf_k,
        final_top_k=config.final_top_k,
        lexical=_configuration_metrics("lexical-only", cases, lexical_results, lexical_runtime),
        semantic=_configuration_metrics("semantic-only", cases, semantic_results, semantic_runtime),
        hybrid_rrf=_configuration_metrics(
            "hybrid-rrf",
            cases,
            tuple(hybrid_results),
            rrf_runtime,
        ),
        total_runtime_seconds=time.perf_counter() - total_started,
        semantic_model_load_seconds=semantic_report.model_load_seconds,
        semantic_query_embedding_seconds=semantic_report.query_embedding_seconds,
        semantic_document_embedding_seconds=semantic_report.document_embedding_seconds,
        semantic_ranking_seconds=semantic_report.ranking_seconds,
        rrf_fusion_seconds=rrf_runtime,
    )


def _configuration_to_dict(metrics: ConfigurationMetrics) -> dict[str, object]:
    return {
        "name": metrics.name,
        "case_count": metrics.case_count,
        "macro_recall_at_5": metrics.macro_recall_at_5,
        "macro_ndcg_at_5": metrics.macro_ndcg_at_5,
        "macro_ndcg_at_10": metrics.macro_ndcg_at_10,
        "evidence_state_match_rate": metrics.evidence_state_match_rate,
        "conflict_stale_complete_rate": metrics.conflict_stale_complete_rate,
        "injection_content_inert_rate": metrics.injection_content_inert_rate,
        "injection_candidates_surfaced": metrics.injection_candidates_surfaced,
        "workspace_leakage_count": metrics.workspace_leakage_count,
        "workspace_leakage_rate": metrics.workspace_leakage_rate,
        "total_candidates": metrics.total_candidates,
        "average_candidates": metrics.average_candidates,
        "runtime_seconds": metrics.runtime_seconds,
        "category_breakdown": [
            {
                "category": category.category.value,
                "case_count": category.case_count,
                "recall_at_5": category.recall_at_5,
                "ndcg_at_5": category.ndcg_at_5,
                "ndcg_at_10": category.ndcg_at_10,
                "evidence_state_match_rate": category.evidence_state_match_rate,
                "conflict_stale_complete_rate": category.conflict_stale_complete_rate,
                "injection_content_inert_rate": category.injection_content_inert_rate,
                "injection_candidates_surfaced": category.injection_candidates_surfaced,
                "workspace_leakage_rate": category.workspace_leakage_rate,
            }
            for category in metrics.category_breakdown
        ],
        "case_results": [
            {
                "case_id": result.case_id,
                "category": result.category.value,
                "ranked_candidate_ids": [
                    str(candidate_id) for candidate_id in result.ranked_candidate_ids
                ],
                "rank_scores": list(result.rank_scores),
                "retrieved_relevance_grades": list(result.retrieved_relevance_grades),
                "evidence_state_match": result.evidence_state_match,
                "conflict_stale_complete": result.conflict_stale_complete,
                "injection_content_inert": result.injection_content_inert,
                "injection_candidates_surfaced": result.injection_candidates_surfaced,
                "unauthorized_candidates_surfaced": result.unauthorized_candidates_surfaced,
            }
            for result in metrics.case_results
        ],
    }


def report_to_dict(report: RRFComparisonReport) -> dict[str, object]:
    return {
        "corpus_case_count": report.corpus_case_count,
        "lexical_top_k": report.lexical_top_k,
        "semantic_top_k": report.semantic_top_k,
        "rrf_k": report.rrf_k,
        "final_top_k": report.final_top_k,
        "runtime_seconds": {
            "total": report.total_runtime_seconds,
            "semantic_model_load": report.semantic_model_load_seconds,
            "semantic_query_embedding": report.semantic_query_embedding_seconds,
            "semantic_document_embedding": report.semantic_document_embedding_seconds,
            "semantic_ranking": report.semantic_ranking_seconds,
            "rrf_fusion": report.rrf_fusion_seconds,
        },
        "lexical": _configuration_to_dict(report.lexical),
        "semantic": _configuration_to_dict(report.semantic),
        "hybrid_rrf": _configuration_to_dict(report.hybrid_rrf),
    }


def format_report(report: RRFComparisonReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False)


__all__ = [
    "DEFAULT_FINAL_TOP_K",
    "DEFAULT_LEXICAL_TOP_K",
    "DEFAULT_RRF_K",
    "DEFAULT_SEMANTIC_TOP_K",
    "ConfigurationMetrics",
    "RRFCandidateScore",
    "RRFComparisonReport",
    "RRFConfig",
    "RankedCaseMetrics",
    "fuse_ranked_candidate_ids",
    "format_report",
    "reciprocal_rank_score",
    "report_to_dict",
    "run_rrf_comparison",
]
