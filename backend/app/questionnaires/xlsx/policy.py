"""Policy for deterministic XLSX questionnaire import."""

from __future__ import annotations

from dataclasses import dataclass

from app.questionnaires.policy import XLSX_IMPORT_VERSION

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_SHEETS = 50
MAX_QUESTIONS = 5000
MAX_HEADER_SCAN_ROWS = 20

QUESTION_HEADERS = frozenset(
    {
        "question",
        "questions",
        "question text",
        "questionnaire question",
    }
)

# Deliberately small and explicit. Generic "id" and "number" headers are not
# accepted because they are too ambiguous for deterministic identity.
SOURCE_QUESTION_ID_HEADERS = frozenset(
    {
        "question id",
        "question_id",
        "question identifier",
    }
)

SECTION_HEADERS = frozenset(
    {
        "section",
        "category",
        "domain",
        "control family",
    }
)


@dataclass(frozen=True, slots=True)
class XlsxImportPolicy:
    """Immutable limits and header policy for XLSX imports."""

    max_file_size_bytes: int = MAX_FILE_SIZE_BYTES
    max_sheets: int = MAX_SHEETS
    max_questions: int = MAX_QUESTIONS
    max_header_scan_rows: int = MAX_HEADER_SCAN_ROWS


__all__ = [
    "QUESTION_HEADERS",
    "SECTION_HEADERS",
    "SOURCE_QUESTION_ID_HEADERS",
    "XLSX_IMPORT_VERSION",
    "XlsxImportPolicy",
]
