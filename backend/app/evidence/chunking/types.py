"""Immutable types for deterministic EvidenceForge chunking."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkResult:
    """One deterministic chunk produced from normalized evidence text."""

    chunk_index: int
    content: str
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None = None
