"""Workspace-scoped repositories for questionnaire responses."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvidenceChunk, EvidenceDocument, EvidenceDocumentVersion
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)


@dataclass(frozen=True, slots=True)
class EvidenceCitationTarget:
    """Evidence metadata resolved from one persisted chunk and its parent chain."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_index: int
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None


def get_version_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
) -> QuestionnaireVersionQuestion | None:
    """Resolve a version-specific question only through its full workspace chain."""

    statement = (
        select(QuestionnaireVersionQuestion)
        .join(
            QuestionnaireVersion,
            (
                (
                    QuestionnaireVersion.id
                    == QuestionnaireVersionQuestion.questionnaire_version_id
                )
                & (
                    QuestionnaireVersion.workspace_id
                    == QuestionnaireVersionQuestion.workspace_id
                )
                & (
                    QuestionnaireVersion.questionnaire_id
                    == QuestionnaireVersionQuestion.questionnaire_id
                )
            ),
        )
        .where(
            QuestionnaireVersionQuestion.workspace_id == workspace_id,
            QuestionnaireVersionQuestion.questionnaire_version_id
            == questionnaire_version_id,
            QuestionnaireVersionQuestion.id == questionnaire_version_question_id,
        )
    )
    return db.scalar(statement)


def lock_response(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
) -> QuestionnaireResponse | None:
    """Lock an existing logical response for serialized revision allocation."""

    statement = (
        select(QuestionnaireResponse)
        .where(
            QuestionnaireResponse.workspace_id == workspace_id,
            QuestionnaireResponse.questionnaire_version_id == questionnaire_version_id,
            QuestionnaireResponse.questionnaire_version_question_id
            == questionnaire_version_question_id,
        )
        .with_for_update()
    )
    return db.scalar(statement)


def get_response(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    response_id: uuid.UUID,
) -> QuestionnaireResponse | None:
    """Load one logical response only inside the authorized workspace."""

    return db.scalar(
        select(QuestionnaireResponse).where(
            QuestionnaireResponse.workspace_id == workspace_id,
            QuestionnaireResponse.id == response_id,
        )
    )


def list_responses_for_version(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
) -> tuple[QuestionnaireResponse, ...]:
    """List responses belonging to one authorized questionnaire version."""

    statement = (
        select(QuestionnaireResponse)
        .join(
            QuestionnaireVersionQuestion,
            (
                (QuestionnaireVersionQuestion.id
                 == QuestionnaireResponse.questionnaire_version_question_id)
                & (
                    QuestionnaireVersionQuestion.workspace_id
                    == QuestionnaireResponse.workspace_id
                )
                & (
                    QuestionnaireVersionQuestion.questionnaire_id
                    == QuestionnaireResponse.questionnaire_id
                )
                & (
                    QuestionnaireVersionQuestion.questionnaire_version_id
                    == QuestionnaireResponse.questionnaire_version_id
                )
            ),
        )
        .where(
            QuestionnaireResponse.workspace_id == workspace_id,
            QuestionnaireResponse.questionnaire_id == questionnaire_id,
            QuestionnaireResponse.questionnaire_version_id == questionnaire_version_id,
        )
        .order_by(
            QuestionnaireVersionQuestion.ordinal,
            QuestionnaireResponse.id,
        )
    )
    return tuple(db.scalars(statement))


def get_response_for_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_version_id: uuid.UUID,
    questionnaire_version_question_id: uuid.UUID,
) -> QuestionnaireResponse | None:
    """Load a response by its version-specific question identity."""

    return db.scalar(
        select(QuestionnaireResponse).where(
            QuestionnaireResponse.workspace_id == workspace_id,
            QuestionnaireResponse.questionnaire_version_id == questionnaire_version_id,
            QuestionnaireResponse.questionnaire_version_question_id
            == questionnaire_version_question_id,
        )
    )


def get_current_revision(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    response_id: uuid.UUID,
) -> QuestionnaireResponseRevision | None:
    """Return the latest immutable revision for a response."""

    return db.scalar(
        select(QuestionnaireResponseRevision)
        .where(
            QuestionnaireResponseRevision.workspace_id == workspace_id,
            QuestionnaireResponseRevision.response_id == response_id,
        )
        .order_by(
            QuestionnaireResponseRevision.revision_number.desc(),
            QuestionnaireResponseRevision.id.desc(),
        )
        .limit(1)
    )


def list_revisions(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    response_id: uuid.UUID,
) -> tuple[QuestionnaireResponseRevision, ...]:
    """List immutable response revisions in revision order."""

    statement = (
        select(QuestionnaireResponseRevision)
        .where(
            QuestionnaireResponseRevision.workspace_id == workspace_id,
            QuestionnaireResponseRevision.response_id == response_id,
        )
        .order_by(
            QuestionnaireResponseRevision.revision_number,
            QuestionnaireResponseRevision.id,
        )
    )
    return tuple(db.scalars(statement))


def list_revision_citations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    response_revision_id: uuid.UUID,
) -> tuple[QuestionnaireResponseCitation, ...]:
    """List server-resolved citation relationships deterministically."""

    statement = (
        select(QuestionnaireResponseCitation)
        .where(
            QuestionnaireResponseCitation.workspace_id == workspace_id,
            QuestionnaireResponseCitation.response_revision_id == response_revision_id,
        )
        .order_by(
            QuestionnaireResponseCitation.citation_order,
            QuestionnaireResponseCitation.id,
        )
    )
    return tuple(db.scalars(statement))


def next_revision_number(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    response_id: uuid.UUID,
) -> int:
    """Compute the next revision number while the response row is locked."""

    latest = db.scalar(
        select(func.max(QuestionnaireResponseRevision.revision_number)).where(
            QuestionnaireResponseRevision.workspace_id == workspace_id,
            QuestionnaireResponseRevision.response_id == response_id,
        )
    )
    return (latest or 0) + 1


def resolve_evidence_chunks(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    chunk_ids: tuple[uuid.UUID, ...],
) -> tuple[EvidenceCitationTarget, ...]:
    """Resolve citation metadata through chunk, version, document, workspace joins."""

    if not chunk_ids:
        return ()

    statement = (
        select(
            EvidenceChunk.id,
            EvidenceDocument.id,
            EvidenceDocumentVersion.id,
            EvidenceDocumentVersion.version_number,
            EvidenceChunk.chunk_index,
            EvidenceChunk.content_hash,
            EvidenceChunk.normalized_start_byte,
            EvidenceChunk.normalized_end_byte,
            EvidenceChunk.section_label,
            EvidenceChunk.page_number,
        )
        .join(
            EvidenceDocumentVersion,
            EvidenceDocumentVersion.id == EvidenceChunk.document_version_id,
        )
        .join(
            EvidenceDocument,
            EvidenceDocument.id == EvidenceDocumentVersion.document_id,
        )
        .where(
            EvidenceChunk.id.in_(chunk_ids),
            EvidenceDocument.workspace_id == workspace_id,
        )
    )
    rows = db.execute(statement).all()
    by_id = {
        row[0]: EvidenceCitationTarget(
            chunk_id=row[0],
            document_id=row[1],
            version_id=row[2],
            version_number=row[3],
            chunk_index=row[4],
            content_hash=row[5],
            normalized_start_byte=row[6],
            normalized_end_byte=row[7],
            section_label=row[8],
            page_number=row[9],
        )
        for row in rows
    }
    return tuple(by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id)
