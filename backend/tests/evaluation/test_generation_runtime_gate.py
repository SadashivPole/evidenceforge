"""Deterministic unit and integration tests for Phase 2E.3 Real Model Runtime Gate.

Covers:
1. runtime unavailable classification
2. model load failure classification
3. timeout classification
4. malformed output classification -> is_valid_schema=False
5. Pydantic schema failure classification -> is_valid_schema=False
6. schema-valid + citation-invalid -> is_valid_schema=True, is_citation_valid=False
7. schema-valid + status-invalid -> is_valid_schema=True, is_status_valid=False
8. successful validation path -> is_valid_schema=True, is_citation_valid=True
9. no persistence during evaluation
10. no autonomous approval during evaluation
11. repeated-run metadata and consistency
12. host environment discovery
13. deterministic harness latency labeled as harness execution latency
14. synthetic resource values not reported as real model measurements
15. model projection security and redaction
16. 5-case corpus execution across all benchmark categories
17. deterministic evaluation artifact serialization
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import uuid

import pytest
from sqlalchemy.orm import Session

from app.evaluation.generation import (
    GenerationBenchmarkCategory,
    create_sample_generation_benchmark,
)
from app.evaluation.runtime_gate import (
    CandidateMeasurementStatus,
    DeterministicRuntimeAdapter,
    EnvironmentDiscovery,
    EvaluationFailureType,
    EvaluationRawOutput,
    ModelCandidateSpec,
    _probe_linux_memory,
    _probe_windows_memory,
    discover_environment,
    get_runtime_candidate_registry,
    normalize_and_validate_runtime_output,
    probe_host_memory,
    run_runtime_gate_evaluation,
)
from app.questionnaires.generation.projection import (
    project_generation_context_to_model_input,
)
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseRevision,
)
from app.questionnaires.types import ResponseStatus


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


@pytest.fixture
def sample_spec() -> ModelCandidateSpec:
    return ModelCandidateSpec(
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
        measurement_status=CandidateMeasurementStatus.MEASURED,
    )


def test_01_runtime_unavailable_classification(sample_spec):
    """1. Verify runtime unavailable failure classification."""
    context, _ = _build_mock_context()
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="runtime_unavailable")
    with pytest.raises(RuntimeError, match="not installed or available"):
        asyncio.run(adapter.load_model())

    model_input = project_generation_context_to_model_input(context)
    raw = asyncio.run(adapter.generate_raw(model_input))
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.RUNTIME_UNAVAILABLE
    assert norm.draft_payload is None
    assert norm.validated_draft is None
    assert norm.is_valid_schema is False
    assert norm.is_citation_valid is False
    assert norm.is_status_valid is False
    assert "not available on host" in (norm.error_message or "")

    registry = get_runtime_candidate_registry()
    unavail = [
        c for c in registry if c.measurement_status == CandidateMeasurementStatus.UNAVAILABLE
    ]
    assert len(unavail) >= 5


def test_02_model_load_failure_classification(sample_spec):
    """2. Verify model load failure classification."""
    context, _ = _build_mock_context()
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="model_load_failure")
    with pytest.raises(RuntimeError, match="Failed to load model"):
        asyncio.run(adapter.load_model())

    model_input = project_generation_context_to_model_input(context)
    raw = asyncio.run(adapter.generate_raw(model_input))
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.MODEL_LOAD_FAILURE
    assert norm.draft_payload is None
    assert norm.validated_draft is None
    assert norm.is_valid_schema is False
    assert "weights into memory" in (norm.error_message or "")


def test_03_timeout_classification(sample_spec):
    """3. Verify timeout failure classification."""
    context, _ = _build_mock_context()
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="timeout")
    model_input = project_generation_context_to_model_input(context)
    raw = asyncio.run(adapter.generate_raw(model_input, timeout_seconds=2.0))
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.TIMEOUT
    assert norm.draft_payload is None
    assert norm.validated_draft is None
    assert norm.is_valid_schema is False
    assert "timed out" in (norm.error_message or "")


def test_04_malformed_json_schema_valid_false(sample_spec):
    """4. Verify malformed JSON classification -> is_valid_schema=False."""
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text="{'answer': 'unterminated json string",
        deterministic_harness_execution_latency_ms=10.0,
    )
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.MALFORMED_JSON
    assert norm.draft_payload is None
    assert norm.validated_draft is None
    assert norm.is_valid_schema is False
    assert norm.is_citation_valid is False
    assert norm.is_status_valid is False
    assert "JSON decoding failed" in (norm.error_message or "")


def test_05_pydantic_schema_failure_schema_valid_false(sample_spec):
    """5. Verify Pydantic schema validation failure -> is_valid_schema=False."""
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "Answer attempting invalid status",
                "status": "APPROVED",  # Disallowed for model proposal
                "citation_handles": ["EVIDENCE-1"],
            }
        ),
        deterministic_harness_execution_latency_ms=10.0,
    )
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.SCHEMA_VALIDATION_FAILURE
    assert norm.draft_payload is None
    assert norm.validated_draft is None
    assert norm.is_valid_schema is False
    assert norm.is_citation_valid is False
    assert norm.is_status_valid is False


def test_06_schema_valid_and_citation_invalid(sample_spec):
    """6. Verify schema-valid + citation-invalid status."""
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "Answer referencing non-existent citation",
                "status": "PROPOSED",
                "citation_handles": ["EVIDENCE-99"],
            }
        ),
        deterministic_harness_execution_latency_ms=10.0,
    )
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.CITATION_VALIDATION_FAILURE
    assert norm.draft_payload is not None
    assert norm.validated_draft is None
    # Crucial distinction: JSON is valid schema, but citation is invalid
    assert norm.is_valid_schema is True
    assert norm.is_citation_valid is False
    assert "does not exist in the authorized evidence context" in (norm.error_message or "")


def test_07_schema_valid_and_status_invalid(sample_spec):
    """7. Verify schema-valid + status-invalid -> is_valid_schema=True, is_status_valid=False."""
    # Context with empty evidence where PROPOSED is forbidden by server status guards
    empty_context = GenerationContext(
        question_id=uuid.uuid4(),
        questionnaire_id=uuid.uuid4(),
        questionnaire_version_id=uuid.uuid4(),
        questionnaire_version_question_id=uuid.uuid4(),
        question_text="Is MFA enforced?",
        section_path=(),
        authorized_workspace_id=uuid.uuid4(),
        evidence_context=(),
        total_evidence_chunks=0,
        total_evidence_tokens=0,
        search_version="v1",
        selection_version="v1",
        generation_boundary_version="v1",
    )
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "Answer proposing PROPOSED on empty context without citations",
                "status": "PROPOSED",
                "citation_handles": [],
            }
        ),
        deterministic_harness_execution_latency_ms=10.0,
    )
    norm = normalize_and_validate_runtime_output(empty_context, raw)

    assert norm.failure_type == EvaluationFailureType.STATUS_VALIDATION_FAILURE
    assert norm.draft_payload is not None
    assert norm.validated_draft is None
    # Schema was valid, but status guard failed
    assert norm.is_valid_schema is True
    assert norm.is_status_valid is False


def test_08_successful_validation_path(sample_spec):
    """8. Verify successful validation path -> all booleans True."""
    context, _ = _build_mock_context()
    raw = EvaluationRawOutput(
        raw_text=json.dumps(
            {
                "answer": "MFA is strictly enforced across all corporate systems.",
                "status": "PROPOSED",
                "citation_handles": ["EVIDENCE-1"],
                "uncertainty_notes": None,
            }
        ),
        deterministic_harness_execution_latency_ms=15.0,
    )
    norm = normalize_and_validate_runtime_output(context, raw)

    assert norm.failure_type == EvaluationFailureType.SUCCESS
    assert norm.draft_payload is not None
    assert norm.validated_draft is not None
    assert norm.is_valid_schema is True
    assert norm.is_citation_valid is True
    assert norm.is_status_valid is True
    assert norm.validated_draft.validation_passed is True
    assert norm.validated_draft.status == ResponseStatus.PROPOSED
    assert norm.validated_draft.citation_handles == ("EVIDENCE-1",)


def test_09_no_persistence_during_evaluation(db_session: Session, sample_spec):
    """9. Verify evaluation harness performs zero database mutations."""
    init_resp_count = db_session.query(QuestionnaireResponse).count()
    init_rev_count = db_session.query(QuestionnaireResponseRevision).count()

    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, runs_count=2, base_seed=42))

    db_session.expire_all()
    final_resp_count = db_session.query(QuestionnaireResponse).count()
    final_rev_count = db_session.query(QuestionnaireResponseRevision).count()

    assert final_resp_count == init_resp_count
    assert final_rev_count == init_rev_count
    assert artifact.summary_report.total_evaluations == 10


def test_10_no_approval_during_evaluation(sample_spec):
    """10. Verify model output is never autonomously approved."""
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, runs_count=2, base_seed=42))

    for rec in artifact.summary_report.case_records:
        if rec.normalized_payload:
            assert rec.normalized_payload["status"] != "APPROVED"
            assert rec.normalized_payload["status"] in ("PROPOSED", "INSUFFICIENT_EVIDENCE")


def test_11_repeated_run_metadata(sample_spec):
    """11. Verify repeated run metadata collection and consistency calculation."""
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, runs_count=3, base_seed=100))

    assert artifact.summary_report.total_runs == 3
    assert artifact.summary_report.cases_per_run == 5
    assert artifact.summary_report.total_evaluations == 15
    assert artifact.summary_report.run_consistency_rate == 1.0
    assert artifact.metadata["base_seed"] == 100
    assert artifact.summary_report.deterministic_harness_execution_latency_ms >= 0.0


def test_12_environment_discovery():
    """12. Verify host environment discovery probes."""
    env = discover_environment()

    assert isinstance(env, EnvironmentDiscovery)
    assert env.cpu_count >= 1
    assert env.ram_total_gb > 0.0
    assert isinstance(env.docker_available, bool)
    assert isinstance(env.runtimes_available, dict)
    assert env.disk_total_gb > 0.0


def test_12b_windows_ram_discovery_mocked(monkeypatch):
    """12b. Verify Windows RAM discovery with mocked GlobalMemoryStatusEx converts bytes -> GB."""
    import ctypes

    class DummyKernel32:
        def GlobalMemoryStatusEx(self, stat_ptr):
            # Simulate 16GB total, 8.5GB available
            target = getattr(stat_ptr, "_obj", stat_ptr)
            target.ullTotalPhys = int(16.0 * (1024**3))
            target.ullAvailPhys = int(8.5 * (1024**3))
            return 1

    class DummyWinDLL:
        kernel32 = DummyKernel32()

    monkeypatch.setattr(ctypes, "windll", DummyWinDLL(), raising=False)
    monkeypatch.setattr("sys.platform", "win32")

    win_mem = _probe_windows_memory()
    assert win_mem is not None
    assert win_mem[0] == 16.0
    assert win_mem[1] == 8.5

    # Verify probe_host_memory uses it
    total, avail = probe_host_memory()
    assert total == 16.0
    assert avail == 8.5


def test_12c_linux_meminfo_parsing(monkeypatch, tmp_path):
    """12c. Verify Linux /proc/meminfo parsing."""
    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text(
        "MemTotal:       32800000 kB\nMemFree:         4100000 kB\nMemAvailable:   16400000 kB\n"
    )

    # Disable windows probe
    monkeypatch.setattr("app.evaluation.runtime_gate._probe_windows_memory", lambda: None)
    monkeypatch.setattr(
        "os.path.exists",
        lambda path: path == "/proc/meminfo" or os.path.exists(path),
    )

    # Mock open specifically for /proc/meminfo
    real_open = open

    def fake_open(file, *args, **kwargs):
        if file == "/proc/meminfo":
            return real_open(fake_meminfo, *args, **kwargs)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    linux_mem = _probe_linux_memory()
    assert linux_mem is not None
    # 32800000 kB / 1024^2 = 31.28 GB; 16400000 kB / 1024^2 = 15.64 GB
    assert linux_mem[0] == 31.28
    assert linux_mem[1] == 15.64


def test_12d_failed_ram_probe_returns_explicit_zero_without_fabrication(monkeypatch):
    """12d. Verify unsupported/failed probe returns 0.0 with zero synthetic fabrication."""
    monkeypatch.setattr("app.evaluation.runtime_gate._probe_windows_memory", lambda: None)
    monkeypatch.setattr("app.evaluation.runtime_gate._probe_linux_memory", lambda: None)
    monkeypatch.setattr("sys.modules", {k: v for k, v in sys.modules.items() if k != "psutil"})

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    total, avail = probe_host_memory()
    assert total == 0.0
    assert avail == 0.0


def test_13_deterministic_harness_latency_labeling(sample_spec):
    """13. Verify harness execution latency is explicitly labeled as harness latency."""
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, runs_count=1, base_seed=42))

    summary = artifact.summary_report
    assert hasattr(summary, "deterministic_harness_execution_latency_ms")
    assert summary.deterministic_harness_execution_latency_ms >= 0.0
    assert summary.min_harness_latency_ms >= 0.0
    assert summary.max_harness_latency_ms >= 0.0


def test_14_synthetic_resource_values_not_reported_as_real_measurements(sample_spec):
    """14. Verify synthetic resource values are not reported as real model measurements."""
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, runs_count=1, base_seed=42))

    summary = artifact.summary_report

    # Real model resource metrics must be None or NOT MEASURED
    assert summary.mean_cpu_utilization_pct is None
    assert summary.peak_ram_mb is None
    assert summary.peak_vram_mb is None

    assert summary.real_model_ttft_status == "NOT MEASURED"
    assert summary.real_model_throughput_status == "NOT MEASURED"
    assert summary.real_model_cpu_status == "NOT MEASURED"
    assert summary.real_model_ram_status == "NOT MEASURED"
    assert summary.real_model_vram_status == "NOT MEASURED"
    assert summary.real_model_load_latency_status == "NOT MEASURED"


def test_15_model_projection_security():
    """15. Verify model projection redacts internal UUIDs, DB credentials, and tools."""
    context, _ = _build_mock_context()
    model_input = project_generation_context_to_model_input(context)

    # Validate visible projection
    assert model_input.question_text == context.question_text
    assert len(model_input.evidence_items) == 1
    assert model_input.evidence_items[0].citation_handle == "EVIDENCE-1"

    # Validate that ModelGenerationInput does NOT carry raw DB ids or sensitive credentials
    input_dict = dataclasses.asdict(model_input)
    assert "workspace_id" not in input_dict
    assert "db_credentials" not in input_dict
    assert "tools" not in input_dict
    assert "sql" not in input_dict
    assert "auth" not in input_dict

    # Check evidence item projection
    item_dict = input_dict["evidence_items"][0]
    assert "chunk_id" not in item_dict
    assert "document_id" not in item_dict
    assert item_dict["citation_handle"] == "EVIDENCE-1"


def test_16_five_case_corpus_execution(sample_spec):
    """16. Verify all 5 benchmark categories are evaluated with expected behavior."""
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    cases = create_sample_generation_benchmark()
    assert len(cases) == 5

    categories = {c.category for c in cases}
    assert categories == {
        GenerationBenchmarkCategory.SUPPORTED,
        GenerationBenchmarkCategory.AMBIGUOUS,
        GenerationBenchmarkCategory.INSUFFICIENT_EVIDENCE,
        GenerationBenchmarkCategory.CONFLICTING_STALE,
        GenerationBenchmarkCategory.MALICIOUS_INJECTED,
    }

    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, test_cases=cases, runs_count=1))
    summary = artifact.summary_report

    assert summary.output_schema_validity_rate == 1.0
    assert summary.fixture_citation_handle_safety_rate == 1.0
    assert summary.fixture_abstention_accuracy == 1.0
    assert summary.fixture_injection_resistance_rate == 1.0
    assert summary.fixture_keyword_support_rate == 1.0


def test_17_deterministic_evaluation_artifact_serialization(sample_spec):
    """17. Verify deterministic JSON serialization of the runtime evaluation artifact."""
    adapter = DeterministicRuntimeAdapter(sample_spec, behavior_mode="ideal")
    artifact = asyncio.run(run_runtime_gate_evaluation(adapter, runs_count=1, base_seed=42))

    json_str = artifact.to_json(indent=2)
    assert isinstance(json_str, str)

    parsed = json.loads(json_str)
    assert "metadata" in parsed
    assert "environment" in parsed
    assert "candidate_spec" in parsed
    assert "summary_report" in parsed
    assert "human_grounding_items" in parsed

    assert parsed["metadata"]["status"] == "MEASURED"
    assert parsed["candidate_spec"]["model_id"] == "deterministic-eval-harness"
    assert parsed["summary_report"]["total_evaluations"] == 5

    # Verify resource fields in summary report
    summary_dict = parsed["summary_report"]
    assert summary_dict["real_model_ttft_status"] == "NOT MEASURED"
    assert summary_dict["real_model_cpu_status"] == "NOT MEASURED"

    # Verify human grounding review placeholder
    human_items = parsed["human_grounding_items"]
    assert len(human_items) == 5
    for item in human_items:
        assert item["semantic_groundedness_review"] == "NOT MEASURED - Requires Human Adjudication"
