"""SQLAlchemy models for immutable questionnaire persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
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


class Questionnaire(Base):
    """Stable logical questionnaire identity inside one workspace."""

    __tablename__ = "questionnaires"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaires_workspace_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_questionnaires_workspace_name",
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
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class QuestionnaireVersion(Base):
    """Immutable canonical imported representation of a questionnaire."""

    __tablename__ = "questionnaire_versions"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_versions_workspace_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "id",
            name="uq_questionnaire_versions_workspace_questionnaire_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "version_number",
            name="uq_questionnaire_versions_questionnaire_version",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "canonical_hash_version",
            "canonical_sha256",
            name="uq_questionnaire_versions_representation",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_questionnaire_versions_positive_version",
        ),
        CheckConstraint(
            "question_count >= 0",
            name="ck_questionnaire_versions_nonnegative_question_count",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id"],
            ["questionnaires.workspace_id", "questionnaires.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_versions_questionnaire_workspace",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    questionnaire_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    question_identity_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    canonical_hash_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class QuestionnaireQuestion(Base):
    """Stable logical question identity shared across versions."""

    __tablename__ = "questionnaire_questions"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_questions_workspace_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "id",
            name="uq_questionnaire_questions_workspace_questionnaire_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "question_id",
            name="uq_questionnaire_questions_identity",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "identity_fingerprint",
            name="uq_questionnaire_questions_fingerprint",
        ),
        CheckConstraint(
            "identity_kind IN ('explicit', 'fallback')",
            name="ck_questionnaire_questions_identity_kind",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id"],
            ["questionnaires.workspace_id", "questionnaires.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_questions_questionnaire_workspace",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    questionnaire_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    identity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    normalized_question_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sheet_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    normalized_section_path: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
    )
    source_question_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class QuestionnaireVersionQuestion(Base):
    """Immutable question snapshot belonging to one questionnaire version."""

    __tablename__ = "questionnaire_version_questions"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_version_questions_workspace_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "questionnaire_version_id",
            "ordinal",
            name="uq_questionnaire_version_questions_ordinal",
        ),
        UniqueConstraint(
            "workspace_id",
            "questionnaire_id",
            "questionnaire_version_id",
            "questionnaire_question_id",
            name="uq_questionnaire_version_questions_question",
        ),
        CheckConstraint(
            "ordinal > 0",
            name="ck_questionnaire_version_questions_positive_ordinal",
        ),
        CheckConstraint(
            "source_row > 0",
            name="ck_questionnaire_version_questions_positive_source_row",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id", "questionnaire_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.questionnaire_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_version_questions_version_scope",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id", "questionnaire_question_id"],
            [
                "questionnaire_questions.workspace_id",
                "questionnaire_questions.questionnaire_id",
                "questionnaire_questions.id",
            ],
            ondelete="RESTRICT",
            name="fk_questionnaire_version_questions_question_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    questionnaire_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    questionnaire_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    questionnaire_question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    section_path: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    normalized_question_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_question_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class QuestionnaireImportAttempt(Base):
    """Immutable provenance record for one questionnaire import attempt."""

    __tablename__ = "questionnaire_import_attempts"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_questionnaire_import_attempts_workspace_id_id",
        ),
        CheckConstraint(
            "outcome IN ('CREATED', 'IDENTICAL_DUPLICATE', 'CANONICAL_DUPLICATE', 'FAILED')",
            name="ck_questionnaire_import_attempts_outcome",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "questionnaire_id"],
            ["questionnaires.workspace_id", "questionnaires.id"],
            ondelete="CASCADE",
            name="fk_questionnaire_import_attempts_questionnaire_scope",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "resulting_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_import_attempts_resulting_version_scope",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "duplicate_of_version_id"],
            [
                "questionnaire_versions.workspace_id",
                "questionnaire_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_questionnaire_import_attempts_duplicate_version_scope",
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
    questionnaire_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    duplicate_of_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    question_identity_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    canonical_hash_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    question_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
