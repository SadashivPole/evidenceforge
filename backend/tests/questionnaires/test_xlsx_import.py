from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from app.questionnaires.xlsx.errors import (
    AmbiguousQuestionColumnError,
    InvalidQuestionValueError,
    InvalidSourceQuestionIdError,
    InvalidXlsxWorkbookError,
    NoQuestionColumnError,
    TooManyQuestionsError,
    TooManySheetsError,
    UnsupportedXlsxExtensionError,
    XlsxFileTooLargeError,
    XlsxImportError,
)
from app.questionnaires.xlsx.policy import XlsxImportPolicy
from app.questionnaires.xlsx.service import import_xlsx


def make_workbook_bytes(
    worksheets: list[tuple[str, list[list[object]]]],
) -> bytes:
    """Create an in-memory XLSX workbook for tests."""

    workbook = Workbook()

    first = True

    for sheet_name, rows in worksheets:
        if first:
            worksheet = workbook.active
            worksheet.title = sheet_name
            first = False
        else:
            worksheet = workbook.create_sheet(sheet_name)

        for row in rows:
            worksheet.append(row)

    buffer = BytesIO()
    workbook.save(buffer)

    return buffer.getvalue()


def test_import_valid_questionnaire() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Section", "Question"],
                    ["Governance", "Do you have an incident response plan?"],
                    [None, "Is the plan reviewed annually?"],
                ],
            )
        ]
    )

    result = import_xlsx(
        data=data,
        filename="vendor-questionnaire.xlsx",
    )

    questionnaire = result.questionnaire

    assert questionnaire.name == "vendor-questionnaire"
    assert questionnaire.source_filename == "vendor-questionnaire.xlsx"
    assert len(questionnaire.questions) == 2

    first = questionnaire.questions[0]
    second = questionnaire.questions[1]

    assert first.sheet_name == "Security"
    assert first.source_row == 2
    assert first.question_text == "Do you have an incident response plan?"
    assert first.section_path == ("Governance",)

    assert second.source_row == 3
    assert second.question_text == "Is the plan reviewed annually?"
    assert second.section_path == ("Governance",)

    assert len(result.imported_sheets) == 1
    assert result.imported_sheets[0].header_row == 1
    assert result.imported_sheets[0].question_header == "Question"
    assert result.imported_sheets[0].section_header == "Section"
    assert result.imported_sheets[0].question_count == 2


def test_import_accepts_case_and_whitespace_in_headers() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["  qUeStIoN   TeXt  "],
                    ["Do you review privileged access?"],
                ],
            )
        ]
    )

    result = import_xlsx(
        data=data,
        filename="questionnaire.xlsx",
    )

    assert len(result.questionnaire.questions) == 1
    assert result.questionnaire.questions[0].question_text == ("Do you review privileged access?")


def test_import_supports_questionnaire_question_header() -> None:
    data = make_workbook_bytes(
        [
            (
                "Controls",
                [
                    ["Questionnaire Question"],
                    ["Are backups encrypted?"],
                ],
            )
        ]
    )

    result = import_xlsx(
        data=data,
        filename="controls.xlsx",
    )

    assert result.questionnaire.questions[0].question_text == "Are backups encrypted?"


def test_blank_rows_are_ignored() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    [None],
                    ["   "],
                    ["Are logs retained?"],
                    [None],
                ],
            )
        ]
    )

    result = import_xlsx(
        data=data,
        filename="security.xlsx",
    )

    assert len(result.questionnaire.questions) == 1
    assert result.questionnaire.questions[0].source_row == 4


def test_invalid_explicit_source_id_is_an_xlsx_import_error() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question ID", "Question"],
                    ["Q" * 256, "Question one"],
                ],
            )
        ]
    )

    with pytest.raises(InvalidSourceQuestionIdError) as error:
        import_xlsx(data=data, filename="security.xlsx")

    assert isinstance(error.value, XlsxImportError)


def test_duplicate_questions_remain_distinguishable_by_fallback_occurrence() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["Do you encrypt data at rest?"],
                    ["Do you encrypt data at rest?"],
                ],
            )
        ]
    )

    result = import_xlsx(
        data=data,
        filename="security.xlsx",
    )

    first, second = result.questionnaire.questions

    assert first.question_text == second.question_text
    assert first.source_row != second.source_row
    assert first.question_id != second.question_id


def test_question_ids_are_deterministic_across_imports() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Section", "Question"],
                    ["Encryption", "Is data encrypted at rest?"],
                    ["Encryption", "Is data encrypted in transit?"],
                ],
            )
        ]
    )

    first = import_xlsx(
        data=data,
        filename="security.xlsx",
    )

    second = import_xlsx(
        data=data,
        filename="security.xlsx",
    )

    first_ids = tuple(question.question_id for question in first.questionnaire.questions)

    second_ids = tuple(question.question_id for question in second.questionnaire.questions)

    assert first_ids == second_ids


def test_multiple_sheets_are_supported() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["Do you have MFA?"],
                ],
            ),
            (
                "Privacy",
                [
                    ["Question"],
                    ["Do you have a privacy policy?"],
                ],
            ),
        ]
    )

    result = import_xlsx(
        data=data,
        filename="combined.xlsx",
    )

    assert len(result.questionnaire.questions) == 2

    assert [sheet.sheet_name for sheet in result.imported_sheets] == ["Security", "Privacy"]


def test_sheet_without_question_header_is_reported_as_ignored() -> None:
    data = make_workbook_bytes(
        [
            (
                "Instructions",
                [
                    ["This sheet contains instructions only"],
                ],
            ),
            (
                "Security",
                [
                    ["Question"],
                    ["Do you use MFA?"],
                ],
            ),
        ]
    )

    result = import_xlsx(
        data=data,
        filename="security.xlsx",
    )

    assert len(result.questionnaire.questions) == 1
    assert len(result.ignored_sheets) == 1
    assert result.ignored_sheets[0].sheet_name == "Instructions"
    assert result.ignored_sheets[0].reason == ("no supported question header found")


def test_no_question_header_fails() -> None:
    data = make_workbook_bytes(
        [
            (
                "Instructions",
                [
                    ["Name", "Value"],
                    ["Owner", "Security Team"],
                ],
            )
        ]
    )

    with pytest.raises(NoQuestionColumnError):
        import_xlsx(
            data=data,
            filename="invalid.xlsx",
        )


def test_ambiguous_question_headers_fail() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question", "Questions"],
                    ["Do you have MFA?", "Do you have MFA?"],
                ],
            )
        ]
    )

    with pytest.raises(AmbiguousQuestionColumnError):
        import_xlsx(
            data=data,
            filename="ambiguous.xlsx",
        )


def test_formula_question_is_rejected() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["=SUM(A1:A2)"],
                ],
            )
        ]
    )

    with pytest.raises(InvalidQuestionValueError):
        import_xlsx(
            data=data,
            filename="formula.xlsx",
        )


def test_unsupported_extension_is_rejected() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["Do you use MFA?"],
                ],
            )
        ]
    )

    with pytest.raises(UnsupportedXlsxExtensionError):
        import_xlsx(
            data=data,
            filename="security.xls",
        )


def test_oversized_workbook_is_rejected() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["Do you use MFA?"],
                ],
            )
        ]
    )

    policy = XlsxImportPolicy(max_file_size_bytes=len(data) - 1)

    with pytest.raises(XlsxFileTooLargeError):
        import_xlsx(
            data=data,
            filename="security.xlsx",
            policy=policy,
        )


def test_too_many_sheets_are_rejected() -> None:
    data = make_workbook_bytes(
        [
            ("Security", [["Question"], ["Do you use MFA?"]]),
            ("Privacy", [["Question"], ["Do you have a privacy policy?"]]),
        ]
    )

    policy = XlsxImportPolicy(max_sheets=1)

    with pytest.raises(TooManySheetsError):
        import_xlsx(
            data=data,
            filename="security.xlsx",
            policy=policy,
        )


def test_too_many_questions_are_rejected() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["Question one"],
                    ["Question two"],
                    ["Question three"],
                ],
            )
        ]
    )

    policy = XlsxImportPolicy(max_questions=2)

    with pytest.raises(TooManyQuestionsError):
        import_xlsx(
            data=data,
            filename="security.xlsx",
            policy=policy,
        )


def test_corrupt_xlsx_is_rejected() -> None:
    with pytest.raises(InvalidXlsxWorkbookError):
        import_xlsx(
            data=b"this is not an xlsx workbook",
            filename="corrupt.xlsx",
        )


def test_unicode_question_normalization_is_preserved_deterministically() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    ["  Do   you have\u00a0MFA?  "],
                ],
            )
        ]
    )

    result = import_xlsx(
        data=data,
        filename="security.xlsx",
    )

    assert result.questionnaire.questions[0].question_text == "Do you have MFA?"


def test_empty_questionnaire_rows_fail_after_valid_header() -> None:
    data = make_workbook_bytes(
        [
            (
                "Security",
                [
                    ["Question"],
                    [None],
                    ["   "],
                ],
            )
        ]
    )

    with pytest.raises(NoQuestionColumnError):
        import_xlsx(
            data=data,
            filename="empty.xlsx",
        )
