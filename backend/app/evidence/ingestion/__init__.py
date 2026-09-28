"""Public interface for EvidenceForge evidence ingestion."""

from .service import ingest
from .types import IngestionInput, IngestionResult

__all__ = [
    "IngestionInput",
    "IngestionResult",
    "ingest",
]
