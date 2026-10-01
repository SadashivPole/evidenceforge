"""Domain types for immutable generation context and validated draft representations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.evidence.citations.types import EvidenceCitation
from app.questionnaires.types import ResponseStatus


@dataclass(frozen=True, slots=True)
class GenerationEvidenceItem:
    """One immutable evidence candidate exposed to the generation boundary with an opaque handle."""

    citation_handle: str  # e.g., "EVIDENCE-1"
    evidence_chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    document_name: str | None
    document_version_number: int
    latest_document_version_number: int | None
    is_latest_document_version: bool | None
    document_status: str
    conflict_group_id: str | None
    chunk_index: int
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None
    content: str
    token_count: int
    rrf_score: float | None = None
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    fallback_used: bool | None = None


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Server-constructed, immutable context provided to the generation layer."""

    question_id: uuid.UUID
    questionnaire_id: uuid.UUID
    questionnaire_version_id: uuid.UUID
    questionnaire_version_question_id: uuid.UUID
    question_text: str
    section_path: tuple[str, ...]
    authorized_workspace_id: uuid.UUID
    evidence_context: tuple[GenerationEvidenceItem, ...]
    total_evidence_chunks: int
    total_evidence_tokens: int
    search_version: str
    selection_version: str
    generation_boundary_version: str

    def get_evidence_by_handle(self, handle: str) -> GenerationEvidenceItem | None:
        """Lookup an evidence item by its exact opaque citation handle."""
        for item in self.evidence_context:
            if item.citation_handle == handle:
                return item
        return None


@dataclass(frozen=True, slots=True)
class ValidatedDraftResponse:
    """Server-validated draft response ready for human review workbench."""

    question_id: uuid.UUID
    workspace_id: uuid.UUID
    answer: str | None
    status: ResponseStatus
    resolved_citations: tuple[EvidenceCitation, ...]
    cited_chunk_ids: tuple[uuid.UUID, ...]
    citation_handles: tuple[str, ...]
    uncertainty_notes: str | None
    generation_boundary_version: str
    validation_passed: bool
