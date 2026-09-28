"""Immutable citation types for EvidenceForge evidence provenance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """Exact immutable reference to one persisted evidence chunk."""

    workspace_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_id: uuid.UUID
    chunk_index: int
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None
