"""API tests for workspace-scoped evidence document metadata."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import WorkspaceMembership, WorkspaceRole
from tests.conftest import (
    auth_headers,
    create_principal,
    create_workspace_with_owner,
)


def test_create_evidence_document(
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
        name="Evidence Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=auth_headers(principal),
        json={"name": "Security Policy"},
    )

    assert response.status_code == 201

    body = response.json()

    assert body["workspace_id"] == str(workspace.id)
    assert body["name"] == "Security Policy"
    assert body["source_type"] == "file"
    assert body["status"] == "active"
    assert body["created_by_user_id"] == str(principal.user.id)


def test_duplicate_evidence_document_name_returns_conflict(
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
        name="Evidence Workspace",
    )

    headers = auth_headers(principal)

    first = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=headers,
        json={"name": "Security Policy"},
    )

    second = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=headers,
        json={"name": "Security Policy"},
    )

    assert first.status_code == 201
    assert second.status_code == 409


def test_list_and_get_evidence_document(
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
        name="Evidence Workspace",
    )

    headers = auth_headers(principal)

    create_response = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=headers,
        json={"name": "Access Control Policy"},
    )

    assert create_response.status_code == 201

    document_id = create_response.json()["id"]

    list_response = client.get(
        f"/workspaces/{workspace.id}/documents",
        headers=headers,
    )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["id"] == document_id

    get_response = client.get(
        f"/workspaces/{workspace.id}/documents/{document_id}",
        headers=headers,
    )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == document_id


def test_cross_workspace_document_access_returns_not_found(
    client: TestClient,
    db_session: Session,
) -> None:
    owner_a = create_principal(
        db_session,
        email="owner-a@example.com",
        display_name="Owner A",
    )
    workspace_a = create_workspace_with_owner(
        db_session,
        owner_a,
        name="Workspace A",
    )

    document_response = client.post(
        f"/workspaces/{workspace_a.id}/documents",
        headers=auth_headers(owner_a),
        json={"name": "Private Evidence"},
    )

    assert document_response.status_code == 201
    document_id = document_response.json()["id"]

    owner_b = create_principal(
        db_session,
        email="owner-b@example.com",
        display_name="Owner B",
    )
    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Workspace B",
    )

    response = client.get(
        f"/workspaces/{workspace_b.id}/documents/{document_id}",
        headers=auth_headers(owner_b),
    )

    assert response.status_code == 404


def test_viewer_cannot_create_evidence_document(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="owner@example.com",
        display_name="Owner",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Evidence Workspace",
    )

    viewer = create_principal(
        db_session,
        email="viewer@example.com",
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
        f"/workspaces/{workspace.id}/documents",
        headers=auth_headers(viewer),
        json={"name": "Viewer Attempt"},
    )

    assert response.status_code == 403


def test_unauthenticated_document_access_is_rejected(
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
        name="Evidence Workspace",
    )

    response = client.get(
        f"/workspaces/{workspace.id}/documents",
    )

    assert response.status_code == 401


def test_document_name_is_normalized(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="normalize@example.com",
        display_name="Normalize",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Evidence Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=auth_headers(principal),
        json={"name": "  Security Policy  "},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Security Policy"


def test_blank_document_name_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="blank@example.com",
        display_name="Blank",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Evidence Workspace",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=auth_headers(principal),
        json={"name": "   "},
    )

    assert response.status_code == 422
