"""Immutable input and output types for evidence ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SupportedExtension = Literal[".txt", ".md"]
CanonicalMediaType = Literal["text/plain", "text/markdown"]
NormalizationVersion = Literal["text-v1"]


@dataclass(frozen=True, slots=True)
class IngestionInput:
    """Untrusted metadata and already-materialized file bytes."""

    original_filename: str
    media_type: str | None
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Canonicalized result of a successful evidence ingestion."""

    original_filename: str
    extension: SupportedExtension
    media_type: CanonicalMediaType

    raw_size_bytes: int
    normalized_size_bytes: int

    raw_sha256: str
    normalized_sha256: str

    normalization_version: NormalizationVersion
    normalized_text: str
