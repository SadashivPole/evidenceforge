"""Acceptance coverage for Phase 1K response authorization and integrity rules."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import WorkspaceRole
from app.questionnaires.responses.errors import (
    QuestionnaireVersionQuestionNotFoundError,
    ResponseAuthorizationError,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseRevision,
)
from app.questionnaires.responses.service import save_response
from app.questionnaires.types import ResponseStatus
from tests.conftest import create_principal, create_workspace_with_owner
from tests.questionnaires.test_questionnaire_responses import (
    _create_chunk,
    _import_one_question,
)


def test_owner_admin_and_member_can_write_but_only_owner_and_admin_can_approve(
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="accept-owner@example.com",
        display_name="Acceptance Owner",
    )
    admin = create_principal(
        db_session,
        email="accept-admin@example.com",
        display_name="Acceptance Admin",
    )
    member = create_principal(
        db_session,
        email="accept-member@example.com",
        display_name="Acceptance Member",
    )
    viewer = create_principal(
        db_session,
        email="accept-viewer@example.com",
        display_name="Acceptance Viewer",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Acceptance Role Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        questionnaire_name="Acceptance Role Questionnaire",
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        content="Role acceptance evidence.",
    )

    for actor, role, answer in (
        (member, WorkspaceRole.MEMBER, "Member answer"),
        (admin, WorkspaceRole.ADMIN, "Admin answer"),
        (owner, WorkspaceRole.OWNER, "Owner answer"),
    ):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=actor.user.id,
            actor_role=role,
            questionnaire_id=version.questionnaire_id,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer=answer,
            status=ResponseStatus.NEEDS_REVIEW,
        )

    for actor, role, answer in (
        (owner, WorkspaceRole.OWNER, "Owner approval"),
        (admin, WorkspaceRole.ADMIN, "Admin approval"),
    ):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=actor.user.id,
            actor_role=role,
            questionnaire_id=version.questionnaire_id,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer=answer,
            status=ResponseStatus.APPROVED,
            citation_chunk_ids=(chunk.id,),
        )

    for actor, role in (
        (member, WorkspaceRole.MEMBER),
        (viewer, WorkspaceRole.VIEWER),
    ):
        with pytest.raises(ResponseAuthorizationError):
            save_response(
                db_session,
                workspace_id=workspace.id,
                actor_user_id=actor.user.id,
                actor_role=role,
                questionnaire_id=version.questionnaire_id,
                questionnaire_version_id=version.id,
                questionnaire_version_question_id=version_question.id,
                answer="Rejected approval",
                status=ResponseStatus.APPROVED,
                citation_chunk_ids=(chunk.id,),
            )


def test_every_existing_response_status_is_accepted_with_its_required_shape(
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="accept-status@example.com",
        display_name="Acceptance Status",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Acceptance Status Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        questionnaire_name="Acceptance Status Questionnaire",
    )
    first_chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        content="First status evidence.",
    )
    second_chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        content="Second status evidence.",
    )

    cases = (
        (ResponseStatus.PROPOSED, "Proposed", ()),
        (ResponseStatus.NEEDS_REVIEW, "Needs review", ()),
        (ResponseStatus.STALE_SOURCE, "Stale source", ()),
        (ResponseStatus.INSUFFICIENT_EVIDENCE, None, ()),
        (ResponseStatus.NOT_APPLICABLE, None, ()),
        (ResponseStatus.DO_NOT_DISCLOSE, None, ()),
        (
            ResponseStatus.CONFLICTING_SOURCES,
            "Conflicting",
            (first_chunk.id, second_chunk.id),
        ),
        (ResponseStatus.APPROVED, "Approved", (first_chunk.id,)),
    )

    for status, answer, citation_ids in cases:
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=owner.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_id=version.questionnaire_id,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer=answer,
            status=status,
            citation_chunk_ids=citation_ids,
        )

    assert (
        db_session.scalar(
            select(func.count()).select_from(QuestionnaireResponseRevision)
        )
        == len(cases)
    )


def test_cross_workspace_questionnaire_version_question_is_rejected(
    db_session: Session,
) -> None:
    owner_a = create_principal(
        db_session,
        email="accept-cross-a@example.com",
        display_name="Acceptance Cross A",
    )
    owner_b = create_principal(
        db_session,
        email="accept-cross-b@example.com",
        display_name="Acceptance Cross B",
    )
    workspace_a = create_workspace_with_owner(
        db_session,
        owner_a,
        name="Acceptance Cross Workspace A",
    )
    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Acceptance Cross Workspace B",
    )
    version_a, question_a = _import_one_question(
        db_session,
        workspace_id=workspace_a.id,
        actor_user_id=owner_a.user.id,
        questionnaire_name="Acceptance Cross Questionnaire A",
    )
    _version_b, question_b = _import_one_question(
        db_session,
        workspace_id=workspace_b.id,
        actor_user_id=owner_b.user.id,
        questionnaire_name="Acceptance Cross Questionnaire B",
    )

    with pytest.raises(QuestionnaireVersionQuestionNotFoundError):
        save_response(
            db_session,
            workspace_id=workspace_b.id,
            actor_user_id=owner_b.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_id=version_a.questionnaire_id,
            questionnaire_version_id=version_a.id,
            questionnaire_version_question_id=question_a.id,
            answer="Cross workspace",
            status=ResponseStatus.PROPOSED,
        )

    with pytest.raises(QuestionnaireVersionQuestionNotFoundError):
        save_response(
            db_session,
            workspace_id=workspace_a.id,
            actor_user_id=owner_a.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_id=version_a.questionnaire_id,
            questionnaire_version_id=version_a.id,
            questionnaire_version_question_id=question_b.id,
            answer="Cross version question",
            status=ResponseStatus.PROPOSED,
        )

    assert (
        db_session.scalar(
            select(func.count()).select_from(QuestionnaireResponse)
        )
        == 0
    )


def test_cited_evidence_chunk_cannot_be_deleted(db_session: Session) -> None:
    owner = create_principal(
        db_session,
        email="accept-delete@example.com",
        display_name="Acceptance Delete",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Acceptance Delete Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        questionnaire_name="Acceptance Delete Questionnaire",
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        content="Evidence that must remain cited.",
    )
    save_response(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        actor_role=WorkspaceRole.OWNER,
        questionnaire_id=version.questionnaire_id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=version_question.id,
        answer="Cited",
        status=ResponseStatus.PROPOSED,
        citation_chunk_ids=(chunk.id,),
    )

    db_session.delete(chunk)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
