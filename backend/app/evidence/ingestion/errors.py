"""Exceptions for the EvidenceForge evidence-ingestion service."""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all expected ingestion failures."""


class IngestionValidationError(IngestionError):
    """Input failed a deterministic ingestion validation rule."""


class InvalidInputTypeError(IngestionValidationError):
    """Input metadata or raw content has an invalid runtime type."""


class InvalidFilenameError(IngestionValidationError):
    """Filename metadata is missing, malformed, or exceeds policy limits."""


class UnsupportedExtensionError(IngestionValidationError):
    """The final filename extension is not supported."""

    def __init__(self, extension: str | None) -> None:
        self.extension = extension
        super().__init__("Unsupported file extension")


class UnsupportedMediaTypeError(IngestionValidationError):
    """The declared media type is missing, malformed, unsupported, or mismatched."""

    def __init__(
        self,
        media_type: str | None,
        extension: str | None,
    ) -> None:
        self.media_type = media_type
        self.extension = extension
        super().__init__("Unsupported or mismatched media type")


class FileTooLargeError(IngestionValidationError):
    """Raw input exceeds the configured byte limit."""

    def __init__(
        self,
        actual_bytes: int,
        maximum_bytes: int,
    ) -> None:
        self.actual_bytes = actual_bytes
        self.maximum_bytes = maximum_bytes
        super().__init__("File exceeds the maximum permitted size")


class InvalidUtf8Error(IngestionValidationError):
    """Raw bytes are not valid UTF-8."""

    def __init__(self, byte_offset: int | None = None) -> None:
        self.byte_offset = byte_offset
        super().__init__("File is not valid UTF-8")


class EmptyContentError(IngestionValidationError):
    """Decoded content is empty."""


class WhitespaceOnlyContentError(IngestionValidationError):
    """Decoded content contains no non-whitespace characters."""
