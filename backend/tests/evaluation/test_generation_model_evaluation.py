"""Deterministic unit and integration tests for Phase 2E.2 Real Generation Model Evaluation."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy.orm import Session

from app.evaluation.generation import (
    GenerationBenchmarkCategory,
    create_sample_generation_benchmark,
)
from app.evaluation.generation_evaluator import (
    DeterministicEvaluationAdapter,
    EvaluationFailureType,
    EvaluationRawOutput,
    ModelCandidateSpec,
    get_candidate_models_registry,
    normalize_and_validate_output,
    run_candidate_evaluation,
)
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseRevision,
)


def _build_mock_context() -> tuple[GenerationContext, uuid.UUID]:
    chunk_id = uuid.uuid4()
    item = GenerationEvidenceItem(
        citation_handle="EVIDENCE-1",
        evidence_chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_number=1,
        document_name="Security Policy",
        document_version_number=1,
        latest_document_version_number=1,
        is_latest_document_version=True,
        document_status="active",
        conflict_group_id=None,
        chunk_index=0,
        content_hash="hash-1",
        normalized_start_byte=0,
        normalized_end_byte=100,
        section_label="Access Control",
        page_number=1,
        content="Multi-factor authentication (MFA) is enforced for all corporate accounts.",
        token_count=15,
        rrf_score=0.05,
        lexical_rank=1,
        semantic_rank=1,
    )
    context = GenerationContext(
        question_id=uuid.uuid4(),
        questionnaire_id=uuid.uuid4(),
        questionnaire_version_id=uuid.uuid4(),
        questionnaire_version_question_id=uuid.uuid4(),
        question_text="Is MFA enforced for all users?",
        section_path=("Access Control",),
        authorized_workspace_id=uuid.uuid4(),
        evidence_context=(item,),
        total_evidence_chunks=1,
        total_evidence_tokens=15,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )
    return context, chunk_id


# Test 1: Evaluation adapter isolation (never touches DB)
def test_evaluation_adapter_isolation(db_session: Session) -> None:
    spec = ModelCandidateSpec(
        model_id="eval-test-mock",
        version_tag="v1.0",
        runtime="in-memory",
        tokenizer_name=None,
        parameters_info="Test Mock",
        execution_mode="evaluation-only",
        context_length=4000,
        structured_output_mechanism="json",
        hardware_requirements="CPU",
        privacy_data_handling="Local",
        is_environment_executable=True,
    )
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()

    artifact = asyncio.run(run_candidate_evaluation(adapter, benchmark_cases, runs_count=2))

    assert artifact.summary_report.total_evaluations == 10
    # Zero responses or revisions persisted to database
    assert db_session.query(QuestionnaireResponse).count() == 0
    assert db_session.query(QuestionnaireResponseRevision).count() == 0


# Test 2: Schema failure classification
def test_schema_failure_classification() -> None:
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "Test",
                "status": "APPROVED",
                "citation_handles": ["EVIDENCE-1"],
            }
        ),
        latency_ms=10.0,
    )
    res = normalize_and_validate_output(context, raw)
    assert res.failure_type == EvaluationFailureType.SCHEMA_VALIDATION_FAILURE
    assert res.validated_draft is None
    assert "not allowed for model output" in (res.error_message or "")


# Test 3: Citation failure classification
def test_citation_failure_classification() -> None:
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "Test",
                "status": "PROPOSED",
                "citation_handles": ["EVIDENCE-99"],
            }
        ),
        latency_ms=10.0,
    )
    res = normalize_and_validate_output(context, raw)
    assert res.failure_type == EvaluationFailureType.CITATION_VALIDATION_FAILURE
    assert res.validated_draft is None
    assert "does not exist in the authorized" in (res.error_message or "")


# Test 4: Successful validator path
def test_successful_validator_path() -> None:
    context, chunk_id = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "MFA is enforced.",
                "status": "PROPOSED",
                "citation_handles": ["EVIDENCE-1"],
            }
        ),
        latency_ms=12.0,
    )
    res = normalize_and_validate_output(context, raw)
    assert res.failure_type == EvaluationFailureType.SUCCESS
    assert res.validated_draft is not None
    assert res.validated_draft.validation_passed is True
    assert res.validated_draft.cited_chunk_ids == (chunk_id,)


# Test 5: No persistence during evaluation
def test_no_persistence_during_full_eval_run(db_session: Session) -> None:
    spec = get_candidate_models_registry()[0]
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()

    artifact = asyncio.run(run_candidate_evaluation(adapter, benchmark_cases, runs_count=3))
    assert artifact.summary_report.total_evaluations == 15
    assert db_session.query(QuestionnaireResponse).count() == 0
    assert db_session.query(QuestionnaireResponseRevision).count() == 0


# Test 6: No human approval occurred (responses remain unapproved)
def test_no_human_approval_occurred(db_session: Session) -> None:
    spec = get_candidate_models_registry()[0]
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()

    artifact = asyncio.run(run_candidate_evaluation(adapter, benchmark_cases, runs_count=1))
    for item in artifact.human_grounding_items:
        assert item.status in ("PROPOSED", "INSUFFICIENT_EVIDENCE")
        assert "NOT MEASURED" in item.semantic_groundedness_review
    assert db_session.query(QuestionnaireResponse).count() == 0


# Test 7: Injection fixture behavior
def test_injection_fixture_behavior() -> None:
    spec = get_candidate_models_registry()[0]
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()
    inj_case = next(
        c for c in benchmark_cases if c.category == GenerationBenchmarkCategory.MALICIOUS_INJECTED
    )

    artifact = asyncio.run(run_candidate_evaluation(adapter, [inj_case], runs_count=1))
    rec = artifact.summary_report.case_records[0]
    assert rec.is_injection_resisted is True
    assert rec.is_correct_abstention is True


# Test 8: Stale / conflict fixture behavior
def test_stale_conflict_fixture_behavior() -> None:
    spec = get_candidate_models_registry()[0]
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()
    stale_case = next(
        c for c in benchmark_cases if c.category == GenerationBenchmarkCategory.CONFLICTING_STALE
    )

    artifact = asyncio.run(run_candidate_evaluation(adapter, [stale_case], runs_count=1))
    rec = artifact.summary_report.case_records[0]
    assert rec.is_correct_abstention is True
    assert rec.failure_type == EvaluationFailureType.SUCCESS


# Test 9: Metric determinism across repeated runs
def test_metric_determinism_across_runs() -> None:
    spec = get_candidate_models_registry()[0]
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()

    art1 = asyncio.run(
        run_candidate_evaluation(adapter, benchmark_cases, runs_count=3, base_seed=100)
    )
    art2 = asyncio.run(
        run_candidate_evaluation(adapter, benchmark_cases, runs_count=3, base_seed=100)
    )

    rep1 = art1.summary_report
    rep2 = art2.summary_report
    assert rep1.output_schema_validity_rate == rep2.output_schema_validity_rate
    assert rep1.fixture_citation_precision == rep2.fixture_citation_precision
    assert rep1.fixture_citation_recall == rep2.fixture_citation_recall
    assert rep1.fixture_abstention_accuracy == rep2.fixture_abstention_accuracy


# Test 10: Run metadata completeness
def test_run_metadata_completeness() -> None:
    spec = get_candidate_models_registry()[0]
    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()

    artifact = asyncio.run(
        run_candidate_evaluation(adapter, benchmark_cases, runs_count=2, base_seed=42)
    )

    assert "evaluator_version" in artifact.metadata
    assert "timestamp_epoch" in artifact.metadata
    assert artifact.metadata["runs_count"] == 2
    assert artifact.metadata["base_seed"] == 42
    assert artifact.candidate_spec.model_id == "deterministic-eval-harness"

    json_str = artifact.to_json()
    assert "deterministic-eval-harness" in json_str
    assert "human_grounding_items" in json_str


# Test 11: Unavailable model classified as NOT MEASURED
def test_unavailable_model_classified_not_measured() -> None:
    spec = next(
        s
        for s in get_candidate_models_registry()
        if s.model_id == "meta-llama/Llama-3.1-8B-Instruct"
    )
    assert spec.is_environment_executable is False

    adapter = DeterministicEvaluationAdapter(spec)
    benchmark_cases = create_sample_generation_benchmark()

    artifact = asyncio.run(run_candidate_evaluation(adapter, benchmark_cases, runs_count=1))
    assert artifact.summary_report.is_measured is False
    assert "NOT MEASURED" in artifact.summary_report.measurement_status
    assert artifact.summary_report.total_evaluations == 0


# Test 12: Malformed output never bypasses validation
def test_malformed_output_never_bypasses_validation() -> None:
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text="not a valid json { broken",
        latency_ms=5.0,
    )
    res = normalize_and_validate_output(context, raw)
    assert res.failure_type == EvaluationFailureType.MALFORMED_JSON
    assert res.validated_draft is None
    assert "JSON parsing error" in (res.error_message or "")
