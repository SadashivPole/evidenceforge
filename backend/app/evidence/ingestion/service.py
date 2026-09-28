"""Pure deterministic evidence-ingestion service."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import cast

from app.evidence.ingestion.errors import (
    FileTooLargeError,
    InvalidFilenameError,
    InvalidInputTypeError,
    UnsupportedExtensionError,
    UnsupportedMediaTypeError,
)
from app.evidence.ingestion.normalization import decode_and_normalize_text
from app.evidence.ingestion.policy import (
    DEFAULT_POLICY,
    SUPPORTED_EXTENSION_MEDIA_TYPES,
    IngestionPolicy,
)
from app.evidence.ingestion.types import (
    CanonicalMediaType,
    IngestionInput,
    IngestionResult,
    NormalizationVersion,
    SupportedExtension,
)

_MEDIA_TYPE_TOKEN_PATTERN = re.compile(
    r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+/"
    r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+$"
)


def ingest(
    input_data: IngestionInput,
    *,
    policy: IngestionPolicy = DEFAULT_POLICY,
) -> IngestionResult:
    """Validate, normalize, and hash one evidence file.

    This function is intentionally side-effect-free. It does not perform
    filesystem access, database access, network access, authorization,
    auditing, or API response mapping.
    """
    _validate_input_types(input_data)

    extension = _extract_extension(
        input_data.original_filename,
        policy=policy,
    )

    _validate_media_type(
        input_data.media_type,
        extension,
    )

    raw_size_bytes = len(input_data.raw_bytes)

    if raw_size_bytes > policy.max_raw_bytes:
        raise FileTooLargeError(
            actual_bytes=raw_size_bytes,
            maximum_bytes=policy.max_raw_bytes,
        )

    normalized_text = decode_and_normalize_text(input_data.raw_bytes)

    raw_sha256 = hashlib.sha256(input_data.raw_bytes).hexdigest()

    normalized_bytes = normalized_text.encode("utf-8")
    normalized_sha256 = hashlib.sha256(normalized_bytes).hexdigest()

    return IngestionResult(
        original_filename=input_data.original_filename,
        extension=cast(SupportedExtension, extension),
        media_type=cast(
            CanonicalMediaType,
            SUPPORTED_EXTENSION_MEDIA_TYPES[extension],
        ),
        raw_size_bytes=raw_size_bytes,
        normalized_size_bytes=len(normalized_bytes),
        raw_sha256=raw_sha256,
        normalized_sha256=normalized_sha256,
        normalization_version=cast(
            NormalizationVersion,
            policy.normalization_version,
        ),
        normalized_text=normalized_text,
    )


def _validate_input_types(input_data: IngestionInput) -> None:
    """Reject malformed runtime input before accessing its fields."""
    if not isinstance(input_data, IngestionInput):
        raise InvalidInputTypeError()

    if not isinstance(input_data.original_filename, str):
        raise InvalidInputTypeError()

    if input_data.media_type is not None and not isinstance(
        input_data.media_type,
        str,
    ):
        raise InvalidInputTypeError()

    if not isinstance(input_data.raw_bytes, bytes):
        raise InvalidInputTypeError()


def _validate_filename(
    filename: str,
    *,
    maximum_length: int,
) -> None:
    """Validate filename metadata without treating it as a filesystem path."""
    if not filename:
        raise InvalidFilenameError()

    if len(filename) > maximum_length:
        raise InvalidFilenameError()

    for character in filename:
        if unicodedata.category(character) == "Cc":
            raise InvalidFilenameError()


def _extract_extension(
    filename: str,
    *,
    policy: IngestionPolicy,
) -> str:
    """Extract a supported extension from the final filename segment."""
    _validate_filename(
        filename,
        maximum_length=policy.max_filename_length,
    )

    final_segment = re.split(r"[/\\]", filename)[-1]

    if not final_segment:
        raise InvalidFilenameError()

    if final_segment.startswith(".") and final_segment.count(".") == 1:
        raise InvalidFilenameError()

    if "." not in final_segment:
        raise UnsupportedExtensionError(None)

    extension = f".{final_segment.rsplit('.', 1)[1].lower()}"

    if extension not in SUPPORTED_EXTENSION_MEDIA_TYPES:
        raise UnsupportedExtensionError(extension)

    return extension


def _validate_media_type(
    media_type: str | None,
    extension: str,
) -> None:
    """Validate and enforce the extension/media-type policy."""
    if media_type is None:
        raise UnsupportedMediaTypeError(
            media_type=None,
            extension=extension,
        )

    canonical_media_type = _parse_media_type(
        media_type,
        extension=extension,
    )

    expected_media_type = SUPPORTED_EXTENSION_MEDIA_TYPES[extension]

    if canonical_media_type != expected_media_type:
        raise UnsupportedMediaTypeError(
            media_type=canonical_media_type,
            extension=extension,
        )


def _parse_media_type(
    media_type: str,
    *,
    extension: str,
) -> str:
    """Parse and canonicalize a restricted media-type value."""
    value = media_type.strip()

    if not value:
        raise UnsupportedMediaTypeError(
            media_type=media_type,
            extension=extension,
        )

    parts = _split_media_type(
        value,
        original_media_type=media_type,
        extension=extension,
    )

    if not parts:
        raise UnsupportedMediaTypeError(
            media_type=media_type,
            extension=extension,
        )

    base_type = parts[0].strip().lower()

    if not _MEDIA_TYPE_TOKEN_PATTERN.fullmatch(base_type):
        raise UnsupportedMediaTypeError(
            media_type=media_type,
            extension=extension,
        )

    parameters: dict[str, str] = {}

    for parameter in parts[1:]:
        if "=" not in parameter:
            raise UnsupportedMediaTypeError(
                media_type=media_type,
                extension=extension,
            )

        name, parameter_value = parameter.split("=", 1)
        name = name.strip().lower()
        parameter_value = parameter_value.strip()

        if not name or name in parameters:
            raise UnsupportedMediaTypeError(
                media_type=media_type,
                extension=extension,
            )

        if name != "charset":
            raise UnsupportedMediaTypeError(
                media_type=media_type,
                extension=extension,
            )

        parameters[name] = _parse_parameter_value(
            parameter_value,
            media_type=media_type,
            extension=extension,
        )

    charset = parameters.get("charset")

    if charset is not None and charset.lower() != "utf-8":
        raise UnsupportedMediaTypeError(
            media_type=media_type,
            extension=extension,
        )

    return base_type


def _split_media_type(
    value: str,
    *,
    original_media_type: str,
    extension: str,
) -> list[str]:
    """Split media-type parameters while respecting quoted values."""
    parts: list[str] = []
    current: list[str] = []

    in_quotes = False
    escaped = False

    for character in value:
        if escaped:
            current.append(character)
            escaped = False
            continue

        if in_quotes and character == "\\":
            current.append(character)
            escaped = True
            continue

        if character == '"':
            current.append(character)
            in_quotes = not in_quotes
            continue

        if character == ";" and not in_quotes:
            parts.append("".join(current).strip())
            current = []
            continue

        current.append(character)

    if in_quotes or escaped:
        raise UnsupportedMediaTypeError(
            media_type=original_media_type,
            extension=extension,
        )

    parts.append("".join(current).strip())

    return parts


def _parse_parameter_value(
    value: str,
    *,
    media_type: str,
    extension: str,
) -> str:
    """Parse a media-type parameter value."""
    if not value:
        raise UnsupportedMediaTypeError(
            media_type=media_type,
            extension=extension,
        )

    if value.startswith('"'):
        if not value.endswith('"') or len(value) < 2:
            raise UnsupportedMediaTypeError(
                media_type=media_type,
                extension=extension,
            )

        inner = value[1:-1]

        if any(unicodedata.category(character) == "Cc" for character in inner):
            raise UnsupportedMediaTypeError(
                media_type=media_type,
                extension=extension,
            )

        return inner

    if '"' in value:
        raise UnsupportedMediaTypeError(
            media_type=media_type,
            extension=extension,
        )

    return value
