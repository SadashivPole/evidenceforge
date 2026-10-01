"""Deterministic evidence embedding generation and backfill primitives."""

from app.evidence.embeddings.config import (
    DEFAULT_EMBEDDING_CONFIG,
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_VERSION,
    EmbeddingConfig,
)
from app.evidence.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingContentHashMismatchError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingGenerationError,
    EmbeddingModelUnavailableError,
    EmbeddingPersistenceError,
)
from app.evidence.embeddings.service import (
    EmbeddingGenerator,
    backfill_workspace,
    list_workspace_embedding_targets,
    load_embedding_model,
    persist_embedding,
)
from app.evidence.embeddings.types import (
    BackfillReport,
    EmbeddingFailure,
    EmbeddingPersistenceStatus,
    EmbeddingTarget,
)

__all__ = [
    "BackfillReport",
    "DEFAULT_EMBEDDING_CONFIG",
    "DEFAULT_MODEL_ID",
    "DEFAULT_MODEL_VERSION",
    "EmbeddingConfig",
    "EmbeddingFailure",
    "EmbeddingConfigurationError",
    "EmbeddingContentHashMismatchError",
    "EmbeddingDimensionError",
    "EmbeddingError",
    "EmbeddingGenerationError",
    "EmbeddingGenerator",
    "EmbeddingModelUnavailableError",
    "EmbeddingPersistenceError",
    "EmbeddingPersistenceStatus",
    "EmbeddingTarget",
    "backfill_workspace",
    "list_workspace_embedding_targets",
    "load_embedding_model",
    "persist_embedding",
]
