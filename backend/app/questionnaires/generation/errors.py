"""Exception hierarchy for generation boundary validation and citation resolution."""

from __future__ import annotations


class GenerationBoundaryError(Exception):
    """Base exception for all generation boundary validation errors."""


class GenerationWorkspaceMismatchError(GenerationBoundaryError):
    """Raised when generation context or evidence violates workspace boundaries."""


class GenerationCitationValidationError(GenerationBoundaryError):
    """Raised when an untrusted model output cites unknown, unselected, or invalid handles."""


class GenerationStatusValidationError(GenerationBoundaryError):
    """Raised when a model proposes an invalid, forbidden, or unsupported response status."""


class GenerationOutputLengthError(GenerationBoundaryError):
    """Raised when model output exceeds configured character or count limits."""


class GenerationPayloadMalformedError(GenerationBoundaryError):
    """Raised when model output fails structural or schema validation."""


class GenerationProviderUnavailableError(GenerationBoundaryError):
    """Raised when a configured generation provider cannot be reached (timeout, connection, 5xx)."""


class GenerationResponseTooLargeError(GenerationBoundaryError):
    """Raised when the provider responds with a body exceeding the enforced byte ceiling.

    Non-retriable by construction: it is not a connectivity error, so the
    caller's retry loop never applies it and the payload is never parsed.
    """
