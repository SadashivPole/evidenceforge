"""Public interface for deterministic EvidenceForge chunking."""

from .policy import (
    DEFAULT_CHUNKING_POLICY,
    ChunkingPolicy,
)
from .service import chunk_ingestion_result, chunk_text
from .types import ChunkResult

__all__ = [
    "ChunkResult",
    "ChunkingPolicy",
    "DEFAULT_CHUNKING_POLICY",
    "chunk_ingestion_result",
    "chunk_text",
]
