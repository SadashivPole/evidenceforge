"""Deterministic server-to-model projection interface.

Strictly projects server-authorized GenerationContext into a sanitized
ModelGenerationInput, hiding all internal database UUIDs, authorization scopes,
and database credentials from the future generation layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.questionnaires.generation.types import GenerationContext, GenerationEvidenceItem


@dataclass(frozen=True, slots=True)
class ModelEvidenceItem:
    """Sanitized evidence chunk visible to the model via opaque citation handle."""

    citation_handle: str
    content: str
    token_count: int
    document_name: str | None = None
    document_version_number: int = 1
    is_latest_document_version: bool | None = None
    document_status: str = "active"
    has_conflict: bool = False
    section_label: str | None = None
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class ModelGenerationInput:
    """Safe, bounded input projection provided to future generation providers."""

    question_text: str
    section_path: tuple[str, ...]
    evidence_items: tuple[ModelEvidenceItem, ...]
    total_evidence_tokens: int
    has_stale_or_conflicting_evidence: bool
    projection_version: str = "model-projection-v1"


def project_evidence_item(item: GenerationEvidenceItem) -> ModelEvidenceItem:
    """Project a server GenerationEvidenceItem into a sanitized ModelEvidenceItem."""
    return ModelEvidenceItem(
        citation_handle=item.citation_handle,
        content=item.content,
        token_count=item.token_count,
        document_name=item.document_name,
        document_version_number=item.document_version_number,
        is_latest_document_version=item.is_latest_document_version,
        document_status=item.document_status,
        has_conflict=(item.conflict_group_id is not None),
        section_label=item.section_label,
        page_number=item.page_number,
    )


def project_generation_context_to_model_input(
    context: GenerationContext,
    *,
    projection_version: str = "model-projection-v1",
) -> ModelGenerationInput:
    """Deterministic server-to-model projection function.

    Guarantees:
    - Raw chunk UUIDs (evidence_chunk_id), document UUIDs (document_id),
      version UUIDs (version_id), and workspace UUIDs (authorized_workspace_id)
      are strictly excluded.
    - Opaque citation handles ('EVIDENCE-1'..'EVIDENCE-5') are preserved.
    - Document status and freshness/conflict markers are preserved for reasoning.
    """
    projected_items = tuple(project_evidence_item(item) for item in context.evidence_context)

    has_stale_or_conflict = any(
        (item.is_latest_document_version is False)
        or (item.document_status.lower() not in {"active", "current"})
        or (item.conflict_group_id is not None)
        for item in context.evidence_context
    )

    return ModelGenerationInput(
        question_text=context.question_text,
        section_path=context.section_path,
        evidence_items=projected_items,
        total_evidence_tokens=context.total_evidence_tokens,
        has_stale_or_conflicting_evidence=has_stale_or_conflict,
        projection_version=projection_version,
    )
