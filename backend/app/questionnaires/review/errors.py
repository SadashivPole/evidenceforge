"""Exception hierarchy for questionnaire review workflow operations."""

from __future__ import annotations


class ReviewError(Exception):
    """Base exception for all human review workflow errors."""


class ReviewAuthorizationError(ReviewError):
    """Raised when an actor lacks the required workspace role to review or approve a response."""


class ReviewValidationError(ReviewError):
    """Raised when a review decision payload violates business or schema constraints."""


class ReviewCitationValidationError(ReviewError):
    """Raised when a review decision cites invalid, unselected, or cross-workspace evidence."""


class ReviewWorkspaceMismatchError(ReviewError):
    """Raised when question, review context, or workspace identifiers do not match."""


class ReviewQuestionNotFoundError(ReviewError):
    """Raised when a questionnaire version question cannot be found."""
