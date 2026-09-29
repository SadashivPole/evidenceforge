"""Errors raised by questionnaire response persistence."""

from __future__ import annotations


class ResponsePersistenceError(RuntimeError):
    """Base error for questionnaire response persistence failures."""


class ResponseValidationError(ResponsePersistenceError):
    """Raised when a response or its citations fail domain validation."""


class ResponseAuthorizationError(ResponsePersistenceError):
    """Raised when the actor cannot perform the requested response operation."""


class ResponseNotFoundError(ResponsePersistenceError):
    """Raised when a response is not visible in the requested workspace."""


class QuestionnaireVersionQuestionNotFoundError(ResponsePersistenceError):
    """Raised when a version-specific question is not visible in the workspace."""


class EvidenceChunkCitationNotFoundError(ResponseValidationError):
    """Raised when a requested evidence chunk is not in the same workspace."""


class ResponseIntegrityError(ResponsePersistenceError):
    """Raised when the database rejects a response mutation."""
