"""Explicit errors for deterministic embedding generation and backfill."""

from __future__ import annotations


class EmbeddingError(RuntimeError):
    """Base error for the embedding lifecycle."""


class EmbeddingConfigurationError(EmbeddingError):
    """The configured model/runtime contract cannot be used safely."""


class EmbeddingModelUnavailableError(EmbeddingError):
    """The local embedding model could not be loaded or used."""


class EmbeddingContentHashMismatchError(EmbeddingError):
    """Stored chunk content does not match its persisted content hash."""


class EmbeddingDimensionError(EmbeddingError):
    """Generated or configured vector dimension is not 384."""


class EmbeddingGenerationError(EmbeddingError):
    """The encoder failed or returned invalid embedding values."""


class EmbeddingPersistenceError(EmbeddingError):
    """A generated embedding could not be persisted safely."""
