"""Citation construction helpers for EvidenceForge search results."""

from __future__ import annotations

import uuid

from app.evidence.citations.types import EvidenceCitation
from app.evidence.search.types import SearchChunkCandidate


def citation_from_candidate(
    candidate: SearchChunkCandidate,
    *,
    workspace_id: uuid.UUID,
) -> EvidenceCitation:
    """Build an immutable citation from a persisted search candidate."""

    return EvidenceCitation(
        workspace_id=workspace_id,
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        version_number=candidate.version_number,
        chunk_id=candidate.chunk_id,
        chunk_index=candidate.chunk_index,
        content_hash=candidate.content_hash,
        normalized_start_byte=candidate.normalized_start_byte,
        normalized_end_byte=candidate.normalized_end_byte,
        section_label=candidate.section_label,
        page_number=candidate.page_number,
    )
