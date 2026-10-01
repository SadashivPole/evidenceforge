"""Server-side citation validator and generation context builder."""

from __future__ import annotations

import uuid
from typing import Any

from app.evidence.citations.types import EvidenceCitation
from app.evidence.context.types import ContextSelectionResult
from app.questionnaires.generation.config import (
    DEFAULT_GENERATION_BOUNDARY_CONFIG,
    GenerationBoundaryConfig,
)
from app.questionnaires.generation.errors import (
    GenerationCitationValidationError,
    GenerationPayloadMalformedError,
    GenerationStatusValidationError,
    GenerationWorkspaceMismatchError,
)
from app.questionnaires.generation.schemas import GeneratedDraftPayload
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
    ValidatedDraftResponse,
)
from app.questionnaires.persistence.models import QuestionnaireVersionQuestion
from app.questionnaires.types import ResponseStatus


def is_stale_or_conflicting_evidence(item: GenerationEvidenceItem) -> bool:
    """Determine whether a selected evidence candidate has stale or conflicting markers.

    Server-owned invariant:
    An evidence item is considered stale/conflicting if:
    - is_latest_document_version is False
    - conflict_group_id is not None
    - document_status indicates a non-current state (e.g. superseded, archived, deprecated)
    """

    if item.is_latest_document_version is False:
        return True
    if item.conflict_group_id is not None:
        return True
    doc_status = (item.document_status or "").strip().lower()
    if doc_status and doc_status not in {"active", "current"}:
        return True
    return False


def build_generation_context(
    question: QuestionnaireVersionQuestion,
    context_result: ContextSelectionResult,
    *,
    search_version: str = "hybrid-rrf-v1",
    config: GenerationBoundaryConfig = DEFAULT_GENERATION_BOUNDARY_CONFIG,
    fallback_used: bool = False,
    document_names: dict[uuid.UUID, str] | None = None,
) -> GenerationContext:
    """Build an immutable, server-controlled GenerationContext from selected evidence."""

    if question.workspace_id != context_result.workspace_id:
        raise GenerationWorkspaceMismatchError(
            f"Question workspace {question.workspace_id} does not match "
            f"selection workspace {context_result.workspace_id}"
        )

    doc_names = document_names or {}
    evidence_items: list[GenerationEvidenceItem] = []

    for index, sel in enumerate(context_result.selected_candidates, start=1):
        handle = f"EVIDENCE-{index}"
        doc_name = doc_names.get(sel.document_id)

        item = GenerationEvidenceItem(
            citation_handle=handle,
            evidence_chunk_id=sel.chunk_id,
            document_id=sel.document_id,
            version_id=sel.version_id,
            version_number=sel.version_number,
            document_name=doc_name,
            document_version_number=sel.document_version_number,
            latest_document_version_number=sel.latest_document_version_number,
            is_latest_document_version=sel.is_latest_document_version,
            document_status=sel.document_status,
            conflict_group_id=sel.conflict_group_id,
            chunk_index=sel.chunk_index,
            content_hash=sel.content_hash,
            normalized_start_byte=sel.normalized_start_byte,
            normalized_end_byte=sel.normalized_end_byte,
            section_label=sel.section_label,
            page_number=sel.page_number,
            content=sel.content,
            token_count=sel.token_count,
            rrf_score=sel.rrf_score,
            lexical_rank=sel.lexical_rank,
            semantic_rank=sel.semantic_rank,
            fallback_used=fallback_used,
        )
        evidence_items.append(item)

    return GenerationContext(
        question_id=question.id,
        questionnaire_id=question.questionnaire_id,
        questionnaire_version_id=question.questionnaire_version_id,
        questionnaire_version_question_id=question.id,
        question_text=question.normalized_question_text,
        section_path=tuple(question.section_path or ()),
        authorized_workspace_id=question.workspace_id,
        evidence_context=tuple(evidence_items),
        total_evidence_chunks=len(evidence_items),
        total_evidence_tokens=context_result.total_input_tokens,
        search_version=search_version,
        selection_version=context_result.selection_version,
        generation_boundary_version=config.generation_boundary_version,
    )


def validate_generated_draft(
    context: GenerationContext,
    untrusted_payload: GeneratedDraftPayload | dict[str, Any],
    *,
    config: GenerationBoundaryConfig = DEFAULT_GENERATION_BOUNDARY_CONFIG,
) -> ValidatedDraftResponse:
    """Validate untrusted model output against authoritative server context.

    Enforces server-owned invariants:
    1. Schema validation (Pydantic parsing with extra="forbid").
    2. Model proposed status restricted to PROPOSED or INSUFFICIENT_EVIDENCE only.
    3. Citation resolution: every handle must map to a candidate in context.
    4. Server evidence-state guard: PROPOSED is rejected if context has stale/conflicting evidence.
    5. Status integrity: PROPOSED requires citations, non-empty evidence, and non-empty answer.
    6. Clean resolution to immutable EvidenceCitation records preserving authoritative chunk_index.
    """

    if isinstance(untrusted_payload, GeneratedDraftPayload):
        parsed = untrusted_payload
    elif isinstance(untrusted_payload, dict):
        try:
            parsed = GeneratedDraftPayload.model_validate(untrusted_payload)
        except Exception as exc:
            raise GenerationPayloadMalformedError(
                f"Generated draft payload failed schema validation: {exc}"
            ) from exc
    else:
        raise GenerationPayloadMalformedError(
            f"Unsupported payload type {type(untrusted_payload).__name__}"
        )

    # 1. Validate citation handles against server context
    resolved_citations: list[EvidenceCitation] = []
    cited_chunk_ids: list[uuid.UUID] = []

    for handle in parsed.citation_handles:
        evidence_item = context.get_evidence_by_handle(handle)
        if evidence_item is None:
            raise GenerationCitationValidationError(
                f"Citation handle '{handle}' does not exist in the authorized evidence context. "
                f"Available handles: {[e.citation_handle for e in context.evidence_context]}"
            )

        citation = EvidenceCitation(
            workspace_id=context.authorized_workspace_id,
            document_id=evidence_item.document_id,
            version_id=evidence_item.version_id,
            version_number=evidence_item.version_number,
            chunk_id=evidence_item.evidence_chunk_id,
            chunk_index=evidence_item.chunk_index,
            content_hash=evidence_item.content_hash,
            normalized_start_byte=evidence_item.normalized_start_byte,
            normalized_end_byte=evidence_item.normalized_end_byte,
            section_label=evidence_item.section_label,
            page_number=evidence_item.page_number,
        )
        resolved_citations.append(citation)
        cited_chunk_ids.append(evidence_item.evidence_chunk_id)

    # 2. Status integrity & Server-Owned Evidence Guard
    if parsed.status == ResponseStatus.PROPOSED:
        if not context.evidence_context:
            raise GenerationStatusValidationError(
                "Model returned status PROPOSED but evidence context is empty. "
                "Must be INSUFFICIENT_EVIDENCE when no supporting evidence exists."
            )

        # Server-owned evidence freshness and conflict guard
        stale_or_conflicting = [
            item for item in context.evidence_context if is_stale_or_conflicting_evidence(item)
        ]
        if stale_or_conflicting:
            raise GenerationStatusValidationError(
                "Model returned status PROPOSED but selected evidence context contains stale "
                "or conflicting evidence. When selected evidence has stale/conflict markers "
                "(is_latest_document_version=False, superseded status, or conflict_group_id), "
                "model must return INSUFFICIENT_EVIDENCE."
            )

        if not parsed.citation_handles:
            raise GenerationStatusValidationError(
                "Model returned status PROPOSED without citing any evidence handles. "
                "A proposed answer must cite supporting evidence."
            )
        if not parsed.answer or not parsed.answer.strip():
            raise GenerationStatusValidationError(
                "Model returned status PROPOSED with an empty answer."
            )

    return ValidatedDraftResponse(
        question_id=context.question_id,
        workspace_id=context.authorized_workspace_id,
        answer=parsed.answer,
        status=parsed.status,
        resolved_citations=tuple(resolved_citations),
        cited_chunk_ids=tuple(cited_chunk_ids),
        citation_handles=tuple(parsed.citation_handles),
        uncertainty_notes=parsed.uncertainty_notes,
        generation_boundary_version=config.generation_boundary_version,
        validation_passed=True,
    )


class GenerationDraftValidator:
    """Service encapsulating server-side draft validation and context construction."""

    def __init__(
        self, config: GenerationBoundaryConfig = DEFAULT_GENERATION_BOUNDARY_CONFIG
    ) -> None:
        self.config = config

    def validate(
        self,
        context: GenerationContext,
        untrusted_payload: GeneratedDraftPayload | dict[str, Any],
    ) -> ValidatedDraftResponse:
        return validate_generated_draft(context, untrusted_payload, config=self.config)
