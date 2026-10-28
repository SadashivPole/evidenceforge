"""Deterministic GenerationContext construction for generation-boundary tests.

No database involved: evidence items are fabricated with stable UUIDs so
tests can assert projection sanitization (raw identifiers must never reach
the model surface).
"""

from __future__ import annotations

import uuid

from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
)

_NS = uuid.UUID("4b2b7d28-7c05-4e6f-bb9d-8d4d3c2a1111")


def make_generation_context(*, handles: int = 2, stale: bool = False) -> GenerationContext:
    """Build a generation context with `handles` fabricated evidence items.

    When `stale` is true, the first item is marked as a stale conflicting
    document (is_latest_document_version=False, conflict group set).
    """
    items: list[GenerationEvidenceItem] = []
    for index in range(1, handles + 1):
        items.append(
            GenerationEvidenceItem(
                citation_handle=f"EVIDENCE-{index}",
                evidence_chunk_id=uuid.uuid5(_NS, f"chunk-{index}"),
                document_id=uuid.uuid5(_NS, f"doc-{index}"),
                version_id=uuid.uuid5(_NS, f"ver-{index}"),
                version_number=3,
                document_name=f"policy-{index}.pdf",
                document_version_number=3,
                latest_document_version_number=4 if stale and index == 1 else 3,
                is_latest_document_version=not (stale and index == 1),
                document_status="active",
                conflict_group_id="conflict-1" if stale and index == 1 else None,
                chunk_index=7,
                content_hash=f"hash-{index}",
                normalized_start_byte=10,
                normalized_end_byte=210,
                section_label="Controls",
                page_number=2,
                content=(
                    f"Synthetic evidence passage {index} describing the control review cadence."
                ),
                token_count=42,
            )
        )

    return GenerationContext(
        question_id=uuid.uuid5(_NS, "question"),
        questionnaire_id=uuid.uuid5(_NS, "questionnaire"),
        questionnaire_version_id=uuid.uuid5(_NS, "questionnaire-version"),
        questionnaire_version_question_id=uuid.uuid5(_NS, "questionnaire-version-question"),
        question_text="How often does the duty officer review the access gate?",
        section_path=("Security", "Operational Controls"),
        authorized_workspace_id=uuid.uuid5(_NS, "workspace"),
        evidence_context=tuple(items),
        total_evidence_chunks=len(items),
        total_evidence_tokens=len(items) * 42,
        search_version="hybrid-rrf-v1",
        selection_version="context-selection-v1",
        generation_boundary_version="generation-boundary-v1",
    )
