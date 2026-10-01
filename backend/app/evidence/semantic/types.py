"""Immutable types for workspace-authorized semantic retrieval."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.evidence.search.types import SearchChunkCandidate


@dataclass(frozen=True, slots=True)
class SemanticSearchResult:
    """One attributable semantic retrieval candidate with similarity, freshness, and provenance."""

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
    document_status: str = "active"
    latest_document_version_number: int | None = None
    is_latest_document_version: bool | None = None
    conflict_group_id: str | None = None

    def __post_init__(self) -> None:
        if (
            self.latest_document_version_number is not None
            and self.is_latest_document_version is None
        ):
            object.__setattr__(
                self,
                "is_latest_document_version",
                self.version_number == self.latest_document_version_number,
            )

    @property
    def document_version_number(self) -> int:
        """Authoritative persisted document version number."""
        return self.version_number

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
            document_status=self.document_status,
            latest_document_version_number=self.latest_document_version_number,
            is_latest_document_version=self.is_latest_document_version,
            conflict_group_id=self.conflict_group_id,
        )
