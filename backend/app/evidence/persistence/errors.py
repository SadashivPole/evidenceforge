"""Errors raised by EvidenceForge persistence operations."""

from __future__ import annotations


class EvidencePersistenceError(Exception):
    """Base error for evidence persistence failures."""


class EvidenceDocumentNotFoundError(EvidencePersistenceError):
    """Raised when a document is not available in the requested workspace."""


class EvidencePersistenceValidationError(EvidencePersistenceError):
    """Raised when deterministic ingestion data fails persistence validation."""
