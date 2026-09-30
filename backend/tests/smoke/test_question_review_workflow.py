"""Local HTTP smoke test for the Phase 1O question review workflow."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import QuestionnaireGroundingResponse, QuestionnaireResponseResponse
from app.auth import hash_token
from app.models import ApiToken, User
from app.questionnaires.persistence.models import QuestionnaireVersionQuestion


class SmokePrincipal:
    """Temporary authenticated principal used only by this smoke test."""

    def __init__(self, user_id: uuid.UUID, token: str) -> None:
        self.user_id = user_id
        self.token = token


@pytest.fixture()
def temporary_principal(db_session: Session) -> Iterator[SmokePrincipal]:
    """Create and revoke one temporary bearer token around the smoke test."""

    raw_token = secrets.token_urlsafe(32)
    user = User(
        external_subject=f"phase1o-smoke:{uuid.uuid4()}",
        email=f"phase1o-smoke-{uuid.uuid4().hex}@example.test",
        display_name="Phase 1O smoke test user",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ApiToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            name="phase1o-local-smoke",
        )
    )
    db_session.commit()

    try:
        yield SmokePrincipal(user_id=user.id, token=raw_token)
    finally:
        token = db_session.scalar(
            select(ApiToken).where(ApiToken.token_hash == hash_token(raw_token))
        )
        if token is not None:
            token.revoked_at = datetime.now(UTC)
            db_session.commit()
            db_session.delete(token)
            db_session.commit()


def _headers(principal: SmokePrincipal) -> dict[str, str]:
    return {"Authorization": f"Bearer {principal.token}"}


def _expect_status(response, expected: int, operation: str) -> dict:
    """Fail with the operation, status, and safe response body on mismatch."""

    if response.status_code != expected:
        pytest.fail(
            f"{operation}: expected HTTP {expected}, got {response.status_code}; "
            f"response body: {response.text[:2000]}"
        )
    return response.json()


def _workbook_bytes(question_text: str) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Security"
    worksheet.append(["Section", "Question"])
    worksheet.append(["Access Control", question_text])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _grounding_path(
    *,
    workspace_id: str,
    questionnaire_id: str,
    version_id: str,
    question_id: str,
) -> str:
    return (
        f"/workspaces/{workspace_id}/questionnaires/{questionnaire_id}"
        f"/versions/{version_id}/questions/{question_id}/grounding"
    )


def _response_path(
    *,
    workspace_id: str,
    questionnaire_id: str,
    version_id: str,
    question_id: str,
) -> str:
    return (
        f"/workspaces/{workspace_id}/questionnaires/{questionnaire_id}"
        f"/versions/{version_id}/questions/{question_id}/response"
    )


def test_question_review_workflow_over_http(
    client: TestClient,
    db_session: Session,
    temporary_principal: SmokePrincipal,
) -> None:
    """Exercise workspace, evidence, questionnaire, grounding, and response APIs."""

    headers = _headers(temporary_principal)
    suffix = uuid.uuid4().hex[:12]
    question_text = "MFA is required for privileged users."

    workspace_body = _expect_status(
        client.post(
            "/workspaces",
            headers=headers,
            json={"name": f"Phase 1O smoke workspace {suffix}"},
        ),
        201,
        "create workspace",
    )
    workspace_id = workspace_body["id"]
    assert workspace_body["created_by_user_id"] == str(temporary_principal.user_id)

    document_body = _expect_status(
        client.post(
            f"/workspaces/{workspace_id}/documents",
            headers=headers,
            json={"name": f"phase1o-smoke-evidence-{suffix}.md"},
        ),
        201,
        "create evidence document",
    )
    document_id = document_body["id"]

    evidence_body = _expect_status(
        client.post(
            f"/workspaces/{workspace_id}/documents/{document_id}/versions",
            headers=headers,
            files={
                "file": (
                    "phase1o-smoke-evidence.md",
                    f"# Access Control\n\n{question_text}\n".encode(),
                    "text/markdown",
                )
            },
        ),
        201,
        "upload deterministic evidence",
    )
    version_id = evidence_body["version_id"]
    assert evidence_body["document_id"] == document_id
    assert evidence_body["version_number"] == 1
    assert evidence_body["chunk_count"] > 0

    questionnaire_body = _expect_status(
        client.post(
            f"/workspaces/{workspace_id}/questionnaires/import",
            headers=headers,
            files={
                "file": (
                    f"phase1o-smoke-questionnaire-{suffix}.xlsx",
                    _workbook_bytes(question_text),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ),
        200,
        "import minimal questionnaire",
    )
    questionnaire_id = questionnaire_body["questionnaire_id"]
    questionnaire_version_id = questionnaire_body["version_id"]
    questions = questionnaire_body["questions"]
    assert questionnaire_body["outcome"] == "CREATED"
    assert len(questions) == 1
    assert questions[0]["question_text"] == question_text

    version_questions = list(
        db_session.scalars(
            select(QuestionnaireVersionQuestion).where(
                QuestionnaireVersionQuestion.questionnaire_version_id
                == uuid.UUID(questionnaire_version_id)
            )
        )
    )
    assert len(version_questions) == 1
    questionnaire_version_question_id = str(version_questions[0].id)

    grounding_body = _expect_status(
        client.get(
            _grounding_path(
                workspace_id=workspace_id,
                questionnaire_id=questionnaire_id,
                version_id=questionnaire_version_id,
                question_id=questionnaire_version_question_id,
            ),
            headers=headers,
        ),
        200,
        "ground questionnaire question",
    )
    grounding_contract = QuestionnaireGroundingResponse.model_validate(grounding_body)
    assert grounding_contract.workspace_id == uuid.UUID(workspace_id)
    assert grounding_contract.questionnaire_id == uuid.UUID(questionnaire_id)
    assert grounding_body["workspace_id"] == workspace_id
    assert grounding_body["questionnaire_id"] == questionnaire_id
    assert grounding_body["questionnaire_version_id"] == questionnaire_version_id
    assert (
        grounding_body["questionnaire_version_question_id"]
        == questionnaire_version_question_id
    )
    assert grounding_body["status"] == "MATCHED"
    assert grounding_body["normalized_query"] == question_text
    assert isinstance(grounding_body["results"], list)
    assert grounding_body["results"]
    assert isinstance(grounding_body["citations"], list)
    assert grounding_body["citations"]

    first_result = grounding_body["results"][0]
    candidate = first_result["candidate"]
    for field in (
        "chunk_id",
        "document_id",
        "version_id",
        "version_number",
        "chunk_index",
        "content",
        "content_hash",
        "normalized_start_byte",
        "normalized_end_byte",
    ):
        assert field in candidate
    assert candidate["document_id"] == document_id
    assert candidate["version_id"] == version_id
    assert candidate["content"]

    citation_chunk_id = candidate["chunk_id"]
    assert citation_chunk_id in {
        citation["chunk_id"] for citation in grounding_body["citations"]
    }

    answer = "Privileged users are required to use MFA."
    response_path = _response_path(
        workspace_id=workspace_id,
        questionnaire_id=questionnaire_id,
        version_id=questionnaire_version_id,
        question_id=questionnaire_version_question_id,
    )
    saved_body = _expect_status(
        client.put(
            response_path,
            headers=headers,
            json={
                "answer": answer,
                "status": "PROPOSED",
                "citation_chunk_ids": [citation_chunk_id],
            },
        ),
        200,
        "save questionnaire response",
    )
    saved_contract = QuestionnaireResponseResponse.model_validate(saved_body)
    assert saved_contract.current_revision.revision_number == 1
    assert saved_contract.current_revision.status.value == "PROPOSED"
    assert saved_contract.current_revision.answer == answer
    assert saved_body["questionnaire_id"] == questionnaire_id
    assert saved_body["questionnaire_version_id"] == questionnaire_version_id
    assert (
        saved_body["questionnaire_version_question_id"]
        == questionnaire_version_question_id
    )
    assert saved_body["current_revision"]["revision_number"] == 1
    assert saved_body["current_revision"]["status"] == "PROPOSED"
    assert saved_body["current_revision"]["answer"] == answer
    assert len(saved_body["current_revision"]["citations"]) == 1
    assert (
        saved_body["current_revision"]["citations"][0]["evidence_chunk_id"]
        == citation_chunk_id
    )

    reloaded_body = _expect_status(
        client.get(response_path, headers=headers),
        200,
        "reload questionnaire response",
    )
    reloaded_contract = QuestionnaireResponseResponse.model_validate(reloaded_body)
    assert reloaded_contract.id == saved_contract.id
    assert reloaded_contract.current_revision == saved_contract.current_revision
    reloaded_current = reloaded_body["current_revision"]
    assert reloaded_current["revision_number"] == 1
    assert reloaded_current["status"] == "PROPOSED"
    assert reloaded_current["answer"] == answer
    assert reloaded_current["citations"][0]["evidence_chunk_id"] == citation_chunk_id
    assert reloaded_body["id"] == saved_body["id"]
    assert reloaded_body["current_revision"] == saved_body["current_revision"]
    assert reloaded_body["revisions"] == saved_body["revisions"]
    assert reloaded_current["citations"][0]["content_hash"] == candidate["content_hash"]
