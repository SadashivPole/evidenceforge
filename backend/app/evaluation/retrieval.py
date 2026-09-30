"""Deterministic evaluation primitives for lexical evidence retrieval."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.evidence.search.policy import DEFAULT_SEARCH_POLICY, SearchPolicy
from app.evidence.search.service import search_chunks
from app.evidence.search.types import SearchChunkCandidate

SUPPORT_RELEVANCE_THRESHOLD = 2


class EvaluationCategory(StrEnum):
    """Evaluation categories defined by the retrieval evaluation plan."""

    SUPPORTED = "SUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_STALE = "CONFLICTING_STALE"
    MALICIOUS_INJECTED = "MALICIOUS_INJECTED"


@dataclass(frozen=True, slots=True)
class GoldRelevance:
    """Independent graded relevance judgment for one evidence candidate."""

    candidate_id: uuid.UUID
    relevance: int

    def __post_init__(self) -> None:
        if self.relevance not in {0, 1, 2, 3}:
            raise ValueError("Gold relevance must be one of 0, 1, 2, or 3")


@dataclass(frozen=True, slots=True)
class EvaluationCandidate:
    """One candidate plus the workspace in which it is eligible."""

    workspace_id: uuid.UUID
    candidate: SearchChunkCandidate

    @property
    def candidate_id(self) -> uuid.UUID:
        """Return the repository-native immutable chunk identifier."""

        return self.candidate.chunk_id


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """Immutable reproducible retrieval evaluation input."""

    case_id: str
    category: EvaluationCategory
    workspace_id: uuid.UUID
    question: str
    candidates: tuple[EvaluationCandidate, ...]
    gold_relevance: tuple[GoldRelevance, ...]

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("Evaluation case ID cannot be empty")
        if not self.question:
            raise ValueError("Evaluation question cannot be empty")

        candidate_ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Evaluation candidates must have unique IDs")

        gold_ids = tuple(label.candidate_id for label in self.gold_relevance)
        if len(set(gold_ids)) != len(gold_ids):
            raise ValueError("Gold relevance labels must have unique candidate IDs")
        if not set(gold_ids).issubset(set(candidate_ids)):
            raise ValueError("Gold relevance labels must reference evaluation candidates")

    @property
    def gold_relevance_by_candidate(self) -> Mapping[uuid.UUID, int]:
        """Return immutable gold labels keyed by candidate ID."""

        return MappingProxyType(
            {label.candidate_id: label.relevance for label in self.gold_relevance}
        )

    @property
    def eligible_candidates(self) -> tuple[SearchChunkCandidate, ...]:
        """Return only candidates belonging to this case's authorized workspace."""

        return tuple(
            item.candidate for item in self.candidates if item.workspace_id == self.workspace_id
        )


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """Measured retrieval metrics for one evaluation case."""

    case_id: str
    category: EvaluationCategory
    retrieved_candidate_ids: tuple[uuid.UUID, ...]
    retrieved_relevance_grades: tuple[int, ...]
    recall_at_5: int
    ndcg_at_5: float
    ndcg_at_10: float


@dataclass(frozen=True, slots=True)
class CategoryMetrics:
    """Macro metrics for one evaluation category."""

    category: EvaluationCategory
    case_count: int
    macro_recall_at_5: float
    macro_ndcg_at_5: float
    macro_ndcg_at_10: float


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Complete deterministic retrieval evaluation result."""

    case_results: tuple[EvaluationCaseResult, ...]
    case_count: int
    macro_recall_at_5: float
    macro_ndcg_at_5: float
    macro_ndcg_at_10: float
    category_breakdown: tuple[CategoryMetrics, ...]


def support_recall_at_5(
    retrieved_candidate_ids: Sequence[uuid.UUID],
    gold_relevance: Mapping[uuid.UUID, int],
) -> int:
    """Return 1 when supporting evidence appears in the first five results.

    Gold relevance grades 3 and 2 count as supporting evidence. Grades 1 and 0
    remain visible to nDCG but do not count as a Recall@5 support hit.
    """

    return int(
        any(
            gold_relevance.get(candidate_id, 0) >= SUPPORT_RELEVANCE_THRESHOLD
            for candidate_id in retrieved_candidate_ids[:5]
        )
    )


def ndcg_at_k(
    retrieved_candidate_ids: Sequence[uuid.UUID],
    gold_relevance: Mapping[uuid.UUID, int],
    *,
    k: int,
) -> float:
    """Compute standard graded nDCG at k, returning zero for zero ideal gain."""

    if k < 1:
        raise ValueError("nDCG cutoff must be positive")

    retrieved_grades = [
        gold_relevance.get(candidate_id, 0) for candidate_id in retrieved_candidate_ids[:k]
    ]
    ideal_grades = sorted(gold_relevance.values(), reverse=True)[:k]
    ideal_gain = _dcg(ideal_grades)

    if ideal_gain == 0:
        return 0.0

    return _dcg(retrieved_grades) / ideal_gain


def evaluate_case(
    case: EvaluationCase,
    *,
    policy: SearchPolicy = DEFAULT_SEARCH_POLICY,
    result_limit: int = 10,
) -> EvaluationCaseResult:
    """Run the existing lexical retriever against one isolated evaluation case."""

    results = search_chunks(
        case.eligible_candidates,
        case.question,
        policy=policy,
        limit=result_limit,
    )
    retrieved_candidate_ids = tuple(result.candidate.chunk_id for result in results)
    gold_relevance = case.gold_relevance_by_candidate
    retrieved_relevance_grades = tuple(
        gold_relevance.get(candidate_id, 0) for candidate_id in retrieved_candidate_ids
    )

    return EvaluationCaseResult(
        case_id=case.case_id,
        category=case.category,
        retrieved_candidate_ids=retrieved_candidate_ids,
        retrieved_relevance_grades=retrieved_relevance_grades,
        recall_at_5=support_recall_at_5(retrieved_candidate_ids, gold_relevance),
        ndcg_at_5=ndcg_at_k(retrieved_candidate_ids, gold_relevance, k=5),
        ndcg_at_10=ndcg_at_k(retrieved_candidate_ids, gold_relevance, k=10),
    )


def evaluate_cases(
    cases: Sequence[EvaluationCase],
    *,
    policy: SearchPolicy = DEFAULT_SEARCH_POLICY,
    result_limit: int = 10,
) -> EvaluationReport:
    """Evaluate cases in input order and aggregate deterministic macro metrics."""

    case_ids = tuple(case.case_id for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Evaluation case IDs must be unique")
    if not cases:
        raise ValueError("At least one evaluation case is required")

    case_results = tuple(
        evaluate_case(case, policy=policy, result_limit=result_limit) for case in cases
    )
    category_breakdown = tuple(
        _category_metrics(category, case_results)
        for category in EvaluationCategory
        if any(result.category == category for result in case_results)
    )

    return EvaluationReport(
        case_results=case_results,
        case_count=len(case_results),
        macro_recall_at_5=_mean(result.recall_at_5 for result in case_results),
        macro_ndcg_at_5=_mean(result.ndcg_at_5 for result in case_results),
        macro_ndcg_at_10=_mean(result.ndcg_at_10 for result in case_results),
        category_breakdown=category_breakdown,
    )


def report_to_dict(report: EvaluationReport) -> dict[str, object]:
    """Convert a report to stable JSON-compatible output data."""

    return {
        "case_results": [
            {
                "case_id": result.case_id,
                "category": result.category.value,
                "retrieved_candidate_ids": [
                    str(candidate_id) for candidate_id in result.retrieved_candidate_ids
                ],
                "retrieved_relevance_grades": list(result.retrieved_relevance_grades),
                "recall_at_5": result.recall_at_5,
                "ndcg_at_5": result.ndcg_at_5,
                "ndcg_at_10": result.ndcg_at_10,
            }
            for result in report.case_results
        ],
        "case_count": report.case_count,
        "macro_recall_at_5": report.macro_recall_at_5,
        "macro_ndcg_at_5": report.macro_ndcg_at_5,
        "macro_ndcg_at_10": report.macro_ndcg_at_10,
        "category_breakdown": [
            {
                "category": metrics.category.value,
                "case_count": metrics.case_count,
                "macro_recall_at_5": metrics.macro_recall_at_5,
                "macro_ndcg_at_5": metrics.macro_ndcg_at_5,
                "macro_ndcg_at_10": metrics.macro_ndcg_at_10,
            }
            for metrics in report.category_breakdown
        ],
    }


def format_report(report: EvaluationReport) -> str:
    """Render evaluation output without changing metric calculations."""

    return json.dumps(report_to_dict(report), indent=2, sort_keys=False)


def _dcg(grades: Sequence[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def _mean(values: Iterable[float]) -> float:
    numeric_values = tuple(values)
    if not numeric_values:
        return 0.0
    return sum(numeric_values) / len(numeric_values)


def _category_metrics(
    category: EvaluationCategory,
    results: Sequence[EvaluationCaseResult],
) -> CategoryMetrics:
    category_results = tuple(result for result in results if result.category == category)
    return CategoryMetrics(
        category=category,
        case_count=len(category_results),
        macro_recall_at_5=_mean(result.recall_at_5 for result in category_results),
        macro_ndcg_at_5=_mean(result.ndcg_at_5 for result in category_results),
        macro_ndcg_at_10=_mean(result.ndcg_at_10 for result in category_results),
    )


__all__ = [
    "CategoryMetrics",
    "EvaluationCandidate",
    "EvaluationCase",
    "EvaluationCaseResult",
    "EvaluationCategory",
    "EvaluationReport",
    "GoldRelevance",
    "evaluate_case",
    "evaluate_cases",
    "format_report",
    "ndcg_at_k",
    "report_to_dict",
    "support_recall_at_5",
]
