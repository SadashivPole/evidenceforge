"""Transactional questionnaire response and citation persistence service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import WorkspaceRole
from app.questionnaires.responses.errors import (
    EvidenceChunkCitationNotFoundError,
    QuestionnaireVersionQuestionNotFoundError,
    ResponseAuthorizationError,
    ResponseIntegrityError,
    ResponseNotFoundError,
    ResponsePersistenceError,
    ResponseValidationError,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)
from app.questionnaires.responses.repositories import (
    EvidenceCitationTarget,
    get_current_revision,
    get_response,
    get_response_for_question,
    get_version_question,
    list_responses_for_version,
    list_revision_citations,
    list_revisions,
    lock_response,
    next_revision_number,
    resolve_evidence_chunks,
)
from app.questionnaires.types import ResponseStatus

_NULL_ANSWER_STATUSES = frozenset(
    {
        ResponseStatus.INSUFFICIENT_EVIDENCE,
        ResponseStatus.NOT_APPLICABLE,
        ResponseStatus.DO_NOT_DISCLOSE,
    }
)
_MAX_CONCURRENCY_RETRIES = 3


@dataclass(frozen=True, slots=True)
class ResponsePersistenceResult:
    """Committed response mutation result."""

    response: QuestionnaireResponse
    revision: QuestionnaireResponseRevision
    citations: tuple[QuestionnaireResponseCitation, ...]
    revision_created: bool


@dataclass(frozen=True, slots=True)
class ResponseReadResult:
    """Response plus its complete immutable revision history."""

    response: QuestionnaireResponse
    revisions: tuple[QuestionnaireResponseRevision, ...]
    citations_by_revision: dict[uuid.UUID, tuple[QuestionnaireResponseCitation, ...]]


def _coerce_status(status: ResponseStatus | str) -> ResponseStatus:
    try:
        return status if isinstance(status, ResponseStatus) else ResponseStatus(status)
    except ValueError as exc:
        raise ResponseValidationError("Unsupported response status") from exc


def _validate_response_values(
    *,
    answer: str | None,
    status: ResponseStatus,
    citation_chunk_ids: tuple[uuid.UUID, ...],
) -> None:
    """Validate only the approved Phase 1K status rules."""

    if len(set(citation_chunk_ids)) != len(citation_chunk_ids):
        raise ResponseValidationError("A citation chunk cannot be supplied more than once")

    if answer is None and status not in _NULL_ANSWER_STATUSES:
        raise ResponseValidationError("This response status requires an answer")

    if status is ResponseStatus.CONFLICTING_SOURCES and len(citation_chunk_ids) < 2:
        raise ResponseValidationError("CONFLICTING_SOURCES requires at least two citations")

    if status is ResponseStatus.APPROVED and not citation_chunk_ids:
        raise ResponseValidationError("APPROVED requires at least one citation")


def _is_identical_update(
    *,
    current: QuestionnaireResponseRevision,
    current_citations: tuple[QuestionnaireResponseCitation, ...],
    answer: str | None,
    status: ResponseStatus,
    citation_chunk_ids: tuple[uuid.UUID, ...],
) -> bool:
    """Return whether a write has exactly the current immutable state."""

    existing_ids = tuple(citation.evidence_chunk_id for citation in current_citations)
    return (
        current.answer == answer
        and current.status == status.value
        and existing_ids == citation_chunk_ids
    )


def _build_citations(
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
    targets: tuple[EvidenceCitationTarget, ...],
) -> tuple[QuestionnaireResponseCitation, ...]:
    """Build relationship rows from server-resolved evidence metadata."""

    return tuple(
        QuestionnaireResponseCitation(
            workspace_id=workspace_id,
            response_revision_id=revision_id,
            citation_order=citation_order,
            evidence_document_id=target.document_id,
            evidence_version_id=target.version_id,
            evidence_chunk_id=target.chunk_id,
            evidence_version_number=target.version_number,
            evidence_chunk_index=target.chunk_index,
            content_hash=target.content_hash,
            normalized_start_byte=target.normalized_start_byte,
            normalized_end_byte=target.normalized_end_byte,
            section_label=target.section_label,
            page_number=target.page_number,
        )
        for citation_order, target in enumerate(targets, start=1)
    )


def _persist_once(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    questionnaire_id: uuid.UUID | None,
    answer: str | None,
    status: ResponseStatus,
    citation_chunk_ids: tuple[uuid.UUID, ...],
) -> ResponsePersistenceResult:
    question = get_version_question(
        db,
        workspace_id=workspace_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
    )
    if question is None or (
        questionnaire_id is not None and question.questionnaire_id != questionnaire_id
    ):
        raise QuestionnaireVersionQuestionNotFoundError("Questionnaire version question not found")

    targets = resolve_evidence_chunks(
        db,
        workspace_id=workspace_id,
        chunk_ids=citation_chunk_ids,
    )
    if len(targets) != len(citation_chunk_ids):
        raise EvidenceChunkCitationNotFoundError(
            "One or more citation chunks are not in the workspace"
        )

    response = lock_response(
        db,
        workspace_id=workspace_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
    )

    if response is None:
        response = QuestionnaireResponse(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            questionnaire_id=question.questionnaire_id,
            questionnaire_version_id=questionnaire_version_id,
            questionnaire_version_question_id=questionnaire_version_question_id,
        )
        db.add(response)
        db.flush()
        revision_number = 1
    else:
        current = get_current_revision(
            db,
            workspace_id=workspace_id,
            response_id=response.id,
        )
        if current is None:
            raise ResponseIntegrityError("Response has no revision history")

        current_citations = list_revision_citations(
            db,
            workspace_id=workspace_id,
            response_revision_id=current.id,
        )
        if _is_identical_update(
            current=current,
            current_citations=current_citations,
            answer=answer,
            status=status,
            citation_chunk_ids=citation_chunk_ids,
        ):
            db.commit()
            return ResponsePersistenceResult(
                response=response,
                revision=current,
                citations=current_citations,
                revision_created=False,
            )
        revision_number = next_revision_number(
            db,
            workspace_id=workspace_id,
            response_id=response.id,
        )

    revision = QuestionnaireResponseRevision(
        workspace_id=workspace_id,
        response_id=response.id,
        revision_number=revision_number,
        answer=answer,
        status=status.value,
        author_user_id=actor_user_id,
    )
    db.add(revision)
    db.flush()

    citations = _build_citations(
        workspace_id=workspace_id,
        revision_id=revision.id,
        targets=targets,
    )
    db.add_all(citations)

    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        action=(
            "questionnaire.response.created"
            if revision_number == 1
            else "questionnaire.response.revised"
        ),
        resource_type="questionnaire_response",
        resource_id=str(response.id),
        metadata={
            "questionnaire_id": str(response.questionnaire_id),
            "questionnaire_version_id": str(response.questionnaire_version_id),
            "questionnaire_version_question_id": str(response.questionnaire_version_question_id),
            "revision_id": str(revision.id),
            "revision_number": revision.revision_number,
            "status": status.value,
            "citation_count": len(citations),
        },
    )

    db.commit()
    return ResponsePersistenceResult(
        response=response,
        revision=revision,
        citations=citations,
        revision_created=True,
    )


def save_response(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    answer: str | None,
    status: ResponseStatus | str,
    citation_chunk_ids: tuple[uuid.UUID, ...] = (),
    questionnaire_id: uuid.UUID | None = None,
    actor_role: WorkspaceRole,
) -> ResponsePersistenceResult:
    """Create or revise one response in one transaction.

    The logical response and revision-number uniqueness constraints are backed by
    a short retry loop. When two PostgreSQL transactions create the same logical
    response concurrently, the losing transaction rolls back and retries against
    the row created by the winner before allocating its revision number.
    """

    response_status = _coerce_status(status)
    normalized_citation_ids = tuple(citation_chunk_ids)
    _validate_response_values(
        answer=answer,
        status=response_status,
        citation_chunk_ids=normalized_citation_ids,
    )

    if actor_role not in {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
    }:
        raise ResponseAuthorizationError("Insufficient workspace role")

    if response_status is ResponseStatus.APPROVED and actor_role not in {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    }:
        raise ResponseAuthorizationError("Only OWNER or ADMIN may approve responses")

    for attempt in range(_MAX_CONCURRENCY_RETRIES):
        try:
            return _persist_once(
                db,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                questionnaire_version_id=questionnaire_version_id,
                questionnaire_version_question_id=questionnaire_version_question_id,
                questionnaire_id=questionnaire_id,
                answer=answer,
                status=response_status,
                citation_chunk_ids=normalized_citation_ids,
            )
        except (
            ResponseValidationError,
            QuestionnaireVersionQuestionNotFoundError,
            ResponseAuthorizationError,
            ResponseIntegrityError,
        ):
            db.rollback()
            raise
        except IntegrityError as exc:
            db.rollback()
            if attempt + 1 < _MAX_CONCURRENCY_RETRIES:
                continue
            raise ResponseIntegrityError(
                "Questionnaire response persistence violated an integrity constraint"
            ) from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise ResponsePersistenceError("Questionnaire response persistence failed") from exc
        except Exception:
            db.rollback()
            raise

    raise ResponseIntegrityError("Questionnaire response concurrency retry exhausted")


def get_response_or_raise(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    response_id: uuid.UUID,
) -> ResponseReadResult:
    """Read one response and every immutable revision/citation relationship."""

    response = get_response(
        db,
        workspace_id=workspace_id,
        response_id=response_id,
    )
    if response is None:
        raise ResponseNotFoundError("Questionnaire response not found")

    revisions = list_revisions(
        db,
        workspace_id=workspace_id,
        response_id=response.id,
    )
    if not revisions:
        raise ResponseIntegrityError("Questionnaire response has no revisions")

    citations_by_revision = {
        revision.id: list_revision_citations(
            db,
            workspace_id=workspace_id,
            response_revision_id=revision.id,
        )
        for revision in revisions
    }
    return ResponseReadResult(
        response=response,
        revisions=revisions,
        citations_by_revision=citations_by_revision,
    )


def list_latest_responses_for_version(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
) -> tuple[ResponseReadResult, ...]:
    """Return the latest revision for each response in one questionnaire version."""

    responses = list_responses_for_version(
        db,
        workspace_id=workspace_id,
        questionnaire_id=questionnaire_id,
        questionnaire_version_id=questionnaire_version_id,
    )
    results: list[ResponseReadResult] = []
    for response in responses:
        revision = get_current_revision(
            db,
            workspace_id=workspace_id,
            response_id=response.id,
        )
        if revision is None:
            raise ResponseIntegrityError("Questionnaire response has no revisions")
        results.append(
            ResponseReadResult(
                response=response,
                revisions=(revision,),
                citations_by_revision={
                    revision.id: list_revision_citations(
                        db,
                        workspace_id=workspace_id,
                        response_revision_id=revision.id,
                    )
                },
            )
        )
    return tuple(results)


def get_response_for_question_or_none(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
) -> ResponseReadResult | None:
    """Read the response for a version question, if one has been created."""

    response = get_response_for_question(
        db,
        workspace_id=workspace_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
    )
    if response is None:
        return None
    return get_response_or_raise(
        db,
        workspace_id=workspace_id,
        response_id=response.id,
    )


# Explicit aliases keep the service vocabulary discoverable for callers that use
# "persist" or "create_or_update" terminology.
persist_response = save_response
create_or_update_response = save_response
