"""Deterministic token counting utilities for evidence context selection.

Generation-model token accounting remains OPEN. Current context budgeting
uses the configured retrieval tokenizer only as an interim bounded measurement,
not as a final generation-token guarantee.
"""

from __future__ import annotations

import re
from typing import Any, Protocol


class TokenCounter(Protocol):
    """Protocol for evidence token counting."""

    @property
    def tokenizer_version(self) -> str:
        """Version string identifying the active token counting strategy."""
        ...

    @property
    def is_exact_model_tokenizer(self) -> bool:
        """Indicate whether this counter uses an exact model tokenizer or an approximation."""
        ...

    def count_tokens(self, text: str) -> int:
        """Return the non-negative integer token count for the given text."""
        ...


class ExactModelTokenCounter:
    """Exact token counter that delegates to an explicit model or tokenizer instance."""

    def __init__(
        self,
        tokenizer: Any,
        version: str = "exact-model-tokenizer-v1",
    ) -> None:
        self.tokenizer = tokenizer
        self._version = version

    @property
    def tokenizer_version(self) -> str:
        return self._version

    @property
    def is_exact_model_tokenizer(self) -> bool:
        return True

    def count_tokens(self, text: str) -> int:
        if not text or not text.strip():
            return 0
        if hasattr(self.tokenizer, "encode"):
            tokens = self.tokenizer.encode(
                text,
                add_special_tokens=False,
                truncation=False,
            )
            return len(tokens)
        if callable(self.tokenizer):
            return int(self.tokenizer(text))
        raise ValueError("Configured tokenizer does not expose encode() or callable interface")


class HeuristicWhitespacePunctuationTokenCounter:
    """Deterministic regex-based heuristic token counting approximation.

    IMPORTANT: This counter is an approximation / test double based on
    whitespace and punctuation splitting. It does NOT run an exact model
    WordPiece or BPE tokenizer and does NOT claim exact model token counts.
    It is used for bounded interim measurement in test/offline environments
    where model weights are not loaded.
    """

    _TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]")

    def __init__(self, version: str = "heuristic-whitespace-punctuation-v1") -> None:
        self._version = version

    @property
    def tokenizer_version(self) -> str:
        return self._version

    @property
    def is_exact_model_tokenizer(self) -> bool:
        return False

    def count_tokens(self, text: str) -> int:
        if not text or not text.strip():
            return 0
        tokens = self._TOKEN_PATTERN.findall(text)
        count = 0
        for t in tokens:
            if len(t) <= 12:
                count += 1
            else:
                # Subword approximation for long tokens (e.g. hashes, base64 strings)
                count += (len(t) + 5) // 6
        return max(0, count)


# Default counter for test/offline execution where live weights are not loaded.
DEFAULT_TOKEN_COUNTER: TokenCounter = HeuristicWhitespacePunctuationTokenCounter()
