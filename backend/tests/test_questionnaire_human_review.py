"""Deterministic unit and integration tests for Phase 2D.4 Human Review Integration."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.evidence.citations.types import EvidenceCitation
from app.models import (
    AuditEvent,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
    ValidatedDraftResponse,
)
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseRevision,
)
from app.questionnaires.review.errors import (
    ReviewAuthorizationError,
    ReviewCitationValidationError,
    ReviewValidationError,
    ReviewWorkspaceMismatchError,
)
from app.questionnaires.review.service import (
    apply_review_decision,
    build_review_context,
)
from app.questionnaires.review.types import (
    ReviewAction,
    ReviewDecision,
    ReviewDraftContext,
)
from app.questionnaires.types import ResponseStatus
from tests.conftest import (
    TestPrincipal as ConftestPrincipal,
)
from tests.conftest import (
    auth_headers,
    create_principal,
    create_workspace_with_owner,
)
from tests.questionnaires.test_questionnaire_responses import (
    _create_chunk,
    _import_one_question,
)


def _setup_review_environment(
    db: Session,
    *,
    evidence_count: int = 2,
    document_status: str = "active",
    is_latest: bool = True,
    conflict_group_id: str | None = None,
) -> tuple[
    WorkspaceMembership,
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
    list[EvidenceChunk],
    ReviewDraftContext,
    ValidatedDraftResponse,
    ConftestPrincipal,
]:
    principal = create_principal(
        db,
        email=f"reviewer-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Reviewer User",
    )
    workspace = create_workspace_with_owner(
        db,
        principal,
        name=f"Review Workspace {uuid.uuid4().hex[:8]}",
    )
    owner_membership = (
        db.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == principal.user.id,
        )
        .first()
    )
    assert owner_membership is not None

    version, question = _import_one_question(
        db,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Is multi-factor authentication enforced for all users?",
    )

    chunks = []
    gen_items = []
    resolved_citations = []
    cited_chunk_ids = []
    cited_handles = []

    for i in range(1, evidence_count + 1):
        chunk = _create_chunk(
            db,
            workspace_id=workspace.id,
            actor_user_id=principal.user.id,
            content=f"Access control evidence policy chunk {i} requiring MFA.",
        )
        chunks.append(chunk)

        doc_version = db.get(EvidenceDocumentVersion, chunk.document_version_id)
        assert doc_version is not None
        doc = db.get(EvidenceDocument, doc_version.document_id)
        assert doc is not None

        handle = f"EVIDENCE-{i}"
        item = GenerationEvidenceItem(
            citation_handle=handle,
            evidence_chunk_id=chunk.id,
            document_id=doc.id,
            version_id=doc_version.id,
            version_number=doc_version.version_number,
            document_name=doc.name,
            document_version_number=doc_version.version_number,
            latest_document_version_number=doc_version.version_number,
            is_latest_document_version=is_latest,
            document_status=document_status,
            conflict_group_id=conflict_group_id,
            chunk_index=chunk.chunk_index,
            content_hash=chunk.content_hash,
            normalized_start_byte=chunk.normalized_start_byte,
            normalized_end_byte=chunk.normalized_end_byte,
            section_label=chunk.section_label,
            page_number=chunk.page_number,
            content=chunk.content,
            token_count=15,
            rrf_score=0.03,
            lexical_rank=i,
            semantic_rank=i,
            fallback_used=False,
        )
        gen_items.append(item)

        citation = EvidenceCitation(
            workspace_id=workspace.id,
            document_id=doc.id,
            version_id=doc_version.id,
            version_number=doc_version.version_number,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            content_hash=chunk.content_hash,
            normalized_start_byte=chunk.normalized_start_byte,
            normalized_end_byte=chunk.normalized_end_byte,
            section_label=chunk.section_label,
            page_number=chunk.page_number,
        )
        resolved_citations.append(citation)
        cited_chunk_ids.append(chunk.id)
        cited_handles.append(handle)

    gen_context = GenerationContext(
        question_id=question.id,
        questionnaire_id=question.questionnaire_id,
        questionnaire_version_id=question.questionnaire_version_id,
        questionnaire_version_question_id=question.id,
        question_text=question.normalized_question_text,
        section_path=tuple(question.section_path or ()),
        authorized_workspace_id=workspace.id,
        evidence_context=tuple(gen_items),
        total_evidence_chunks=len(gen_items),
        total_evidence_tokens=len(gen_items) * 15,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )

    draft_status = ResponseStatus.PROPOSED if gen_items else ResponseStatus.INSUFFICIENT_EVIDENCE
    draft_answer = (
        "Yes, MFA is mandatory for all employee accounts."
        if gen_items
        else "Insufficient evidence to answer question."
    )
    draft = ValidatedDraftResponse(
        question_id=question.id,
        workspace_id=workspace.id,
        answer=draft_answer,
        status=draft_status,
        resolved_citations=tuple(resolved_citations),
        cited_chunk_ids=tuple(cited_chunk_ids),
        citation_handles=tuple(cited_handles),
        uncertainty_notes=(
            "Directly confirmed by access policy." if gen_items else "No evidence found."
        ),
        generation_boundary_version="generation-boundary-v1",
        validation_passed=True,
    )

    review_context = build_review_context(gen_context, draft)
    return owner_membership, version, question, chunks, review_context, draft, principal


# Scenario 1: ValidatedDraftResponse can be loaded into review context
def test_scenario_01_load_validated_draft_into_review_context(
    db_session: Session,
) -> None:
    _, _, question, chunks, review_context, draft, _ = _setup_review_environment(db_session)

    assert review_context.question_id == question.id
    assert review_context.draft_answer == draft.answer
    assert review_context.proposed_status == ResponseStatus.PROPOSED
    assert len(review_context.evidence_items) == 2
    assert review_context.cited_handles == ("EVIDENCE-1", "EVIDENCE-2")
    assert review_context.evidence_items[0].is_cited_by_draft is True
    assert review_context.has_stale_or_conflicting_evidence is False


# Scenario 2: Authorized reviewer can read reviewable draft
def test_scenario_02_authorized_reviewer_can_read_review_context(
    db_session: Session,
) -> None:
    membership, _, question, _, review_context, _, _ = _setup_review_environment(db_session)
    assert membership.role in (WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER)
    assert review_context.authorized_workspace_id == membership.workspace_id


# Scenario 3: Unauthorized workspace access is rejected
def test_scenario_03_unauthorized_workspace_access_rejected(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)
    different_workspace_id = uuid.uuid4()

    decision = ReviewDecision(action=ReviewAction.ACCEPT)
    with pytest.raises(ReviewWorkspaceMismatchError, match="does not match"):
        apply_review_decision(
            db_session,
            workspace_id=different_workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision,
        )


# Scenario 4: Non-reviewer access follows existing role rules (Viewer rejected)
def test_scenario_04_viewer_role_cannot_review(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    decision = ReviewDecision(action=ReviewAction.ACCEPT)
    with pytest.raises(ReviewAuthorizationError, match="not permitted"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=WorkspaceRole.VIEWER,
            review_context=review_context,
            decision=decision,
        )


# Scenario 5: Reviewer can approve a valid draft (ACCEPT action)
def test_scenario_05_reviewer_can_approve_valid_draft(
    db_session: Session,
) -> None:
    membership, _, _, chunks, review_context, draft, _ = _setup_review_environment(db_session)

    decision = ReviewDecision(action=ReviewAction.ACCEPT)
    result = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision,
    )

    assert result.is_approved is True
    assert result.revision.status == ResponseStatus.APPROVED.value
    assert result.revision.answer == draft.answer
    assert result.revision.revision_number == 1
    assert result.revision.author_user_id == membership.user_id
    assert len(result.citations) == 2
    assert {c.evidence_chunk_id for c in result.citations} == {chunks[0].id, chunks[1].id}


# Scenario 6: Reviewer can edit and approve (EDIT_AND_APPROVE action)
def test_scenario_06_reviewer_can_edit_and_approve(
    db_session: Session,
) -> None:
    membership, _, _, chunks, review_context, _, _ = _setup_review_environment(db_session)

    edited_text = "MFA is strictly required on all corporate and cloud infrastructure."
    decision = ReviewDecision(
        action=ReviewAction.EDIT_AND_APPROVE,
        edited_answer=edited_text,
        selected_citation_handles=("EVIDENCE-1",),
    )
    result = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision,
    )

    assert result.is_approved is True
    assert result.revision.status == ResponseStatus.APPROVED.value
    assert result.revision.answer == edited_text
    assert len(result.citations) == 1
    assert result.citations[0].evidence_chunk_id == chunks[0].id


# Scenario 7: Reviewer can reject (REJECT action)
def test_scenario_07_reviewer_can_reject_draft(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    decision = ReviewDecision(
        action=ReviewAction.REJECT,
        rejection_notes="Evidence is insufficient to confirm contractor policies.",
    )
    result = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision,
    )

    assert result.is_approved is False
    assert result.revision.status == ResponseStatus.NEEDS_REVIEW.value


# Scenario 8 & 9: Approval creates new immutable revision; existing revisions remain unchanged
def test_scenario_08_09_immutable_revision_lifecycle(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    # 1. First review action: Reject -> Revision #1 (NEEDS_REVIEW)
    res1 = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.REJECT),
    )
    assert res1.revision.revision_number == 1
    assert res1.revision.status == ResponseStatus.NEEDS_REVIEW.value

    # 2. Second review action: Edit & Approve -> Revision #2 (APPROVED)
    res2 = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(
            action=ReviewAction.EDIT_AND_APPROVE,
            edited_answer="MFA confirmed by security team.",
        ),
    )
    assert res2.revision.revision_number == 2
    assert res2.revision.status == ResponseStatus.APPROVED.value

    # Verify both revisions exist in database and Revision #1 is immutable
    revisions = (
        db_session.query(QuestionnaireResponseRevision)
        .filter(QuestionnaireResponseRevision.response_id == res1.response.id)
        .order_by(QuestionnaireResponseRevision.revision_number.asc())
        .all()
    )
    assert len(revisions) == 2
    assert revisions[0].revision_number == 1
    assert revisions[0].status == ResponseStatus.NEEDS_REVIEW.value
    assert revisions[1].revision_number == 2
    assert revisions[1].status == ResponseStatus.APPROVED.value


# Scenario 10 & 24: Reviewer identity and audit events recorded
def test_scenario_10_24_reviewer_audit_event_recorded(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.ACCEPT),
    )

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.workspace_id == membership.workspace_id)
        .all()
    )
    review_events = [e for e in events if e.action == "questionnaire.response.reviewed"]
    assert len(review_events) >= 1
    event = review_events[0]
    assert event.actor_user_id == membership.user_id
    assert event.event_metadata["review_action"] == ReviewAction.ACCEPT.value
    assert event.event_metadata["is_approved"] is True


# Scenario 11 & 18: Validated citations preserved with exact provenance
def test_scenario_11_18_provenance_preserved_on_approval(
    db_session: Session,
) -> None:
    membership, _, _, chunks, review_context, _, _ = _setup_review_environment(db_session)

    res = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.ACCEPT),
    )

    cit = res.citations[0]
    chunk = chunks[0]
    assert cit.evidence_chunk_id == chunk.id
    assert cit.evidence_chunk_index == chunk.chunk_index
    assert cit.content_hash == chunk.content_hash
    assert cit.normalized_start_byte == chunk.normalized_start_byte
    assert cit.normalized_end_byte == chunk.normalized_end_byte


# Scenario 12: Arbitrary browser-supplied citation UUID rejected
def test_scenario_12_arbitrary_citation_uuid_rejected(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)
    fake_chunk_id = uuid.uuid4()

    decision = ReviewDecision(
        action=ReviewAction.EDIT_AND_APPROVE,
        edited_answer="MFA required.",
        selected_chunk_ids=(fake_chunk_id,),
    )

    with pytest.raises(ReviewCitationValidationError, match="not in the validated review context"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision,
        )


# Scenario 13: Cross-workspace citation rejected
def test_scenario_13_cross_workspace_citation_rejected(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    # Create chunk in another workspace
    other_principal = create_principal(
        db_session,
        email="other-ws@example.com",
        display_name="Other Principal",
    )
    other_ws = create_workspace_with_owner(db_session, other_principal, name="Other WS")
    other_chunk = _create_chunk(
        db_session,
        workspace_id=other_ws.id,
        actor_user_id=other_principal.user.id,
        content="Other workspace evidence",
    )

    decision = ReviewDecision(
        action=ReviewAction.EDIT_AND_APPROVE,
        edited_answer="MFA required.",
        selected_chunk_ids=(other_chunk.id,),
    )

    with pytest.raises(ReviewCitationValidationError, match="not in the validated review context"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision,
        )


# Scenario 14: Invalid/fabricated evidence reference is rejected
def test_scenario_14_fabricated_citation_handle_rejected(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    decision = ReviewDecision(
        action=ReviewAction.EDIT_AND_APPROVE,
        edited_answer="MFA required.",
        selected_citation_handles=("EVIDENCE-99",),  # Fabricated handle
    )

    with pytest.raises(ReviewCitationValidationError, match="not in the validated review context"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision,
        )


# Scenario 15: Content-hash and byte-range provenance verified on persisted citation
def test_scenario_15_content_hash_and_offsets_verified(
    db_session: Session,
) -> None:
    membership, _, _, chunks, review_context, _, _ = _setup_review_environment(db_session)

    res = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.ACCEPT),
    )

    persisted_cit = res.citations[0]
    expected_chunk = chunks[0]
    assert persisted_cit.content_hash == expected_chunk.content_hash
    assert persisted_cit.normalized_start_byte == expected_chunk.normalized_start_byte
    assert persisted_cit.normalized_end_byte == expected_chunk.normalized_end_byte


# Scenario 16 & 17: Stale and conflict metadata preserved
def test_scenario_16_17_stale_and_conflict_metadata_preserved(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(
        db_session,
        document_status="superseded",
        is_latest=False,
        conflict_group_id="auth_group_1",
    )

    assert review_context.has_stale_or_conflicting_evidence is True
    item = review_context.evidence_items[0]
    assert item.document_status == "superseded"
    assert item.is_latest_document_version is False
    assert item.conflict_group_id == "auth_group_1"


# Scenario 19: Proposed draft cannot auto-approve without explicit human action
def test_scenario_19_no_auto_approval_without_human_action(
    db_session: Session,
) -> None:
    _, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    # Merely constructing or loading review context writes ZERO database response rows
    count = db_session.query(QuestionnaireResponse).count()
    assert count == 0


# Scenario 21: Review of INSUFFICIENT_EVIDENCE
def test_scenario_21_review_insufficient_evidence(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(
        db_session,
        evidence_count=0,
    )
    assert review_context.proposed_status == ResponseStatus.INSUFFICIENT_EVIDENCE

    # 1. Reviewer rejects the draft due to insufficient evidence -> NEEDS_REVIEW
    decision_reject = ReviewDecision(
        action=ReviewAction.REJECT,
        rejection_notes="No evidence found for proprietary encryption.",
    )
    result = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision_reject,
    )

    assert result.revision.status == ResponseStatus.NEEDS_REVIEW.value
    assert result.is_approved is False
    assert len(result.citations) == 0

    # 2. Attempting to persist INSUFFICIENT_EVIDENCE via review action is strictly rejected
    decision_invalid = ReviewDecision(
        action=ReviewAction.EDIT_AND_APPROVE,
        edited_answer="No evidence found.",
        target_status=ResponseStatus.INSUFFICIENT_EVIDENCE,
    )
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision_invalid,
        )


# Scenario 22: Rejecting a draft does not create an approved response
def test_scenario_22_rejecting_does_not_approve(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    res = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.REJECT),
    )
    assert res.is_approved is False
    assert res.revision.status != ResponseStatus.APPROVED.value


# Scenario 23: Duplicate/concurrent review follows existing revision rules
def test_scenario_23_identical_decision_is_idempotent(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    decision = ReviewDecision(action=ReviewAction.ACCEPT)
    res1 = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision,
    )
    res2 = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision,
    )

    # Identical decision doesn't create extra revisions
    assert res1.revision.id == res2.revision.id
    assert res2.revision_created is False


# Scenario 25: Same review input behaves deterministically
def test_scenario_25_deterministic_review_behavior(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    res = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.ACCEPT),
    )

    assert res.is_approved is True
    assert res.response.workspace_id == membership.workspace_id
    assert res.revision.author_user_id == membership.user_id


def test_scenario_20_unvalidated_draft_empty_answer_rejected(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    decision = ReviewDecision(
        action=ReviewAction.EDIT_AND_APPROVE,
        edited_answer="   ",  # Empty whitespace
    )
    with pytest.raises(ReviewValidationError, match="EDIT_AND_APPROVE requires a non-empty"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision,
        )


# FastApi TestClient Route Integration Test
def test_review_endpoint_api_integration(
    client: TestClient,
    db_session: Session,
) -> None:
    membership, version, question, chunks, _, _, principal = _setup_review_environment(db_session)

    headers = auth_headers(principal)

    url = (
        f"/workspaces/{membership.workspace_id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{question.id}/review"
    )

    payload = {
        "action": "ACCEPT",
        "edited_answer": "MFA is required across all services.",
        "selected_chunk_ids": [str(chunks[0].id)],
    }

    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["action"] == "ACCEPT"
    assert data["is_approved"] is True
    assert data["revision"]["status"] == "APPROVED"
    assert data["revision"]["answer"] == "MFA is required across all services."
    assert len(data["revision"]["citations"]) == 1
    assert data["revision"]["citations"][0]["evidence_chunk_id"] == str(chunks[0].id)


def test_review_decision_mutual_exclusivity(
    client: TestClient,
    db_session: Session,
) -> None:
    membership, version, question, chunks, review_context, _, principal = _setup_review_environment(
        db_session
    )

    # 1. Service-level test
    decision = ReviewDecision(
        action=ReviewAction.ACCEPT,
        selected_citation_handles=("EVIDENCE-1",),
        selected_chunk_ids=(chunks[0].id,),
    )
    with pytest.raises(
        ReviewValidationError,
        match="mutually exclusive",
    ):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=decision,
        )

    # 2. API-level test (schema validation catches it)
    headers = auth_headers(principal)
    url = (
        f"/workspaces/{membership.workspace_id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{question.id}/review"
    )
    response = client.post(
        url,
        json={
            "action": "ACCEPT",
            "selected_citation_handles": ["EVIDENCE-1"],
            "selected_chunk_ids": [str(chunks[0].id)],
        },
        headers=headers,
    )
    assert response.status_code == 422
    err_text = response.text.lower()
    assert "mutually exclusive" in err_text or "cannot supply both" in err_text


def test_review_decision_schema_bounds_and_forbid_extra(
    client: TestClient,
    db_session: Session,
) -> None:
    membership, version, question, chunks, _, _, principal = _setup_review_environment(db_session)
    headers = auth_headers(principal)
    url = (
        f"/workspaces/{membership.workspace_id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{question.id}/review"
    )

    # 1. Answer too long (> 4000 characters)
    response = client.post(
        url,
        json={"action": "ACCEPT", "edited_answer": "x" * 4001},
        headers=headers,
    )
    assert response.status_code == 422

    # 2. Rejection notes too long (> 1000 characters)
    response = client.post(
        url,
        json={"action": "REJECT", "rejection_notes": "x" * 1001},
        headers=headers,
    )
    assert response.status_code == 422

    # 3. Too many citation handles (> 5)
    response = client.post(
        url,
        json={
            "action": "ACCEPT",
            "selected_citation_handles": [f"EVIDENCE-{i}" for i in range(1, 7)],
        },
        headers=headers,
    )
    assert response.status_code == 422

    # 4. Invalid citation handle format
    response = client.post(
        url,
        json={
            "action": "ACCEPT",
            "selected_citation_handles": ["INVALID-CITATION-1"],
        },
        headers=headers,
    )
    assert response.status_code == 422

    # 5. Unexpected extra field (extra="forbid")
    response = client.post(
        url,
        json={
            "action": "ACCEPT",
            "unexpected_field": "disallowed",
        },
        headers=headers,
    )
    assert response.status_code == 422


def test_review_action_approve_alias_equivalence(
    client: TestClient,
    db_session: Session,
) -> None:
    membership, version, question, chunks, review_context, _, principal = _setup_review_environment(
        db_session
    )

    # 1. Service-level test
    decision = ReviewDecision(action=ReviewAction.APPROVE)
    res = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=decision,
    )
    assert res.is_approved is True
    assert res.revision.status == ResponseStatus.APPROVED.value

    # 2. API-level test with action="APPROVE"
    headers = auth_headers(principal)
    url = (
        f"/workspaces/{membership.workspace_id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{question.id}/review"
    )
    response = client.post(
        url,
        json={
            "action": "APPROVE",
            "edited_answer": "Approved via APPROVE alias.",
        },
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "APPROVE"
    assert data["is_approved"] is True
    assert data["revision"]["status"] == "APPROVED"


def test_review_state_machine_integrity_service(
    db_session: Session,
) -> None:
    membership, _, _, _, review_context, _, _ = _setup_review_environment(db_session)

    # 1. ACCEPT with no target_status -> APPROVED
    res_accept = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.ACCEPT, target_status=None),
    )
    assert res_accept.is_approved is True
    assert res_accept.revision.status == ResponseStatus.APPROVED.value

    # 2. APPROVE with no target_status -> APPROVED
    res_approve = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(action=ReviewAction.APPROVE, target_status=None),
    )
    assert res_approve.is_approved is True
    assert res_approve.revision.status == ResponseStatus.APPROVED.value

    # 3. EDIT_AND_APPROVE with no target_status -> APPROVED
    res_edit = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(
            action=ReviewAction.EDIT_AND_APPROVE,
            edited_answer="Human verified MFA requirement.",
            target_status=None,
        ),
    )
    assert res_edit.is_approved is True
    assert res_edit.revision.status == ResponseStatus.APPROVED.value

    # 4. REJECT with no target_status -> NEEDS_REVIEW
    res_reject = apply_review_decision(
        db_session,
        workspace_id=membership.workspace_id,
        actor_user_id=membership.user_id,
        actor_role=membership.role,
        review_context=review_context,
        decision=ReviewDecision(
            action=ReviewAction.REJECT,
            rejection_notes="Insufficient evidence for claim.",
            target_status=None,
        ),
    )
    assert res_reject.is_approved is False
    assert res_reject.revision.status == ResponseStatus.NEEDS_REVIEW.value

    # 5. ACCEPT + NEEDS_REVIEW -> rejected
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.ACCEPT,
                target_status=ResponseStatus.NEEDS_REVIEW,
            ),
        )

    # 6. APPROVE + NEEDS_REVIEW -> rejected
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.APPROVE,
                target_status=ResponseStatus.NEEDS_REVIEW,
            ),
        )

    # 7. EDIT_AND_APPROVE + NEEDS_REVIEW -> rejected
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.EDIT_AND_APPROVE,
                edited_answer="Some edited answer",
                target_status=ResponseStatus.NEEDS_REVIEW,
            ),
        )

    # 8. REJECT + APPROVED -> rejected
    with pytest.raises(ReviewValidationError, match="requires status NEEDS_REVIEW"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.REJECT,
                target_status=ResponseStatus.APPROVED,
            ),
        )

    # 9. REJECT + PROPOSED -> rejected
    with pytest.raises(ReviewValidationError, match="requires status NEEDS_REVIEW"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.REJECT,
                target_status=ResponseStatus.PROPOSED,
            ),
        )

    # 10. REJECT + INSUFFICIENT_EVIDENCE -> rejected
    with pytest.raises(ReviewValidationError, match="requires status NEEDS_REVIEW"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.REJECT,
                target_status=ResponseStatus.INSUFFICIENT_EVIDENCE,
            ),
        )

    # 11. APPROVE + PROPOSED -> rejected
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.APPROVE,
                target_status=ResponseStatus.PROPOSED,
            ),
        )

    # 12. APPROVE + CONFLICTING/STALE status -> rejected
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.APPROVE,
                target_status=ResponseStatus.CONFLICTING_SOURCES,
            ),
        )
    with pytest.raises(ReviewValidationError, match="requires status APPROVED"):
        apply_review_decision(
            db_session,
            workspace_id=membership.workspace_id,
            actor_user_id=membership.user_id,
            actor_role=membership.role,
            review_context=review_context,
            decision=ReviewDecision(
                action=ReviewAction.APPROVE,
                target_status=ResponseStatus.STALE_SOURCE,
            ),
        )


def test_review_state_machine_integrity_api(
    client: TestClient,
    db_session: Session,
) -> None:
    membership, version, question, chunks, _, _, principal = _setup_review_environment(db_session)
    headers = auth_headers(principal)
    url = (
        f"/workspaces/{membership.workspace_id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{question.id}/review"
    )

    # ACCEPT with invalid target_status -> 422
    resp1 = client.post(
        url,
        json={"action": "ACCEPT", "target_status": "NEEDS_REVIEW"},
        headers=headers,
    )
    assert resp1.status_code == 422
    assert "target_status to be None or APPROVED" in resp1.text

    # APPROVE with invalid target_status -> 422
    resp2 = client.post(
        url,
        json={"action": "APPROVE", "target_status": "PROPOSED"},
        headers=headers,
    )
    assert resp2.status_code == 422
    assert "target_status to be None or APPROVED" in resp2.text

    # EDIT_AND_APPROVE with invalid target_status -> 422
    resp3 = client.post(
        url,
        json={
            "action": "EDIT_AND_APPROVE",
            "edited_answer": "Valid text",
            "target_status": "INSUFFICIENT_EVIDENCE",
        },
        headers=headers,
    )
    assert resp3.status_code == 422
    assert "target_status to be None or APPROVED" in resp3.text

    # REJECT with invalid target_status (APPROVED) -> 422
    resp4 = client.post(
        url,
        json={"action": "REJECT", "target_status": "APPROVED"},
        headers=headers,
    )
    assert resp4.status_code == 422
    assert "target_status to be None or NEEDS_REVIEW" in resp4.text

    # REJECT with invalid target_status (PROPOSED) -> 422
    resp5 = client.post(
        url,
        json={"action": "REJECT", "target_status": "PROPOSED"},
        headers=headers,
    )
    assert resp5.status_code == 422
    assert "target_status to be None or NEEDS_REVIEW" in resp5.text

    # REJECT with target_status=None -> 200 with revision.status == "NEEDS_REVIEW"
    resp6 = client.post(
        url,
        json={"action": "REJECT", "rejection_notes": "Not adequate"},
        headers=headers,
    )
    assert resp6.status_code == 200
    data6 = resp6.json()
    assert data6["action"] == "REJECT"
    assert data6["is_approved"] is False
    assert data6["revision"]["status"] == "NEEDS_REVIEW"
