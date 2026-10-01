"""Domain types for human review workbench and decision execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.evidence.citations.types import EvidenceCitation
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)
from app.questionnaires.types import ResponseStatus


class ReviewAction(StrEnum):
    """Explicit human reviewer decisions."""

    ACCEPT = "ACCEPT"
    APPROVE = "APPROVE"
    EDIT_AND_APPROVE = "EDIT_AND_APPROVE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class ReviewEvidenceItem:
    """One candidate evidence item formatted for reviewer inspection."""

    citation_handle: str
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
    is_cited_by_draft: bool = False


@dataclass(frozen=True, slots=True)
class ReviewDraftContext:
    """Server-authorized review context combining question, draft, and evidence."""

    question_id: uuid.UUID
    questionnaire_id: uuid.UUID
    questionnaire_version_id: uuid.UUID
    questionnaire_version_question_id: uuid.UUID
    question_text: str
    section_path: tuple[str, ...]
    authorized_workspace_id: uuid.UUID
    draft_answer: str | None
    proposed_status: ResponseStatus
    uncertainty_notes: str | None
    evidence_items: tuple[ReviewEvidenceItem, ...]
    cited_handles: tuple[str, ...]
    cited_chunk_ids: tuple[uuid.UUID, ...]
    resolved_citations: tuple[EvidenceCitation, ...]
    search_version: str
    selection_version: str
    generation_boundary_version: str
    has_stale_or_conflicting_evidence: bool

    def get_evidence_by_handle(self, handle: str) -> ReviewEvidenceItem | None:
        """Lookup evidence item by its opaque handle."""
        for item in self.evidence_items:
            if item.citation_handle == handle:
                return item
        return None

    def get_evidence_by_chunk_id(self, chunk_id: uuid.UUID) -> ReviewEvidenceItem | None:
        """Lookup evidence item by chunk ID."""
        for item in self.evidence_items:
            if item.evidence_chunk_id == chunk_id:
                return item
        return None


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """Reviewer decision payload indicating action and optional answer/citation adjustments."""

    action: ReviewAction
    edited_answer: str | None = None
    selected_citation_handles: tuple[str, ...] | None = None
    selected_chunk_ids: tuple[uuid.UUID, ...] | None = None
    rejection_notes: str | None = None
    target_status: ResponseStatus | None = None


@dataclass(frozen=True, slots=True)
class ReviewExecutionResult:
    """Committed outcome of a human review decision."""

    response: QuestionnaireResponse
    revision: QuestionnaireResponseRevision
    citations: tuple[QuestionnaireResponseCitation, ...]
    action: ReviewAction
    is_approved: bool
    revision_created: bool
