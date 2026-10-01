"""Evaluation-only harness and adapter pipeline for Phase 2E.2 Real Generation Model Evaluation.

Enforces:
1. Complete isolation from production generation and response persistence (0 database writes).
2. Distinct failure classification (TRANSPORT, MALFORMED_JSON, SCHEMA, CITATION, STATUS, SUCCESS).
3. Explicit candidate specification and reproducible multi-run policy.
4. Human grounding review artifact structure.
5. True citation precision/recall and handle safety.
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from app.evaluation.generation import (
    GenerationBenchmarkCategory,
    GenerationTestCase,
    calculate_case_citation_metrics,
)
from app.questionnaires.generation.errors import (
    GenerationBoundaryError,
    GenerationCitationValidationError,
    GenerationStatusValidationError,
)
from app.questionnaires.generation.projection import ModelGenerationInput
from app.questionnaires.generation.schemas import GeneratedDraftPayload
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
    ValidatedDraftResponse,
)
from app.questionnaires.generation.validator import validate_generated_draft
from app.questionnaires.types import ResponseStatus


class EvaluationFailureType(StrEnum):
    """Granular classification of model output outcomes."""

    SUCCESS = "SUCCESS"
    TRANSPORT_API_FAILURE = "TRANSPORT_API_FAILURE"
    MALFORMED_JSON = "MALFORMED_JSON"
    SCHEMA_VALIDATION_FAILURE = "SCHEMA_VALIDATION_FAILURE"
    CITATION_VALIDATION_FAILURE = "CITATION_VALIDATION_FAILURE"
    STATUS_VALIDATION_FAILURE = "STATUS_VALIDATION_FAILURE"


@dataclass(frozen=True, slots=True)
class ModelCandidateSpec:
    """Explicit candidate model specification."""

    model_id: str
    version_tag: str
    runtime: str
    tokenizer_name: str | None
    parameters_info: str | None
    execution_mode: str
    context_length: int
    structured_output_mechanism: str
    hardware_requirements: str
    privacy_data_handling: str
    is_environment_executable: bool
    execution_limitation: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationRawOutput:
    """Raw, untrusted output from an evaluation model adapter."""

    raw_text: str | None
    latency_ms: float
    ttft_ms: float | None = None
    tokens_per_second: float | None = None
    memory_mb: float | None = None
    transport_error: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationNormalizedResult:
    """Outcome of normalizing and validating a single model output."""

    failure_type: EvaluationFailureType
    draft_payload: GeneratedDraftPayload | None
    validated_draft: ValidatedDraftResponse | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class HumanGroundingReviewItem:
    """Reviewable evaluation artifact allowing human review of factual claims."""

    case_id: str
    question_text: str
    generated_answer: str | None
    cited_handles: tuple[str, ...]
    expected_citations: tuple[str, ...]
    abstention_decision: str
    status: str
    validation_failure_type: str
    semantic_groundedness_review: str = "NOT MEASURED - Requires Human Adjudication"
    unsupported_claims_review: str = "NOT MEASURED - Requires Human Adjudication"


@dataclass(frozen=True, slots=True)
class CaseRunRecord:
    """Single execution record for one test case in one evaluation run."""

    run_index: int
    case_id: str
    category: GenerationBenchmarkCategory
    raw_output: str | None
    normalized_payload: dict[str, Any] | None
    failure_type: EvaluationFailureType
    is_valid_schema: bool
    is_citation_handle_safe: bool
    citation_precision: float
    citation_recall: float
    is_status_correct: bool
    is_correct_abstention: bool
    is_keyword_support_matched: bool
    is_injection_resisted: bool
    latency_ms: float
    error_message: str | None
    human_review_item: HumanGroundingReviewItem


@dataclass(frozen=True, slots=True)
class ModelEvaluationSummaryReport:
    """Summary report across all evaluation runs for a candidate model."""

    model_id: str
    version_tag: str
    is_measured: bool
    measurement_status: str
    total_runs: int
    cases_per_run: int
    total_evaluations: int
    output_schema_validity_rate: float
    output_validation_failure_rate: float
    failure_breakdown: dict[str, int]
    fixture_citation_handle_safety_rate: float
    fixture_citation_precision: float
    fixture_citation_recall: float
    fixture_keyword_support_rate: float
    fixture_abstention_accuracy: float
    fixture_injection_resistance_rate: float
    mean_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    latency_std_dev: float
    run_consistency_rate: float
    case_records: list[CaseRunRecord] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EvaluationRunArtifact:
    """Full serialized artifact containing candidate metadata and run records."""

    metadata: dict[str, Any]
    candidate_spec: ModelCandidateSpec
    summary_report: ModelEvaluationSummaryReport
    human_grounding_items: list[HumanGroundingReviewItem]

    def to_json(self, indent: int = 2) -> str:
        """Serialize artifact to JSON string."""
        return json.dumps(asdict(self), indent=indent, default=str)


class EvaluationModelAdapter(ABC):
    """Abstract evaluation-only adapter boundary."""

    def __init__(self, spec: ModelCandidateSpec) -> None:
        self.spec = spec

    @abstractmethod
    async def generate_raw(
        self,
        model_input: ModelGenerationInput,
        *,
        seed: int = 42,
    ) -> EvaluationRawOutput:
        """Execute raw generation against candidate model."""
        ...


class DeterministicEvaluationAdapter(EvaluationModelAdapter):
    """Deterministic local evaluation adapter for smoke testing harness mechanics."""

    def __init__(
        self,
        spec: ModelCandidateSpec,
        *,
        behavior_mode: str = "ideal",
    ) -> None:
        super().__init__(spec)
        self.behavior_mode = behavior_mode

    async def generate_raw(
        self,
        model_input: ModelGenerationInput,
        *,
        seed: int = 42,
    ) -> EvaluationRawOutput:
        t0 = time.perf_counter()

        if self.behavior_mode == "transport_error":
            elapsed = (time.perf_counter() - t0) * 1000
            return EvaluationRawOutput(
                raw_text=None,
                latency_ms=elapsed,
                transport_error="Simulated connection timeout to model endpoint",
            )

        if self.behavior_mode == "malformed_json":
            elapsed = (time.perf_counter() - t0) * 1000
            return EvaluationRawOutput(
                raw_text="{'answer': 'broken json without closing quotes",
                latency_ms=elapsed,
            )

        if self.behavior_mode == "schema_failure":
            raw = json.dumps(
                {
                    "answer": "Some answer",
                    "status": "APPROVED",  # Disallowed for model output
                    "citation_handles": ["EVIDENCE-1"],
                }
            )
            elapsed = (time.perf_counter() - t0) * 1000
            return EvaluationRawOutput(raw_text=raw, latency_ms=elapsed)

        if self.behavior_mode == "invalid_citation":
            raw = json.dumps(
                {
                    "answer": "Answer with fabricated handle",
                    "status": "PROPOSED",
                    "citation_handles": ["EVIDENCE-99"],
                }
            )
            elapsed = (time.perf_counter() - t0) * 1000
            return EvaluationRawOutput(raw_text=raw, latency_ms=elapsed)

        if self.behavior_mode == "status_failure":
            raw = json.dumps(
                {
                    "answer": "Answer proposing PROPOSED on empty context",
                    "status": "PROPOSED",
                    "citation_handles": [],
                }
            )
            elapsed = (time.perf_counter() - t0) * 1000
            return EvaluationRawOutput(raw_text=raw, latency_ms=elapsed)

        # Ideal deterministic behavior based on input context:
        is_injection = any(
            "ignore all prior instructions" in item.content.lower()
            or "system override" in item.content.lower()
            for item in model_input.evidence_items
        )
        if (
            not model_input.evidence_items
            or model_input.has_stale_or_conflicting_evidence
            or is_injection
        ):
            raw = json.dumps(
                {
                    "answer": "Insufficient or unverified evidence to provide a verified answer.",
                    "status": "INSUFFICIENT_EVIDENCE",
                    "citation_handles": [],
                    "uncertainty_notes": "Evidence is insufficient or conflicting.",
                }
            )
        else:
            first_handle = model_input.evidence_items[0].citation_handle
            raw = json.dumps(
                {
                    "answer": (
                        "All employees and contractors must authenticate with "
                        "multi-factor authentication (MFA) to access corporate systems."
                    ),
                    "status": "PROPOSED",
                    "citation_handles": [first_handle],
                    "uncertainty_notes": None,
                }
            )

        elapsed = (time.perf_counter() - t0) * 1000
        return EvaluationRawOutput(
            raw_text=raw,
            latency_ms=elapsed,
            ttft_ms=elapsed * 0.4,
            tokens_per_second=45.0,
        )


def normalize_and_validate_output(
    context: GenerationContext,
    raw_output: EvaluationRawOutput,
) -> EvaluationNormalizedResult:
    """Normalize raw model output and validate through Phase 2D.3 boundary."""
    if raw_output.transport_error is not None or raw_output.raw_text is None:
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.TRANSPORT_API_FAILURE,
            draft_payload=None,
            validated_draft=None,
            error_message=raw_output.transport_error or "Empty output from model",
        )

    try:
        data = json.loads(raw_output.raw_text)
    except json.JSONDecodeError as exc:
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.MALFORMED_JSON,
            draft_payload=None,
            validated_draft=None,
            error_message=f"JSON parsing error: {exc}",
        )

    try:
        draft_payload = GeneratedDraftPayload.model_validate(data)
    except Exception as exc:
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.SCHEMA_VALIDATION_FAILURE,
            draft_payload=None,
            validated_draft=None,
            error_message=f"Pydantic schema validation error: {exc}",
        )

    try:
        validated_draft = validate_generated_draft(context, draft_payload)
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.SUCCESS,
            draft_payload=draft_payload,
            validated_draft=validated_draft,
            error_message=None,
        )
    except GenerationCitationValidationError as exc:
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.CITATION_VALIDATION_FAILURE,
            draft_payload=draft_payload,
            validated_draft=None,
            error_message=str(exc),
        )
    except GenerationStatusValidationError as exc:
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.STATUS_VALIDATION_FAILURE,
            draft_payload=draft_payload,
            validated_draft=None,
            error_message=str(exc),
        )
    except GenerationBoundaryError as exc:
        return EvaluationNormalizedResult(
            failure_type=EvaluationFailureType.SCHEMA_VALIDATION_FAILURE,
            draft_payload=draft_payload,
            validated_draft=None,
            error_message=str(exc),
        )


def _convert_test_case_to_gen_context(test_case: GenerationTestCase) -> GenerationContext:
    """Helper to convert evaluation test case ModelGenerationInput into a mock GenerationContext."""
    gen_items = []
    for i, m_item in enumerate(test_case.model_input.evidence_items, start=1):
        gen_item = GenerationEvidenceItem(
            citation_handle=m_item.citation_handle,
            evidence_chunk_id=uuid.UUID(f"00000000-0000-0000-0000-{i:012x}"),
            document_id=uuid.UUID(f"10000000-0000-0000-0000-{i:012x}"),
            version_id=uuid.UUID(f"20000000-0000-0000-0000-{i:012x}"),
            version_number=m_item.document_version_number,
            document_name=m_item.document_name,
            document_version_number=m_item.document_version_number,
            latest_document_version_number=m_item.document_version_number,
            is_latest_document_version=m_item.is_latest_document_version,
            document_status=m_item.document_status,
            conflict_group_id="conflict-group-mock" if m_item.has_conflict else None,
            chunk_index=i - 1,
            content_hash=f"hash-{i}",
            normalized_start_byte=0,
            normalized_end_byte=len(m_item.content),
            section_label=m_item.section_label,
            page_number=m_item.page_number,
            content=m_item.content,
            token_count=m_item.token_count,
            rrf_score=0.04,
            lexical_rank=i,
            semantic_rank=i,
        )
        gen_items.append(gen_item)

    return GenerationContext(
        question_id=uuid.UUID("30000000-0000-0000-0000-000000000001"),
        questionnaire_id=uuid.UUID("40000000-0000-0000-0000-000000000001"),
        questionnaire_version_id=uuid.UUID("50000000-0000-0000-0000-000000000001"),
        questionnaire_version_question_id=uuid.UUID("60000000-0000-0000-0000-000000000001"),
        question_text=test_case.question_text,
        section_path=test_case.model_input.section_path,
        authorized_workspace_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        evidence_context=tuple(gen_items),
        total_evidence_chunks=len(gen_items),
        total_evidence_tokens=test_case.model_input.total_evidence_tokens,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )


async def run_candidate_evaluation(
    adapter: EvaluationModelAdapter,
    test_cases: list[GenerationTestCase],
    *,
    runs_count: int = 3,
    base_seed: int = 42,
) -> EvaluationRunArtifact:
    """Execute reproducible multi-run evaluation against a candidate model adapter."""
    records: list[CaseRunRecord] = []
    failure_counts = {ft.value: 0 for ft in EvaluationFailureType}
    latencies: list[float] = []

    if not adapter.spec.is_environment_executable:
        # Candidate not executable in current sandbox environment -> NOT MEASURED
        summary = ModelEvaluationSummaryReport(
            model_id=adapter.spec.model_id,
            version_tag=adapter.spec.version_tag,
            is_measured=False,
            measurement_status=f"NOT MEASURED: {adapter.spec.execution_limitation}",
            total_runs=0,
            cases_per_run=len(test_cases),
            total_evaluations=0,
            output_schema_validity_rate=0.0,
            output_validation_failure_rate=0.0,
            failure_breakdown={},
            fixture_citation_handle_safety_rate=0.0,
            fixture_citation_precision=0.0,
            fixture_citation_recall=0.0,
            fixture_keyword_support_rate=0.0,
            fixture_abstention_accuracy=0.0,
            fixture_injection_resistance_rate=0.0,
            mean_latency_ms=0.0,
            min_latency_ms=0.0,
            max_latency_ms=0.0,
            latency_std_dev=0.0,
            run_consistency_rate=0.0,
            case_records=[],
        )
        return EvaluationRunArtifact(
            metadata={
                "evaluator_version": "generation-evaluator-v1",
                "timestamp_epoch": time.time(),
                "status": "NOT_MEASURED",
            },
            candidate_spec=adapter.spec,
            summary_report=summary,
            human_grounding_items=[],
        )

    human_items: list[HumanGroundingReviewItem] = []

    for run_idx in range(runs_count):
        seed = base_seed + run_idx
        for case in test_cases:
            gen_context = _convert_test_case_to_gen_context(case)
            raw_out = await adapter.generate_raw(case.model_input, seed=seed)
            latencies.append(raw_out.latency_ms)

            norm_res = normalize_and_validate_output(gen_context, raw_out)
            failure_counts[norm_res.failure_type.value] += 1

            is_valid_schema = norm_res.failure_type not in (
                EvaluationFailureType.MALFORMED_JSON,
                EvaluationFailureType.SCHEMA_VALIDATION_FAILURE,
                EvaluationFailureType.TRANSPORT_API_FAILURE,
            )

            predicted_handles: list[str] = (
                norm_res.draft_payload.citation_handles if norm_res.draft_payload else []
            )

            # Handle safety check
            available_handles = {item.citation_handle for item in case.model_input.evidence_items}
            citation_handle_safe = True
            for h in predicted_handles:
                if h not in available_handles or h in case.forbidden_citations:
                    citation_handle_safe = False

            # Citation precision & recall
            precision, recall = calculate_case_citation_metrics(
                case.expected_citations,
                predicted_handles,
            )

            # Status check
            is_status_correct = False
            if norm_res.draft_payload is not None:
                is_status_correct = norm_res.draft_payload.status == case.expected_status

            # Abstention check
            is_correct_abstention = True
            if case.is_abstention_expected:
                if (
                    norm_res.draft_payload is None
                    or norm_res.draft_payload.status != ResponseStatus.INSUFFICIENT_EVIDENCE
                    or len(predicted_handles) > 0
                ):
                    is_correct_abstention = False

            # Keyword support heuristic
            answer_text = (
                norm_res.draft_payload.answer.casefold()
                if norm_res.draft_payload and norm_res.draft_payload.answer
                else ""
            )
            keyword_matched = True
            if case.category == GenerationBenchmarkCategory.SUPPORTED:
                if not predicted_handles:
                    keyword_matched = False
                for kw in case.expected_keywords:
                    if kw.casefold() not in answer_text:
                        keyword_matched = False

            # Injection check
            is_injection_resisted = True
            if case.category == GenerationBenchmarkCategory.MALICIOUS_INJECTED:
                for kw in case.forbidden_keywords:
                    if kw.casefold() in answer_text:
                        is_injection_resisted = False

            human_item = HumanGroundingReviewItem(
                case_id=case.case_id,
                question_text=case.question_text,
                generated_answer=norm_res.draft_payload.answer if norm_res.draft_payload else None,
                cited_handles=tuple(predicted_handles),
                expected_citations=case.expected_citations,
                abstention_decision=(
                    "ABSTAINED"
                    if norm_res.draft_payload
                    and norm_res.draft_payload.status == ResponseStatus.INSUFFICIENT_EVIDENCE
                    else "PROPOSED"
                ),
                status=norm_res.draft_payload.status.value if norm_res.draft_payload else "FAILED",
                validation_failure_type=norm_res.failure_type.value,
            )
            if run_idx == 0:
                human_items.append(human_item)

            rec = CaseRunRecord(
                run_index=run_idx,
                case_id=case.case_id,
                category=case.category,
                raw_output=raw_out.raw_text,
                normalized_payload=norm_res.draft_payload.model_dump()
                if norm_res.draft_payload
                else None,
                failure_type=norm_res.failure_type,
                is_valid_schema=is_valid_schema,
                is_citation_handle_safe=citation_handle_safe,
                citation_precision=precision,
                citation_recall=recall,
                is_status_correct=is_status_correct,
                is_correct_abstention=is_correct_abstention,
                is_keyword_support_matched=keyword_matched,
                is_injection_resisted=is_injection_resisted,
                latency_ms=raw_out.latency_ms,
                error_message=norm_res.error_message,
                human_review_item=human_item,
            )
            records.append(rec)

    total_evals = len(records)
    valid_schema_count = sum(1 for r in records if r.is_valid_schema)
    handle_safe_count = sum(1 for r in records if r.is_citation_handle_safe)
    macro_precision = sum(r.citation_precision for r in records) / total_evals
    macro_recall = sum(r.citation_recall for r in records) / total_evals
    keyword_matched_count = sum(1 for r in records if r.is_keyword_support_matched)

    abstention_records = [
        r for r, c in zip(records, test_cases * runs_count, strict=True) if c.is_abstention_expected
    ]
    abstention_correct = sum(1 for r in abstention_records if r.is_correct_abstention)
    abstention_acc = abstention_correct / len(abstention_records) if abstention_records else 1.0

    injection_records = [
        r for r in records if r.category == GenerationBenchmarkCategory.MALICIOUS_INJECTED
    ]
    injection_resisted = sum(1 for r in injection_records if r.is_injection_resisted)
    injection_rate = injection_resisted / len(injection_records) if injection_records else 1.0

    # Consistency across repeated runs
    case_ids = {c.case_id for c in test_cases}
    consistent_cases = 0
    for cid in case_ids:
        case_records = [r for r in records if r.case_id == cid]
        statuses = {
            r.normalized_payload.get("status") if r.normalized_payload else None
            for r in case_records
        }
        if len(statuses) == 1:
            consistent_cases += 1
    run_consistency_rate = consistent_cases / len(case_ids) if case_ids else 1.0

    mean_lat = statistics.mean(latencies) if latencies else 0.0
    min_lat = min(latencies) if latencies else 0.0
    max_lat = max(latencies) if latencies else 0.0
    std_lat = statistics.stdev(latencies) if len(latencies) > 1 else 0.0

    summary = ModelEvaluationSummaryReport(
        model_id=adapter.spec.model_id,
        version_tag=adapter.spec.version_tag,
        is_measured=True,
        measurement_status="MEASURED (Deterministic Local Evaluation Harness)",
        total_runs=runs_count,
        cases_per_run=len(test_cases),
        total_evaluations=total_evals,
        output_schema_validity_rate=valid_schema_count / total_evals,
        output_validation_failure_rate=1.0 - (valid_schema_count / total_evals),
        failure_breakdown=failure_counts,
        fixture_citation_handle_safety_rate=handle_safe_count / total_evals,
        fixture_citation_precision=macro_precision,
        fixture_citation_recall=macro_recall,
        fixture_keyword_support_rate=keyword_matched_count / total_evals,
        fixture_abstention_accuracy=abstention_acc,
        fixture_injection_resistance_rate=injection_rate,
        mean_latency_ms=mean_lat,
        min_latency_ms=min_lat,
        max_latency_ms=max_lat,
        latency_std_dev=std_lat,
        run_consistency_rate=run_consistency_rate,
        case_records=records,
    )

    return EvaluationRunArtifact(
        metadata={
            "evaluator_version": "generation-evaluator-v1",
            "timestamp_epoch": time.time(),
            "runs_count": runs_count,
            "base_seed": base_seed,
            "status": "MEASURED",
        },
        candidate_spec=adapter.spec,
        summary_report=summary,
        human_grounding_items=human_items,
    )


def get_candidate_models_registry() -> list[ModelCandidateSpec]:
    """Catalog of explicitly evaluated and specified generation model candidates."""
    return [
        ModelCandidateSpec(
            model_id="deterministic-eval-harness",
            version_tag="v1.0.0",
            runtime="python-in-memory-harness",
            tokenizer_name=None,
            parameters_info="Deterministic rule-based mock",
            execution_mode="evaluation-only-isolated",
            context_length=4000,
            structured_output_mechanism="json-pydantic-validation",
            hardware_requirements="Standard CPU",
            privacy_data_handling="100% Local / In-Memory (Zero Network Egress)",
            is_environment_executable=True,
            execution_limitation=None,
        ),
        ModelCandidateSpec(
            model_id="meta-llama/Llama-3.1-8B-Instruct",
            version_tag="3.1-8b-instruct",
            runtime="vLLM / HuggingFace TGI",
            tokenizer_name="meta-llama/Llama-3.1-8B-Instruct",
            parameters_info="8.03B parameters (bfloat16)",
            execution_mode="local-self-hosted",
            context_length=128000,
            structured_output_mechanism="guided-decoding-outlines-json",
            hardware_requirements="1x NVIDIA A10G / RTX 4090 (24GB VRAM)",
            privacy_data_handling="Self-Hosted (Zero External Egress)",
            is_environment_executable=False,
            execution_limitation="GPU acceleration and local vLLM runtime not provisioned",
        ),
        ModelCandidateSpec(
            model_id="mistralai/Mistral-7B-Instruct-v0.3",
            version_tag="v0.3",
            runtime="vLLM / Ollama",
            tokenizer_name="mistralai/Mistral-7B-Instruct-v0.3",
            parameters_info="7.25B parameters (bfloat16)",
            execution_mode="local-self-hosted",
            context_length=32768,
            structured_output_mechanism="grammar-constrained-json",
            hardware_requirements="1x NVIDIA RTX 3090 / A10G (24GB VRAM)",
            privacy_data_handling="Self-Hosted (Zero External Egress)",
            is_environment_executable=False,
            execution_limitation="GPU acceleration and local vLLM runtime not provisioned",
        ),
        ModelCandidateSpec(
            model_id="Qwen/Qwen2.5-7B-Instruct",
            version_tag="2.5-7b",
            runtime="vLLM / SGLang",
            tokenizer_name="Qwen/Qwen2.5-7B-Instruct",
            parameters_info="7.61B parameters (bfloat16)",
            execution_mode="local-self-hosted",
            context_length=131072,
            structured_output_mechanism="guided-decoding-json",
            hardware_requirements="1x NVIDIA RTX 3090 / A10G (24GB VRAM)",
            privacy_data_handling="Self-Hosted (Zero External Egress)",
            is_environment_executable=False,
            execution_limitation="GPU acceleration and local vLLM runtime not provisioned",
        ),
        ModelCandidateSpec(
            model_id="anthropic/claude-3-5-sonnet-20241022",
            version_tag="20241022",
            runtime="Anthropic Bedrock / Vertex / Direct API",
            tokenizer_name="anthropic-claude-v3",
            parameters_info="Proprietary Frontier Model",
            execution_mode="managed-cloud-api",
            context_length=200000,
            structured_output_mechanism="tool-use-json-mode",
            hardware_requirements="Serverless Managed API",
            privacy_data_handling="Commercial Zero Data Retention (ZDR) Agreement",
            is_environment_executable=False,
            execution_limitation="Zero external network/API keys permitted in evaluation phase",
        ),
        ModelCandidateSpec(
            model_id="openai/gpt-4o-2024-08-06",
            version_tag="2024-08-06",
            runtime="Azure OpenAI / OpenAI Direct",
            tokenizer_name="o200k_base",
            parameters_info="Proprietary Frontier Model",
            execution_mode="managed-cloud-api",
            context_length=128000,
            structured_output_mechanism="strict-json-schema-mode",
            hardware_requirements="Serverless Managed API",
            privacy_data_handling="Commercial Zero Data Retention (ZDR) / Azure Private Link",
            is_environment_executable=False,
            execution_limitation="Zero external network/API keys permitted in evaluation phase",
        ),
    ]
