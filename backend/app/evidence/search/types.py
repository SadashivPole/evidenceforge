"""Immutable types for deterministic EvidenceForge search."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchChunkCandidate:
    """One persisted evidence chunk eligible for deterministic search."""

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


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One deterministic search match."""

    candidate: SearchChunkCandidate
    score: int
    matched_terms: tuple[str, ...]
    exact_phrase_match: bool
    occurrence_count: int
