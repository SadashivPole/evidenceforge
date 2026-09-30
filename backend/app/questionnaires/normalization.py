"""Deterministic questionnaire text normalization."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize Unicode and collapse whitespace deterministically."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def normalize_section_path(values: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and remove empty section labels."""

    result: list[str] = []

    for value in values:
        normalized = normalize_text(value)
        if normalized:
            result.append(normalized)

    return tuple(result)


def normalize_filename(filename: str) -> str:
    """Normalize a questionnaire filename."""

    return normalize_text(filename)
