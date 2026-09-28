"""Database access helpers for deterministic EvidenceForge search."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence.search.types import SearchChunkCandidate
from app.models import EvidenceChunk, EvidenceDocument, EvidenceDocumentVersion


def list_search_candidates(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
) -> tuple[SearchChunkCandidate, ...]:
    """Load workspace-scoped persisted chunks as immutable search candidates.

    Optional document/version filters narrow the search scope while preserving
    workspace isolation.
    """

    statement = (
        select(
            EvidenceChunk.id,
            EvidenceDocument.id,
            EvidenceDocumentVersion.id,
            EvidenceDocumentVersion.version_number,
            EvidenceChunk.chunk_index,
            EvidenceChunk.content,
            EvidenceChunk.content_hash,
            EvidenceChunk.normalized_start_byte,
            EvidenceChunk.normalized_end_byte,
            EvidenceChunk.section_label,
            EvidenceChunk.page_number,
        )
        .join(
            EvidenceDocumentVersion,
            EvidenceDocumentVersion.id == EvidenceChunk.document_version_id,
        )
        .join(
            EvidenceDocument,
            EvidenceDocument.id == EvidenceDocumentVersion.document_id,
        )
        .where(EvidenceDocument.workspace_id == workspace_id)
    )

    if document_id is not None:
        statement = statement.where(EvidenceDocument.id == document_id)

    if version_id is not None:
        statement = statement.where(EvidenceDocumentVersion.id == version_id)

    statement = statement.order_by(
        EvidenceDocument.id,
        EvidenceDocumentVersion.version_number,
        EvidenceChunk.chunk_index,
        EvidenceChunk.id,
    )

    rows = db.execute(statement).all()

    return tuple(
        SearchChunkCandidate(
            chunk_id=chunk_id,
            document_id=document_id_value,
            version_id=version_id_value,
            version_number=version_number,
            chunk_index=chunk_index,
            content=content,
            content_hash=content_hash,
            normalized_start_byte=normalized_start_byte,
            normalized_end_byte=normalized_end_byte,
            section_label=section_label,
            page_number=page_number,
        )
        for (
            chunk_id,
            document_id_value,
            version_id_value,
            version_number,
            chunk_index,
            content,
            content_hash,
            normalized_start_byte,
            normalized_end_byte,
            section_label,
            page_number,
        ) in rows
    )
