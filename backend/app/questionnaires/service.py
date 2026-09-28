"""Deterministic questionnaire domain services."""

from __future__ import annotations

import hashlib
import uuid

from app.questionnaires.normalization import (
    normalize_section_path,
    normalize_text,
)
from app.questionnaires.policy import QUESTION_ID_VERSION
from app.questionnaires.types import (
    Questionnaire,
    QuestionnaireQuestion,
    QuestionnaireStatus,
)


def generate_question_id(
    *,
    sheet_name: str,
    source_row: int,
    question_text: str,
    section_path: tuple[str, ...],
) -> str:
    """Generate a deterministic identifier for one questionnaire question."""

    normalized_sheet = normalize_text(sheet_name)
    normalized_question = normalize_text(question_text)
    normalized_sections = normalize_section_path(section_path)

    material = "\x1f".join(
        (
            QUESTION_ID_VERSION,
            normalized_sheet,
            str(source_row),
            normalized_question,
            "\x1e".join(normalized_sections),
        )
    )

    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()

    return f"q_{digest[:32]}"


def build_question(
    *,
    ordinal: int,
    sheet_name: str,
    source_row: int,
    question_text: str,
    section_path: tuple[str, ...] = (),
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

    question_id = generate_question_id(
        sheet_name=normalized_sheet,
        source_row=source_row,
        question_text=normalized_text,
        section_path=normalized_sections,
    )

    return QuestionnaireQuestion(
        question_id=question_id,
        ordinal=ordinal,
        sheet_name=normalized_sheet,
        source_row=source_row,
        question_text=normalized_text,
        section_path=normalized_sections,
    )


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

    ordinals = [question.ordinal for question in questions]

    if ordinals != list(range(1, len(questions) + 1)):
        raise ValueError("question ordinals must be contiguous starting at 1")

    return Questionnaire(
        questionnaire_id=questionnaire_id or uuid.uuid4(),
        name=normalized_name,
        source_filename=normalized_filename,
        status=QuestionnaireStatus.IMPORTED,
        questions=questions,
    )