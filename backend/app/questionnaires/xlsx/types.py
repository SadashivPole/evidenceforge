"""Immutable types returned by the XLSX questionnaire importer."""

from __future__ import annotations

from dataclasses import dataclass

from app.questionnaires.types import Questionnaire


@dataclass(frozen=True, slots=True)
class ImportedSheet:
    """Audit metadata for a worksheet that produced questions."""

    sheet_name: str
    header_row: int
    question_header: str
    section_header: str | None
    question_count: int


@dataclass(frozen=True, slots=True)
class IgnoredSheet:
    """Audit metadata for a worksheet that did not produce questions."""

    sheet_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class XlsxImportResult:
    """Complete deterministic XLSX import result."""

    questionnaire: Questionnaire
    imported_sheets: tuple[ImportedSheet, ...]
    ignored_sheets: tuple[IgnoredSheet, ...]