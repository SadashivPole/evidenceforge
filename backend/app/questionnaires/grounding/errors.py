"""Errors raised by deterministic questionnaire evidence grounding."""

from __future__ import annotations


class GroundingError(RuntimeError):
    """Base error for deterministic questionnaire grounding."""


class GroundingQuestionNotFoundError(GroundingError):
    """Raised when a question is not in the requested workspace and version."""


class GroundingQueryValidationError(GroundingError):
    """Raised when the persisted question cannot form a valid search query."""
