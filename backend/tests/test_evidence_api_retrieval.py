"""API integration tests for evidence version and chunk retrieval."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import (
    auth_headers,
    create_principal,
    create_workspace_with_owner,
)


def _create_uploaded_document(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    workspace_name: str,
    document_name: str,
):
    principal = create_principal(
        db_session,
        email=email,
        display_name="Owner",
    )

    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name=workspace_name,
    )

    document_response = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=auth_headers(principal),
        json={"name": document_name},
    )

    assert document_response.status_code == 201

    document_id = uuid.UUID(document_response.json()["id"])

    upload_response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "security.md",
                (
                    b"# Access Control\n\n"
                    b"Access is reviewed quarterly.\n\n"
                    b"# Monitoring\n\n"
                    b"Security monitoring is performed continuously.\n"
                ),
                "text/markdown",
            )
        },
    )

    assert upload_response.status_code == 201

    body = upload_response.json()

    return (
        principal,
        workspace,
        document_id,
        uuid.UUID(body["version_id"]),
    )


def test_list_versions_returns_metadata_without_extracted_text(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id, version_id = _create_uploaded_document(
        client,
        db_session,
        email="versions@example.com",
        workspace_name="Versions Workspace",
        document_name="Versioned Evidence",
    )

    response = client.get(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body) == 1
    assert body[0]["id"] == str(version_id)
    assert body[0]["document_id"] == str(document_id)
    assert body[0]["version_number"] == 1
    assert body[0]["original_filename"] == "security.md"
    assert body[0]["media_type"] == "text/markdown"
    assert body[0]["normalization_version"] == "text-v1"
    assert body[0]["extracted_text"] if False else "extracted_text" not in body[0]


def test_get_version_returns_metadata_without_extracted_text(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id, version_id = _create_uploaded_document(
        client,
        db_session,
        email="version@example.com",
        workspace_name="Single Version Workspace",
        document_name="Single Version Evidence",
    )

    response = client.get(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions/{version_id}",
        headers=auth_headers(principal),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == str(version_id)
    assert body["document_id"] == str(document_id)
    assert body["version_number"] == 1
    assert body["chunking_version"] == "text-chunk-v1"
    assert "extracted_text" not in body


def test_list_chunks_returns_deterministic_chunk_metadata_and_content(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id, version_id = _create_uploaded_document(
        client,
        db_session,
        email="chunks@example.com",
        workspace_name="Chunks Workspace",
        document_name="Chunk Evidence",
    )

    response = client.get(
        (f"/workspaces/{workspace.id}/documents/{document_id}/versions/{version_id}/chunks"),
        headers=auth_headers(principal),
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body) >= 1

    first_chunk = body[0]

    assert first_chunk["document_version_id"] == str(version_id)
    assert first_chunk["chunk_index"] == 0
    assert first_chunk["content"]
    assert len(first_chunk["content_hash"]) == 64
    assert first_chunk["normalized_start_byte"] >= 0
    assert first_chunk["normalized_end_byte"] > first_chunk["normalized_start_byte"]


def test_missing_version_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id, _ = _create_uploaded_document(
        client,
        db_session,
        email="missing-version@example.com",
        workspace_name="Missing Version Workspace",
        document_name="Missing Version Evidence",
    )

    missing_version_id = uuid.uuid4()

    response = client.get(
        (f"/workspaces/{workspace.id}/documents/{document_id}/versions/{missing_version_id}"),
        headers=auth_headers(principal),
    )

    assert response.status_code == 404


def test_missing_document_versions_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="missing-document@example.com",
        display_name="Owner",
    )

    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Missing Document Workspace",
    )

    missing_document_id = uuid.uuid4()

    response = client.get(
        f"/workspaces/{workspace.id}/documents/{missing_document_id}/versions",
        headers=auth_headers(principal),
    )

    assert response.status_code == 404


def test_cross_workspace_version_access_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    owner_a, workspace_a, document_id, version_id = _create_uploaded_document(
        client,
        db_session,
        email="retrieval-a@example.com",
        workspace_name="Retrieval Workspace A",
        document_name="Private Retrieval Evidence",
    )

    owner_b = create_principal(
        db_session,
        email="retrieval-b@example.com",
        display_name="Owner B",
    )

    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Retrieval Workspace B",
    )

    response = client.get(
        f"/workspaces/{workspace_b.id}/documents/{document_id}/versions/{version_id}",
        headers=auth_headers(owner_b),
    )

    assert response.status_code == 404
    assert owner_a.user.id is not None
    assert workspace_a.id is not None


def test_cross_workspace_chunk_access_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    owner_a, workspace_a, document_id, version_id = _create_uploaded_document(
        client,
        db_session,
        email="chunk-a@example.com",
        workspace_name="Chunk Workspace A",
        document_name="Private Chunk Evidence",
    )

    owner_b = create_principal(
        db_session,
        email="chunk-b@example.com",
        display_name="Owner B",
    )

    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Chunk Workspace B",
    )

    response = client.get(
        (f"/workspaces/{workspace_b.id}/documents/{document_id}/versions/{version_id}/chunks"),
        headers=auth_headers(owner_b),
    )

    assert response.status_code == 404
    assert owner_a.user.id is not None
    assert workspace_a.id is not None


def test_viewer_can_read_versions_and_chunks(
    client: TestClient,
    db_session: Session,
) -> None:
    owner, workspace, document_id, version_id = _create_uploaded_document(
        client,
        db_session,
        email="viewer-read-owner@example.com",
        workspace_name="Viewer Read Workspace",
        document_name="Viewer Read Evidence",
    )

    from app.models import WorkspaceMembership, WorkspaceRole

    viewer = create_principal(
        db_session,
        email="viewer-read@example.com",
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

    version_response = client.get(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions/{version_id}",
        headers=auth_headers(viewer),
    )

    assert version_response.status_code == 200

    chunk_response = client.get(
        (f"/workspaces/{workspace.id}/documents/{document_id}/versions/{version_id}/chunks"),
        headers=auth_headers(viewer),
    )

    assert chunk_response.status_code == 200
    assert owner.user.id is not None
