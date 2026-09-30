"""Repository helpers for transactional questionnaire persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.questionnaires.persistence.models import (
    Questionnaire,
    QuestionnaireImportAttempt,
    QuestionnaireQuestion,
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)


def lock_questionnaire(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
) -> Questionnaire | None:
    """Load and lock one questionnaire inside the authorized workspace."""

    statement = (
        select(Questionnaire)
        .where(
            Questionnaire.id == questionnaire_id,
            Questionnaire.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    return db.scalar(statement)


def find_questionnaire_by_name(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    name: str,
) -> Questionnaire | None:
    """Load one questionnaire by normalized workspace-scoped name."""

    return db.scalar(
        select(Questionnaire).where(
            Questionnaire.workspace_id == workspace_id,
            Questionnaire.name == name,
        )
    )


def find_duplicate_version(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    canonical_hash_version: str,
    canonical_sha256: str,
) -> QuestionnaireVersion | None:
    """Find an existing immutable canonical representation."""

    return db.scalar(
        select(QuestionnaireVersion)
        .where(
            QuestionnaireVersion.workspace_id == workspace_id,
            QuestionnaireVersion.questionnaire_id == questionnaire_id,
            QuestionnaireVersion.canonical_hash_version == canonical_hash_version,
            QuestionnaireVersion.canonical_sha256 == canonical_sha256,
        )
        .limit(1)
    )


def next_version_number(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
) -> int:
    """Return the next version number while the parent row is locked."""

    latest = db.scalar(
        select(func.max(QuestionnaireVersion.version_number)).where(
            QuestionnaireVersion.workspace_id == workspace_id,
            QuestionnaireVersion.questionnaire_id == questionnaire_id,
        )
    )
    return (latest or 0) + 1


def find_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    question_id: str,
) -> QuestionnaireQuestion | None:
    """Find one stable logical question identity."""

    return db.scalar(
        select(QuestionnaireQuestion).where(
            QuestionnaireQuestion.workspace_id == workspace_id,
            QuestionnaireQuestion.questionnaire_id == questionnaire_id,
            QuestionnaireQuestion.question_id == question_id,
        )
    )


def list_version_questions(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
) -> tuple[QuestionnaireVersionQuestion, ...]:
    """List immutable snapshots for one authorized version."""

    statement = (
        select(QuestionnaireVersionQuestion)
        .where(
            QuestionnaireVersionQuestion.workspace_id == workspace_id,
            QuestionnaireVersionQuestion.questionnaire_id == questionnaire_id,
            QuestionnaireVersionQuestion.questionnaire_version_id == questionnaire_version_id,
        )
        .order_by(
            QuestionnaireVersionQuestion.ordinal,
            QuestionnaireVersionQuestion.id,
        )
    )
    return tuple(db.scalars(statement))


def list_import_attempts(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID | None = None,
) -> tuple[QuestionnaireImportAttempt, ...]:
    """List immutable import provenance in deterministic order."""

    statement = select(QuestionnaireImportAttempt).where(
        QuestionnaireImportAttempt.workspace_id == workspace_id,
    )

    if questionnaire_id is not None:
        statement = statement.where(
            QuestionnaireImportAttempt.questionnaire_id == questionnaire_id,
        )

    statement = statement.order_by(
        QuestionnaireImportAttempt.created_at,
        QuestionnaireImportAttempt.id,
    )

    return tuple(db.scalars(statement))
