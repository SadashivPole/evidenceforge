"""API tests for workspace-scoped questionnaire XLSX import."""

from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.models import WorkspaceMembership, WorkspaceRole
from tests.conftest import (
    auth_headers,
    create_principal,
    create_workspace_with_owner,
)


def make_workbook_bytes() -> bytes:
    """Create a deterministic questionnaire workbook for API tests."""

    workbook = Workbook()

    worksheet = workbook.active
    worksheet.title = "Security"

    worksheet.append(["Section", "Question"])
    worksheet.append(
        [
            "Access Control",
            "Do you enforce multi-factor authentication?",
        ]
    )
    worksheet.append(
        [
            None,
            "Are privileged accounts reviewed regularly?",
        ]
    )

    instructions = workbook.create_sheet("Instructions")
    instructions.append(["This worksheet contains guidance only."])

    buffer = BytesIO()
    workbook.save(buffer)

    return buffer.getvalue()


def test_questionnaire_xlsx_import_preview(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="owner@example.com",
        display_name="Owner",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Questionnaire Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(principal),
        files={
            "file": (
                "vendor-questionnaire.xlsx",
                make_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["name"] == "vendor-questionnaire"
    assert body["source_filename"] == "vendor-questionnaire.xlsx"
    assert body["status"] == "IMPORTED"
    assert body["parser_version"] == "xlsx-import-v1"

    assert len(body["questions"]) == 2

    first = body["questions"][0]

    assert first["ordinal"] == 1
    assert first["sheet_name"] == "Security"
    assert first["source_row"] == 2
    assert first["question_text"] == (
        "Do you enforce multi-factor authentication?"
    )
    assert first["section_path"] == ["Access Control"]

    second = body["questions"][1]

    assert second["ordinal"] == 2
    assert second["source_row"] == 3
    assert second["section_path"] == ["Access Control"]

    assert len(body["imported_sheets"]) == 1
    assert body["imported_sheets"][0]["sheet_name"] == "Security"
    assert body["imported_sheets"][0]["question_count"] == 2

    assert len(body["ignored_sheets"]) == 1
    assert body["ignored_sheets"][0]["sheet_name"] == "Instructions"


def test_question_ids_are_stable_across_repeated_api_imports(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="stable@example.com",
        display_name="Stable Import",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Stable Workspace",
    )

    headers = auth_headers(principal)
    workbook = make_workbook_bytes()

    first = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=headers,
        files={
            "file": (
                "questionnaire.xlsx",
                workbook,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    second = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=headers,
        files={
            "file": (
                "questionnaire.xlsx",
                workbook,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200

    first_ids = [
        question["question_id"]
        for question in first.json()["questions"]
    ]

    second_ids = [
        question["question_id"]
        for question in second.json()["questions"]
    ]

    assert first_ids == second_ids


def test_unsupported_questionnaire_extension_returns_415(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="extension@example.com",
        display_name="Extension",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Extension Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(principal),
        files={
            "file": (
                "questionnaire.xls",
                b"not an xlsx workbook",
                "application/vnd.ms-excel",
            )
        },
    )

    assert response.status_code == 415


def test_corrupt_questionnaire_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="corrupt@example.com",
        display_name="Corrupt",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Corrupt Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(principal),
        files={
            "file": (
                "corrupt.xlsx",
                b"definitely not a real workbook",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 422


def test_oversized_questionnaire_returns_413(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="large@example.com",
        display_name="Large File",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Large File Workspace",
    )

    oversized = b"x" * (10 * 1024 * 1024 + 1)

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(principal),
        files={
            "file": (
                "large.xlsx",
                oversized,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 413


def test_viewer_cannot_import_questionnaire(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="owner-questionnaire@example.com",
        display_name="Owner",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Viewer Workspace",
    )

    viewer = create_principal(
        db_session,
        email="viewer-questionnaire@example.com",
        display_name="Viewer",
    )

    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=viewer.user.id,
            role=WorkspaceRole.VIEWER,
        )
    )
    db_session.commit()

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(viewer),
        files={
            "file": (
                "questionnaire.xlsx",
                make_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 403


def test_cross_workspace_questionnaire_import_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="cross-owner@example.com",
        display_name="Owner",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Private Workspace",
    )

    other = create_principal(
        db_session,
        email="cross-other@example.com",
        display_name="Other User",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        headers=auth_headers(other),
        files={
            "file": (
                "questionnaire.xlsx",
                make_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 404


def test_unauthenticated_questionnaire_import_returns_401(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="unauth@example.com",
        display_name="Unauthenticated Test",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Auth Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/questionnaires/import",
        files={
            "file": (
                "questionnaire.xlsx",
                make_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 401