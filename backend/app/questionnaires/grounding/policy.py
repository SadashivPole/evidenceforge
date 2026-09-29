"""Policy definitions for deterministic questionnaire grounding."""

from __future__ import annotations

from typing import Final

from app.evidence.search.policy import DEFAULT_SEARCH_POLICY, SearchPolicy

GroundingPolicy = SearchPolicy
DEFAULT_GROUNDING_POLICY: Final[GroundingPolicy] = DEFAULT_SEARCH_POLICY
