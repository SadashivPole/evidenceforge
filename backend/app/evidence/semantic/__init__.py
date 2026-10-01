"""Workspace-authorized semantic retrieval primitives."""

from app.evidence.semantic.config import (
    DEFAULT_SEMANTIC_MAX_TOP_K,
    DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    DEFAULT_SEMANTIC_TOP_K,
    SemanticRetrievalConfig,
)
from app.evidence.semantic.errors import (
    InvalidQueryVectorError,
    QueryEmbeddingError,
    SemanticAuthorizationError,
    SemanticConfigurationError,
    SemanticDatabaseError,
    SemanticDimensionMismatchError,
    SemanticModelUnavailableError,
    SemanticQueryValidationError,
    SemanticRetrievalError,
    SemanticWorkspaceMismatchError,
)
from app.evidence.semantic.query import (
    generate_query_embedding,
    normalize_semantic_query,
    validate_query_vector,
)
from app.evidence.semantic.repository import query_semantic_candidates
from app.evidence.semantic.service import SemanticRetriever
from app.evidence.semantic.types import SemanticSearchResult

__all__ = [
    "DEFAULT_SEMANTIC_MAX_TOP_K",
    "DEFAULT_SEMANTIC_RETRIEVAL_CONFIG",
    "DEFAULT_SEMANTIC_TOP_K",
    "InvalidQueryVectorError",
    "QueryEmbeddingError",
    "SemanticAuthorizationError",
    "SemanticConfigurationError",
    "SemanticDatabaseError",
    "SemanticDimensionMismatchError",
    "SemanticModelUnavailableError",
    "SemanticQueryValidationError",
    "SemanticRetrievalConfig",
    "SemanticRetrievalError",
    "SemanticRetriever",
    "SemanticSearchResult",
    "SemanticWorkspaceMismatchError",
    "generate_query_embedding",
    "normalize_semantic_query",
    "query_semantic_candidates",
    "validate_query_vector",
]
