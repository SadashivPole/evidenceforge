"""Policy definitions for deterministic EvidenceForge search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MIN_QUERY_LENGTH: Final[int] = 2
MAX_QUERY_LENGTH: Final[int] = 256
DEFAULT_SEARCH_LIMIT: Final[int] = 10
MAX_SEARCH_LIMIT: Final[int] = 50
SEARCH_VERSION: Final[str] = "text-search-v1"


@dataclass(frozen=True, slots=True)
class SearchPolicy:
    """Immutable policy controlling deterministic search behavior."""

    min_query_length: int = MIN_QUERY_LENGTH
    max_query_length: int = MAX_QUERY_LENGTH
    default_limit: int = DEFAULT_SEARCH_LIMIT
    max_limit: int = MAX_SEARCH_LIMIT
    search_version: str = SEARCH_VERSION


DEFAULT_SEARCH_POLICY: Final[SearchPolicy] = SearchPolicy()
