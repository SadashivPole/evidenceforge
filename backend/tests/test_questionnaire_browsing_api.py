"""Security and contract tests for the guided questionnaire browser."""

from __future__ import annotations

import uuid
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.questionnaires.persistence.models import (
    Questionnaire,
    QuestionnaireVersion,
)
from tests.conftest import auth_headers, create_principal, create_workspace_with_owner


def _questionnaire_workbook() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Access Control"
    worksheet.append(["Section", "Question"])
    worksheet.append(["Identity", "Is MFA required for administrator accounts?"])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _import_questionnaire(client: TestClient, principal, workspace) -> dict[str, object]:
    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(principal),
        files={
            "file": (
                "guided-mfa.xlsx",
                _questionnaire_workbook(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_browse_questionnaire_version_and_question_returns_version_question_uuid(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="guided-browser@example.invalid",
        display_name="Guided Browser",
    )
    workspace = create_workspace_with_owner(db_session, principal, name="Guided Browser Workspace")
    imported = _import_questionnaire(client, principal, workspace)

    questionnaires = client.get(
        f"/workspaces/{workspace.id}/questionnaires",
        headers=auth_headers(principal),
    )
    assert questionnaires.status_code == 200, questionnaires.text
    assert len(questionnaires.json()) == 1
    item = questionnaires.json()[0]
    assert item["id"] == imported["questionnaire_id"]
    assert item["latest_version_id"] == imported["version_id"]
    assert item["question_count"] == 1
    assert item["source_filename"] == "guided-mfa.xlsx"

    versions = client.get(
        f"/workspaces/{workspace.id}/questionnaires/{imported['questionnaire_id']}/versions",
        headers=auth_headers(principal),
    )
    assert versions.status_code == 200, versions.text
    assert len(versions.json()) == 1
    assert versions.json()[0]["id"] == imported["version_id"]

    questions = client.get(
        f"/workspaces/{workspace.id}/questionnaires/{imported['questionnaire_id']}"
        f"/versions/{imported['version_id']}/questions",
        headers=auth_headers(principal),
    )
    assert questions.status_code == 200, questions.text
    rows = questions.json()
    assert len(rows) == 1
    assert str(uuid.UUID(rows[0]["id"])) == rows[0]["id"]
    assert rows[0]["question_text"] == "Is MFA required for administrator accounts?"
    assert rows[0]["stable_question_id"] == imported["questions"][0]["question_id"]
    assert rows[0]["stable_question_id"] != rows[0]["id"]


def test_questionnaire_browser_is_workspace_scoped(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="guided-owner@example.invalid",
        display_name="Workspace Owner",
    )
    other = create_principal(
        db_session,
        email="guided-other@example.invalid",
        display_name="Different Workspace User",
    )
    workspace = create_workspace_with_owner(db_session, owner, name="Private Guided Workspace")

    response = client.get(
        f"/workspaces/{workspace.id}/questionnaires",
        headers=auth_headers(other),
    )
    assert response.status_code == 404


def test_questionnaire_browser_requires_authentication(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="guided-auth@example.invalid",
        display_name="Guided Auth",
    )
    workspace = create_workspace_with_owner(db_session, principal, name="Guided Auth Workspace")

    response = client.get(f"/workspaces/{workspace.id}/questionnaires")
    assert response.status_code == 401


def test_browsing_unknown_questionnaire_version_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="guided-missing@example.invalid",
        display_name="Guided Missing",
    )
    workspace = create_workspace_with_owner(db_session, principal, name="Guided Missing Workspace")

    response = client.get(
        f"/workspaces/{workspace.id}/questionnaires/{uuid.uuid4()}"
        f"/versions/{uuid.uuid4()}/questions",
        headers=auth_headers(principal),
    )
    assert response.status_code == 404


def test_questionnaire_list_fetches_latest_versions_in_one_batch(
    client: TestClient,
    db_session: Session,
) -> None:
    """Keep version-query count constant as the workspace grows."""

    principal = create_principal(
        db_session,
        email="guided-query-count@example.invalid",
        display_name="Guided Query Counter",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Guided Query Count Workspace",
    )

    for index in range(4):
        questionnaire = Questionnaire(
            workspace_id=workspace.id,
            name=f"Query Count Questionnaire {index}",
            created_by_user_id=principal.user.id,
        )
        db_session.add(questionnaire)
        db_session.flush()

        for version_number in (1, 2):
            hash_char = "a" if version_number == 1 else "b"
            db_session.add(
                QuestionnaireVersion(
                    workspace_id=workspace.id,
                    questionnaire_id=questionnaire.id,
                    version_number=version_number,
                    raw_sha256=hash_char * 64,
                    canonical_sha256=hash_char * 64,
                    source_filename=f"query-count-{index}-v{version_number}.xlsx",
                    parser_version="test-v1",
                    normalization_version="test-v1",
                    question_identity_version="test-v1",
                    canonical_hash_version="test-v1",
                    question_count=version_number,
                    created_by_user_id=principal.user.id,
                )
            )
    db_session.commit()

    version_selects: list[str] = []

    def count_version_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "questionnaire_versions" in statement
        ):
            version_selects.append(statement)

    event.listen(client.app.state.engine, "before_cursor_execute", count_version_selects)
    try:
        response = client.get(
            f"/workspaces/{workspace.id}/questionnaires",
            headers=auth_headers(principal),
        )
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", count_version_selects)

    assert response.status_code == 200, response.text
    assert len(response.json()) == 4
    assert len(version_selects) == 1
    assert all(item["latest_version_number"] == 2 for item in response.json())
    assert all(item["question_count"] == 2 for item in response.json())
