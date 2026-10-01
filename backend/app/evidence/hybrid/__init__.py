"""Public interface for production hybrid retrieval with RRF."""

from app.evidence.hybrid.config import (
    APPROVED_RRF_K,
    DEFAULT_HYBRID_FINAL_TOP_K,
    DEFAULT_HYBRID_LEXICAL_TOP_K,
    DEFAULT_HYBRID_RETRIEVAL_CONFIG,
    DEFAULT_HYBRID_SEMANTIC_TOP_K,
    DEFAULT_RRF_K,
    HYBRID_SEARCH_VERSION,
    MAX_APPROVED_FINAL_TOP_K,
    MAX_APPROVED_LEXICAL_TOP_K,
    MAX_APPROVED_SEMANTIC_TOP_K,
    HybridRetrievalConfig,
)
from app.evidence.hybrid.errors import (
    HybridAuthorizationError,
    HybridConfigurationError,
    HybridQueryValidationError,
    HybridRetrievalError,
    HybridWorkspaceMismatchError,
)
from app.evidence.hybrid.rrf import (
    fuse_hybrid_results,
    reciprocal_rank_score,
)
from app.evidence.hybrid.service import HybridRetriever
from app.evidence.hybrid.types import (
    HybridRetrievalReport,
    HybridSearchResult,
)

__all__ = [
    "APPROVED_RRF_K",
    "DEFAULT_HYBRID_FINAL_TOP_K",
    "DEFAULT_HYBRID_LEXICAL_TOP_K",
    "DEFAULT_HYBRID_RETRIEVAL_CONFIG",
    "DEFAULT_HYBRID_SEMANTIC_TOP_K",
    "DEFAULT_RRF_K",
    "HYBRID_SEARCH_VERSION",
    "HybridAuthorizationError",
    "HybridConfigurationError",
    "HybridQueryValidationError",
    "HybridRetrievalConfig",
    "HybridRetrievalError",
    "HybridRetrievalReport",
    "HybridRetriever",
    "HybridSearchResult",
    "HybridWorkspaceMismatchError",
    "MAX_APPROVED_FINAL_TOP_K",
    "MAX_APPROVED_LEXICAL_TOP_K",
    "MAX_APPROVED_SEMANTIC_TOP_K",
    "fuse_hybrid_results",
    "reciprocal_rank_score",
]
