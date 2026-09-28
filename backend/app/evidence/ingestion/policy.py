"""Policy definitions for EvidenceForge evidence ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_RAW_BYTES: Final[int] = 5 * 1024 * 1024
MAX_FILENAME_LENGTH: Final[int] = 255
NORMALIZATION_VERSION: Final[str] = "text-v1"

SUPPORTED_EXTENSION_MEDIA_TYPES: Final[dict[str, str]] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
}


@dataclass(frozen=True, slots=True)
class IngestionPolicy:
    """Immutable policy controlling deterministic text ingestion."""

    max_raw_bytes: int = MAX_RAW_BYTES
    max_filename_length: int = MAX_FILENAME_LENGTH
    normalization_version: str = NORMALIZATION_VERSION


DEFAULT_POLICY: Final[IngestionPolicy] = IngestionPolicy()
