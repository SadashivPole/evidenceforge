"""Evaluation-only semantic retrieval benchmark primitives.

This module deliberately does not participate in production evidence retrieval. It
loads one local sentence-transformers model, ranks only workspace-authorized
fixture candidates, and reports retrieval metrics against independent gold labels.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.evaluation.retrieval import (
    EvaluationCandidate,
    EvaluationCase,
    EvaluationCategory,
    ndcg_at_k,
    support_recall_at_5,
)

DEFAULT_MODEL_ID = "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
EXPECTED_EMBEDDING_DIMENSION = 384
DEFAULT_TOP_K = 10
DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_WORD_PIECES = 250


@dataclass(frozen=True, slots=True)
class SemanticBenchmarkConfig:
    """Fixed configuration for one reproducible semantic benchmark run."""

    model_id: str = DEFAULT_MODEL_ID
    embedding_dimension: int = EXPECTED_EMBEDDING_DIMENSION
    top_k: int = DEFAULT_TOP_K
    batch_size: int = DEFAULT_BATCH_SIZE
    max_input_word_pieces: int = DEFAULT_MAX_WORD_PIECES
    similarity: str = "cosine"

    def __post_init__(self) -> None:
        if self.embedding_dimension != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError("The Phase 2C model baseline must use 384 dimensions")
        if self.top_k < 1:
            raise ValueError("Semantic top_k must be positive")
        if self.batch_size < 1:
            raise ValueError("Semantic batch_size must be positive")
        if self.max_input_word_pieces < 1:
            raise ValueError("Semantic input window must be positive")
        if self.similarity != "cosine":
            raise ValueError("The semantic benchmark supports cosine similarity only")


@dataclass(frozen=True, slots=True)
class EmbeddingWindow:
    """Deterministic model-input window while retaining the source candidate ID."""

    text: str
    original_word_pieces: int
    used_word_pieces: int
    was_windowed: bool


@dataclass(frozen=True, slots=True)
class SemanticCandidateMetadata:
    """Provenance metadata retained alongside a semantic ranking result."""

    candidate_id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_index: int
    section_label: str | None


@dataclass(frozen=True, slots=True)
class SemanticCaseResult:
    """Semantic ranking output for one fixed evaluation case."""

    case_id: str
    category: EvaluationCategory
    ranked_candidate_ids: tuple[uuid.UUID, ...]
    similarity_scores: tuple[float, ...]
    retrieved_relevance_grades: tuple[int, ...]
    ranked_metadata: tuple[SemanticCandidateMetadata, ...]
    ranked_document_windowed: tuple[bool, ...]
    query_was_windowed: bool
    injection_candidates_surfaced: int
    injection_content_inert: int | None
    unauthorized_candidates_surfaced: int
    evidence_state_match: int
    conflict_stale_complete: int


@dataclass(frozen=True, slots=True)
class SemanticCategoryMetrics:
    """Retrieval and evidence-state metrics for one category."""

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
class SemanticBenchmarkReport:
    """Complete evaluation-only semantic benchmark report."""

    model: str
    embedding_dimension: int
    query_encoder: str
    document_encoder: str
    similarity: str
    top_k: int
    batch_size: int
    max_input_word_pieces: int
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
    model_load_seconds: float
    query_embedding_seconds: float
    document_embedding_seconds: float
    ranking_seconds: float
    category_breakdown: tuple[SemanticCategoryMetrics, ...]
    case_results: tuple[SemanticCaseResult, ...]


def _token_ids(tokenizer: Any, text: str) -> tuple[int, ...]:
    tokenize = getattr(tokenizer, "tokenize", None)
    convert_tokens_to_ids = getattr(tokenizer, "convert_tokens_to_ids", None)
    if callable(tokenize) and callable(convert_tokens_to_ids):
        tokens = tokenize(text, add_special_tokens=False)
        return tuple(int(token_id) for token_id in convert_tokens_to_ids(tokens))
    encoded = tokenizer.encode(text, add_special_tokens=False, truncation=False)
    return tuple(int(token_id) for token_id in encoded)


def prepare_embedding_window(
    text: str,
    tokenizer: Any,
    *,
    max_word_pieces: int = DEFAULT_MAX_WORD_PIECES,
) -> EmbeddingWindow:
    """Apply explicit deterministic prefix windowing without changing source text."""

    if max_word_pieces < 1:
        raise ValueError("max_word_pieces must be positive")

    token_ids = _token_ids(tokenizer, text)
    original_word_pieces = len(token_ids)
    if original_word_pieces <= max_word_pieces:
        return EmbeddingWindow(
            text=text,
            original_word_pieces=original_word_pieces,
            used_word_pieces=original_word_pieces,
            was_windowed=False,
        )

    bounded_ids = token_ids[:max_word_pieces]
    bounded_text = tokenizer.decode(
        bounded_ids,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return EmbeddingWindow(
        text=bounded_text,
        original_word_pieces=original_word_pieces,
        used_word_pieces=max_word_pieces,
        was_windowed=True,
    )


def _as_normalized_vector(
    output: Any,
    *,
    expected_dimension: int,
    encoder_name: str,
) -> tuple[float, ...]:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - sentence-transformers supplies numpy
        raise RuntimeError("numpy is required by the local semantic benchmark") from exc

    array = np.asarray(output, dtype=float)
    if array.ndim == 2 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 1 or array.shape[0] != expected_dimension:
        raise RuntimeError(
            f"{encoder_name} returned dimension {array.shape}; expected ({expected_dimension},)"
        )
    if not np.isfinite(array).all():
        raise RuntimeError(f"{encoder_name} returned a non-finite embedding")

    norm = float(np.linalg.norm(array))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise RuntimeError(f"{encoder_name} did not return a normalized embedding: norm={norm}")
    return tuple(float(value) for value in array)


def _as_normalized_matrix(
    output: Any,
    *,
    row_count: int,
    expected_dimension: int,
    encoder_name: str,
) -> tuple[tuple[float, ...], ...]:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - sentence-transformers supplies numpy
        raise RuntimeError("numpy is required by the local semantic benchmark") from exc

    array = np.asarray(output, dtype=float)
    if array.ndim != 2 or array.shape != (row_count, expected_dimension):
        raise RuntimeError(
            f"{encoder_name} returned shape {array.shape}; "
            f"expected ({row_count}, {expected_dimension})"
        )
    if not np.isfinite(array).all():
        raise RuntimeError(f"{encoder_name} returned a non-finite embedding")

    norms = np.linalg.norm(array, axis=1)
    if not np.allclose(norms, 1.0, rtol=1e-3, atol=1e-3):
        raise RuntimeError(f"{encoder_name} did not return normalized embeddings")
    return tuple(tuple(float(value) for value in row) for row in array)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Calculate cosine similarity for the already normalized local vectors."""

    return sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    )


def _call_encoder(
    model: Any,
    encoder_name: str,
    texts: Sequence[str],
    config: SemanticBenchmarkConfig,
) -> tuple[tuple[float, ...], ...]:
    encoder = getattr(model, encoder_name, None)
    if not callable(encoder):
        raise RuntimeError(
            f"The loaded model does not provide the required {encoder_name}() encoder"
        )
    output = encoder(
        list(texts),
        batch_size=config.batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return _as_normalized_matrix(
        output,
        row_count=len(texts),
        expected_dimension=config.embedding_dimension,
        encoder_name=encoder_name,
    )


def _model_embedding_dimension(model: Any) -> int | None:
    current_getter = getattr(model, "get_embedding_dimension", None)
    if callable(current_getter):
        return current_getter()
    legacy_getter = getattr(model, "get_sentence_embedding_dimension", None)
    if callable(legacy_getter):
        return legacy_getter()
    return None


@lru_cache(maxsize=1)
def _load_cached_model(model_id: str) -> Any:
    """Load exactly one local model per benchmark process; never use fake vectors."""

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - exercised when optional dep is absent
        raise RuntimeError(
            "sentence-transformers is required for the real semantic benchmark"
        ) from exc

    try:
        model = SentenceTransformer(model_id, device="cpu")
    except Exception as exc:  # pragma: no cover - depends on local model/network state
        raise RuntimeError(
            f"Unable to load local semantic model {model_id!r}; "
            "the benchmark does not fall back to fake embeddings"
        ) from exc

    dimension = _model_embedding_dimension(model)
    if dimension != EXPECTED_EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"Loaded model dimension {dimension} does not match {EXPECTED_EMBEDDING_DIMENSION}"
        )
    return model


def load_local_semantic_model(
    config: SemanticBenchmarkConfig,
) -> tuple[Any, float]:
    """Load the configured local CPU model and return load duration."""

    started = time.perf_counter()
    model = _load_cached_model(config.model_id)
    return model, time.perf_counter() - started


def measure_injection_content_inert(
    surfaced_candidates: Sequence[EvaluationCandidate],
    *,
    observed_content_by_candidate: Mapping[uuid.UUID, str] | None = None,
    instruction_effect_observed: bool = False,
) -> int | None:
    """Measure inert handling from observed surfaced content and effects.

    No surfaced injection-like candidate means inertness was not measurable. When
    one is surfaced, unchanged content and no observed instruction effect are
    required for a successful inert result. The optional observations make
    failure handling testable without adding execution behavior to the evaluator.
    """

    injected_candidates = tuple(item for item in surfaced_candidates if item.injection_like)
    if not injected_candidates:
        return None
    if instruction_effect_observed:
        return 0

    observed_content = observed_content_by_candidate or {
        item.candidate_id: item.candidate.content for item in injected_candidates
    }
    content_unchanged = all(
        observed_content.get(item.candidate_id) == item.candidate.content
        for item in injected_candidates
    )
    return int(content_unchanged)


def aggregate_injection_content_inert_rate(
    measurements: Sequence[int | None],
) -> float | None:
    """Average only observed malicious-case inertness measurements."""

    observed = tuple(value for value in measurements if value is not None)
    return sum(observed) / len(observed) if observed else None


def count_unauthorized_candidates_surfaced(
    ranked_candidate_ids: Sequence[uuid.UUID],
    candidates: Sequence[EvaluationCandidate],
    authorized_workspace_id: uuid.UUID,
) -> int:
    """Count unauthorized candidates observed in an evaluated ranking."""

    candidates_by_id = {item.candidate_id: item for item in candidates}
    return sum(
        int(
            candidate_id in candidates_by_id
            and candidates_by_id[candidate_id].workspace_id != authorized_workspace_id
        )
        for candidate_id in ranked_candidate_ids
    )


class SemanticRetriever:
    """Evaluation-only semantic ranker over one authorized case at a time."""

    def __init__(
        self,
        model: Any,
        config: SemanticBenchmarkConfig,
        *,
        model_load_seconds: float = 0.0,
    ) -> None:
        dimension = _model_embedding_dimension(model)
        if dimension != config.embedding_dimension:
            raise ValueError(f"Model dimension {dimension} does not match benchmark configuration")
        self.model = model
        self.config = config
        self.model_load_seconds = model_load_seconds
        self.query_embedding_seconds = 0.0
        self.document_embedding_seconds = 0.0
        self.ranking_seconds = 0.0

    @property
    def tokenizer(self) -> Any:
        tokenizer = getattr(self.model, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError("The local semantic model does not expose its tokenizer")
        return tokenizer

    def rank_case(self, case: EvaluationCase) -> SemanticCaseResult:
        """Rank only candidates belonging to the case's authorized workspace."""

        eligible_items = tuple(
            item for item in case.candidates if item.workspace_id == case.workspace_id
        )
        query_window = prepare_embedding_window(
            case.question,
            self.tokenizer,
            max_word_pieces=self.config.max_input_word_pieces,
        )
        document_windows = tuple(
            prepare_embedding_window(
                item.candidate.content,
                self.tokenizer,
                max_word_pieces=self.config.max_input_word_pieces,
            )
            for item in eligible_items
        )

        query_started = time.perf_counter()
        query_vector = _call_encoder(
            self.model,
            "encode_query",
            (query_window.text,),
            self.config,
        )[0]
        self.query_embedding_seconds += time.perf_counter() - query_started

        document_started = time.perf_counter()
        document_vectors = _call_encoder(
            self.model,
            "encode_document",
            tuple(window.text for window in document_windows),
            self.config,
        )
        self.document_embedding_seconds += time.perf_counter() - document_started

        ranking_started = time.perf_counter()
        ranked = sorted(
            zip(eligible_items, document_vectors, document_windows, strict=True),
            key=lambda row: (-_cosine_similarity(query_vector, row[1]), str(row[0].candidate_id)),
        )[: self.config.top_k]
        self.ranking_seconds += time.perf_counter() - ranking_started

        ranked_candidate_ids = tuple(item.candidate_id for item, _, _ in ranked)
        ranked_metadata = tuple(
            SemanticCandidateMetadata(
                candidate_id=item.candidate_id,
                workspace_id=item.workspace_id,
                document_id=item.candidate.document_id,
                version_id=item.candidate.version_id,
                version_number=item.candidate.version_number,
                chunk_index=item.candidate.chunk_index,
                section_label=item.candidate.section_label,
            )
            for item, _, _ in ranked
        )
        similarity_scores = tuple(
            _cosine_similarity(query_vector, vector) for _, vector, _ in ranked
        )
        grades = case.gold_relevance_by_candidate
        retrieved_relevance_grades = tuple(
            grades.get(candidate_id, 0) for candidate_id in ranked_candidate_ids
        )
        injection_candidates_surfaced = sum(int(item.injection_like) for item, _, _ in ranked)
        injection_content_inert = (
            measure_injection_content_inert(
                tuple(item for item, _, _ in ranked),
                observed_content_by_candidate={
                    item.candidate_id: item.candidate.content for item in case.candidates
                },
            )
            if case.category is EvaluationCategory.MALICIOUS_INJECTED
            else None
        )
        unauthorized_candidates_surfaced = count_unauthorized_candidates_surfaced(
            ranked_candidate_ids,
            case.candidates,
            case.workspace_id,
        )
        conflict_stale_complete = _conflict_stale_complete(case, ranked_candidate_ids)
        evidence_state_match = _evidence_state_match(
            case,
            ranked_candidate_ids,
            conflict_stale_complete=conflict_stale_complete,
            injection_content_inert=injection_content_inert,
        )

        return SemanticCaseResult(
            case_id=case.case_id,
            category=case.category,
            ranked_candidate_ids=ranked_candidate_ids,
            similarity_scores=similarity_scores,
            retrieved_relevance_grades=retrieved_relevance_grades,
            ranked_metadata=ranked_metadata,
            ranked_document_windowed=tuple(window.was_windowed for _, _, window in ranked),
            query_was_windowed=query_window.was_windowed,
            injection_candidates_surfaced=injection_candidates_surfaced,
            injection_content_inert=injection_content_inert,
            unauthorized_candidates_surfaced=unauthorized_candidates_surfaced,
            evidence_state_match=evidence_state_match,
            conflict_stale_complete=conflict_stale_complete,
        )


def _conflict_stale_complete(
    case: EvaluationCase,
    ranked_candidate_ids: Sequence[uuid.UUID],
) -> int:
    gold = case.gold_relevance_by_candidate
    by_id = {item.candidate_id: item for item in case.candidates}
    surfaced = tuple(
        by_id[candidate_id]
        for candidate_id in ranked_candidate_ids
        if candidate_id in by_id and gold.get(candidate_id, 0) > 0
    )
    current = any(item.freshness.value == "CURRENT" for item in surfaced)
    stale = any(item.freshness.value == "STALE" for item in surfaced)
    conflict_groups: dict[str, int] = {}
    for item in surfaced:
        if item.conflict_group is not None:
            conflict_groups[item.conflict_group] = conflict_groups.get(item.conflict_group, 0) + 1
    conflicting = any(count >= 2 for count in conflict_groups.values())
    return int(current and stale and conflicting)


def _evidence_state_match(
    case: EvaluationCase,
    ranked_candidate_ids: Sequence[uuid.UUID],
    *,
    conflict_stale_complete: int,
    injection_content_inert: int | None,
) -> int:
    grades = case.gold_relevance_by_candidate
    retrieved_grades = tuple(grades.get(candidate_id, 0) for candidate_id in ranked_candidate_ids)
    has_direct_support = any(grade >= 3 for grade in retrieved_grades)
    has_support = any(grade >= 2 for grade in retrieved_grades)
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
        return int(injection_content_inert == 1)
    raise AssertionError(f"Unhandled evaluation category: {case.category}")


def _category_metrics(
    category: EvaluationCategory,
    cases_by_id: dict[str, EvaluationCase],
    results: Sequence[SemanticCaseResult],
) -> SemanticCategoryMetrics:
    category_results = tuple(result for result in results if result.category is category)
    inert_measurements = tuple(result.injection_content_inert for result in category_results)
    return SemanticCategoryMetrics(
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


def evaluate_semantic_cases(
    cases: Sequence[EvaluationCase],
    retriever: SemanticRetriever,
) -> SemanticBenchmarkReport:
    """Evaluate fixed cases with the local semantic ranker."""

    if not cases:
        raise ValueError("At least one evaluation case is required")
    case_ids = tuple(case.case_id for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Evaluation case IDs must be unique")

    results = tuple(retriever.rank_case(case) for case in cases)
    cases_by_id = {case.case_id: case for case in cases}
    category_breakdown = tuple(
        _category_metrics(category, cases_by_id, results)
        for category in EvaluationCategory
        if any(result.category is category for result in results)
    )
    conflict_results = tuple(
        result for result in results if result.category is EvaluationCategory.CONFLICTING_STALE
    )
    malicious_results = tuple(
        result for result in results if result.category is EvaluationCategory.MALICIOUS_INJECTED
    )
    inert_measurements = tuple(result.injection_content_inert for result in malicious_results)

    return SemanticBenchmarkReport(
        model=retriever.config.model_id,
        embedding_dimension=retriever.config.embedding_dimension,
        query_encoder="encode_query",
        document_encoder="encode_document",
        similarity=retriever.config.similarity,
        top_k=retriever.config.top_k,
        batch_size=retriever.config.batch_size,
        max_input_word_pieces=retriever.config.max_input_word_pieces,
        case_count=len(results),
        macro_recall_at_5=sum(
            support_recall_at_5(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
            )
            for result in results
        )
        / len(results),
        macro_ndcg_at_5=sum(
            ndcg_at_k(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
                k=5,
            )
            for result in results
        )
        / len(results),
        macro_ndcg_at_10=sum(
            ndcg_at_k(
                result.ranked_candidate_ids,
                cases_by_id[result.case_id].gold_relevance_by_candidate,
                k=10,
            )
            for result in results
        )
        / len(results),
        evidence_state_match_rate=sum(result.evidence_state_match for result in results)
        / len(results),
        conflict_stale_complete_rate=(
            sum(result.conflict_stale_complete for result in conflict_results)
            / len(conflict_results)
            if conflict_results
            else None
        ),
        injection_content_inert_rate=aggregate_injection_content_inert_rate(inert_measurements),
        injection_candidates_surfaced=sum(
            result.injection_candidates_surfaced for result in results
        ),
        workspace_leakage_count=sum(result.unauthorized_candidates_surfaced for result in results),
        workspace_leakage_rate=sum(
            int(result.unauthorized_candidates_surfaced > 0) for result in results
        )
        / len(results),
        model_load_seconds=retriever.model_load_seconds,
        query_embedding_seconds=retriever.query_embedding_seconds,
        document_embedding_seconds=retriever.document_embedding_seconds,
        ranking_seconds=retriever.ranking_seconds,
        category_breakdown=category_breakdown,
        case_results=results,
    )


def run_semantic_benchmark(
    cases: Sequence[EvaluationCase],
    *,
    config: SemanticBenchmarkConfig | None = None,
) -> SemanticBenchmarkReport:
    """Load one local model and run the evaluation-only semantic benchmark."""

    effective_config = config or SemanticBenchmarkConfig()
    model, model_load_seconds = load_local_semantic_model(effective_config)
    retriever = SemanticRetriever(
        model,
        effective_config,
        model_load_seconds=model_load_seconds,
    )
    return evaluate_semantic_cases(cases, retriever)


def report_to_dict(report: SemanticBenchmarkReport) -> dict[str, object]:
    """Convert a semantic report to stable JSON-compatible output."""

    return {
        "model": report.model,
        "embedding_dimension": report.embedding_dimension,
        "query_encoder": report.query_encoder,
        "document_encoder": report.document_encoder,
        "similarity": report.similarity,
        "top_k": report.top_k,
        "batch_size": report.batch_size,
        "max_input_word_pieces": report.max_input_word_pieces,
        "case_count": report.case_count,
        "macro_recall_at_5": report.macro_recall_at_5,
        "macro_ndcg_at_5": report.macro_ndcg_at_5,
        "macro_ndcg_at_10": report.macro_ndcg_at_10,
        "evidence_state_match_rate": report.evidence_state_match_rate,
        "conflict_stale_complete_rate": report.conflict_stale_complete_rate,
        "injection_content_inert_rate": report.injection_content_inert_rate,
        "injection_candidates_surfaced": report.injection_candidates_surfaced,
        "workspace_leakage_count": report.workspace_leakage_count,
        "workspace_leakage_rate": report.workspace_leakage_rate,
        "runtime_seconds": {
            "model_load": report.model_load_seconds,
            "query_embedding": report.query_embedding_seconds,
            "document_embedding": report.document_embedding_seconds,
            "ranking": report.ranking_seconds,
        },
        "category_breakdown": [
            {
                "category": metrics.category.value,
                "case_count": metrics.case_count,
                "recall_at_5": metrics.recall_at_5,
                "ndcg_at_5": metrics.ndcg_at_5,
                "ndcg_at_10": metrics.ndcg_at_10,
                "evidence_state_match_rate": metrics.evidence_state_match_rate,
                "conflict_stale_complete_rate": metrics.conflict_stale_complete_rate,
                "injection_content_inert_rate": metrics.injection_content_inert_rate,
                "injection_candidates_surfaced": metrics.injection_candidates_surfaced,
                "workspace_leakage_rate": metrics.workspace_leakage_rate,
            }
            for metrics in report.category_breakdown
        ],
        "case_results": [
            {
                "case_id": result.case_id,
                "category": result.category.value,
                "ranked_candidate_ids": [
                    str(candidate_id) for candidate_id in result.ranked_candidate_ids
                ],
                "similarity_scores": list(result.similarity_scores),
                "retrieved_relevance_grades": list(result.retrieved_relevance_grades),
                "workspace_ids": [
                    str(metadata.workspace_id) for metadata in result.ranked_metadata
                ],
                "document_ids": [str(metadata.document_id) for metadata in result.ranked_metadata],
                "version_numbers": [metadata.version_number for metadata in result.ranked_metadata],
                "chunk_indexes": [metadata.chunk_index for metadata in result.ranked_metadata],
                "section_labels": [metadata.section_label for metadata in result.ranked_metadata],
                "document_windowed": list(result.ranked_document_windowed),
                "query_was_windowed": result.query_was_windowed,
                "injection_candidates_surfaced": result.injection_candidates_surfaced,
                "injection_content_inert": result.injection_content_inert,
                "unauthorized_candidates_surfaced": result.unauthorized_candidates_surfaced,
                "evidence_state_match": result.evidence_state_match,
                "conflict_stale_complete": result.conflict_stale_complete,
            }
            for result in report.case_results
        ],
    }


def format_report(report: SemanticBenchmarkReport) -> str:
    """Render the semantic benchmark as readable JSON."""

    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False)


__all__ = [
    "DEFAULT_MODEL_ID",
    "EmbeddingWindow",
    "aggregate_injection_content_inert_rate",
    "SemanticBenchmarkConfig",
    "SemanticBenchmarkReport",
    "SemanticCaseResult",
    "SemanticCandidateMetadata",
    "SemanticCategoryMetrics",
    "SemanticRetriever",
    "count_unauthorized_candidates_surfaced",
    "evaluate_semantic_cases",
    "measure_injection_content_inert",
    "format_report",
    "load_local_semantic_model",
    "prepare_embedding_window",
    "report_to_dict",
    "run_semantic_benchmark",
]
