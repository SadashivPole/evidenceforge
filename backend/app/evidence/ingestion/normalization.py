"""Deterministic text normalization for EvidenceForge ingestion."""

from __future__ import annotations

from app.evidence.ingestion.errors import (
    EmptyContentError,
    InvalidUtf8Error,
    WhitespaceOnlyContentError,
)


def decode_and_normalize_text(raw_bytes: bytes) -> str:
    """Decode UTF-8 and apply deterministic text normalization rules.

    Rules:
    - Decode using strict UTF-8 semantics.
    - Remove one leading UTF-8 BOM if present.
    - Normalize CRLF and CR line endings to LF.
    - Reject empty content.
    - Reject whitespace-only content.
    - Normalize whitespace-only blank lines to empty lines.
    - Preserve at most two consecutive blank lines.
    - Preserve trailing whitespace on substantive lines.
    """
    try:
        text = raw_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidUtf8Error(byte_offset=exc.start) from exc

    if text == "":
        raise EmptyContentError()

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    if text.strip() == "":
        raise WhitespaceOnlyContentError()

    lines = text.split("\n")

    normalized_lines: list[str] = []
    blank_run = 0

    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                normalized_lines.append("")
        else:
            blank_run = 0
            normalized_lines.append(line)

    normalized_text = "\n".join(normalized_lines)

    if normalized_text == "":
        raise EmptyContentError()

    if normalized_text.strip() == "":
        raise WhitespaceOnlyContentError()

    return normalized_text
