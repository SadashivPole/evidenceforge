"""Explicit errors for workspace-authorized semantic retrieval."""

from __future__ import annotations


class SemanticRetrievalError(RuntimeError):
    """Base error for all semantic retrieval failures."""


class SemanticConfigurationError(SemanticRetrievalError):
    """Configuration mismatch or incompatible model/runtime contract."""


class SemanticModelUnavailableError(SemanticRetrievalError):
    """Embedding model is unavailable or could not be loaded."""


class SemanticQueryValidationError(SemanticRetrievalError):
    """Query text violates input length, type, or formatting policy."""


class QueryEmbeddingError(SemanticRetrievalError):
    """Query embedding generation failed or returned invalid vector output."""


class SemanticDimensionMismatchError(QueryEmbeddingError):
    """Query or candidate vector dimension is not the expected 384 dimensions."""


class InvalidQueryVectorError(QueryEmbeddingError):
    """Query vector contains non-finite values or is not normalized."""


class SemanticDatabaseError(SemanticRetrievalError):
    """Database vector search query failed."""


class SemanticWorkspaceMismatchError(SemanticRetrievalError):
    """Security violation: candidate from an unauthorized workspace was detected."""


class SemanticAuthorizationError(SemanticRetrievalError):
    """Missing, invalid, or unauthorized workspace scope."""
