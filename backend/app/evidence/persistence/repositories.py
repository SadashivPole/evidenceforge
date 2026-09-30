"""Repository helpers for transactional EvidenceForge persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
)


def lock_document(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
) -> EvidenceDocument | None:
    """Load a document inside the workspace and lock its row for this transaction."""

    statement = (
        select(EvidenceDocument)
        .where(
            EvidenceDocument.id == document_id,
            EvidenceDocument.workspace_id == workspace_id,
        )
        .with_for_update()
    )

    return db.scalar(statement)


def get_document(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
) -> EvidenceDocument | None:
    """Load a document only when it belongs to the requested workspace."""

    statement = select(EvidenceDocument).where(
        EvidenceDocument.id == document_id,
        EvidenceDocument.workspace_id == workspace_id,
    )

    return db.scalar(statement)


def find_duplicate_version(
    db: Session,
    *,
    document_id: uuid.UUID,
    normalized_sha256: str,
    normalization_version: str,
    chunking_version: str,
) -> EvidenceDocumentVersion | None:
    """Find an existing immutable representation for a document."""

    statement = (
        select(EvidenceDocumentVersion)
        .where(
            EvidenceDocumentVersion.document_id == document_id,
            EvidenceDocumentVersion.normalized_sha256 == normalized_sha256,
            EvidenceDocumentVersion.normalization_version == normalization_version,
            EvidenceDocumentVersion.chunking_version == chunking_version,
        )
        .limit(1)
    )

    return db.scalar(statement)


def next_version_number(
    db: Session,
    *,
    document_id: uuid.UUID,
) -> int:
    """Return the next one-based version number.

    The caller must hold the document row lock before calling this function.
    """

    latest_version = db.scalar(
        select(func.max(EvidenceDocumentVersion.version_number)).where(
            EvidenceDocumentVersion.document_id == document_id,
        )
    )

    return (latest_version or 0) + 1


def list_document_versions(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
) -> list[EvidenceDocumentVersion] | None:
    """List versions when the document belongs to the requested workspace.

    Returns None when the document is not visible inside the workspace.
    """

    document = get_document(
        db,
        workspace_id=workspace_id,
        document_id=document_id,
    )

    if document is None:
        return None

    statement = (
        select(EvidenceDocumentVersion)
        .where(EvidenceDocumentVersion.document_id == document.id)
        .order_by(
            EvidenceDocumentVersion.version_number,
            EvidenceDocumentVersion.id,
        )
    )

    return list(db.scalars(statement))


def get_document_version(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
) -> EvidenceDocumentVersion | None:
    """Load one version only inside the authorized workspace and document."""

    statement = (
        select(EvidenceDocumentVersion)
        .join(
            EvidenceDocument,
            EvidenceDocument.id == EvidenceDocumentVersion.document_id,
        )
        .where(
            EvidenceDocument.workspace_id == workspace_id,
            EvidenceDocument.id == document_id,
            EvidenceDocumentVersion.id == version_id,
        )
    )

    return db.scalar(statement)


def list_version_chunks(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
) -> tuple[EvidenceChunk, ...] | None:
    """List chunks only when the requested version belongs to the workspace."""

    version = get_document_version(
        db,
        workspace_id=workspace_id,
        document_id=document_id,
        version_id=version_id,
    )

    if version is None:
        return None

    statement = (
        select(EvidenceChunk)
        .where(EvidenceChunk.document_version_id == version.id)
        .order_by(
            EvidenceChunk.chunk_index,
            EvidenceChunk.id,
        )
    )

    return tuple(db.scalars(statement))
