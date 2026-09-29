"""SQLAlchemy models for immutable questionnaire responses and citations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.questionnaires.types import ResponseStatus

_RESPONSE_STATUS_VALUES = ", ".join(
    f"'{status.value}'" for status in ResponseStatus
)


class QuestionnaireResponse(Base):
    """One logical response for one questionnaire-version question."""

    __tablename__ = "questionnaire_responses"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "questionnaire_version_id",
            "questionnaire_version_question_id",
            name="uq_questionnaire_responses_version_question",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_responses_workspace_id_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id", "questionnaire_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.questionnaire_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_responses_version_scope",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "questionnaire_id",
                "questionnaire_version_id",
                "questionnaire_version_question_id",
            ],
            [
                "questionnaire_version_questions.workspace_id",
                "questionnaire_version_questions.questionnaire_id",
                "questionnaire_version_questions.questionnaire_version_id",
                "questionnaire_version_questions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_responses_version_question_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    questionnaire_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    questionnaire_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    questionnaire_version_question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class QuestionnaireResponseRevision(Base):
    """Immutable answer/status snapshot for a logical questionnaire response."""

    __tablename__ = "questionnaire_response_revisions"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "response_id",
            "revision_number",
            name="uq_questionnaire_response_revisions_number",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_response_revisions_workspace_id_id",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_questionnaire_response_revisions_positive_number",
        ),
        CheckConstraint(
            f"status IN ({_RESPONSE_STATUS_VALUES})",
            name="ck_questionnaire_response_revisions_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "response_id"],
            [
                "questionnaire_responses.workspace_id",
                "questionnaire_responses.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_response_revisions_response_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    response_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class QuestionnaireResponseCitation(Base):
    """Server-resolved immutable relationship from a revision to an evidence chunk."""

    __tablename__ = "questionnaire_response_citations"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "response_revision_id",
            "evidence_chunk_id",
            name="uq_questionnaire_response_citations_revision_chunk",
        ),
        UniqueConstraint(
            "workspace_id",
            "response_revision_id",
            "citation_order",
            name="uq_questionnaire_response_citations_revision_order",
        ),
        CheckConstraint(
            "citation_order >= 1",
            name="ck_questionnaire_response_citations_positive_order",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "response_revision_id"],
            [
                "questionnaire_response_revisions.workspace_id",
                "questionnaire_response_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_response_citations_revision_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    response_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    citation_order: Mapped[int] = mapped_column(Integer, nullable=False)

    # These identifiers and the denormalized metadata are populated only from
    # the joined persisted EvidenceChunk chain by the response service.
    evidence_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_documents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    evidence_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_document_versions.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    evidence_chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_chunks.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    evidence_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_start_byte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    normalized_end_byte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    section_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
