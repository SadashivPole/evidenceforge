"""Deterministic questionnaire domain services."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid

from app.questionnaires.normalization import (
    normalize_section_path,
    normalize_text,
)
from app.questionnaires.policy import (
    MAX_SOURCE_QUESTION_ID_LENGTH,
    QUESTION_IDENTITY_VERSION,
    QUESTION_NORMALIZATION_VERSION,
    QUESTIONNAIRE_HASH_VERSION,
    XLSX_IMPORT_VERSION,
)
from app.questionnaires.types import (
    Questionnaire,
    QuestionnaireQuestion,
    QuestionnaireStatus,
)


def serialize_canonical_json(value: object) -> str:
    """Serialize canonical questionnaire data deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def normalize_source_question_id(value: str) -> str:
    """Normalize and validate one explicit source question identifier."""

    nfkc_value = unicodedata.normalize("NFKC", value)

    if any(unicodedata.category(character) == "Cc" for character in nfkc_value):
        raise ValueError("source_question_id contains control characters")

    normalized = normalize_text(nfkc_value)

    if not normalized:
        raise ValueError("source_question_id must not be empty")

    if len(normalized) > MAX_SOURCE_QUESTION_ID_LENGTH:
        raise ValueError(
            "source_question_id exceeds the maximum length of "
            f"{MAX_SOURCE_QUESTION_ID_LENGTH} characters"
        )

    return normalized


def _identity_object(
    *,
    normalized_sheet: str,
    normalized_question: str,
    normalized_sections: tuple[str, ...],
    source_question_id: str | None,
    duplicate_occurrence: int,
) -> dict[str, object]:
    """Build the canonical identity object used for stable question IDs."""

    if source_question_id is not None:
        return {
            "identity_kind": "explicit",
            "identity_version": QUESTION_IDENTITY_VERSION,
            "source_question_id": normalize_source_question_id(source_question_id),
        }

    if duplicate_occurrence < 0:
        raise ValueError("duplicate_occurrence must be >= 0")

    return {
        "duplicate_occurrence": duplicate_occurrence,
        "identity_kind": "fallback",
        "identity_version": QUESTION_IDENTITY_VERSION,
        "normalized_question_text": normalized_question,
        "normalized_section_path": list(normalized_sections),
        "normalized_sheet_name": normalized_sheet,
    }


def generate_question_id(
    *,
    sheet_name: str,
    source_row: int | None = None,
    question_text: str,
    section_path: tuple[str, ...],
    source_question_id: str | None = None,
    duplicate_occurrence: int = 0,
) -> str:
    """Generate a stable SHA-256 identity independent of source row.

    ``source_row`` remains accepted for compatibility with existing callers,
    but is intentionally not read or included in the identity material.
    """

    del source_row

    normalized_sheet = normalize_text(sheet_name)
    normalized_question = normalize_text(question_text)
    normalized_sections = normalize_section_path(section_path)

    if not normalized_sheet:
        raise ValueError("sheet_name must not be empty")
    if not normalized_question:
        raise ValueError("question_text must not be empty")

    normalized_source_id = (
        normalize_source_question_id(source_question_id) if source_question_id is not None else None
    )

    identity = _identity_object(
        normalized_sheet=normalized_sheet,
        normalized_question=normalized_question,
        normalized_sections=normalized_sections,
        source_question_id=normalized_source_id,
        duplicate_occurrence=duplicate_occurrence,
    )
    identity_bytes = serialize_canonical_json(identity).encode("utf-8")

    return hashlib.sha256(identity_bytes).hexdigest()


def build_question(
    *,
    ordinal: int,
    sheet_name: str,
    source_row: int,
    question_text: str,
    section_path: tuple[str, ...] = (),
    source_question_id: str | None = None,
    duplicate_occurrence: int = 0,
) -> QuestionnaireQuestion:
    """Build one normalized questionnaire question."""

    normalized_text = normalize_text(question_text)
    normalized_sections = normalize_section_path(section_path)

    if not normalized_text:
        raise ValueError("question_text must not be empty")

    if source_row < 1:
        raise ValueError("source_row must be >= 1")

    if ordinal < 1:
        raise ValueError("ordinal must be >= 1")

    normalized_sheet = normalize_text(sheet_name)

    if not normalized_sheet:
        raise ValueError("sheet_name must not be empty")

    normalized_source_id = (
        normalize_source_question_id(source_question_id) if source_question_id is not None else None
    )
    identity_kind = "explicit" if normalized_source_id is not None else "fallback"

    question_id = generate_question_id(
        sheet_name=normalized_sheet,
        question_text=normalized_text,
        section_path=normalized_sections,
        source_question_id=normalized_source_id,
        duplicate_occurrence=duplicate_occurrence,
    )

    return QuestionnaireQuestion(
        question_id=question_id,
        ordinal=ordinal,
        sheet_name=normalized_sheet,
        source_row=source_row,
        question_text=normalized_text,
        section_path=normalized_sections,
        identity_kind=identity_kind,
        source_question_id=normalized_source_id,
    )


def canonical_questionnaire_representation(
    questionnaire: Questionnaire,
) -> dict[str, object]:
    """Build the pure canonical representation used for questionnaire hashing."""

    questions: list[dict[str, object]] = []

    for question in questionnaire.questions:
        questions.append(
            {
                "identity_kind": question.identity_kind,
                "normalized_question_text": question.normalized_question_text,
                "ordinal": question.ordinal,
                "question_id": question.question_id,
                "section_path": list(question.normalized_section_path),
                "sheet_name": question.normalized_sheet_name,
                "source_question_id": question.source_question_id,
            }
        )

    return {
        "hash_version": QUESTIONNAIRE_HASH_VERSION,
        "questions": questions,
    }


def hash_questionnaire(questionnaire: Questionnaire) -> str:
    """Hash the canonical questionnaire representation with SHA-256."""

    representation = canonical_questionnaire_representation(questionnaire)
    canonical_bytes = serialize_canonical_json(representation).encode("utf-8")

    return hashlib.sha256(canonical_bytes).hexdigest()


def build_questionnaire(
    *,
    name: str,
    source_filename: str,
    questions: tuple[QuestionnaireQuestion, ...],
    questionnaire_id: uuid.UUID | None = None,
) -> Questionnaire:
    """Build a validated normalized questionnaire."""

    normalized_name = normalize_text(name)
    normalized_filename = normalize_text(source_filename)

    if not normalized_name:
        raise ValueError("name must not be empty")

    if not normalized_filename:
        raise ValueError("source_filename must not be empty")

    if not questions:
        raise ValueError("questionnaire must contain at least one question")

    question_ids = [question.question_id for question in questions]

    if len(question_ids) != len(set(question_ids)):
        raise ValueError("question IDs must be unique")

    explicit_ids = [
        question.source_question_id
        for question in questions
        if question.source_question_id is not None
    ]

    if len(explicit_ids) != len(set(explicit_ids)):
        raise ValueError("explicit source question IDs must be unique")

    ordinals = [question.ordinal for question in questions]

    if ordinals != list(range(1, len(questions) + 1)):
        raise ValueError("question ordinals must be contiguous starting at 1")

    questionnaire = Questionnaire(
        questionnaire_id=questionnaire_id or uuid.uuid4(),
        name=normalized_name,
        source_filename=normalized_filename,
        status=QuestionnaireStatus.IMPORTED,
        questions=questions,
        parser_version=XLSX_IMPORT_VERSION,
        normalization_version=QUESTION_NORMALIZATION_VERSION,
        question_identity_version=QUESTION_IDENTITY_VERSION,
        hash_version=QUESTIONNAIRE_HASH_VERSION,
    )

    return Questionnaire(
        questionnaire_id=questionnaire.questionnaire_id,
        name=questionnaire.name,
        source_filename=questionnaire.source_filename,
        status=questionnaire.status,
        questions=questionnaire.questions,
        parser_version=questionnaire.parser_version,
        normalization_version=questionnaire.normalization_version,
        question_identity_version=questionnaire.question_identity_version,
        hash_version=questionnaire.hash_version,
        normalized_questionnaire_sha256=hash_questionnaire(questionnaire),
    )


__all__ = [
    "build_question",
    "build_questionnaire",
    "canonical_questionnaire_representation",
    "generate_question_id",
    "hash_questionnaire",
    "normalize_source_question_id",
    "serialize_canonical_json",
]
