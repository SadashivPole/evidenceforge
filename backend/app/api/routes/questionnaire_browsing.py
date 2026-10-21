"""Workspace-authorized browsing endpoints for the guided questionnaire UI."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    QuestionnaireBrowserItem,
    QuestionnaireVersionBrowserItem,
    QuestionnaireVersionQuestionBrowserItem,
)
from app.audit import record_audit_event
from app.auth import WorkspaceContext, get_workspace_context
from app.db import get_db
from app.questionnaires.persistence.models import (
    Questionnaire,
    QuestionnaireQuestion,
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)

router = APIRouter(prefix="/workspaces", tags=["questionnaire-browser"])

DbSession = Annotated[Session, Depends(get_db)]
WorkspaceAccess = Annotated[WorkspaceContext, Depends(get_workspace_context)]


@router.get(
    "/{workspace_id}/questionnaires",
    response_model=list[QuestionnaireBrowserItem],
)
def list_questionnaires(
    context: WorkspaceAccess,
    db: DbSession,
) -> list[QuestionnaireBrowserItem]:
    """List readable questionnaire names and each one's latest version."""

    questionnaires = list(
        db.scalars(
            select(Questionnaire)
            .where(Questionnaire.workspace_id == context.workspace.id)
            .order_by(Questionnaire.name, Questionnaire.id)
        )
    )

    items: list[QuestionnaireBrowserItem] = []
    latest_by_questionnaire_id: dict[uuid.UUID, QuestionnaireVersion] = {}

    if questionnaires:
        # Find the latest version number for each questionnaire in this workspace.
        # This avoids executing one additional query for every questionnaire.
        latest_version_numbers = (
            select(
                QuestionnaireVersion.questionnaire_id.label("questionnaire_id"),
                func.max(QuestionnaireVersion.version_number).label("latest_version_number"),
            )
            .where(
                QuestionnaireVersion.workspace_id == context.workspace.id,
            )
            .group_by(QuestionnaireVersion.questionnaire_id)
            .subquery()
        )

        # Fetch all latest versions in a single query.
        latest_versions = db.scalars(
            select(QuestionnaireVersion)
            .join(
                latest_version_numbers,
                (QuestionnaireVersion.questionnaire_id == latest_version_numbers.c.questionnaire_id)
                & (
                    QuestionnaireVersion.version_number
                    == latest_version_numbers.c.latest_version_number
                ),
            )
            .where(QuestionnaireVersion.workspace_id == context.workspace.id)
        )

        latest_by_questionnaire_id = {
            version.questionnaire_id: version for version in latest_versions
        }

    for questionnaire in questionnaires:
        latest = latest_by_questionnaire_id.get(questionnaire.id)

        items.append(
            QuestionnaireBrowserItem(
                id=questionnaire.id,
                name=questionnaire.name,
                created_at=questionnaire.created_at,
                latest_version_id=latest.id if latest else None,
                latest_version_number=latest.version_number if latest else None,
                source_filename=latest.source_filename if latest else None,
                imported_at=latest.created_at if latest else None,
                question_count=latest.question_count if latest else 0,
            )
        )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="questionnaire.listed",
        resource_type="questionnaire_collection",
        resource_id=str(context.workspace.id),
        metadata={"count": len(items)},
    )
    db.commit()
    return items


@router.get(
    "/{workspace_id}/questionnaires/{questionnaire_id}/versions",
    response_model=list[QuestionnaireVersionBrowserItem],
)
def list_questionnaire_versions(
    questionnaire_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> list[QuestionnaireVersionBrowserItem]:
    """List immutable versions for a questionnaire inside the authorized workspace."""

    questionnaire = db.scalar(
        select(Questionnaire).where(
            Questionnaire.id == questionnaire_id,
            Questionnaire.workspace_id == context.workspace.id,
        )
    )
    if questionnaire is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire not found",
        )

    versions = list(
        db.scalars(
            select(QuestionnaireVersion)
            .where(
                QuestionnaireVersion.workspace_id == context.workspace.id,
                QuestionnaireVersion.questionnaire_id == questionnaire_id,
            )
            .order_by(QuestionnaireVersion.version_number.desc())
        )
    )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="questionnaire.versions_listed",
        resource_type="questionnaire",
        resource_id=str(questionnaire_id),
        metadata={"count": len(versions)},
    )
    db.commit()

    return [
        QuestionnaireVersionBrowserItem(
            id=version.id,
            questionnaire_id=version.questionnaire_id,
            version_number=version.version_number,
            source_filename=version.source_filename,
            question_count=version.question_count,
            created_at=version.created_at,
        )
        for version in versions
    ]


@router.get(
    "/{workspace_id}/questionnaires/{questionnaire_id}"
    "/versions/{questionnaire_version_id}/questions",
    response_model=list[QuestionnaireVersionQuestionBrowserItem],
)
def list_questionnaire_version_questions(
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> list[QuestionnaireVersionQuestionBrowserItem]:
    """List source-worded questions with the version-question UUID grounding requires."""

    version = db.scalar(
        select(QuestionnaireVersion).where(
            QuestionnaireVersion.id == questionnaire_version_id,
            QuestionnaireVersion.questionnaire_id == questionnaire_id,
            QuestionnaireVersion.workspace_id == context.workspace.id,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire version not found",
        )

    rows = db.execute(
        select(QuestionnaireVersionQuestion, QuestionnaireQuestion)
        .join(
            QuestionnaireQuestion,
            QuestionnaireQuestion.id == QuestionnaireVersionQuestion.questionnaire_question_id,
        )
        .where(
            QuestionnaireVersionQuestion.workspace_id == context.workspace.id,
            QuestionnaireVersionQuestion.questionnaire_id == questionnaire_id,
            QuestionnaireVersionQuestion.questionnaire_version_id == questionnaire_version_id,
        )
        .order_by(
            QuestionnaireVersionQuestion.ordinal,
            QuestionnaireVersionQuestion.id,
        )
    ).all()

    items = [
        QuestionnaireVersionQuestionBrowserItem(
            id=version_question.id,
            ordinal=version_question.ordinal,
            source_row=version_question.source_row,
            sheet_name=version_question.sheet_name,
            section_path=list(version_question.section_path or []),
            question_text=(
                version_question.source_question_text or version_question.normalized_question_text
            ),
            source_question_id=version_question.source_question_id,
            stable_question_id=logical_question.question_id,
        )
        for version_question, logical_question in rows
    ]

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="questionnaire.questions_listed",
        resource_type="questionnaire_version",
        resource_id=str(questionnaire_version_id),
        metadata={"count": len(items)},
    )
    db.commit()
    return items


__all__ = ["router"]
