from __future__ import annotations

import uuid

import pytest

from app.questionnaires.service import (
    build_question,
    build_questionnaire,
    generate_question_id,
)
from app.questionnaires.types import QuestionnaireStatus


def test_question_id_is_deterministic() -> None:
    first = generate_question_id(
        sheet_name="Security",
        source_row=12,
        question_text="  Do you have an incident response plan? ",
        section_path=("Security Governance",),
    )

    second = generate_question_id(
        sheet_name="Security",
        source_row=12,
        question_text="Do you have an incident response plan?",
        section_path=("Security Governance",),
    )

    assert first == second


def test_question_id_does_not_change_when_source_row_changes() -> None:
    first = generate_question_id(
        sheet_name="Security",
        source_row=12,
        question_text="Do you have an incident response plan?",
        section_path=("Security Governance",),
    )

    second = generate_question_id(
        sheet_name="Security",
        source_row=13,
        question_text="Do you have an incident response plan?",
        section_path=("Security Governance",),
    )

    assert first == second


def test_build_question_normalizes_text() -> None:
    question = build_question(
        ordinal=1,
        sheet_name=" Security ",
        source_row=5,
        question_text="  Do   you have\nan IR plan?  ",
        section_path=(" Governance ", ""),
    )

    assert question.sheet_name == "Security"
    assert question.question_text == "Do you have an IR plan?"
    assert question.section_path == ("Governance",)
    assert question.source_row == 5
    assert question.ordinal == 1


def test_build_question_rejects_empty_question() -> None:
    with pytest.raises(ValueError, match="question_text"):
        build_question(
            ordinal=1,
            sheet_name="Security",
            source_row=1,
            question_text="   ",
        )


def test_build_question_rejects_invalid_row() -> None:
    with pytest.raises(ValueError, match="source_row"):
        build_question(
            ordinal=1,
            sheet_name="Security",
            source_row=0,
            question_text="Do you have an IR plan?",
        )


def test_build_questionnaire_has_stable_structure() -> None:
    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=10,
        question_text="Do you have an incident response plan?",
    )

    questionnaire_id = uuid.uuid4()

    questionnaire = build_questionnaire(
        name="Vendor Security Questionnaire",
        source_filename="vendor-questionnaire.xlsx",
        questions=(question,),
        questionnaire_id=questionnaire_id,
    )

    assert questionnaire.questionnaire_id == questionnaire_id
    assert questionnaire.status is QuestionnaireStatus.IMPORTED
    assert questionnaire.name == "Vendor Security Questionnaire"
    assert questionnaire.source_filename == "vendor-questionnaire.xlsx"
    assert len(questionnaire.questions) == 1
    assert questionnaire.questions[0].question_id == question.question_id


def test_build_questionnaire_rejects_duplicate_ids() -> None:
    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=10,
        question_text="Question A",
    )

    duplicate = type(question)(
        question_id=question.question_id,
        ordinal=2,
        sheet_name="Security",
        source_row=11,
        question_text="Question B",
        section_path=(),
    )

    with pytest.raises(ValueError, match="unique"):
        build_questionnaire(
            name="Questionnaire",
            source_filename="questionnaire.xlsx",
            questions=(question, duplicate),
        )


def test_build_questionnaire_rejects_non_contiguous_ordinals() -> None:
    first = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=10,
        question_text="Question A",
    )

    second = build_question(
        ordinal=3,
        sheet_name="Security",
        source_row=12,
        question_text="Question B",
    )

    with pytest.raises(ValueError, match="contiguous"):
        build_questionnaire(
            name="Questionnaire",
            source_filename="questionnaire.xlsx",
            questions=(first, second),
        )
