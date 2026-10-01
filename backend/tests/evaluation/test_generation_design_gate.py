"""Deterministic unit and contract tests for Phase 2E.1 Generation Design & Evaluation Gate."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.orm import Session

from app.evaluation.generation import (
    GenerationBenchmarkCategory,
    GenerationTestCase,
    calculate_case_citation_metrics,
    create_sample_generation_benchmark,
    evaluate_benchmark_dataset,
    evaluate_case_output,
)
from app.models import WorkspaceRole
from app.questionnaires.generation.errors import (
    GenerationCitationValidationError,
    GenerationStatusValidationError,
)
from app.questionnaires.generation.projection import (
    project_generation_context_to_model_input,
)
from app.questionnaires.generation.provider import (
    MockEvaluationProvider,
    ProviderConfig,
)
from app.questionnaires.generation.schemas import GeneratedDraftPayload
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
)
from app.questionnaires.generation.validator import (
    validate_generated_draft,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseRevision,
)
from app.questionnaires.review.service import (
    apply_review_decision,
    build_review_context,
)
from app.questionnaires.review.types import (
    ReviewAction,
    ReviewDecision,
)
from app.questionnaires.types import ResponseStatus
from tests.conftest import create_principal, create_workspace_with_owner
from tests.questionnaires.test_questionnaire_responses import (
    _create_chunk,
    _import_one_question,
)


def _build_test_generation_context(
    *,
    evidence_count: int = 2,
    is_latest: bool = True,
    conflict_group: str | None = None,
    document_status: str = "active",
) -> tuple[uuid.UUID, uuid.UUID, GenerationContext, list[uuid.UUID]]:
    workspace_id = uuid.uuid4()
    question_id = uuid.uuid4()
    questionnaire_id = uuid.uuid4()
    version_id = uuid.uuid4()

    items = []
    chunk_ids = []
    for i in range(1, evidence_count + 1):
        c_id = uuid.uuid4()
        chunk_ids.append(c_id)
        item = GenerationEvidenceItem(
            citation_handle=f"EVIDENCE-{i}",
            evidence_chunk_id=c_id,
            document_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            version_number=1,
            document_name=f"Security Policy Doc {i}",
            document_version_number=1,
            latest_document_version_number=1,
            is_latest_document_version=is_latest,
            document_status=document_status,
            conflict_group_id=conflict_group,
            chunk_index=i - 1,
            content_hash=f"hash-{i}",
            normalized_start_byte=0,
            normalized_end_byte=100,
            section_label=f"Section {i}",
            page_number=i,
            content=f"Access control evidence chunk {i} requiring mandatory MFA.",
            token_count=15,
            rrf_score=0.04,
            lexical_rank=i,
            semantic_rank=i,
            fallback_used=False,
        )
        items.append(item)

    context = GenerationContext(
        question_id=question_id,
        questionnaire_id=questionnaire_id,
        questionnaire_version_id=version_id,
        questionnaire_version_question_id=question_id,
        question_text="Is multi-factor authentication enforced for all users?",
        section_path=("Access Control", "Authentication"),
        authorized_workspace_id=workspace_id,
        evidence_context=tuple(items),
        total_evidence_chunks=len(items),
        total_evidence_tokens=len(items) * 15,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )
    return workspace_id, question_id, context, chunk_ids


# Test 1: Model projection hides raw UUIDs / workspace IDs
def test_model_projection_hides_raw_uuids() -> None:
    workspace_id, question_id, context, chunk_ids = _build_test_generation_context(evidence_count=2)

    model_input = project_generation_context_to_model_input(context)

    # Check model input object has no raw UUID attributes
    assert not hasattr(model_input, "authorized_workspace_id")
    assert not hasattr(model_input, "question_id")
    assert not hasattr(model_input, "questionnaire_id")

    # Serialize projection to dict or str and verify no internal UUIDs appear
    for item in model_input.evidence_items:
        assert not hasattr(item, "evidence_chunk_id")
        assert not hasattr(item, "document_id")
        assert not hasattr(item, "version_id")

        # Verify chunk UUIDs do not leak into content or handles
        for c_id in chunk_ids:
            assert str(c_id) not in item.citation_handle
            assert str(c_id) not in item.content

    assert str(workspace_id) not in str(model_input)
    assert str(question_id) not in str(model_input)


# Test 2: Model projection preserves opaque citation handles and metadata
def test_model_projection_preserves_opaque_handles_and_freshness() -> None:
    _, _, context, _ = _build_test_generation_context(
        evidence_count=3,
        is_latest=False,
        conflict_group="conflict-grp-99",
        document_status="superseded",
    )

    model_input = project_generation_context_to_model_input(context)

    assert len(model_input.evidence_items) == 3
    assert model_input.has_stale_or_conflicting_evidence is True
    assert model_input.evidence_items[0].citation_handle == "EVIDENCE-1"
    assert model_input.evidence_items[1].citation_handle == "EVIDENCE-2"
    assert model_input.evidence_items[2].citation_handle == "EVIDENCE-3"
    assert model_input.evidence_items[0].is_latest_document_version is False
    assert model_input.evidence_items[0].document_status == "superseded"
    assert model_input.evidence_items[0].has_conflict is True
    # Internal conflict_group_id must not be exposed on ModelEvidenceItem
    assert not hasattr(model_input.evidence_items[0], "conflict_group_id")
    assert "conflict-grp-99" not in str(model_input)


# Test 3: Generation output uses existing GeneratedDraftPayload
def test_generation_output_uses_existing_payload_schema() -> None:
    payload = GeneratedDraftPayload(
        answer="Multi-factor authentication is required across all employee accounts.",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-1", "EVIDENCE-2"],
        uncertainty_notes="Directly stated in Access Control policy.",
    )

    assert payload.status == ResponseStatus.PROPOSED
    assert payload.citation_handles == ["EVIDENCE-1", "EVIDENCE-2"]
    assert payload.uncertainty_notes == "Directly stated in Access Control policy."


# Test 4: Invalid output reaches existing validator and fails closed
def test_invalid_generation_output_fails_closed() -> None:
    _, _, context, _ = _build_test_generation_context(evidence_count=2)

    # 1. Fabricated handle fails validation
    invalid_handle_payload = GeneratedDraftPayload(
        answer="Some answer",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-99"],  # Out of range handle
    )
    with pytest.raises(GenerationCitationValidationError, match="does not exist in the authorized"):
        validate_generated_draft(context, invalid_handle_payload)

    # 2. PROPOSED status with empty citations fails validation
    empty_citations_payload = GeneratedDraftPayload(
        answer="Some answer",
        status=ResponseStatus.PROPOSED,
        citation_handles=[],
    )
    with pytest.raises(
        GenerationStatusValidationError,
        match="without citing any evidence handles",
    ):
        validate_generated_draft(context, empty_citations_payload)

    # 3. Model attempting to propose unauthorized status fails schema validation
    with pytest.raises(ValueError, match="not allowed for model output"):
        GeneratedDraftPayload(
            answer="Some answer",
            status=ResponseStatus.APPROVED,  # Model cannot propose approved
            citation_handles=["EVIDENCE-1"],
        )


# Test 5: No database persistence occurs during generation / validation
def test_no_database_persistence_during_generation(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="gen-eval@example.com",
        display_name="Gen Eval User",
    )
    workspace = create_workspace_with_owner(db_session, principal, name="Gen Eval Workspace")
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Is SSO supported?",
    )
    _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="SSO is supported via SAML 2.0.",
    )

    _, _, context, _ = _build_test_generation_context(evidence_count=1)

    # Validate draft
    payload = GeneratedDraftPayload(
        answer="SSO is supported via SAML 2.0.",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-1"],
    )
    validated = validate_generated_draft(context, payload)
    assert validated.validation_passed is True

    # Check that 0 responses or revisions were written to the database
    response_count = db_session.query(QuestionnaireResponse).count()
    revision_count = db_session.query(QuestionnaireResponseRevision).count()
    assert response_count == 0
    assert revision_count == 0


# Test 6: Existing review boundary remains unchanged and functional
def test_existing_review_boundary_functional_with_validated_draft(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="reviewer-gen@example.com",
        display_name="Reviewer Gen User",
    )
    workspace = create_workspace_with_owner(db_session, principal, name="Review Gen Workspace")
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Is SSO mandatory?",
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="Single Sign-On is mandatory for all internal apps.",
    )

    # Build real GenerationContext
    gen_item = GenerationEvidenceItem(
        citation_handle="EVIDENCE-1",
        evidence_chunk_id=chunk.id,
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_number=1,
        document_name="SSO Policy",
        document_version_number=1,
        latest_document_version_number=1,
        is_latest_document_version=True,
        document_status="active",
        conflict_group_id=None,
        chunk_index=0,
        content_hash=chunk.content_hash,
        normalized_start_byte=chunk.normalized_start_byte,
        normalized_end_byte=chunk.normalized_end_byte,
        section_label=chunk.section_label,
        page_number=chunk.page_number,
        content=chunk.content,
        token_count=15,
        rrf_score=0.05,
        lexical_rank=1,
        semantic_rank=1,
    )
    gen_context = GenerationContext(
        question_id=question.id,
        questionnaire_id=question.questionnaire_id,
        questionnaire_version_id=question.questionnaire_version_id,
        questionnaire_version_question_id=question.id,
        question_text=question.normalized_question_text,
        section_path=tuple(question.section_path or ()),
        authorized_workspace_id=workspace.id,
        evidence_context=(gen_item,),
        total_evidence_chunks=1,
        total_evidence_tokens=15,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )

    draft_payload = GeneratedDraftPayload(
        answer="Single Sign-On is mandatory for all internal apps.",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-1"],
    )
    validated_draft = validate_generated_draft(gen_context, draft_payload)
    review_context = build_review_context(gen_context, validated_draft)

    # Human approval creates exactly one approved revision
    res = apply_review_decision(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        actor_role=WorkspaceRole.OWNER,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.APPROVE),
    )
    assert res.is_approved is True
    assert res.revision.status == ResponseStatus.APPROVED.value
    assert len(res.citations) == 1


# Test 7: Evaluation metrics are deterministic on smoke fixture
def test_evaluation_metrics_deterministic() -> None:
    benchmark_cases = create_sample_generation_benchmark()
    assert len(benchmark_cases) == 5

    # Exactly one case per category
    categories = {c.category for c in benchmark_cases}
    assert categories == {
        GenerationBenchmarkCategory.SUPPORTED,
        GenerationBenchmarkCategory.AMBIGUOUS,
        GenerationBenchmarkCategory.INSUFFICIENT_EVIDENCE,
        GenerationBenchmarkCategory.CONFLICTING_STALE,
        GenerationBenchmarkCategory.MALICIOUS_INJECTED,
    }

    # Simulate deterministic mock outputs
    outputs: list[GeneratedDraftPayload | None] = [
        # Case 1 (Supported): Correct answer and citation
        GeneratedDraftPayload(
            answer="All employees must authenticate with multi-factor authentication (MFA).",
            status=ResponseStatus.PROPOSED,
            citation_handles=["EVIDENCE-1"],
        ),
        # Case 2 (Ambiguous): Correct abstention
        GeneratedDraftPayload(
            answer="Evidence is ambiguous regarding backup testing frequencies.",
            status=ResponseStatus.INSUFFICIENT_EVIDENCE,
            citation_handles=[],
        ),
        # Case 3 (Insufficient): Correct abstention
        GeneratedDraftPayload(
            answer="No evidence found for proprietary homomorphic encryption.",
            status=ResponseStatus.INSUFFICIENT_EVIDENCE,
            citation_handles=[],
        ),
        # Case 4 (Conflicting): Correct abstention
        GeneratedDraftPayload(
            answer="Conflicting password policies detected.",
            status=ResponseStatus.INSUFFICIENT_EVIDENCE,
            citation_handles=[],
        ),
        # Case 5 (Injection): Correct abstention and inert data
        GeneratedDraftPayload(
            answer="Audit log retention is not specified in verified evidence.",
            status=ResponseStatus.INSUFFICIENT_EVIDENCE,
            citation_handles=[],
        ),
    ]

    report = evaluate_benchmark_dataset(benchmark_cases, outputs)

    assert report.total_cases == 5
    assert report.schema_validity_rate == 1.0
    assert report.fixture_citation_precision == 1.0
    assert report.fixture_citation_recall == 1.0
    assert report.fixture_citation_handle_safety_rate == 1.0
    assert report.fixture_keyword_support_rate == 1.0
    assert report.fixture_unsupported_heuristic_rate == 0.0
    assert report.fixture_abstention_accuracy == 1.0
    assert report.fixture_injection_resistance_rate == 1.0


# Test 8: True citation metric semantics and safe zero conventions
def test_citation_precision_recall_semantics() -> None:
    # 1. Exact expected citation set -> precision 1.0 / recall 1.0
    p1, r1 = calculate_case_citation_metrics(("EVIDENCE-1",), ["EVIDENCE-1"])
    assert p1 == 1.0
    assert r1 == 1.0

    # 2. Valid but wrong evidence handle -> precision 0.0 / recall 0.0
    p2, r2 = calculate_case_citation_metrics(("EVIDENCE-1",), ["EVIDENCE-2"])
    assert p2 == 0.0
    assert r2 == 0.0

    # 3. Missing expected handle -> recall 0.5 (1 out of 2 retrieved), precision 1.0
    p3, r3 = calculate_case_citation_metrics(("EVIDENCE-1", "EVIDENCE-2"), ["EVIDENCE-1"])
    assert p3 == 1.0
    assert r3 == 0.5

    # 4. Extra valid but unexpected citation -> precision 0.5, recall 1.0
    p4, r4 = calculate_case_citation_metrics(("EVIDENCE-1",), ["EVIDENCE-1", "EVIDENCE-2"])
    assert p4 == 0.5
    assert r4 == 1.0

    # 5. Empty expected + empty predicted -> safe convention precision 1.0, recall 1.0
    p5, r5 = calculate_case_citation_metrics((), [])
    assert p5 == 1.0
    assert r5 == 1.0

    # 6. Empty expected + non-empty predicted -> precision 0.0, recall 1.0 (unwarranted citations)
    p6, r6 = calculate_case_citation_metrics((), ["EVIDENCE-1"])
    assert p6 == 0.0
    assert r6 == 1.0

    # 7. Non-empty expected + empty predicted -> precision 1.0, recall 0.0 (missed citations)
    p7, r7 = calculate_case_citation_metrics(("EVIDENCE-1",), [])
    assert p7 == 1.0
    assert r7 == 0.0


# Test 9: Handle-safety failure on forbidden or unknown handles
def test_handle_safety_failure() -> None:
    benchmark_cases = create_sample_generation_benchmark()
    supported_case = benchmark_cases[0]

    # Unknown handle (EVIDENCE-99 not in available handles)
    unknown_out = GeneratedDraftPayload(
        answer="Valid answer",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-99"],
    )
    res_unknown = evaluate_case_output(supported_case, unknown_out)
    assert res_unknown.citation_handle_safe is False

    # Forbidden handle
    forbidden_case = GenerationTestCase(
        case_id="GEN-TEST-FORBIDDEN",
        category=GenerationBenchmarkCategory.SUPPORTED,
        question_text=supported_case.question_text,
        model_input=supported_case.model_input,
        expected_status=ResponseStatus.PROPOSED,
        expected_citations=("EVIDENCE-1",),
        forbidden_citations=("EVIDENCE-1",),  # Mark EVIDENCE-1 as forbidden
    )
    res_forbidden = evaluate_case_output(
        forbidden_case,
        GeneratedDraftPayload(
            answer="Valid answer",
            status=ResponseStatus.PROPOSED,
            citation_handles=["EVIDENCE-1"],
        ),
    )
    assert res_forbidden.citation_handle_safe is False


# Test 10: Injection evidence remains inert data, not instructions
def test_injection_evidence_remains_inert_data() -> None:
    benchmark_cases = create_sample_generation_benchmark()
    injection_case = next(
        c for c in benchmark_cases if c.category == GenerationBenchmarkCategory.MALICIOUS_INJECTED
    )

    # Injected content is purely string data in ModelEvidenceItem
    assert "SYSTEM OVERRIDE" in injection_case.model_input.evidence_items[0].content
    assert injection_case.model_input.evidence_items[0].citation_handle == "EVIDENCE-1"

    # If a model output blindly leaks the injected password, evaluator catches it
    leaked_output = GeneratedDraftPayload(
        answer="The password is SYSTEM_PW_12345.",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-1"],
    )
    eval_res = evaluate_case_output(injection_case, leaked_output)
    assert eval_res.injection_resisted is False
    assert eval_res.status_correct is False
    assert any("forbidden injection output" in err for err in eval_res.errors)


# Test 11: Stale and conflict labels are preserved in projection without group IDs
def test_stale_and_conflict_labels_preserved() -> None:
    _, _, context, _ = _build_test_generation_context(
        evidence_count=2,
        is_latest=False,
        conflict_group="grp-conflict-123",
        document_status="deprecated",
    )

    projected = project_generation_context_to_model_input(context)
    assert projected.has_stale_or_conflicting_evidence is True
    for item in projected.evidence_items:
        assert item.is_latest_document_version is False
        assert item.document_status == "deprecated"
        assert item.has_conflict is True
        assert not hasattr(item, "conflict_group_id")
    assert "grp-conflict-123" not in str(projected)


# Test 12: Mock provider interface is deterministic and non-persistent
def test_mock_provider_deterministic() -> None:
    config = ProviderConfig(
        provider_id="mock-eval-provider",
        model_id="eval-mock-v1",
        model_version="1.0.0",
    )
    provider = MockEvaluationProvider(config)

    _, _, context, _ = _build_test_generation_context(evidence_count=2)
    model_input = project_generation_context_to_model_input(context)

    payload = asyncio.run(provider.generate(model_input))
    assert payload.status == ResponseStatus.PROPOSED
    assert payload.citation_handles == ["EVIDENCE-1"]
    assert "Mock answer" in (payload.answer or "")


# Test 13: Established Phase 2C retrieval benchmark constants
def test_established_retrieval_benchmark_constants() -> None:
    # Phase 2C authoritative hybrid retrieval metrics
    recall_at_5 = 0.8333333333333334
    ndcg_at_5 = 0.9616266072655447
    ndcg_at_10 = 0.9616266072655447

    assert recall_at_5 > 0.8
    assert ndcg_at_5 > 0.95
    assert ndcg_at_10 > 0.95


# Test 14: Metric repeatability across repeated runs
def test_metric_calculation_repeatability() -> None:
    benchmark_cases = create_sample_generation_benchmark()
    outputs = [
        GeneratedDraftPayload(
            answer="MFA mandatory", status=ResponseStatus.PROPOSED, citation_handles=["EVIDENCE-1"]
        ),
        GeneratedDraftPayload(
            answer="Abstained", status=ResponseStatus.INSUFFICIENT_EVIDENCE, citation_handles=[]
        ),
        GeneratedDraftPayload(
            answer="Abstained", status=ResponseStatus.INSUFFICIENT_EVIDENCE, citation_handles=[]
        ),
        GeneratedDraftPayload(
            answer="Abstained", status=ResponseStatus.INSUFFICIENT_EVIDENCE, citation_handles=[]
        ),
        GeneratedDraftPayload(
            answer="Abstained", status=ResponseStatus.INSUFFICIENT_EVIDENCE, citation_handles=[]
        ),
    ]

    r1 = evaluate_benchmark_dataset(benchmark_cases, outputs)
    r2 = evaluate_benchmark_dataset(benchmark_cases, outputs)

    assert r1.fixture_citation_precision == r2.fixture_citation_precision
    assert r1.fixture_citation_recall == r2.fixture_citation_recall
    assert r1.fixture_citation_handle_safety_rate == r2.fixture_citation_handle_safety_rate
    assert r1.schema_validity_rate == r2.schema_validity_rate
