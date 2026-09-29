from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.styles import Font

from app.questionnaires.policy import (
    MAX_SOURCE_QUESTION_ID_LENGTH,
    QUESTION_IDENTITY_VERSION,
    QUESTION_NORMALIZATION_VERSION,
    QUESTIONNAIRE_HASH_VERSION,
    XLSX_IMPORT_VERSION,
)
from app.questionnaires.service import (
    build_question,
    build_questionnaire,
    canonical_questionnaire_representation,
    generate_question_id,
    normalize_source_question_id,
    serialize_canonical_json,
)
from app.questionnaires.xlsx.errors import DuplicateSourceQuestionIdError
from app.questionnaires.xlsx.service import import_xlsx


def make_workbook_bytes(
    rows: list[list[object]],
    *,
    filename_metadata: tuple[str, str] | None = None,
    style: bool = False,
) -> bytes:
    """Create an XLSX workbook with deterministic questionnaire rows."""

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Security"

    for row in rows:
        worksheet.append(row)

    if style:
        worksheet["A1"].font = Font(bold=True, italic=True)
        worksheet["B1"].font = Font(bold=True, color="FF0000")

    if filename_metadata is not None:
        workbook.properties.creator = filename_metadata[0]
        workbook.properties.title = filename_metadata[1]
        workbook.properties.created = datetime(2020, 1, 1, tzinfo=UTC)
        workbook.properties.modified = datetime(2030, 1, 1, tzinfo=UTC)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def import_rows(rows: list[list[object]], *, filename: str = "questionnaire.xlsx"):
    """Import a single-sheet questionnaire fixture."""

    return import_xlsx(
        data=make_workbook_bytes(rows),
        filename=filename,
    ).questionnaire


def test_version_constants_are_exact() -> None:
    assert XLSX_IMPORT_VERSION == "xlsx-import-v1"
    assert QUESTION_NORMALIZATION_VERSION == "question-normalization-v1"
    assert QUESTION_IDENTITY_VERSION == "question-identity-v2"
    assert QUESTIONNAIRE_HASH_VERSION == "questionnaire-hash-v1"


def test_empty_source_question_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_source_question_id("   ")


@pytest.mark.parametrize(
    "value",
    ["Q-001\n", "Q-001\r", "Q-001\t", "Q-001\x00"],
)
def test_control_character_in_source_question_id_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="control characters"):
        normalize_source_question_id(value)


def test_source_question_id_maximum_length_is_accepted() -> None:
    value = "Q" * MAX_SOURCE_QUESTION_ID_LENGTH

    assert normalize_source_question_id(value) == value


def test_source_question_id_above_maximum_length_is_rejected() -> None:
    value = "Q" * (MAX_SOURCE_QUESTION_ID_LENGTH + 1)

    with pytest.raises(ValueError, match="maximum length"):
        normalize_source_question_id(value)


def test_source_question_id_uses_nfkc_and_preserves_case() -> None:
    assert normalize_source_question_id(" Ｑ－００１ ") == "Q-001"
    assert normalize_source_question_id("q-ABC") == "q-ABC"


def test_explicit_source_id_produces_a_full_deterministic_sha256_id() -> None:
    first = generate_question_id(
        sheet_name="Security",
        source_row=2,
        question_text="Question text A",
        section_path=("Access",),
        source_question_id=" Q-001 ",
    )
    second = generate_question_id(
        sheet_name="Different Sheet",
        source_row=99,
        question_text="Changed question text",
        section_path=("Different",),
        source_question_id="Q-001",
    )

    assert first == second
    assert len(first) == 64
    assert first == first.lower()


def test_duplicate_explicit_source_id_is_rejected() -> None:
    data = make_workbook_bytes(
        [
            ["Question ID", "Question"],
            ["Q-001", "Question one"],
            [" Q-001 ", "Question two"],
        ]
    )

    with pytest.raises(DuplicateSourceQuestionIdError):
        import_xlsx(data=data, filename="questionnaire.xlsx")


def test_same_explicit_id_remains_stable_when_text_changes() -> None:
    first = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text="Question one",
        source_question_id="Q-001",
    )
    second = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=20,
        question_text="Changed question wording",
        source_question_id="Q-001",
    )

    assert first.question_id == second.question_id
    assert first.identity_kind == "explicit"
    assert first.source_question_id == "Q-001"


def test_fallback_identity_excludes_source_row() -> None:
    first = generate_question_id(
        sheet_name="Security",
        source_row=2,
        question_text="Question one",
        section_path=(),
    )
    second = generate_question_id(
        sheet_name="Security",
        source_row=200,
        question_text="Question one",
        section_path=(),
    )

    assert first == second


def test_fallback_identity_includes_sheet_section_and_text() -> None:
    base = generate_question_id(
        sheet_name="Security",
        source_row=2,
        question_text="Question one",
        section_path=("Access",),
    )

    assert (
        generate_question_id(
            sheet_name="Privacy",
            source_row=2,
            question_text="Question one",
            section_path=("Access",),
        )
        != base
    )
    assert (
        generate_question_id(
            sheet_name="Security",
            source_row=2,
            question_text="Question one",
            section_path=("Governance",),
        )
        != base
    )
    assert (
        generate_question_id(
            sheet_name="Security",
            source_row=2,
            question_text="Question two",
            section_path=("Access",),
        )
        != base
    )


def test_duplicate_occurrence_is_deterministic_and_distinguishes_fallback_duplicates() -> None:
    first = import_rows(
        [
            ["Question"],
            ["Same question"],
            ["Same question"],
        ]
    )
    second = import_rows(
        [
            ["Question"],
            ["Same question"],
            ["Same question"],
        ]
    )

    first_ids = [question.question_id for question in first.questions]
    second_ids = [question.question_id for question in second.questions]

    assert first_ids == second_ids
    assert first_ids[0] != first_ids[1]
    assert all(question.identity_kind == "fallback" for question in first.questions)


def test_identity_is_independent_of_python_hash_randomization() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    script = """
from app.questionnaires.service import generate_question_id
print(generate_question_id(
    sheet_name='Security',
    source_row=12,
    question_text='Question',
    section_path=('Access',),
    duplicate_occurrence=1,
))
"""
    values: list[str] = []

    for seed in ("1", "2"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = str(backend_root)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        values.append(completed.stdout.strip())

    assert values[0] == values[1]


def test_reordering_preserves_unique_ids_changes_ordinals_and_hash() -> None:
    first = import_rows(
        [
            ["Question"],
            ["Question one"],
            ["Question two"],
        ]
    )
    second = import_rows(
        [
            ["Question"],
            ["Question two"],
            ["Question one"],
        ]
    )

    first_by_text = {question.normalized_question_text: question for question in first.questions}
    second_by_text = {question.normalized_question_text: question for question in second.questions}

    assert first_by_text["Question one"].question_id == second_by_text["Question one"].question_id
    assert first_by_text["Question two"].question_id == second_by_text["Question two"].question_id
    assert [question.ordinal for question in second.questions] == [1, 2]
    assert [question.normalized_question_text for question in second.questions] == [
        "Question two",
        "Question one",
    ]
    assert first.normalized_questionnaire_sha256 != second.normalized_questionnaire_sha256


def test_inserting_blank_rows_preserves_semantic_hash_and_source_rows_remain_provenance() -> None:
    first = import_rows(
        [
            ["Question"],
            ["Question one"],
            ["Question two"],
        ]
    )
    second = import_rows(
        [
            ["Question"],
            [None],
            ["Question one"],
            [None],
            ["Question two"],
        ]
    )

    assert first.normalized_questionnaire_sha256 == second.normalized_questionnaire_sha256
    assert [question.source_row for question in first.questions] == [2, 3]
    assert [question.source_row for question in second.questions] == [3, 5]
    assert [question.question_id for question in first.questions] == [
        question.question_id for question in second.questions
    ]


def test_changed_fallback_text_and_section_change_identity() -> None:
    text_changed = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text="Changed text",
        section_path=("Access",),
    )
    original = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text="Original text",
        section_path=("Access",),
    )
    section_changed = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text="Original text",
        section_path=("Governance",),
    )

    assert text_changed.question_id != original.question_id
    assert section_changed.question_id != original.question_id


def test_canonical_json_is_sorted_compact_and_utf8_friendly() -> None:
    value = {"z": [1, "é"], "a": None}

    assert serialize_canonical_json(value) == '{"a":null,"z":[1,"é"]}'
    assert " " not in serialize_canonical_json(value)
    assert serialize_canonical_json(value).encode("utf-8")


def test_canonical_representation_uses_null_and_empty_section_path() -> None:
    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text="Question one",
    )
    questionnaire = build_questionnaire(
        name="Questionnaire",
        source_filename="source.xlsx",
        questions=(question,),
    )

    representation = canonical_questionnaire_representation(questionnaire)
    entry = representation["questions"][0]

    assert entry["source_question_id"] is None
    assert entry["section_path"] == []


def test_unicode_nfkc_equivalence_produces_same_identity_and_hash() -> None:
    first = import_rows(
        [
            ["Question"],
            ["Does the team use ＭＦＡ?"],
        ]
    )
    second = import_rows(
        [
            ["Question"],
            ["Does the team use MFA?"],
        ]
    )

    assert first.questions[0].question_id == second.questions[0].question_id
    assert first.normalized_questionnaire_sha256 == second.normalized_questionnaire_sha256


def test_filename_does_not_affect_canonical_hash() -> None:
    rows = [["Question"], ["Question one"]]

    first = import_rows(rows, filename="first.xlsx")
    second = import_rows(rows, filename="renamed.xlsx")

    assert first.source_filename != second.source_filename
    assert first.normalized_questionnaire_sha256 == second.normalized_questionnaire_sha256


def test_xlsx_styles_timestamps_and_metadata_do_not_affect_canonical_hash() -> None:
    rows = [["Question"], ["Question one"]]
    first_data = make_workbook_bytes(
        rows,
        filename_metadata=("Author A", "Title A"),
        style=False,
    )
    second_data = make_workbook_bytes(
        rows,
        filename_metadata=("Author B", "Title B"),
        style=True,
    )

    first = import_xlsx(data=first_data, filename="first.xlsx").questionnaire
    second = import_xlsx(data=second_data, filename="second.xlsx").questionnaire

    assert first.normalized_questionnaire_sha256 == second.normalized_questionnaire_sha256


def test_questionnaire_exposes_version_metadata_and_hash() -> None:
    questionnaire = import_rows([["Question"], ["Question one"]])

    assert questionnaire.parser_version == XLSX_IMPORT_VERSION
    assert questionnaire.normalization_version == QUESTION_NORMALIZATION_VERSION
    assert questionnaire.question_identity_version == QUESTION_IDENTITY_VERSION
    assert questionnaire.hash_version == QUESTIONNAIRE_HASH_VERSION
    assert len(questionnaire.normalized_questionnaire_sha256) == 64
