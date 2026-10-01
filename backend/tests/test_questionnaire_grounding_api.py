"""API tests for authenticated deterministic questionnaire grounding."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import QuestionnaireGroundingResponse
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)
from tests.conftest import auth_headers, create_principal, create_workspace_with_owner
from tests.questionnaires.test_questionnaire_responses import (
    _create_chunk,
    _import_one_question,
)


def _grounding_path(
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    version_id: uuid.UUID,
    question_id: uuid.UUID,
) -> str:
    return (
        f"/workspaces/{workspace_id}/questionnaires/{questionnaire_id}"
        f"/versions/{version_id}/questions/{question_id}/grounding"
    )


def _count(db: Session, model: type[object]) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_grounding_api_returns_matched_schema_and_provenance(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-api-match@example.com",
        display_name="Grounding API Match",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding API Match Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required",
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="MFA is required.",
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=question.id,
        ),
        headers=auth_headers(principal),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["search_version"] == "hybrid-rrf-v1"
    validated = QuestionnaireGroundingResponse.model_validate(body)
    assert validated.status.value == "MATCHED"
    assert validated.search_version == "hybrid-rrf-v1"
    assert validated.workspace_id == workspace.id
    assert validated.questionnaire_id == version.questionnaire_id
    assert validated.questionnaire_version_id == version.id
    assert validated.questionnaire_version_question_id == question.id
    assert validated.normalized_query == "MFA is required"
    assert len(validated.results) == 1
    assert validated.results[0].candidate.chunk_id == chunk.id
    assert len(validated.citations) == 1
    assert validated.citations[0].chunk_id == chunk.id
    assert validated.citations[0].content_hash == chunk.content_hash
    assert validated.citations[0].section_label == "Authentication"
    assert validated.citations[0].page_number == 4


def test_grounding_api_returns_no_matches(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-api-empty@example.com",
        display_name="Grounding API Empty",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding API Empty Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Quantum zebra governance control",
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=question.id,
        ),
        headers=auth_headers(principal),
    )

    assert response.status_code == 200
    body_json = response.json()
    assert body_json["search_version"] == "hybrid-rrf-v1"
    body = QuestionnaireGroundingResponse.model_validate(body_json)
    assert body.status.value == "NO_MATCHES"
    assert body.search_version == "hybrid-rrf-v1"
    assert body.results == []
    assert body.citations == []


def test_grounding_api_search_version_provenance_is_hybrid_rrf_v1(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-search-version@example.com",
        display_name="Grounding Search Version Principal",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding Search Version Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Access control and MFA",
    )
    _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="Access control requires mandatory MFA across systems.",
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=question.id,
        ),
        headers=auth_headers(principal),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["search_version"] == "hybrid-rrf-v1"
    validated = QuestionnaireGroundingResponse.model_validate(body)
    assert validated.search_version == "hybrid-rrf-v1"


def test_grounding_api_requires_authentication(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-api-auth@example.com",
        display_name="Grounding API Auth",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding API Auth Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required",
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=question.id,
        )
    )

    assert response.status_code == 401


def test_grounding_api_rejects_cross_workspace_question_access(
    client: TestClient,
    db_session: Session,
) -> None:
    owner_a = create_principal(
        db_session,
        email="grounding-api-cross-a@example.com",
        display_name="Grounding API Cross A",
    )
    owner_b = create_principal(
        db_session,
        email="grounding-api-cross-b@example.com",
        display_name="Grounding API Cross B",
    )
    workspace_a = create_workspace_with_owner(
        db_session,
        owner_a,
        name="Grounding API Cross Workspace A",
    )
    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Grounding API Cross Workspace B",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace_a.id,
        actor_user_id=owner_a.user.id,
        question_text="MFA is required",
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace_b.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=question.id,
        ),
        headers=auth_headers(owner_b),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Questionnaire version question not found"


def test_grounding_api_rejects_invalid_question_version_relationship(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-api-mismatch@example.com",
        display_name="Grounding API Mismatch",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding API Mismatch Workspace",
    )
    first_version, first_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required",
        questionnaire_name="Grounding API First Questionnaire",
    )
    second_version, _ = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Password rotation is required",
        questionnaire_name="Grounding API Second Questionnaire",
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=first_version.questionnaire_id,
            version_id=second_version.id,
            question_id=first_question.id,
        ),
        headers=auth_headers(principal),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Questionnaire version question not found"


def test_grounding_api_rejects_nonexistent_identifier_and_malformed_uuid(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-api-invalid@example.com",
        display_name="Grounding API Invalid",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding API Invalid Workspace",
    )
    version, _question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required",
    )

    missing = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=uuid.uuid4(),
        ),
        headers=auth_headers(principal),
    )
    malformed = client.get(
        f"/workspaces/{workspace.id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/not-a-uuid/grounding",
        headers=auth_headers(principal),
    )

    assert missing.status_code == 404
    assert malformed.status_code == 422


def test_grounding_api_does_not_create_response_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-api-read-only@example.com",
        display_name="Grounding API Read Only",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding API Read Only Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required",
    )
    _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="MFA is required.",
    )
    before = (
        _count(db_session, QuestionnaireResponse),
        _count(db_session, QuestionnaireResponseRevision),
        _count(db_session, QuestionnaireResponseCitation),
    )

    response = client.get(
        _grounding_path(
            workspace_id=workspace.id,
            questionnaire_id=version.questionnaire_id,
            version_id=version.id,
            question_id=question.id,
        ),
        headers=auth_headers(principal),
    )

    after = (
        _count(db_session, QuestionnaireResponse),
        _count(db_session, QuestionnaireResponseRevision),
        _count(db_session, QuestionnaireResponseCitation),
    )
    assert response.status_code == 200
    assert before == after == (0, 0, 0)
