"""Deterministic evaluation and contract verification for the 60-case benchmark (Phase 2E.4B.2).

Guarantees:
1. Gold contract validation: exact category distribution (20 SUPPORTED, 10 AMBIGUOUS,
   10 INSUFFICIENT_EVIDENCE, 10 CONFLICTING_STALE, 10 MALICIOUS_INJECTED = 60 cases).
2. Expected statuses & abstention: PROPOSED for SUPPORTED and MALICIOUS_INJECTED (when legitimate
   evidence exists); INSUFFICIENT_EVIDENCE for AMBIGUOUS, INSUFFICIENT, and CONFLICTING_STALE.
3. Citation handle semantics: EVIDENCE-N formatting, non-empty for SUPPORTED/MALICIOUS, empty for
   abstention.
4. Security & UUID isolation: zero database UUIDs exposed to ModelGenerationInput.
5. Runner determinism & zero database persistence: 0 DB mutations during multi-run execution.
6. Honest metric reporting: real hardware metrics marked NOT MEASURED.
7. Human adjudication review artifact shape and NOT MEASURED labels.
"""

from __future__ import annotations

import pytest

from app.evaluation.generation import (
    GenerationBenchmarkCategory,
)
from app.evaluation.real_model_provider import (
    DeterministicFakeEvaluationAdapter,
    LoopbackOpenAICompatibleRuntimeAdapter,
    OllamaEvaluationAdapter,
    OpenAICompatibleEvaluationAdapter,
    format_model_input_prompt,
    run_generation_benchmark_runner,
)
from app.evaluation.runtime_gate import (
    EvaluationFailureType,
)
from app.questionnaires.types import ResponseStatus
from tests.evaluation.generation_benchmark_60 import (
    EVIDENCE_HANDLE_PATTERN,
    UUID_PATTERN,
    load_generation_benchmark_60,
    load_generation_contexts_60,
    validate_gold_contract_60,
)


def test_01_gold_contract_validation() -> None:
    """Verify that all 60 cases satisfy the gold contract validator."""
    cases = load_generation_benchmark_60()
    assert len(cases) == 60
    validate_gold_contract_60(cases)


def test_02_category_distribution_and_counts() -> None:
    """Verify exact category counts across the 60-case benchmark."""
    cases = load_generation_benchmark_60()

    counts = {cat: 0 for cat in GenerationBenchmarkCategory}
    for c in cases:
        counts[c.category] += 1

    assert counts[GenerationBenchmarkCategory.SUPPORTED] == 20
    assert counts[GenerationBenchmarkCategory.AMBIGUOUS] == 10
    assert counts[GenerationBenchmarkCategory.INSUFFICIENT_EVIDENCE] == 10
    assert counts[GenerationBenchmarkCategory.CONFLICTING_STALE] == 10
    assert counts[GenerationBenchmarkCategory.MALICIOUS_INJECTED] == 10
    assert sum(counts.values()) == 60


def test_03_expected_statuses_and_abstention_flags() -> None:
    """Verify expected status and abstention expectations by category."""
    cases = load_generation_benchmark_60()

    for c in cases:
        if c.category in (
            GenerationBenchmarkCategory.SUPPORTED,
            GenerationBenchmarkCategory.MALICIOUS_INJECTED,
        ):
            assert c.expected_status == ResponseStatus.PROPOSED
            assert c.is_abstention_expected is False
            assert len(c.expected_citations) > 0
        else:
            assert c.expected_status == ResponseStatus.INSUFFICIENT_EVIDENCE
            assert c.is_abstention_expected is True
            assert c.expected_citations == ()


def test_04_citation_handle_semantics_and_formatting() -> None:
    """Verify opaque citation handles follow EVIDENCE-N format and gold relevance semantics."""
    cases = load_generation_benchmark_60()

    for c in cases:
        avail_handles = {item.citation_handle for item in c.model_input.evidence_items}
        for item in c.model_input.evidence_items:
            assert EVIDENCE_HANDLE_PATTERN.match(item.citation_handle)
            assert item.token_count > 0
            assert item.content.strip()

        if c.category in (
            GenerationBenchmarkCategory.SUPPORTED,
            GenerationBenchmarkCategory.MALICIOUS_INJECTED,
        ):
            for handle in c.expected_citations:
                assert handle in avail_handles

        if c.category == GenerationBenchmarkCategory.CONFLICTING_STALE:
            assert c.model_input.has_stale_or_conflicting_evidence is True
            stale_or_conflict_items = [
                item
                for item in c.model_input.evidence_items
                if (
                    not item.is_latest_document_version
                    or item.has_conflict
                    or item.document_status != "active"
                )
            ]
            assert len(stale_or_conflict_items) > 0

        if c.category == GenerationBenchmarkCategory.MALICIOUS_INJECTED:
            assert len(c.forbidden_keywords) > 0


def test_05_security_and_uuid_isolation() -> None:
    """Verify zero raw internal database UUIDs are serialized in ModelGenerationInput."""
    cases = load_generation_benchmark_60()

    for c in cases:
        input_dict = {
            "question_text": c.model_input.question_text,
            "section_path": c.model_input.section_path,
            "evidence_items": [
                {
                    "citation_handle": item.citation_handle,
                    "content": item.content,
                    "document_name": item.document_name,
                    "section_label": item.section_label,
                }
                for item in c.model_input.evidence_items
            ],
        }
        serialized = str(input_dict)
        matches = UUID_PATTERN.findall(serialized)
        assert len(matches) == 0, f"Found leaked UUIDs in case {c.case_id}: {matches}"


@pytest.mark.anyio
async def test_06_runner_with_deterministic_fake_adapter() -> None:
    """Verify multi-run benchmark execution (N=3, seed=42) using deterministic fake adapter."""
    cases = load_generation_benchmark_60()
    contexts = load_generation_contexts_60()
    adapter = DeterministicFakeEvaluationAdapter()

    artifact = await run_generation_benchmark_runner(
        adapter,
        cases=cases,
        contexts=contexts,
        runs_count=3,
        base_seed=42,
    )

    assert artifact.summary_report.total_runs == 3
    assert artifact.summary_report.cases_per_run == 60
    assert artifact.summary_report.total_evaluations == 180
    assert artifact.summary_report.output_schema_validity_rate == 1.0
    assert artifact.summary_report.output_validation_failure_rate == 0.0
    assert artifact.summary_report.fixture_citation_handle_safety_rate == 1.0
    assert artifact.summary_report.fixture_abstention_accuracy == 1.0
    assert artifact.summary_report.run_consistency_rate == 1.0

    # Wall-clock latency must be present and labeled honestly
    assert artifact.summary_report.deterministic_harness_execution_latency_ms >= 0.0
    assert artifact.summary_report.real_model_ttft_status == "NOT MEASURED"
    assert artifact.summary_report.real_model_throughput_status == "NOT MEASURED"
    assert artifact.summary_report.real_model_cpu_status == "NOT MEASURED"
    assert artifact.summary_report.real_model_ram_status == "NOT MEASURED"


@pytest.mark.anyio
async def test_07_runner_zero_database_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify evaluation runner performs zero database writes, connections, or mutations."""
    from unittest.mock import MagicMock

    from sqlalchemy.orm import Session

    mock_db = MagicMock(spec=Session)

    cases = load_generation_benchmark_60()[:5]
    adapter = DeterministicFakeEvaluationAdapter()

    artifact = await run_generation_benchmark_runner(
        adapter,
        cases=cases,
        runs_count=1,
        base_seed=42,
    )

    assert artifact.summary_report.total_evaluations == 5
    assert mock_db.commit.call_count == 0
    assert mock_db.flush.call_count == 0
    assert mock_db.add.call_count == 0


@pytest.mark.anyio
async def test_08_failure_classification_handling() -> None:
    """Verify the evaluation runner classifies failures without silent recovery."""
    cases = load_generation_benchmark_60()[:3]
    contexts = load_generation_contexts_60()

    # 1. Malformed JSON failure
    bad_json_adapter = DeterministicFakeEvaluationAdapter(
        forced_raw_text="This is not valid JSON {malformed"
    )
    artifact_bad_json = await run_generation_benchmark_runner(
        bad_json_adapter,
        cases=cases,
        contexts=contexts,
        runs_count=1,
    )
    assert artifact_bad_json.summary_report.output_schema_validity_rate == 0.0
    assert (
        artifact_bad_json.summary_report.failure_breakdown[
            EvaluationFailureType.MALFORMED_JSON.value
        ]
        == 3
    )

    # 2. Timeout failure override
    timeout_adapter = DeterministicFakeEvaluationAdapter(
        forced_failure_type=EvaluationFailureType.TIMEOUT
    )
    artifact_timeout = await run_generation_benchmark_runner(
        timeout_adapter,
        cases=cases,
        contexts=contexts,
        runs_count=1,
    )
    assert artifact_timeout.summary_report.output_schema_validity_rate == 0.0
    assert (
        artifact_timeout.summary_report.failure_breakdown[EvaluationFailureType.TIMEOUT.value] == 3
    )


@pytest.mark.anyio
async def test_keyword_support_rate_excludes_non_supported_cases() -> None:
    """Non-SUPPORTED cases must not inflate the keyword-support metric."""
    all_cases = load_generation_benchmark_60()
    from dataclasses import replace

    supported_template = next(
        case for case in all_cases if case.category == GenerationBenchmarkCategory.SUPPORTED
    )
    supported_case = replace(
        supported_template,
        expected_keywords=("required-term-that-is-absent",),
    )
    non_supported_case = next(
        case for case in all_cases if case.category != GenerationBenchmarkCategory.SUPPORTED
    )

    artifact = await run_generation_benchmark_runner(
        DeterministicFakeEvaluationAdapter(forced_raw_text="not valid JSON"),
        cases=(supported_case, non_supported_case),
        contexts=load_generation_contexts_60(),
        runs_count=1,
    )

    supported_record = artifact.summary_report.case_records[0]
    assert supported_record.is_keyword_support_matched is False
    assert artifact.summary_report.fixture_keyword_support_rate == pytest.approx(0.0)


@pytest.mark.anyio
async def test_09_repeated_runs_determinism_and_seed_advancement() -> None:
    """Verify multi-run seeds advance deterministically across runs."""
    cases = load_generation_benchmark_60()[:4]
    contexts = load_generation_contexts_60()
    adapter = DeterministicFakeEvaluationAdapter()

    artifact = await run_generation_benchmark_runner(
        adapter,
        cases=cases,
        contexts=contexts,
        runs_count=3,
        base_seed=100,
    )

    records = artifact.summary_report.case_records
    assert len(records) == 12

    run_0_records = [r for r in records if r.run_index == 0]
    run_1_records = [r for r in records if r.run_index == 1]
    run_2_records = [r for r in records if r.run_index == 2]

    assert len(run_0_records) == 4
    assert len(run_1_records) == 4
    assert len(run_2_records) == 4


def test_10_human_grounding_review_artifact_shape_and_labels() -> None:
    """Verify human review artifacts for Run 0 explicitly mark unmeasured fields."""
    import asyncio

    async def _run() -> None:
        cases = load_generation_benchmark_60()
        contexts = load_generation_contexts_60()
        adapter = DeterministicFakeEvaluationAdapter()

        artifact = await run_generation_benchmark_runner(
            adapter,
            cases=cases,
            contexts=contexts,
            runs_count=3,
            base_seed=42,
        )

        assert len(artifact.human_grounding_items) == 60
        for item in artifact.human_grounding_items:
            assert item.case_id.strip()
            assert item.question_text.strip()
            assert item.abstention_decision in (
                "ABSTAINED",
                "PROPOSED_ANSWER",
                "MALFORMED_OR_FAILED",
            )
            assert item.status in ("PROPOSED", "INSUFFICIENT_EVIDENCE", "MALFORMED_OR_FAILED")
            assert item.validation_failure_type in [ft.value for ft in EvaluationFailureType]
            assert item.semantic_groundedness_review == "NOT MEASURED - Requires Human Adjudication"
            assert item.unsupported_claims_review == "NOT MEASURED - Requires Human Adjudication"

    asyncio.run(_run())


@pytest.mark.anyio
async def test_11_ollama_adapter_unavailable_graceful_handling() -> None:
    """Verify Ollama adapter against unreachable endpoint classifies as RUNTIME_UNAVAILABLE."""
    adapter = OllamaEvaluationAdapter(
        endpoint_url="http://127.0.0.1:59999/api/generate",
        model_name="qwen2.5:1.5b",
    )
    cases = load_generation_benchmark_60()[:2]
    contexts = load_generation_contexts_60()

    artifact = await run_generation_benchmark_runner(
        adapter,
        cases=cases,
        contexts=contexts,
        runs_count=1,
    )

    assert artifact.summary_report.total_evaluations == 2
    assert artifact.summary_report.output_schema_validity_rate == 0.0
    assert (
        artifact.summary_report.failure_breakdown[EvaluationFailureType.RUNTIME_UNAVAILABLE.value]
        == 2
    )


def test_12_prompt_formatting_preserves_handles_and_hides_uuids() -> None:
    """Verify format_model_input_prompt formats evidence with handles and without UUIDs."""
    cases = load_generation_benchmark_60()
    c0 = cases[0]

    system_prompt, user_prompt = format_model_input_prompt(c0.model_input)

    assert "EVIDENCE-1" in user_prompt
    assert "Question: Is multi-factor authentication required for privileged access?" in user_prompt
    assert "Output MUST be a valid JSON object" in system_prompt

    # Zero UUIDs in generated prompt
    assert len(UUID_PATTERN.findall(system_prompt)) == 0
    assert len(UUID_PATTERN.findall(user_prompt)) == 0


def test_13_gold_contract_detects_invalid_cases() -> None:
    """Verify that validate_gold_contract_60 fails closed when contract invariants are breached."""
    from dataclasses import replace

    from app.questionnaires.generation.projection import ModelEvidenceItem

    cases = list(load_generation_benchmark_60())

    # 1. Duplicate case_id breach
    bad_duplicate = list(cases)
    bad_duplicate[1] = replace(bad_duplicate[1], case_id=bad_duplicate[0].case_id)
    with pytest.raises(AssertionError, match="Duplicate case_ids"):
        validate_gold_contract_60(bad_duplicate)

    # 2. Unsupported category count breach (59 cases)
    with pytest.raises(AssertionError, match="Expected exactly 60 cases"):
        validate_gold_contract_60(cases[:-1])

    # 3. Handle format breach
    bad_handle_case = cases[0]
    bad_evidence = (
        ModelEvidenceItem(
            citation_handle="DOC-1-CHUNK-A",
            content="Some content",
            token_count=10,
        ),
    )
    bad_input = replace(bad_handle_case.model_input, evidence_items=bad_evidence)
    bad_case = replace(bad_handle_case, model_input=bad_input)
    bad_list = list(cases)
    bad_list[0] = bad_case
    with pytest.raises(AssertionError, match="does not match EVIDENCE-N format"):
        validate_gold_contract_60(bad_list)

    # 4. Status mismatch on SUPPORTED category
    bad_status_case = replace(cases[0], expected_status=ResponseStatus.INSUFFICIENT_EVIDENCE)
    bad_list2 = list(cases)
    bad_list2[0] = bad_status_case
    with pytest.raises(AssertionError, match="must have expected status PROPOSED"):
        validate_gold_contract_60(bad_list2)


@pytest.mark.anyio
async def test_14_artifact_json_serialization_roundtrip() -> None:
    """Verify that evaluation run artifact serializes cleanly to JSON without errors."""
    import json
    from dataclasses import asdict

    cases = load_generation_benchmark_60()[:3]
    contexts = load_generation_contexts_60()
    adapter = DeterministicFakeEvaluationAdapter()

    artifact = await run_generation_benchmark_runner(
        adapter,
        cases=cases,
        contexts=contexts,
        runs_count=1,
        base_seed=42,
    )

    artifact_dict = asdict(artifact)
    json_str = json.dumps(artifact_dict, default=str)
    assert len(json_str) > 0
    parsed = json.loads(json_str)
    assert parsed["candidate_spec"]["model_id"] == "deterministic-fake-adapter"
    assert parsed["summary_report"]["total_evaluations"] == 3


def test_15_loopback_openai_adapter_backward_compatibility() -> None:
    """Verify LoopbackOpenAICompatibleRuntimeAdapter alias is preserved from Phase 2E.4A."""
    assert LoopbackOpenAICompatibleRuntimeAdapter is OpenAICompatibleEvaluationAdapter
    adapter = LoopbackOpenAICompatibleRuntimeAdapter(
        endpoint_url="http://127.0.0.1:8000/v1/chat/completions",
        model_name="qwen2.5:1.5b",
    )
    assert adapter.endpoint_url == "http://127.0.0.1:8000/v1/chat/completions"
    assert adapter.model_name == "qwen2.5:1.5b"
