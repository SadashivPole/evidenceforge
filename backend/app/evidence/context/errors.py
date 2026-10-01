"""Exception hierarchy for evidence context budgeting and window selection."""

from __future__ import annotations


class ContextSelectionError(Exception):
    """Base exception for all context budgeting and selection errors."""


class ContextSelectionAuthorizationError(ContextSelectionError):
    """Raised when context selection is requested without a valid workspace identity."""


class ContextSelectionWorkspaceMismatchError(ContextSelectionError):
    """Raised when an evidence candidate belongs to an unauthorized workspace."""


class ContextSelectionConfigurationError(ContextSelectionError):
    """Raised when context selection configuration violates approved production bounds."""
