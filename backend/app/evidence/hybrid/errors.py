"""Explicit errors for production hybrid retrieval and RRF fusion."""

from __future__ import annotations


class HybridRetrievalError(RuntimeError):
    """Base error for all hybrid retrieval failures."""


class HybridQueryValidationError(HybridRetrievalError):
    """Query text violates input length, type, or formatting policy."""


class HybridAuthorizationError(HybridRetrievalError):
    """Missing, invalid, or unauthorized workspace scope."""


class HybridWorkspaceMismatchError(HybridRetrievalError):
    """Security violation: candidate from an unauthorized workspace was detected."""


class HybridConfigurationError(HybridRetrievalError):
    """Configuration mismatch or incompatible hybrid retrieval parameters."""
