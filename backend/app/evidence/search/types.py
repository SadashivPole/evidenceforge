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


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One deterministic search match."""

    candidate: SearchChunkCandidate
    score: int
    matched_terms: tuple[str, ...]
    exact_phrase_match: bool
    occurrence_count: int
