"""Database-backed evidence persistence services."""

from .errors import (
    EvidenceDocumentNotFoundError,
    EvidencePersistenceError,
    EvidencePersistenceValidationError,
)
from .ingestion_service import (
    IngestionOutcome,
    IngestionPersistenceResult,
    persist_ingestion,
)

__all__ = [
    "EvidenceDocumentNotFoundError",
    "EvidencePersistenceError",
    "EvidencePersistenceValidationError",
    "IngestionOutcome",
    "IngestionPersistenceResult",
    "persist_ingestion",
]
