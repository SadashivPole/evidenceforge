"""Immutable questionnaire domain types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


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
    """One normalized questionnaire question."""

    question_id: str
    ordinal: int
    sheet_name: str
    source_row: int
    question_text: str
    section_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Questionnaire:
    """Normalized questionnaire representation."""

    questionnaire_id: uuid.UUID
    name: str
    source_filename: str
    status: QuestionnaireStatus
    questions: tuple[QuestionnaireQuestion, ...]


@dataclass(frozen=True, slots=True)
class QuestionDraft:
    """Draft response attached to one questionnaire question."""

    question_id: str
    answer: str | None
    status: ResponseStatus
    citation_ids: tuple[str, ...]