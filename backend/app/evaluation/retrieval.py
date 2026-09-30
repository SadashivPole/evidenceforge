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


class FreshnessState(StrEnum):
    """Evaluation-only freshness annotation for evidence candidates."""

    CURRENT = "CURRENT"
    STALE = "STALE"


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
    """One candidate plus deterministic evaluation-only provenance labels."""

    workspace_id: uuid.UUID
    candidate: SearchChunkCandidate
    freshness: FreshnessState = FreshnessState.CURRENT
    conflict_group: str | None = None
    injection_like: bool = False

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
    claim_boundary: str
    gold_rationale: str
    injection_expectation: str | None = None

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
    evidence_state_match: int
    current_evidence_surfaced: bool
    stale_evidence_surfaced: bool
    conflicting_evidence_surfaced: bool
    conflict_stale_complete: int
    injection_candidates_surfaced: int
    injection_content_inert: int | None
    unauthorized_candidates_surfaced: int


@dataclass(frozen=True, slots=True)
class CategoryMetrics:
    """Macro metrics for one evaluation category."""

    category: EvaluationCategory
    case_count: int
    macro_recall_at_5: float
    macro_ndcg_at_5: float
    macro_ndcg_at_10: float
    evidence_state_match_rate: float
    conflict_stale_complete_rate: float | None
    injection_escape_rate: float | None
    injection_content_inert_rate: float | None
    workspace_leakage_rate: float


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Complete deterministic retrieval evaluation result."""

    case_results: tuple[EvaluationCaseResult, ...]
    case_count: int
    macro_recall_at_5: float
    macro_ndcg_at_5: float
    macro_ndcg_at_10: float
    evidence_state_match_rate: float
    correct_abstention_rate: float | None
    conflict_stale_complete_rate: float | None
    injection_escape_rate: float | None
    injection_content_inert_rate: float
    injection_candidates_surfaced: int
    workspace_leakage_count: int
    workspace_leakage_rate: float
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


def _current_stale_conflict_flags(
    case: EvaluationCase,
    retrieved_candidate_ids: Sequence[uuid.UUID],
    gold_relevance: Mapping[uuid.UUID, int],
) -> tuple[bool, bool, bool, int]:
    """Summarize current, stale, and conflicting evidence surfaced by retrieval."""

    candidates_by_id = {item.candidate_id: item for item in case.candidates}
    retrieved_items = tuple(
        candidates_by_id[candidate_id]
        for candidate_id in retrieved_candidate_ids
        if candidate_id in candidates_by_id and gold_relevance.get(candidate_id, 0) > 0
    )
    current_surfaced = any(item.freshness is FreshnessState.CURRENT for item in retrieved_items)
    stale_surfaced = any(item.freshness is FreshnessState.STALE for item in retrieved_items)

    conflict_counts: dict[str, int] = {}
    for item in retrieved_items:
        if item.conflict_group is not None:
            conflict_counts[item.conflict_group] = conflict_counts.get(item.conflict_group, 0) + 1
    conflicting_surfaced = any(count >= 2 for count in conflict_counts.values())
    complete = int(current_surfaced and stale_surfaced and conflicting_surfaced)
    return current_surfaced, stale_surfaced, conflicting_surfaced, complete


def _injection_metrics(
    case: EvaluationCase,
    retrieved_candidate_ids: Sequence[uuid.UUID],
) -> tuple[int, int | None]:
    """Report surfaced injection-like content and its inert evaluator handling.

    This evaluator only ranks evidence and emits IDs/grades. It has no generation,
    tool, policy, authorization, or state-transition layer. The inert field records
    that instruction-like content remains an evidence candidate, not an instruction.
    It is not an end-to-end injection-escape measurement.
    """

    candidates_by_id = {item.candidate_id: item for item in case.candidates}
    surfaced_candidates = tuple(
        candidates_by_id[candidate_id]
        for candidate_id in retrieved_candidate_ids
        if candidate_id in candidates_by_id and candidates_by_id[candidate_id].injection_like
    )
    surfaced = len(surfaced_candidates)
    if case.category is not EvaluationCategory.MALICIOUS_INJECTED:
        return surfaced, None

    content_inert = int(
        all(
            item.injection_like and isinstance(item.candidate.content, str)
            for item in surfaced_candidates
        )
    )
    return surfaced, content_inert


def _evidence_state_match(
    case: EvaluationCase,
    retrieved_candidate_ids: Sequence[uuid.UUID],
    gold_relevance: Mapping[uuid.UUID, int],
    *,
    conflict_stale_complete: int,
    injection_content_inert: int | None,
) -> int:
    """Measure whether retrieval matches the category's evidence-state condition.

    This is a retrieval/evidence-state metric only. It does not measure an answer,
    reviewer decision, or abstention response.

    SUPPORTED cases match when support exists. AMBIGUOUS cases match when no
    direct-support grade is retrieved but related evidence is present.
    INSUFFICIENT_EVIDENCE cases match when no support grade is retrieved.
    CONFLICTING_STALE cases match when current, stale, and conflicting evidence
    are all surfaced. MALICIOUS_INJECTED cases match when the evaluator remains
    inert.
    """

    retrieved_grades = tuple(
        gold_relevance.get(candidate_id, 0) for candidate_id in retrieved_candidate_ids
    )
    has_direct_support = any(grade >= 3 for grade in retrieved_grades)
    has_support = any(grade >= SUPPORT_RELEVANCE_THRESHOLD for grade in retrieved_grades)
    has_related_evidence = any(grade > 0 for grade in retrieved_grades)

    if case.category is EvaluationCategory.SUPPORTED:
        return int(has_support)
    if case.category is EvaluationCategory.AMBIGUOUS:
        return int(not has_direct_support and has_related_evidence)
    if case.category is EvaluationCategory.INSUFFICIENT_EVIDENCE:
        return int(not has_support)
    if case.category is EvaluationCategory.CONFLICTING_STALE:
        return conflict_stale_complete
    if case.category is EvaluationCategory.MALICIOUS_INJECTED:
        if injection_content_inert is None:
            raise AssertionError("Malicious cases require an inert-content measurement")
        return injection_content_inert
    raise AssertionError(f"Unhandled evaluation category: {case.category}")


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
    current_surfaced, stale_surfaced, conflicting_surfaced, conflict_stale_complete = (
        _current_stale_conflict_flags(case, retrieved_candidate_ids, gold_relevance)
    )
    injection_candidates_surfaced, injection_content_inert = _injection_metrics(
        case,
        retrieved_candidate_ids,
    )
    candidates_by_id = {item.candidate_id: item for item in case.candidates}
    unauthorized_candidates_surfaced = sum(
        int(candidates_by_id[candidate_id].workspace_id != case.workspace_id)
        for candidate_id in retrieved_candidate_ids
        if candidate_id in candidates_by_id
    )

    return EvaluationCaseResult(
        case_id=case.case_id,
        category=case.category,
        retrieved_candidate_ids=retrieved_candidate_ids,
        retrieved_relevance_grades=retrieved_relevance_grades,
        recall_at_5=support_recall_at_5(retrieved_candidate_ids, gold_relevance),
        ndcg_at_5=ndcg_at_k(retrieved_candidate_ids, gold_relevance, k=5),
        ndcg_at_10=ndcg_at_k(retrieved_candidate_ids, gold_relevance, k=10),
        evidence_state_match=_evidence_state_match(
            case,
            retrieved_candidate_ids,
            gold_relevance,
            conflict_stale_complete=conflict_stale_complete,
            injection_content_inert=injection_content_inert,
        ),
        current_evidence_surfaced=current_surfaced,
        stale_evidence_surfaced=stale_surfaced,
        conflicting_evidence_surfaced=conflicting_surfaced,
        conflict_stale_complete=conflict_stale_complete,
        injection_candidates_surfaced=injection_candidates_surfaced,
        injection_content_inert=injection_content_inert,
        unauthorized_candidates_surfaced=unauthorized_candidates_surfaced,
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

    conflict_results = tuple(
        result for result in case_results if result.category is EvaluationCategory.CONFLICTING_STALE
    )
    malicious_results = tuple(
        result
        for result in case_results
        if result.category is EvaluationCategory.MALICIOUS_INJECTED
    )

    return EvaluationReport(
        case_results=case_results,
        case_count=len(case_results),
        macro_recall_at_5=_mean(result.recall_at_5 for result in case_results),
        macro_ndcg_at_5=_mean(result.ndcg_at_5 for result in case_results),
        macro_ndcg_at_10=_mean(result.ndcg_at_10 for result in case_results),
        evidence_state_match_rate=_mean(result.evidence_state_match for result in case_results),
        correct_abstention_rate=None,
        conflict_stale_complete_rate=(
            _mean(result.conflict_stale_complete for result in conflict_results)
            if conflict_results
            else None
        ),
        injection_escape_rate=None,
        injection_content_inert_rate=(
            _mean(result.injection_content_inert for result in malicious_results)
            if malicious_results
            else 0.0
        ),
        injection_candidates_surfaced=sum(
            result.injection_candidates_surfaced for result in case_results
        ),
        workspace_leakage_count=sum(
            result.unauthorized_candidates_surfaced for result in case_results
        ),
        workspace_leakage_rate=_mean(
            int(result.unauthorized_candidates_surfaced > 0) for result in case_results
        ),
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
                "evidence_state_match": result.evidence_state_match,
                "current_evidence_surfaced": result.current_evidence_surfaced,
                "stale_evidence_surfaced": result.stale_evidence_surfaced,
                "conflicting_evidence_surfaced": result.conflicting_evidence_surfaced,
                "conflict_stale_complete": result.conflict_stale_complete,
                "injection_candidates_surfaced": result.injection_candidates_surfaced,
                "injection_content_inert": result.injection_content_inert,
                "unauthorized_candidates_surfaced": result.unauthorized_candidates_surfaced,
            }
            for result in report.case_results
        ],
        "case_count": report.case_count,
        "macro_recall_at_5": report.macro_recall_at_5,
        "macro_ndcg_at_5": report.macro_ndcg_at_5,
        "macro_ndcg_at_10": report.macro_ndcg_at_10,
        "evidence_state_match_rate": report.evidence_state_match_rate,
        "correct_abstention_rate": report.correct_abstention_rate,
        "correct_abstention_interpretation": (
            "Correct Abstention: N/A — response/generation layer not implemented"
        ),
        "conflict_stale_complete_rate": report.conflict_stale_complete_rate,
        "injection_escape_rate": report.injection_escape_rate,
        "injection_escape_interpretation": (
            "Injection Escape: N/A — generation layer not implemented"
        ),
        "injection_content_inert_rate": report.injection_content_inert_rate,
        "injection_candidates_surfaced": report.injection_candidates_surfaced,
        "workspace_leakage_count": report.workspace_leakage_count,
        "workspace_leakage_rate": report.workspace_leakage_rate,
        "category_breakdown": [
            {
                "category": metrics.category.value,
                "case_count": metrics.case_count,
                "macro_recall_at_5": metrics.macro_recall_at_5,
                "macro_ndcg_at_5": metrics.macro_ndcg_at_5,
                "macro_ndcg_at_10": metrics.macro_ndcg_at_10,
                "evidence_state_match_rate": metrics.evidence_state_match_rate,
                "conflict_stale_complete_rate": metrics.conflict_stale_complete_rate,
                "injection_escape_rate": metrics.injection_escape_rate,
                "injection_content_inert_rate": metrics.injection_content_inert_rate,
                "workspace_leakage_rate": metrics.workspace_leakage_rate,
            }
            for metrics in report.category_breakdown
        ],
    }


def format_report(report: EvaluationReport) -> str:
    """Render evaluation output without changing metric calculations."""

    return json.dumps(report_to_dict(report), indent=2, sort_keys=False, ensure_ascii=False)


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
        evidence_state_match_rate=_mean(result.evidence_state_match for result in category_results),
        conflict_stale_complete_rate=(
            _mean(result.conflict_stale_complete for result in category_results)
            if category is EvaluationCategory.CONFLICTING_STALE
            else None
        ),
        injection_escape_rate=None,
        injection_content_inert_rate=(
            _mean(result.injection_content_inert for result in category_results)
            if category is EvaluationCategory.MALICIOUS_INJECTED
            else None
        ),
        workspace_leakage_rate=_mean(
            int(result.unauthorized_candidates_surfaced > 0) for result in category_results
        ),
    )


__all__ = [
    "CategoryMetrics",
    "EvaluationCandidate",
    "EvaluationCase",
    "EvaluationCaseResult",
    "EvaluationCategory",
    "EvaluationReport",
    "FreshnessState",
    "GoldRelevance",
    "evaluate_case",
    "evaluate_cases",
    "format_report",
    "ndcg_at_k",
    "report_to_dict",
    "support_recall_at_5",
]
