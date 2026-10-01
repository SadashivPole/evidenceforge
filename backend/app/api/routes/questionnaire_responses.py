"""Workspace-scoped questionnaire response and citation routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    QuestionnaireGroundingCandidateResponse,
    QuestionnaireGroundingCitationResponse,
    QuestionnaireGroundingResponse,
    QuestionnaireGroundingSearchResultResponse,
    QuestionnaireResponseCitationResponse,
    QuestionnaireResponseLatestResponse,
    QuestionnaireResponseResponse,
    QuestionnaireResponseRevisionResponse,
    QuestionnaireResponseUpsert,
)
from app.auth import WorkspaceContext, assert_workspace_role, get_workspace_context
from app.db import get_db
from app.models import WorkspaceRole
from app.questionnaires.grounding.errors import (
    GroundingError,
    GroundingQueryValidationError,
    GroundingQuestionNotFoundError,
)
from app.questionnaires.grounding.service import ground_question
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.responses.errors import (
    EvidenceChunkCitationNotFoundError,
    QuestionnaireVersionQuestionNotFoundError,
    ResponseAuthorizationError,
    ResponseIntegrityError,
    ResponseNotFoundError,
    ResponsePersistenceError,
    ResponseValidationError,
)
from app.questionnaires.responses.service import (
    ResponseReadResult,
    get_response_for_question_or_none,
    get_response_or_raise,
    list_latest_responses_for_version,
    save_response,
)

router = APIRouter(prefix="/workspaces", tags=["questionnaire-responses"])

DbSession = Annotated[Session, Depends(get_db)]
WorkspaceAccess = Annotated[WorkspaceContext, Depends(get_workspace_context)]

_WRITE_ROLES = (
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
    WorkspaceRole.MEMBER,
)

_RESPONSE_PATH = (
    "/{workspace_id}/questionnaires/{questionnaire_id}"
    "/versions/{questionnaire_version_id}"
    "/questions/{questionnaire_version_question_id}/response"
)

_GROUNDING_PATH = (
    "/{workspace_id}/questionnaires/{questionnaire_id}"
    "/versions/{questionnaire_version_id}"
    "/questions/{questionnaire_version_question_id}/grounding"
)


def _response_http_error(exc: ResponsePersistenceError) -> HTTPException:
    """Map response persistence failures without exposing database details."""

    if isinstance(exc, ResponseAuthorizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient workspace role for this response operation",
        )

    if isinstance(exc, ResponseNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire response not found",
        )

    if isinstance(exc, QuestionnaireVersionQuestionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire version question not found",
        )

    if isinstance(exc, EvidenceChunkCitationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="One or more citation targets are invalid",
        )

    if isinstance(exc, ResponseValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Questionnaire response failed validation",
        )

    if isinstance(exc, ResponseIntegrityError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Questionnaire response could not be persisted",
        )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Questionnaire response persistence failed",
    )


def _grounding_http_error(exc: GroundingError) -> HTTPException:
    """Map grounding failures to safe API responses."""

    if isinstance(exc, GroundingQuestionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire version question not found",
        )

    if isinstance(exc, GroundingQueryValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Questionnaire grounding query failed validation",
        )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Questionnaire grounding failed",
    )


def _citation_response(
    citation,
) -> QuestionnaireResponseCitationResponse:
    return QuestionnaireResponseCitationResponse(
        id=citation.id,
        response_revision_id=citation.response_revision_id,
        citation_order=citation.citation_order,
        evidence_document_id=citation.evidence_document_id,
        evidence_version_id=citation.evidence_version_id,
        evidence_chunk_id=citation.evidence_chunk_id,
        evidence_version_number=citation.evidence_version_number,
        evidence_chunk_index=citation.evidence_chunk_index,
        content_hash=citation.content_hash,
        normalized_start_byte=citation.normalized_start_byte,
        normalized_end_byte=citation.normalized_end_byte,
        section_label=citation.section_label,
        page_number=citation.page_number,
        created_at=citation.created_at,
    )


def _revision_response(
    result: ResponseReadResult,
    revision,
) -> QuestionnaireResponseRevisionResponse:
    return QuestionnaireResponseRevisionResponse(
        id=revision.id,
        response_id=revision.response_id,
        revision_number=revision.revision_number,
        answer=revision.answer,
        status=revision.status,
        author_user_id=revision.author_user_id,
        created_at=revision.created_at,
        citations=[
            _citation_response(citation)
            for citation in result.citations_by_revision.get(revision.id, ())
        ],
    )


def _response_response(
    result: ResponseReadResult,
) -> QuestionnaireResponseResponse:
    revisions = [_revision_response(result, revision) for revision in result.revisions]
    return QuestionnaireResponseResponse(
        id=result.response.id,
        workspace_id=result.response.workspace_id,
        created_by_user_id=result.response.created_by_user_id,
        questionnaire_id=result.response.questionnaire_id,
        questionnaire_version_id=result.response.questionnaire_version_id,
        questionnaire_version_question_id=(result.response.questionnaire_version_question_id),
        current_revision=revisions[-1],
        revisions=revisions,
    )


def _latest_response_response(
    result: ResponseReadResult,
) -> QuestionnaireResponseLatestResponse:
    latest = result.revisions[-1]
    return QuestionnaireResponseLatestResponse(
        id=result.response.id,
        workspace_id=result.response.workspace_id,
        created_by_user_id=result.response.created_by_user_id,
        questionnaire_id=result.response.questionnaire_id,
        questionnaire_version_id=result.response.questionnaire_version_id,
        questionnaire_version_question_id=(result.response.questionnaire_version_question_id),
        current_revision=_revision_response(result, latest),
    )


def _grounding_question_in_scope(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
) -> bool:
    """Check the complete questionnaire/version/question path in one workspace."""

    statement = (
        select(QuestionnaireVersionQuestion.id)
        .join(
            QuestionnaireVersion,
            (
                (QuestionnaireVersion.id == QuestionnaireVersionQuestion.questionnaire_version_id)
                & (QuestionnaireVersion.workspace_id == QuestionnaireVersionQuestion.workspace_id)
                & (
                    QuestionnaireVersion.questionnaire_id
                    == QuestionnaireVersionQuestion.questionnaire_id
                )
            ),
        )
        .where(
            QuestionnaireVersion.workspace_id == workspace_id,
            QuestionnaireVersion.id == questionnaire_version_id,
            QuestionnaireVersion.questionnaire_id == questionnaire_id,
            QuestionnaireVersionQuestion.workspace_id == workspace_id,
            QuestionnaireVersionQuestion.id == questionnaire_version_question_id,
            QuestionnaireVersionQuestion.questionnaire_version_id == questionnaire_version_id,
        )
    )
    return db.scalar(statement) is not None


def _grounding_response(
    result,
    *,
    questionnaire_id: uuid.UUID,
) -> QuestionnaireGroundingResponse:
    """Convert the domain grounding result into the public API schema."""

    return QuestionnaireGroundingResponse(
        workspace_id=result.workspace_id,
        questionnaire_id=questionnaire_id,
        questionnaire_version_id=result.questionnaire_version_id,
        questionnaire_version_question_id=result.questionnaire_version_question_id,
        normalized_query=result.normalized_query,
        search_version=result.search_version,
        result_limit=result.result_limit,
        status=result.status,
        results=[
            QuestionnaireGroundingSearchResultResponse(
                candidate=QuestionnaireGroundingCandidateResponse(
                    chunk_id=search_result.candidate.chunk_id,
                    document_id=search_result.candidate.document_id,
                    version_id=search_result.candidate.version_id,
                    version_number=search_result.candidate.version_number,
                    chunk_index=search_result.candidate.chunk_index,
                    content=search_result.candidate.content,
                    content_hash=search_result.candidate.content_hash,
                    normalized_start_byte=(search_result.candidate.normalized_start_byte),
                    normalized_end_byte=search_result.candidate.normalized_end_byte,
                    section_label=search_result.candidate.section_label,
                    page_number=search_result.candidate.page_number,
                    document_status=search_result.candidate.document_status,
                    document_version_number=search_result.candidate.document_version_number,
                    latest_document_version_number=(
                        search_result.candidate.latest_document_version_number
                    ),
                    is_latest_document_version=search_result.candidate.is_latest_document_version,
                    conflict_group_id=search_result.candidate.conflict_group_id,
                ),
                score=search_result.score,
                matched_terms=list(search_result.matched_terms),
                exact_phrase_match=search_result.exact_phrase_match,
                occurrence_count=search_result.occurrence_count,
                rrf_score=getattr(search_result, "rrf_score", None),
                lexical_rank=getattr(search_result, "lexical_rank", None),
                semantic_rank=getattr(search_result, "semantic_rank", None),
            )
            for search_result in result.results
        ],
        citations=[
            QuestionnaireGroundingCitationResponse(
                workspace_id=citation.workspace_id,
                document_id=citation.document_id,
                version_id=citation.version_id,
                version_number=citation.version_number,
                chunk_id=citation.chunk_id,
                chunk_index=citation.chunk_index,
                content_hash=citation.content_hash,
                normalized_start_byte=citation.normalized_start_byte,
                normalized_end_byte=citation.normalized_end_byte,
                section_label=citation.section_label,
                page_number=citation.page_number,
            )
            for citation in result.citations
        ],
    )


@router.get(
    _GROUNDING_PATH,
    response_model=QuestionnaireGroundingResponse,
)
def ground_questionnaire_question(
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> QuestionnaireGroundingResponse:
    """Return deterministic evidence grounding for one authorized question."""

    if not _grounding_question_in_scope(
        db,
        workspace_id=context.workspace.id,
        questionnaire_id=questionnaire_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
    ):
        raise _grounding_http_error(
            GroundingQuestionNotFoundError(
                "Questionnaire version question not found",
            )
        )

    try:
        result = ground_question(
            db,
            workspace_id=context.workspace.id,
            questionnaire_version_id=questionnaire_version_id,
            questionnaire_version_question_id=questionnaire_version_question_id,
        )
    except GroundingError as exc:
        raise _grounding_http_error(exc) from exc

    return _grounding_response(
        result,
        questionnaire_id=questionnaire_id,
    )


def _upsert_response(
    *,
    context: WorkspaceContext,
    db: Session,
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    payload: QuestionnaireResponseUpsert,
) -> QuestionnaireResponseResponse:
    """Authorize and persist one single-response mutation."""

    assert_workspace_role(context, db, *_WRITE_ROLES)

    try:
        result = save_response(
            db,
            workspace_id=context.workspace.id,
            actor_user_id=context.user.id,
            questionnaire_id=questionnaire_id,
            questionnaire_version_id=questionnaire_version_id,
            questionnaire_version_question_id=questionnaire_version_question_id,
            answer=payload.answer,
            status=payload.status,
            citation_chunk_ids=tuple(payload.citation_chunk_ids),
            actor_role=context.membership.role,
        )
        read_result = get_response_or_raise(
            db,
            workspace_id=context.workspace.id,
            response_id=result.response.id,
        )
    except ResponsePersistenceError as exc:
        raise _response_http_error(exc) from exc

    return _response_response(read_result)


@router.put(
    _RESPONSE_PATH,
    response_model=QuestionnaireResponseResponse,
    status_code=status.HTTP_200_OK,
)
def upsert_questionnaire_response(
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    payload: QuestionnaireResponseUpsert,
    context: WorkspaceAccess,
    db: DbSession,
) -> QuestionnaireResponseResponse:
    """Create or revise one response for a questionnaire-version question."""

    return _upsert_response(
        context=context,
        db=db,
        questionnaire_id=questionnaire_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
        payload=payload,
    )


@router.get(
    _RESPONSE_PATH,
    response_model=QuestionnaireResponseResponse,
)
def read_questionnaire_response(
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> QuestionnaireResponseResponse:
    """Read one response and all revisions for a version-specific question."""

    try:
        result = get_response_for_question_or_none(
            db,
            workspace_id=context.workspace.id,
            questionnaire_version_id=questionnaire_version_id,
            questionnaire_version_question_id=questionnaire_version_question_id,
        )
    except ResponsePersistenceError as exc:
        raise _response_http_error(exc) from exc

    if result is None or result.response.questionnaire_id != questionnaire_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire response not found",
        )
    return _response_response(result)


@router.get(
    "/{workspace_id}/questionnaires/{questionnaire_id}"
    "/versions/{questionnaire_version_id}/responses",
    response_model=list[QuestionnaireResponseLatestResponse],
)
def list_questionnaire_version_responses(
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> list[QuestionnaireResponseLatestResponse]:
    """Return the latest revision for each response in a questionnaire version."""

    try:
        results = list_latest_responses_for_version(
            db,
            workspace_id=context.workspace.id,
            questionnaire_id=questionnaire_id,
            questionnaire_version_id=questionnaire_version_id,
        )
    except ResponsePersistenceError as exc:
        raise _response_http_error(exc) from exc
    return [_latest_response_response(result) for result in results]


@router.get(
    _RESPONSE_PATH + "/history",
    response_model=list[QuestionnaireResponseRevisionResponse],
)
def read_questionnaire_response_history(
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> list[QuestionnaireResponseRevisionResponse]:
    """Read immutable response history through the questionnaire URL."""

    try:
        result = get_response_for_question_or_none(
            db,
            workspace_id=context.workspace.id,
            questionnaire_version_id=questionnaire_version_id,
            questionnaire_version_question_id=questionnaire_version_question_id,
        )
    except ResponsePersistenceError as exc:
        raise _response_http_error(exc) from exc

    if result is None or result.response.questionnaire_id != questionnaire_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire response not found",
        )
    return [_revision_response(result, revision) for revision in result.revisions]


__all__ = ["router"]
