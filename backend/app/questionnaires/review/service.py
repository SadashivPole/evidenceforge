"""Human review execution and review context orchestration service."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import WorkspaceRole
from app.questionnaires.generation.types import GenerationContext, ValidatedDraftResponse
from app.questionnaires.generation.validator import is_stale_or_conflicting_evidence
from app.questionnaires.responses.errors import (
    EvidenceChunkCitationNotFoundError,
    QuestionnaireVersionQuestionNotFoundError,
    ResponseAuthorizationError,
    ResponsePersistenceError,
    ResponseValidationError,
)
from app.questionnaires.responses.service import save_response
from app.questionnaires.review.errors import (
    ReviewAuthorizationError,
    ReviewCitationValidationError,
    ReviewError,
    ReviewQuestionNotFoundError,
    ReviewValidationError,
    ReviewWorkspaceMismatchError,
)
from app.questionnaires.review.types import (
    ReviewAction,
    ReviewDecision,
    ReviewDraftContext,
    ReviewEvidenceItem,
    ReviewExecutionResult,
)
from app.questionnaires.types import ResponseStatus

_REVIEW_WRITE_ROLES = frozenset(
    {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
    }
)


def build_review_context(
    context: GenerationContext,
    draft: ValidatedDraftResponse,
) -> ReviewDraftContext:
    """Build a server-authorized ReviewDraftContext around a ValidatedDraftResponse."""

    if context.question_id != draft.question_id:
        raise ReviewValidationError(
            f"Question ID mismatch: context has {context.question_id}, "
            f"draft has {draft.question_id}"
        )

    if context.authorized_workspace_id != draft.workspace_id:
        raise ReviewWorkspaceMismatchError(
            f"Workspace mismatch: context has {context.authorized_workspace_id}, "
            f"draft has {draft.workspace_id}"
        )

    has_stale_or_conflict = any(
        is_stale_or_conflicting_evidence(item) for item in context.evidence_context
    )

    evidence_items = tuple(
        ReviewEvidenceItem(
            citation_handle=item.citation_handle,
            evidence_chunk_id=item.evidence_chunk_id,
            document_id=item.document_id,
            version_id=item.version_id,
            version_number=item.version_number,
            document_name=item.document_name,
            document_version_number=item.document_version_number,
            latest_document_version_number=item.latest_document_version_number,
            is_latest_document_version=item.is_latest_document_version,
            document_status=item.document_status,
            conflict_group_id=item.conflict_group_id,
            chunk_index=item.chunk_index,
            content_hash=item.content_hash,
            normalized_start_byte=item.normalized_start_byte,
            normalized_end_byte=item.normalized_end_byte,
            section_label=item.section_label,
            page_number=item.page_number,
            content=item.content,
            token_count=item.token_count,
            rrf_score=item.rrf_score,
            lexical_rank=item.lexical_rank,
            semantic_rank=item.semantic_rank,
            is_cited_by_draft=(item.evidence_chunk_id in draft.cited_chunk_ids),
        )
        for item in context.evidence_context
    )

    return ReviewDraftContext(
        question_id=context.question_id,
        questionnaire_id=context.questionnaire_id,
        questionnaire_version_id=context.questionnaire_version_id,
        questionnaire_version_question_id=context.questionnaire_version_question_id,
        question_text=context.question_text,
        section_path=context.section_path,
        authorized_workspace_id=context.authorized_workspace_id,
        draft_answer=draft.answer,
        proposed_status=draft.status,
        uncertainty_notes=draft.uncertainty_notes,
        evidence_items=evidence_items,
        cited_handles=draft.citation_handles,
        cited_chunk_ids=draft.cited_chunk_ids,
        resolved_citations=draft.resolved_citations,
        search_version=context.search_version,
        selection_version=context.selection_version,
        generation_boundary_version=context.generation_boundary_version,
        has_stale_or_conflicting_evidence=has_stale_or_conflict,
    )


def apply_review_decision(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    actor_role: WorkspaceRole,
    review_context: ReviewDraftContext,
    decision: ReviewDecision,
) -> ReviewExecutionResult:
    """Execute an explicit human review decision, persisting an immutable response revision."""

    # 1. Workspace role authorization
    if actor_role not in _REVIEW_WRITE_ROLES:
        raise ReviewAuthorizationError(
            f"Role '{actor_role.value}' is not permitted to review or approve "
            "questionnaire responses"
        )

    # 2. Workspace boundary check
    if review_context.authorized_workspace_id != workspace_id:
        raise ReviewWorkspaceMismatchError(
            f"Review context workspace {review_context.authorized_workspace_id} "
            f"does not match session workspace {workspace_id}"
        )

    # 3. Determine selected citation chunk IDs from decision or draft
    if decision.selected_citation_handles is not None and decision.selected_chunk_ids is not None:
        raise ReviewValidationError(
            "selected_citation_handles and selected_chunk_ids are mutually exclusive; "
            "provide at most one citation selector"
        )

    available_chunk_ids = {item.evidence_chunk_id: item for item in review_context.evidence_items}

    target_chunk_ids: list[uuid.UUID] = []

    if decision.selected_citation_handles is not None:
        for handle in decision.selected_citation_handles:
            item = review_context.get_evidence_by_handle(handle)
            if item is None:
                raise ReviewCitationValidationError(
                    f"Selected citation handle '{handle}' was not in the validated review context"
                )
            target_chunk_ids.append(item.evidence_chunk_id)
    elif decision.selected_chunk_ids is not None:
        for chunk_id in decision.selected_chunk_ids:
            if chunk_id not in available_chunk_ids:
                raise ReviewCitationValidationError(
                    f"Selected citation chunk ID '{chunk_id}' was not in "
                    "the validated review context"
                )
            target_chunk_ids.append(chunk_id)
    else:
        # Default to the validated draft's citations
        target_chunk_ids = list(review_context.cited_chunk_ids)

    # 4. Resolve action parameters and enforce state machine
    action = decision.action
    final_answer: str | None
    final_status: ResponseStatus

    if action in (ReviewAction.ACCEPT, ReviewAction.APPROVE):
        if decision.target_status is not None and decision.target_status != ResponseStatus.APPROVED:
            status_val = decision.target_status.value
            raise ReviewValidationError(
                f"Action '{action.value}' requires status APPROVED, got '{status_val}'"
            )
        final_answer = review_context.draft_answer
        final_status = ResponseStatus.APPROVED
        if not final_answer or not final_answer.strip():
            raise ReviewValidationError("Cannot approve a draft with an empty answer")
        if not target_chunk_ids:
            raise ReviewValidationError("Approved response requires at least one citation")

    elif action == ReviewAction.EDIT_AND_APPROVE:
        if decision.target_status is not None and decision.target_status != ResponseStatus.APPROVED:
            status_val = decision.target_status.value
            raise ReviewValidationError(
                f"Action 'EDIT_AND_APPROVE' requires status APPROVED, got '{status_val}'"
            )
        if decision.edited_answer is None or not decision.edited_answer.strip():
            raise ReviewValidationError("EDIT_AND_APPROVE requires a non-empty human-edited answer")
        final_answer = decision.edited_answer
        final_status = ResponseStatus.APPROVED
        if not target_chunk_ids:
            raise ReviewValidationError("Approved response requires at least one citation")

    elif action == ReviewAction.REJECT:
        if (
            decision.target_status is not None
            and decision.target_status != ResponseStatus.NEEDS_REVIEW
        ):
            status_val = decision.target_status.value
            raise ReviewValidationError(
                f"Action 'REJECT' requires status NEEDS_REVIEW, got '{status_val}'"
            )
        final_status = ResponseStatus.NEEDS_REVIEW
        final_answer = decision.edited_answer or review_context.draft_answer
    else:
        raise ReviewValidationError(f"Unsupported review action '{action}'")

    # 5. Persist through response service (maintains immutable revisions, locking, audit)
    try:
        persist_res = save_response(
            db,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            questionnaire_id=review_context.questionnaire_id,
            questionnaire_version_id=review_context.questionnaire_version_id,
            questionnaire_version_question_id=review_context.questionnaire_version_question_id,
            answer=final_answer,
            status=final_status,
            citation_chunk_ids=tuple(target_chunk_ids),
            actor_role=actor_role,
        )
    except ResponseAuthorizationError as exc:
        raise ReviewAuthorizationError(str(exc)) from exc
    except EvidenceChunkCitationNotFoundError as exc:
        raise ReviewCitationValidationError(str(exc)) from exc
    except QuestionnaireVersionQuestionNotFoundError as exc:
        raise ReviewQuestionNotFoundError(str(exc)) from exc
    except ResponseValidationError as exc:
        raise ReviewValidationError(str(exc)) from exc
    except ResponsePersistenceError as exc:
        raise ReviewError(str(exc)) from exc

    # 6. Additional review audit trail
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        action="questionnaire.response.reviewed",
        resource_type="questionnaire_response",
        resource_id=str(persist_res.response.id),
        metadata={
            "questionnaire_id": str(review_context.questionnaire_id),
            "questionnaire_version_id": str(review_context.questionnaire_version_id),
            "questionnaire_version_question_id": str(
                review_context.questionnaire_version_question_id
            ),
            "revision_id": str(persist_res.revision.id),
            "revision_number": persist_res.revision.revision_number,
            "review_action": action.value,
            "status": final_status.value,
            "is_approved": (final_status == ResponseStatus.APPROVED),
            "citation_count": len(persist_res.citations),
            "rejection_notes": decision.rejection_notes,
        },
    )
    db.commit()

    return ReviewExecutionResult(
        response=persist_res.response,
        revision=persist_res.revision,
        citations=persist_res.citations,
        action=action,
        is_approved=(final_status == ResponseStatus.APPROVED),
        revision_created=persist_res.revision_created,
    )
