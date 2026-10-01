"""Configuration constants and dataclass for evidence context selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_CONTEXT_CHUNKS: Final[int] = 5
MAX_CONTEXT_TOKENS: Final[int] = 4000
DEFAULT_SELECTION_VERSION: Final[str] = "context-selection-v1"


@dataclass(frozen=True, slots=True)
class ContextBudgetConfig:
    """Immutable configuration enforcing hard evidence context bounds.

    Bounds:
    - max_chunks <= 5 (hard upper bound)
    - max_tokens <= 4000 (hard upper bound)
    - allow_oversized_skip: skip candidates exceeding remaining budget rather than truncating
    """

    max_chunks: int = MAX_CONTEXT_CHUNKS
    max_tokens: int = MAX_CONTEXT_TOKENS
    selection_version: str = DEFAULT_SELECTION_VERSION
    allow_oversized_skip: bool = True

    def __post_init__(self) -> None:
        if self.max_chunks < 1:
            raise ValueError("max_chunks must be at least 1")
        if self.max_chunks > MAX_CONTEXT_CHUNKS:
            raise ValueError(f"max_chunks must not exceed approved bound of {MAX_CONTEXT_CHUNKS}")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if self.max_tokens > MAX_CONTEXT_TOKENS:
            raise ValueError(f"max_tokens must not exceed approved bound of {MAX_CONTEXT_TOKENS}")


DEFAULT_CONTEXT_BUDGET_CONFIG: Final[ContextBudgetConfig] = ContextBudgetConfig()
