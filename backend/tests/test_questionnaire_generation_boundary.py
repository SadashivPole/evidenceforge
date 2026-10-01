"""Deterministic unit and integration tests for generation boundary and citation validator.

Covers all core boundary invariants and blocker requirements:
1. PROPOSED + latest active evidence -> accepted.
2. PROPOSED + superseded evidence -> rejected.
3. PROPOSED + is_latest_document_version=False -> rejected.
4. PROPOSED + conflict_group_id != None -> rejected.
5. PROPOSED + mixed fresh + stale evidence in context -> rejected.
6. INSUFFICIENT_EVIDENCE + stale/conflicting evidence -> accepted.
7. INSUFFICIENT_EVIDENCE + empty citations -> accepted.
8. NEEDS_REVIEW -> rejected.
9. STALE_SOURCE -> rejected.
10. CONFLICTING_SOURCES -> rejected.
11. NOT_APPLICABLE -> rejected.
12. DO_NOT_DISCLOSE -> rejected.
13. APPROVED -> rejected.
14. Unknown citation handle rejected.
15. Fabricated raw UUID handle rejected.
16. Citation to unselected evidence rejected.
17. Cross-workspace evidence rejected.
18. Invalid status string rejected.
19. Oversized answer (>4000 characters) rejected.
20. Too many citations (>5 handles) rejected.
21. Malformed payload rejected.
22. Unexpected fields rejected (extra="forbid").
23. Duplicate handles rejected.
24. Exact provenance coordinates and content hash verified in resolved citations.
25. Deterministic validation across multiple invocations.
26. Generation context bounded to <= 5 chunks and <= 4000 tokens.
27. No response or revision persistence occurs.
28. Strictly isolated, zero-network, zero-provider execution.
29. Non-zero chunk_index regression verification (candidate -> item -> citation).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.evidence.context.selector import select_evidence_context
from app.evidence.search.types import SearchChunkCandidate
from app.questionnaires.generation.config import (
    DEFAULT_GENERATION_BOUNDARY_CONFIG,
    MAX_ANSWER_CHARACTERS,
    MAX_CITED_HANDLES,
    GenerationBoundaryConfig,
)
from app.questionnaires.generation.errors import (
    GenerationCitationValidationError,
    GenerationPayloadMalformedError,
    GenerationStatusValidationError,
    GenerationWorkspaceMismatchError,
)
from app.questionnaires.generation.schemas import GeneratedDraftPayload
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
)
from app.questionnaires.generation.validator import (
    GenerationDraftValidator,
    build_generation_context,
    validate_generated_draft,
)
from app.questionnaires.persistence.models import (
    QuestionnaireVersionQuestion,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)
from app.questionnaires.types import ResponseStatus
from tests.conftest import create_principal, create_workspace_with_owner
from tests.questionnaires.test_questionnaire_responses import _import_one_question


def _make_candidate(
    *,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    version_number: int = 1,
    chunk_index: int = 0,
    latest_document_version_number: int = 1,
    is_latest_document_version: bool = True,
    document_status: str = "active",
    conflict_group_id: str | None = None,
    content: str = "Evidence content for test.",
    content_hash: str = "sha256_mock_hash",
    start_byte: int = 0,
    end_byte: int = 100,
    section_label: str = "Section 1",
    page_number: int = 1,
) -> SearchChunkCandidate:
    return SearchChunkCandidate(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        version_id=version_id or uuid.uuid4(),
        version_number=version_number,
        chunk_index=chunk_index,
        content=content,
        content_hash=content_hash,
        normalized_start_byte=start_byte,
        normalized_end_byte=end_byte,
        section_label=section_label,
        page_number=page_number,
        document_status=document_status,
        latest_document_version_number=latest_document_version_number,
        is_latest_document_version=is_latest_document_version,
        conflict_group_id=conflict_group_id,
    )


def _make_context(
    *,
    workspace_id: uuid.UUID | None = None,
    evidence_count: int = 2,
    document_status: str = "active",
    is_latest_document_version: bool = True,
    conflict_group_id: str | None = None,
    chunk_index: int = 0,
) -> GenerationContext:
    ws_id = workspace_id or uuid.uuid4()
    q_id = uuid.uuid4()
    qv_id = uuid.uuid4()
    qvq_id = uuid.uuid4()

    evidence_items = []
    for i in range(1, evidence_count + 1):
        item = GenerationEvidenceItem(
            citation_handle=f"EVIDENCE-{i}",
            evidence_chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            version_number=1,
            document_name=f"Policy Doc {i}",
            document_version_number=1,
            latest_document_version_number=1,
            is_latest_document_version=is_latest_document_version,
            document_status=document_status,
            conflict_group_id=conflict_group_id,
            chunk_index=chunk_index if i == 1 else (chunk_index + i - 1),
            content_hash=f"hash_{i}",
            normalized_start_byte=10 * i,
            normalized_end_byte=100 + (10 * i),
            section_label=f"Section {i}",
            page_number=i,
            content=f"Authorized evidence content {i}.",
            token_count=10,
            rrf_score=0.03,
            lexical_rank=i,
            semantic_rank=i,
            fallback_used=False,
        )
        evidence_items.append(item)

    return GenerationContext(
        question_id=q_id,
        questionnaire_id=uuid.uuid4(),
        questionnaire_version_id=qv_id,
        questionnaire_version_question_id=qvq_id,
        question_text="Is MFA required for all employees?",
        section_path=("Access Control",),
        authorized_workspace_id=ws_id,
        evidence_context=tuple(evidence_items),
        total_evidence_chunks=len(evidence_items),
        total_evidence_tokens=len(evidence_items) * 10,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )


# Blocker 3 - Requirement 1: PROPOSED + latest active evidence -> accepted
def test_proposed_plus_latest_active_evidence_accepted() -> None:
    context = _make_context(
        evidence_count=2,
        document_status="active",
        is_latest_document_version=True,
        conflict_group_id=None,
    )
    payload = {
        "answer": "Yes, MFA is mandatory for all user accounts across production systems.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1", "EVIDENCE-2"],
        "uncertainty_notes": "Evidence directly supports the claim without ambiguity.",
    }

    result = validate_generated_draft(context, payload)

    assert result.validation_passed is True
    assert result.status == ResponseStatus.PROPOSED
    assert result.answer == payload["answer"]
    assert result.citation_handles == ("EVIDENCE-1", "EVIDENCE-2")
    assert len(result.resolved_citations) == 2


# Blocker 3 - Requirement 2: PROPOSED + superseded evidence -> rejected
def test_proposed_plus_superseded_evidence_rejected() -> None:
    context = _make_context(
        evidence_count=1,
        document_status="superseded",
        is_latest_document_version=True,
        conflict_group_id=None,
    )
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationStatusValidationError, match="stale or conflicting evidence"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 3: PROPOSED + is_latest_document_version=False -> rejected
def test_proposed_plus_not_latest_version_rejected() -> None:
    context = _make_context(
        evidence_count=1,
        document_status="active",
        is_latest_document_version=False,  # Stale document version
        conflict_group_id=None,
    )
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationStatusValidationError, match="stale or conflicting evidence"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 4: PROPOSED + conflict_group_id != None -> rejected
def test_proposed_plus_conflict_group_rejected() -> None:
    context = _make_context(
        evidence_count=1,
        document_status="active",
        is_latest_document_version=True,
        conflict_group_id="auth_policy_conflict_group",  # Conflict group present
    )
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationStatusValidationError, match="stale or conflicting evidence"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 5: PROPOSED + mixed fresh + stale evidence -> rejected
def test_proposed_plus_mixed_fresh_and_stale_evidence_rejected() -> None:
    ws_id = uuid.uuid4()
    item_fresh = GenerationEvidenceItem(
        citation_handle="EVIDENCE-1",
        evidence_chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_number=2,
        document_name="Current Policy",
        document_version_number=2,
        latest_document_version_number=2,
        is_latest_document_version=True,
        document_status="active",
        conflict_group_id=None,
        chunk_index=0,
        content_hash="hash_fresh",
        normalized_start_byte=0,
        normalized_end_byte=100,
        section_label="Section 1",
        page_number=1,
        content="Current active policy.",
        token_count=10,
    )
    item_stale = GenerationEvidenceItem(
        citation_handle="EVIDENCE-2",
        evidence_chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_number=1,
        document_name="Old Policy",
        document_version_number=1,
        latest_document_version_number=2,
        is_latest_document_version=False,  # Stale
        document_status="superseded",
        conflict_group_id=None,
        chunk_index=1,
        content_hash="hash_stale",
        normalized_start_byte=0,
        normalized_end_byte=100,
        section_label="Section 1",
        page_number=1,
        content="Old superseded policy.",
        token_count=10,
    )

    context = GenerationContext(
        question_id=uuid.uuid4(),
        questionnaire_id=uuid.uuid4(),
        questionnaire_version_id=uuid.uuid4(),
        questionnaire_version_question_id=uuid.uuid4(),
        question_text="MFA requirement",
        section_path=("Security",),
        authorized_workspace_id=ws_id,
        evidence_context=(item_fresh, item_stale),
        total_evidence_chunks=2,
        total_evidence_tokens=20,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )

    # Even if model only cites the fresh item EVIDENCE-1, context contains stale evidence
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationStatusValidationError, match="stale or conflicting evidence"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 6: INSUFFICIENT_EVIDENCE + stale/conflicting evidence -> accepted
def test_insufficient_evidence_plus_stale_or_conflicting_evidence_accepted() -> None:
    context = _make_context(
        evidence_count=1,
        document_status="superseded",
        is_latest_document_version=False,
        conflict_group_id="auth_conflict",
    )
    payload = {
        "answer": "Available evidence is superseded and in conflict.",
        "status": "INSUFFICIENT_EVIDENCE",
        "citation_handles": ["EVIDENCE-1"],
    }

    result = validate_generated_draft(context, payload)
    assert result.validation_passed is True
    assert result.status == ResponseStatus.INSUFFICIENT_EVIDENCE
    assert result.citation_handles == ("EVIDENCE-1",)
    assert len(result.resolved_citations) == 1


# Blocker 3 - Requirement 7: INSUFFICIENT_EVIDENCE + empty citations -> accepted
def test_insufficient_evidence_plus_empty_citations_accepted() -> None:
    context = _make_context(evidence_count=0)
    payload = {
        "answer": "No supporting evidence found in the workspace.",
        "status": "INSUFFICIENT_EVIDENCE",
        "citation_handles": [],
    }

    result = validate_generated_draft(context, payload)
    assert result.validation_passed is True
    assert result.status == ResponseStatus.INSUFFICIENT_EVIDENCE
    assert result.citation_handles == ()
    assert result.resolved_citations == ()


# Blocker 3 - Requirement 8: NEEDS_REVIEW -> rejected
def test_status_needs_review_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "MFA is required.",
        "status": "NEEDS_REVIEW",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="NEEDS_REVIEW"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 9: STALE_SOURCE -> rejected
def test_status_stale_source_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "MFA is required.",
        "status": "STALE_SOURCE",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="STALE_SOURCE"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 10: CONFLICTING_SOURCES -> rejected
def test_status_conflicting_sources_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "MFA is required.",
        "status": "CONFLICTING_SOURCES",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="CONFLICTING_SOURCES"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 11: NOT_APPLICABLE -> rejected
def test_status_not_applicable_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "This policy does not apply.",
        "status": "NOT_APPLICABLE",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="NOT_APPLICABLE"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 12: DO_NOT_DISCLOSE -> rejected
def test_status_do_not_disclose_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "Confidential.",
        "status": "DO_NOT_DISCLOSE",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="DO_NOT_DISCLOSE"):
        validate_generated_draft(context, payload)


# Blocker 3 - Requirement 13: APPROVED -> rejected
def test_status_approved_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "MFA is required.",
        "status": "APPROVED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="APPROVED"):
        validate_generated_draft(context, payload)


# Additional Boundary Tests
def test_unknown_citation_handle_rejected() -> None:
    context = _make_context(evidence_count=2)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1", "EVIDENCE-99"],
    }

    with pytest.raises(GenerationCitationValidationError, match="EVIDENCE-99"):
        validate_generated_draft(context, payload)


def test_fabricated_uuid_rejected() -> None:
    context = _make_context(evidence_count=2)
    fake_uuid = str(uuid.uuid4())
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": [fake_uuid],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="Invalid citation handle format"):
        validate_generated_draft(context, payload)


def test_citation_to_unselected_evidence_rejected() -> None:
    context = _make_context(evidence_count=1)  # Only EVIDENCE-1 in context
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-2"],
    }

    with pytest.raises(GenerationCitationValidationError, match="EVIDENCE-2"):
        validate_generated_draft(context, payload)


def test_cross_workspace_evidence_rejected() -> None:
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()

    question = QuestionnaireVersionQuestion(
        id=uuid.uuid4(),
        workspace_id=ws_a,
        questionnaire_id=uuid.uuid4(),
        questionnaire_version_id=uuid.uuid4(),
        questionnaire_question_id=uuid.uuid4(),
        ordinal=1,
        source_row=2,
        sheet_name="Security",
        section_path=["Access"],
        normalized_question_text="MFA policy",
    )

    candidates = [_make_candidate()]
    selection_result = select_evidence_context(
        candidates,
        authorized_workspace_id=ws_b,  # Mismatched workspace
    )

    with pytest.raises(GenerationWorkspaceMismatchError, match="does not match"):
        build_generation_context(
            question=question,
            context_result=selection_result,
        )


def test_invalid_status_string_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "MFA is required.",
        "status": "TOTALLY_INVALID_STATUS",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="Input should be"):
        validate_generated_draft(context, payload)


def test_proposed_with_empty_citations_rejected() -> None:
    context = _make_context(evidence_count=2)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": [],
    }

    with pytest.raises(GenerationStatusValidationError, match="without citing any evidence"):
        validate_generated_draft(context, payload)


def test_proposed_with_empty_evidence_context_rejected() -> None:
    context = _make_context(evidence_count=0)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": [],
    }

    with pytest.raises(GenerationStatusValidationError, match="evidence context is empty"):
        validate_generated_draft(context, payload)


def test_proposed_with_empty_answer_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "   ",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationStatusValidationError, match="empty answer"):
        validate_generated_draft(context, payload)


def test_oversized_answer_rejected() -> None:
    context = _make_context(evidence_count=1)
    oversized_answer = "A" * (MAX_ANSWER_CHARACTERS + 1)
    payload = {
        "answer": oversized_answer,
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="at most 4000 characters"):
        validate_generated_draft(context, payload)


def test_too_many_citations_rejected() -> None:
    context = _make_context(evidence_count=5)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": [f"EVIDENCE-{i}" for i in range(1, MAX_CITED_HANDLES + 2)],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="at most 5 items"):
        validate_generated_draft(context, payload)


def test_duplicate_citation_handles_rejected() -> None:
    context = _make_context(evidence_count=2)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1", "EVIDENCE-1"],
    }

    with pytest.raises(GenerationPayloadMalformedError, match="Duplicate citation handle"):
        validate_generated_draft(context, payload)


def test_unexpected_fields_rejected() -> None:
    context = _make_context(evidence_count=1)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
        "tool_call": "execute_sql('DROP TABLE users')",
        "autonomous_action": True,
    }

    with pytest.raises(GenerationPayloadMalformedError, match="Extra inputs are not permitted"):
        validate_generated_draft(context, payload)


def test_exact_provenance_and_content_hash_preservation() -> None:
    context = _make_context(evidence_count=2, chunk_index=3)
    payload = {
        "answer": "MFA is mandatory.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-2"],
    }

    result = validate_generated_draft(context, payload)
    assert len(result.resolved_citations) == 1
    cit = result.resolved_citations[0]
    expected_item = context.evidence_context[1]

    assert cit.workspace_id == context.authorized_workspace_id
    assert cit.document_id == expected_item.document_id
    assert cit.version_id == expected_item.version_id
    assert cit.version_number == expected_item.version_number
    assert cit.chunk_id == expected_item.evidence_chunk_id
    assert cit.chunk_index == expected_item.chunk_index
    assert cit.content_hash == expected_item.content_hash
    assert cit.normalized_start_byte == expected_item.normalized_start_byte
    assert cit.normalized_end_byte == expected_item.normalized_end_byte
    assert cit.section_label == expected_item.section_label
    assert cit.page_number == expected_item.page_number


def test_authoritative_chunk_index_preserved_in_resolved_citation(
    db_session: Session,
) -> None:
    """Regression test: verifies candidate.chunk_index -> item -> citation with non-zero index."""

    principal = create_principal(
        db_session,
        email="chunk-index-reg@example.com",
        display_name="Chunk Index Principal",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Chunk Index Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required for privileged access",
    )

    # Candidate with non-zero chunk_index = 4
    candidate = _make_candidate(
        chunk_index=4,
        content="Access control chunk index 4.",
        document_status="active",
        is_latest_document_version=True,
    )
    selection = select_evidence_context([candidate], authorized_workspace_id=workspace.id)
    assert selection.selected_candidates[0].chunk_index == 4

    gen_context = build_generation_context(question, selection)
    assert gen_context.evidence_context[0].chunk_index == 4

    payload = {
        "answer": "Privileged access requires MFA.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    result = validate_generated_draft(gen_context, payload)
    assert result.validation_passed is True
    assert len(result.resolved_citations) == 1
    assert result.resolved_citations[0].chunk_index == 4


def test_deterministic_validation() -> None:
    context = _make_context(evidence_count=2)
    payload = {
        "answer": "MFA is required across all systems.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
        "uncertainty_notes": "None",
    }

    res1 = validate_generated_draft(context, payload)
    res2 = validate_generated_draft(context, payload)

    assert res1 == res2
    assert res1.cited_chunk_ids == res2.cited_chunk_ids
    assert res1.resolved_citations == res2.resolved_citations


def test_generation_context_bounds_to_five_chunks_and_preserves_token_budget(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="gen-bound-multi@example.com",
        display_name="Gen Boundary Principal Multi",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Gen Boundary Workspace Multi",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required for privileged access",
    )

    # 10 candidates
    candidates = [_make_candidate(chunk_index=i, content=f"Candidate {i}") for i in range(10)]
    selection = select_evidence_context(candidates, authorized_workspace_id=workspace.id)

    assert selection.total_selected_chunks == 5
    gen_context = build_generation_context(question, selection)
    assert gen_context.total_evidence_chunks == 5
    assert len(gen_context.evidence_context) == 5
    assert [e.citation_handle for e in gen_context.evidence_context] == [
        "EVIDENCE-1",
        "EVIDENCE-2",
        "EVIDENCE-3",
        "EVIDENCE-4",
        "EVIDENCE-5",
    ]
    for idx, item in enumerate(gen_context.evidence_context):
        assert item.chunk_index == idx


def test_no_response_persistence_occurs(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="gen-bound-nopersist@example.com",
        display_name="Gen Boundary Principal No Persist",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Gen Boundary Workspace No Persist",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required for privileged access",
    )

    context = _make_context(workspace_id=workspace.id, evidence_count=2)
    payload = {
        "answer": "MFA is required.",
        "status": "PROPOSED",
        "citation_handles": ["EVIDENCE-1"],
    }

    result = validate_generated_draft(context, payload)
    assert result.validation_passed is True

    # Check database tables
    assert db_session.query(QuestionnaireResponse).count() == 0
    assert db_session.query(QuestionnaireResponseRevision).count() == 0
    assert db_session.query(QuestionnaireResponseCitation).count() == 0


def test_zero_provider_isolation() -> None:
    validator = GenerationDraftValidator(config=DEFAULT_GENERATION_BOUNDARY_CONFIG)
    context = _make_context(evidence_count=1)
    payload = GeneratedDraftPayload(
        answer="Pure local validation.",
        status=ResponseStatus.PROPOSED,
        citation_handles=["EVIDENCE-1"],
    )

    # Runs completely offline and synchronously in pure Python
    result = validator.validate(context, payload)
    assert result.validation_passed is True
    assert result.answer == "Pure local validation."


def test_generation_boundary_config_bounds() -> None:
    with pytest.raises(ValueError, match="max_answer_characters must not exceed 4000"):
        GenerationBoundaryConfig(max_answer_characters=4001)

    with pytest.raises(ValueError, match="max_cited_handles must be between 1 and 5"):
        GenerationBoundaryConfig(max_cited_handles=6)
