"""Versioned policy for deterministic questionnaire imports."""

from __future__ import annotations

from dataclasses import dataclass

QUESTIONNAIRE_VERSION = "questionnaire-v1"
XLSX_IMPORT_VERSION = "xlsx-import-v1"
QUESTION_NORMALIZATION_VERSION = "question-normalization-v1"
QUESTION_IDENTITY_VERSION = "question-identity-v2"
QUESTIONNAIRE_HASH_VERSION = "questionnaire-hash-v1"

# Backward-compatible name retained for callers of the pre-1J-D policy.
QUESTION_ID_VERSION = QUESTION_IDENTITY_VERSION

SUPPORTED_EXTENSIONS = frozenset({".xlsx"})

MAX_QUESTIONNAIRE_SIZE_BYTES = 10 * 1024 * 1024
MAX_SHEETS = 50
MAX_QUESTIONS = 5000

MIN_QUESTION_LENGTH = 3
MAX_QUESTION_LENGTH = 4000

MAX_SECTION_LENGTH = 500
MAX_SHEET_NAME_LENGTH = 255
MAX_SOURCE_QUESTION_ID_LENGTH = 255


@dataclass(frozen=True, slots=True)
class QuestionnairePolicy:
    """Immutable questionnaire validation policy."""

    max_size_bytes: int = MAX_QUESTIONNAIRE_SIZE_BYTES
    max_sheets: int = MAX_SHEETS
    max_questions: int = MAX_QUESTIONS
    min_question_length: int = MIN_QUESTION_LENGTH
    max_question_length: int = MAX_QUESTION_LENGTH
    max_section_length: int = MAX_SECTION_LENGTH
    max_sheet_name_length: int = MAX_SHEET_NAME_LENGTH
