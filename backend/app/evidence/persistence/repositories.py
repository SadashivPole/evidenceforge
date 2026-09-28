"""Repository helpers for transactional EvidenceForge persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvidenceDocument, EvidenceDocumentVersion


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
