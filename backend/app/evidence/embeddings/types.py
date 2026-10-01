"""Immutable types used by the embedding generation lifecycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class EmbeddingTarget:
    """One authorized immutable evidence chunk selected for generation."""

    workspace_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_id: uuid.UUID
    chunk_index: int
    content: str
    content_hash: str
    section_label: str | None
    page_number: int | None


class EmbeddingPersistenceStatus(StrEnum):
    """Outcome of one idempotent embedding persistence attempt."""

    CREATED = "CREATED"
    SKIPPED_EXISTING = "SKIPPED_EXISTING"


@dataclass(frozen=True, slots=True)
class EmbeddingFailure:
    """Safe diagnostic for one bounded generation/backfill failure."""

    workspace_id: uuid.UUID
    chunk_id: uuid.UUID
    error_type: str
    message: str
    attempts: int
    retryable: bool


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Bounded report for one workspace-scoped backfill run."""

    workspace_id: uuid.UUID
    configuration_hash: str
    requested_count: int
    generated_count: int
    skipped_count: int
    failed_count: int
    batch_count: int
    failures: tuple[EmbeddingFailure, ...]
