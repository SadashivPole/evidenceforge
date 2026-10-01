"""Frozen configuration for production hybrid retrieval with RRF."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.evidence.search.policy import (
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SEARCH_POLICY,
    MAX_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
    SearchPolicy,
)
from app.evidence.semantic.config import (
    DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    SemanticRetrievalConfig,
)

DEFAULT_RRF_K: Final[int] = 60
APPROVED_RRF_K: Final[int] = 60
DEFAULT_HYBRID_LEXICAL_TOP_K: Final[int] = DEFAULT_SEARCH_LIMIT
DEFAULT_HYBRID_SEMANTIC_TOP_K: Final[int] = DEFAULT_SEARCH_LIMIT
DEFAULT_HYBRID_FINAL_TOP_K: Final[int] = DEFAULT_SEARCH_LIMIT
MAX_APPROVED_LEXICAL_TOP_K: Final[int] = 10
MAX_APPROVED_SEMANTIC_TOP_K: Final[int] = 10
MAX_APPROVED_FINAL_TOP_K: Final[int] = 10
HYBRID_SEARCH_VERSION: Final[str] = "hybrid-rrf-v1"


@dataclass(frozen=True, slots=True)
class HybridRetrievalConfig:
    """Explicit, bounded configuration for hybrid lexical + semantic RRF retrieval.

    Enforces the approved production bounds:
    lexical_top_k <= 10
    semantic_top_k <= 10
    rrf_k = 60 (frozen exactly to 60)
    final_top_k <= 10
    candidate union <= 20
    """

    lexical_top_k: int = DEFAULT_HYBRID_LEXICAL_TOP_K
    semantic_top_k: int = DEFAULT_HYBRID_SEMANTIC_TOP_K
    rrf_k: int = DEFAULT_RRF_K
    final_top_k: int = DEFAULT_HYBRID_FINAL_TOP_K
    max_final_top_k: int = MAX_APPROVED_FINAL_TOP_K
    min_query_length: int = MIN_QUERY_LENGTH
    max_query_length: int = MAX_QUERY_LENGTH
    search_version: str = HYBRID_SEARCH_VERSION
    search_policy: SearchPolicy = DEFAULT_SEARCH_POLICY
    semantic_config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG
    enable_semantic_fallback: bool = True

    def __post_init__(self) -> None:
        if self.lexical_top_k < 1:
            raise ValueError("lexical_top_k must be at least 1")
        if self.lexical_top_k > MAX_APPROVED_LEXICAL_TOP_K:
            raise ValueError(
                f"lexical_top_k must not exceed approved bound of {MAX_APPROVED_LEXICAL_TOP_K}"
            )
        if self.semantic_top_k < 1:
            raise ValueError("semantic_top_k must be at least 1")
        if self.semantic_top_k > MAX_APPROVED_SEMANTIC_TOP_K:
            raise ValueError(
                f"semantic_top_k must not exceed approved bound of {MAX_APPROVED_SEMANTIC_TOP_K}"
            )
        if self.rrf_k != APPROVED_RRF_K:
            raise ValueError(f"rrf_k must be exactly {APPROVED_RRF_K}")
        if self.final_top_k < 1:
            raise ValueError("final_top_k must be at least 1")
        if self.final_top_k > MAX_APPROVED_FINAL_TOP_K:
            raise ValueError(
                f"final_top_k must not exceed approved bound of {MAX_APPROVED_FINAL_TOP_K}"
            )
        if self.max_final_top_k > MAX_APPROVED_FINAL_TOP_K:
            raise ValueError(
                f"max_final_top_k must not exceed approved bound of {MAX_APPROVED_FINAL_TOP_K}"
            )
        if self.min_query_length < 1:
            raise ValueError("min_query_length must be at least 1")
        if self.max_query_length < self.min_query_length:
            raise ValueError("max_query_length must be greater than or equal to min_query_length")


DEFAULT_HYBRID_RETRIEVAL_CONFIG: Final[HybridRetrievalConfig] = HybridRetrievalConfig()
