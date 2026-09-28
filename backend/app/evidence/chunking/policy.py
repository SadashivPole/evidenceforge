"""Policy definitions for deterministic EvidenceForge chunking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

CHUNKING_VERSION: Final[str] = "text-chunk-v1"

CHUNK_TARGET_BYTES: Final[int] = 4096
CHUNK_OVERLAP_BYTES: Final[int] = 512
MINIMUM_PREFERRED_BREAK_BYTES: Final[int] = 1024


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    """Immutable policy controlling deterministic text chunking."""

    version: str = CHUNKING_VERSION
    target_bytes: int = CHUNK_TARGET_BYTES
    overlap_bytes: int = CHUNK_OVERLAP_BYTES
    minimum_preferred_break_bytes: int = MINIMUM_PREFERRED_BREAK_BYTES


DEFAULT_CHUNKING_POLICY: Final[ChunkingPolicy] = ChunkingPolicy()
