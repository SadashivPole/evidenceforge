"""Immutable types for workspace-authorized semantic retrieval."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.evidence.search.types import SearchChunkCandidate


@dataclass(frozen=True, slots=True)
class SemanticSearchResult:
    """One attributable semantic retrieval candidate with similarity and provenance."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_index: int
    content: str
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None
    workspace_id: uuid.UUID
    distance: float
    similarity: float
    model_id: str
    model_version: str
    configuration_hash: str
    embedding_dimension: int

    @property
    def candidate(self) -> SearchChunkCandidate:
        """Expose a SearchChunkCandidate representation for citation construction."""
        return self.to_search_candidate()

    def to_search_candidate(self) -> SearchChunkCandidate:
        """Convert this semantic result to a persisted SearchChunkCandidate."""
        return SearchChunkCandidate(
            chunk_id=self.chunk_id,
            document_id=self.document_id,
            version_id=self.version_id,
            version_number=self.version_number,
            chunk_index=self.chunk_index,
            content=self.content,
            content_hash=self.content_hash,
            normalized_start_byte=self.normalized_start_byte,
            normalized_end_byte=self.normalized_end_byte,
            section_label=self.section_label,
            page_number=self.page_number,
        )
