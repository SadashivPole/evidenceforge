"""Persistence errors for questionnaire imports."""

from __future__ import annotations


class QuestionnairePersistenceError(RuntimeError):
    """Base error for questionnaire persistence failures."""


class QuestionnairePersistenceValidationError(QuestionnairePersistenceError):
    """Raised when a deterministic import result fails integrity checks."""


class QuestionnaireNotFoundError(QuestionnairePersistenceError):
    """Raised when a workspace-scoped questionnaire cannot be resolved."""
