"""API integration tests for evidence version uploads."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EvidenceChunk,
    EvidenceDocumentVersion,
    EvidenceIngestionAttempt,
    WorkspaceMembership,
    WorkspaceRole,
)
from tests.conftest import (
    auth_headers,
    create_principal,
    create_workspace_with_owner,
)


def _create_document(
    client: TestClient,
    db_session: Session,
    *,
    email: str = "owner@example.com",
    workspace_name: str = "Evidence Workspace",
    document_name: str = "Security Policy",
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

    response = client.post(
        f"/workspaces/{workspace.id}/documents",
        headers=auth_headers(principal),
        json={"name": document_name},
    )

    assert response.status_code == 201

    document_id = uuid.UUID(response.json()["id"])

    return principal, workspace, document_id


def test_upload_txt_creates_version_chunks_and_provenance(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="upload@example.com",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "security-policy.txt",
                b"Access control is reviewed quarterly.\n",
                "text/plain",
            )
        },
    )

    assert response.status_code == 201

    body = response.json()

    assert body["outcome"] == "created"
    assert body["document_id"] == str(document_id)
    assert body["version_number"] == 1
    assert body["chunk_count"] == 1

    version = db_session.scalar(
        select(EvidenceDocumentVersion).where(
            EvidenceDocumentVersion.id == uuid.UUID(body["version_id"])
        )
    )

    assert version is not None
    assert version.document_id == document_id
    assert version.original_filename == "security-policy.txt"
    assert version.media_type == "text/plain"
    assert version.normalization_version == "text-v1"
    assert version.extracted_text == "Access control is reviewed quarterly.\n"

    chunks = list(
        db_session.scalars(
            select(EvidenceChunk)
            .where(EvidenceChunk.document_version_id == version.id)
            .order_by(EvidenceChunk.chunk_index)
        )
    )

    assert len(chunks) == 1
    assert chunks[0].content == version.extracted_text

    attempt = db_session.scalar(
        select(EvidenceIngestionAttempt).where(
            EvidenceIngestionAttempt.id == uuid.UUID(body["ingestion_attempt_id"])
        )
    )

    assert attempt is not None
    assert attempt.outcome == "created"
    assert attempt.version_id == version.id
    assert attempt.workspace_id == workspace.id


def test_upload_markdown_succeeds(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="markdown@example.com",
        document_name="Markdown Evidence",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "evidence.md",
                b"# Security Controls\n\nAccess is reviewed annually.\n",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["outcome"] == "created"


def test_duplicate_upload_returns_existing_version(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="duplicate@example.com",
    )

    file_data = (
        "duplicate.txt",
        b"Same evidence content.\n",
        "text/plain",
    )

    first = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={"file": file_data},
    )

    second = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={"file": file_data},
    )

    assert first.status_code == 201
    assert second.status_code == 200

    first_body = first.json()
    second_body = second.json()

    assert first_body["outcome"] == "created"
    assert second_body["outcome"] == "duplicate"
    assert second_body["version_id"] == first_body["version_id"]
    assert second_body["version_number"] == 1
    assert second_body["chunk_count"] == first_body["chunk_count"]

    versions = list(
        db_session.scalars(
            select(EvidenceDocumentVersion).where(
                EvidenceDocumentVersion.document_id == document_id
            )
        )
    )

    assert len(versions) == 1

    attempts = list(
        db_session.scalars(
            select(EvidenceIngestionAttempt).where(
                EvidenceIngestionAttempt.document_id == document_id
            )
        )
    )

    assert len(attempts) == 2


def test_oversized_upload_returns_413(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="large@example.com",
    )

    oversized_content = b"a" * (5 * 1024 * 1024 + 1)

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "large.txt",
                oversized_content,
                "text/plain",
            )
        },
    )

    assert response.status_code == 413


def test_unsupported_extension_returns_415(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="extension@example.com",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "evidence.pdf",
                b"PDF-like content",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 415


def test_mismatched_media_type_returns_415(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="media@example.com",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "evidence.txt",
                b"Plain text evidence.\n",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 415


def test_invalid_utf8_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="utf8@example.com",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "invalid.txt",
                b"\xff\xfe\xfd",
                "text/plain",
            )
        },
    )

    assert response.status_code == 422


def test_empty_upload_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="empty@example.com",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "empty.txt",
                b"",
                "text/plain",
            )
        },
    )

    assert response.status_code == 422


def test_whitespace_only_upload_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    principal, workspace, document_id = _create_document(
        client,
        db_session,
        email="whitespace@example.com",
    )

    response = client.post(
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(principal),
        files={
            "file": (
                "whitespace.txt",
                b"   \n\t  \n",
                "text/plain",
            )
        },
    )

    assert response.status_code == 422


def test_cross_workspace_upload_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    owner_a, workspace_a, document_id = _create_document(
        client,
        db_session,
        email="workspace-a@example.com",
        workspace_name="Workspace A",
        document_name="Private Evidence",
    )

    owner_b = create_principal(
        db_session,
        email="workspace-b@example.com",
        display_name="Owner B",
    )
    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Workspace B",
    )

    response = client.post(
        f"/workspaces/{workspace_b.id}/documents/{document_id}/versions",
        headers=auth_headers(owner_b),
        files={
            "file": (
                "attack.txt",
                b"Cross-workspace attempt.\n",
                "text/plain",
            )
        },
    )

    assert response.status_code == 404

    versions = list(
        db_session.scalars(
            select(EvidenceDocumentVersion).where(
                EvidenceDocumentVersion.document_id == document_id
            )
        )
    )

    assert versions == []

    assert owner_a.user.id is not None
    assert workspace_a.id is not None
    assert workspace_b.id is not None


def test_viewer_cannot_upload(
    client: TestClient,
    db_session: Session,
) -> None:
    owner, workspace, document_id = _create_document(
        client,
        db_session,
        email="owner-upload@example.com",
        document_name="Viewer Protected Evidence",
    )

    viewer = create_principal(
        db_session,
        email="viewer-upload@example.com",
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
        f"/workspaces/{workspace.id}/documents/{document_id}/versions",
        headers=auth_headers(viewer),
        files={
            "file": (
                "viewer.txt",
                b"Viewer should not upload.\n",
                "text/plain",
            )
        },
    )

    assert response.status_code == 403
    assert owner.user.id is not None