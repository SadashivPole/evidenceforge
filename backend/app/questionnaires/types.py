"""Immutable questionnaire domain types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.questionnaires.policy import (
    QUESTION_IDENTITY_VERSION,
    QUESTION_NORMALIZATION_VERSION,
    QUESTIONNAIRE_HASH_VERSION,
    XLSX_IMPORT_VERSION,
)


class QuestionnaireStatus(StrEnum):
    """Lifecycle status for a questionnaire."""

    IMPORTED = "IMPORTED"
    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    COMPLETED = "COMPLETED"


class ResponseStatus(StrEnum):
    """Evidence-backed response status."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STALE_SOURCE = "STALE_SOURCE"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DO_NOT_DISCLOSE = "DO_NOT_DISCLOSE"


@dataclass(frozen=True, slots=True)
class QuestionnaireQuestion:
    """One normalized questionnaire question and its source provenance."""

    question_id: str
    ordinal: int
    sheet_name: str
    source_row: int
    question_text: str
    section_path: tuple[str, ...]
    identity_kind: str = "fallback"
    source_question_id: str | None = None

    @property
    def normalized_sheet_name(self) -> str:
        """Return the normalized worksheet name used by the contract."""

        return self.sheet_name

    @property
    def normalized_question_text(self) -> str:
        """Return the normalized question text used by the contract."""

        return self.question_text

    @property
    def normalized_section_path(self) -> tuple[str, ...]:
        """Return the normalized section path used by the contract."""

        return self.section_path


@dataclass(frozen=True, slots=True)
class Questionnaire:
    """Normalized questionnaire representation and deterministic metadata."""

    questionnaire_id: uuid.UUID
    name: str
    source_filename: str
    status: QuestionnaireStatus
    questions: tuple[QuestionnaireQuestion, ...]
    parser_version: str = XLSX_IMPORT_VERSION
    normalization_version: str = QUESTION_NORMALIZATION_VERSION
    question_identity_version: str = QUESTION_IDENTITY_VERSION
    hash_version: str = QUESTIONNAIRE_HASH_VERSION
    normalized_questionnaire_sha256: str = ""


@dataclass(frozen=True, slots=True)
class QuestionDraft:
    """Draft response attached to one questionnaire question."""

    question_id: str
    answer: str | None
    status: ResponseStatus
    citation_ids: tuple[str, ...]
