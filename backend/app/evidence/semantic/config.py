"""Frozen configuration for workspace-authorized semantic retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.evidence.embeddings.config import (
    DEFAULT_EMBEDDING_CONFIG,
    EXPECTED_EMBEDDING_DIMENSION,
    EmbeddingConfig,
)
from app.evidence.search.policy import (
    DEFAULT_SEARCH_LIMIT,
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    MIN_QUERY_LENGTH,
)

DEFAULT_SEMANTIC_TOP_K: Final[int] = DEFAULT_SEARCH_LIMIT
DEFAULT_SEMANTIC_MAX_TOP_K: Final[int] = MAX_SEARCH_LIMIT


@dataclass(frozen=True, slots=True)
class SemanticRetrievalConfig:
    """Explicit, bounded configuration for authorized semantic retrieval."""

    top_k: int = DEFAULT_SEMANTIC_TOP_K
    min_query_length: int = MIN_QUERY_LENGTH
    max_query_length: int = MAX_QUERY_LENGTH
    max_top_k: int = DEFAULT_SEMANTIC_MAX_TOP_K
    similarity_metric: str = "cosine"
    embedding_dimension: int = EXPECTED_EMBEDDING_DIMENSION
    embedding_config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValueError("semantic top_k must be at least 1")
        if self.top_k > self.max_top_k:
            raise ValueError(f"semantic top_k must not exceed {self.max_top_k}")
        if self.max_top_k < 1:
            raise ValueError("max_top_k must be at least 1")
        if self.min_query_length < 1:
            raise ValueError("min_query_length must be at least 1")
        if self.max_query_length < self.min_query_length:
            raise ValueError("max_query_length must be greater than or equal to min_query_length")
        if self.similarity_metric != "cosine":
            raise ValueError("The semantic retrieval layer supports cosine similarity only")
        if self.embedding_dimension != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"The semantic retrieval layer requires {EXPECTED_EMBEDDING_DIMENSION} dimensions"
            )
        if self.embedding_config.embedding_dimension != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"Embedding configuration dimension {self.embedding_config.embedding_dimension} "
                f"does not match expected dimension {EXPECTED_EMBEDDING_DIMENSION}"
            )
        if self.embedding_config.similarity != "cosine":
            raise ValueError("Embedding configuration must use cosine similarity")

    @property
    def model_id(self) -> str:
        return self.embedding_config.model_id

    @property
    def model_version(self) -> str:
        return self.embedding_config.model_version

    @property
    def configuration_hash(self) -> str:
        return self.embedding_config.configuration_hash


DEFAULT_SEMANTIC_RETRIEVAL_CONFIG: Final[SemanticRetrievalConfig] = SemanticRetrievalConfig()
