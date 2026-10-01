"""Immutable types for evidence context selection and budgeting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.evidence.search.types import SearchChunkCandidate


@dataclass(frozen=True, slots=True)
class SelectedEvidenceCandidate:
    """One evidence chunk selected into the bounded prompt context."""

    candidate: SearchChunkCandidate
    selection_rank: int
    token_count: int
    rrf_score: float | None = None
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    workspace_id: uuid.UUID | None = None

    @property
    def chunk_id(self) -> uuid.UUID:
        return self.candidate.chunk_id

    @property
    def document_id(self) -> uuid.UUID:
        return self.candidate.document_id

    @property
    def version_id(self) -> uuid.UUID:
        return self.candidate.version_id

    @property
    def version_number(self) -> int:
        return self.candidate.version_number

    @property
    def document_version_number(self) -> int:
        return self.candidate.document_version_number

    @property
    def latest_document_version_number(self) -> int | None:
        return self.candidate.latest_document_version_number

    @property
    def is_latest_document_version(self) -> bool | None:
        return self.candidate.is_latest_document_version

    @property
    def document_status(self) -> str:
        return self.candidate.document_status

    @property
    def conflict_group_id(self) -> str | None:
        return self.candidate.conflict_group_id

    @property
    def chunk_index(self) -> int:
        return self.candidate.chunk_index

    @property
    def content(self) -> str:
        return self.candidate.content

    @property
    def content_hash(self) -> str:
        return self.candidate.content_hash

    @property
    def normalized_start_byte(self) -> int:
        return self.candidate.normalized_start_byte

    @property
    def normalized_end_byte(self) -> int:
        return self.candidate.normalized_end_byte

    @property
    def section_label(self) -> str | None:
        return self.candidate.section_label

    @property
    def page_number(self) -> int | None:
        return self.candidate.page_number


@dataclass(frozen=True, slots=True)
class ContextSelectionDiagnostics:
    """Operational telemetry and bounds accounting for one context selection run."""

    total_candidates_considered: int
    total_chunks_selected: int
    total_tokens_selected: int
    remaining_token_budget: int
    oversized_candidates_skipped: int
    budget_exceeded_candidates_skipped: int
    tokenizer_version: str
    is_exact_model_tokenizer: bool
    selection_version: str


@dataclass(frozen=True, slots=True)
class ContextSelectionResult:
    """Deterministic, bounded evidence context ready for response generation."""

    workspace_id: uuid.UUID
    selected_candidates: tuple[SelectedEvidenceCandidate, ...]
    total_selected_chunks: int
    total_input_tokens: int
    truncated_candidate_count: int  # Invariant: 0 (we never silently truncate)
    skipped_candidate_count: int  # oversized + budget exceeded
    selection_version: str
    diagnostics: ContextSelectionDiagnostics
