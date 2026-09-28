"""Errors raised by the EvidenceForge XLSX questionnaire importer."""

from __future__ import annotations


class XlsxImportError(ValueError):
    """Base error for deterministic XLSX questionnaire import."""


class UnsupportedXlsxExtensionError(XlsxImportError):
    """Raised when the supplied filename is not an XLSX file."""


class XlsxFileTooLargeError(XlsxImportError):
    """Raised when the workbook exceeds the configured byte limit."""


class InvalidXlsxWorkbookError(XlsxImportError):
    """Raised when the XLSX payload cannot be parsed as a workbook."""


class TooManySheetsError(XlsxImportError):
    """Raised when the workbook exceeds the configured sheet limit."""


class NoQuestionColumnError(XlsxImportError):
    """Raised when no worksheet contains a supported question header."""


class AmbiguousQuestionColumnError(XlsxImportError):
    """Raised when a worksheet contains multiple supported question headers."""


class TooManyQuestionsError(XlsxImportError):
    """Raised when the workbook contains more questions than permitted."""


class InvalidQuestionValueError(XlsxImportError):
    """Raised when an extracted question contains an unsupported value."""