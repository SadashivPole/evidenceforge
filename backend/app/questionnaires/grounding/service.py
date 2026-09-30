"""Deterministic evidence grounding for questionnaire version questions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence.citations.service import citation_from_candidate
from app.evidence.citations.types import EvidenceCitation
from app.evidence.persistence.search_repository import list_search_candidates
from app.evidence.search.policy import DEFAULT_SEARCH_POLICY, SearchPolicy
from app.evidence.search.service import normalize_query, search_chunks
from app.questionnaires.grounding.errors import (
    GroundingQueryValidationError,
    GroundingQuestionNotFoundError,
)
from app.questionnaires.grounding.policy import DEFAULT_GROUNDING_POLICY
from app.questionnaires.grounding.types import (
    GroundingResult,
    GroundingStatus,
)
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)


def normalize_grounding_query(
    question_text: str,
    *,
    policy: SearchPolicy = DEFAULT_SEARCH_POLICY,
) -> str:
    """Normalize and validate question text through the existing search path."""

    try:
        return normalize_query(question_text, policy=policy)
    except (TypeError, ValueError) as exc:
        raise GroundingQueryValidationError(
            "Question text is not a valid deterministic grounding query"
        ) from exc


def _load_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
) -> QuestionnaireVersionQuestion | None:
    """Load a question only through its workspace/version identity chain."""

    statement = (
        select(QuestionnaireVersionQuestion)
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
            QuestionnaireVersionQuestion.workspace_id == workspace_id,
            QuestionnaireVersionQuestion.id == questionnaire_version_question_id,
            QuestionnaireVersionQuestion.questionnaire_version_id == questionnaire_version_id,
        )
    )
    return db.scalar(statement)


def ground_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
    policy: SearchPolicy = DEFAULT_GROUNDING_POLICY,
    result_limit: int | None = None,
) -> GroundingResult:
    """Ground one authorized questionnaire question against persisted evidence.

    This function performs only reads. It does not create questionnaire responses,
    revisions, citations, or any other persisted records.
    """

    question = _load_question(
        db,
        workspace_id=workspace_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
    )
    if question is None:
        raise GroundingQuestionNotFoundError("Questionnaire version question not found")

    normalized_query = normalize_grounding_query(
        question.normalized_question_text,
        policy=policy,
    )
    effective_limit = policy.default_limit if result_limit is None else result_limit

    try:
        candidates = list_search_candidates(
            db,
            workspace_id=workspace_id,
        )
        results = search_chunks(
            candidates,
            normalized_query,
            policy=policy,
            limit=effective_limit,
        )
    except (TypeError, ValueError) as exc:
        raise GroundingQueryValidationError("Grounding search input is invalid") from exc

    citations: tuple[EvidenceCitation, ...] = tuple(
        citation_from_candidate(
            result.candidate,
            workspace_id=workspace_id,
        )
        for result in results
    )
    status = GroundingStatus.MATCHED if results else GroundingStatus.NO_MATCHES

    return GroundingResult(
        workspace_id=workspace_id,
        questionnaire_version_id=questionnaire_version_id,
        questionnaire_version_question_id=questionnaire_version_question_id,
        normalized_query=normalized_query,
        search_version=policy.search_version,
        result_limit=effective_limit,
        status=status,
        results=tuple(results),
        citations=citations,
    )


__all__ = ["ground_question", "normalize_grounding_query"]
