"""Immutable domain types for deterministic questionnaire grounding."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.evidence.citations.types import EvidenceCitation
from app.evidence.hybrid.types import HybridSearchResult
from app.evidence.search.types import SearchResult


class GroundingStatus(StrEnum):
    """Outcome of one deterministic evidence-grounding execution."""

    MATCHED = "MATCHED"
    NO_MATCHES = "NO_MATCHES"


@dataclass(frozen=True, slots=True)
class GroundingRequest:
    """Immutable inputs and effective policy for one grounding execution."""

    workspace_id: uuid.UUID
    questionnaire_version_id: uuid.UUID
    questionnaire_version_question_id: uuid.UUID
    normalized_query: str
    search_version: str
    result_limit: int


@dataclass(frozen=True, slots=True)
class GroundingResult:
    """Immutable ranked search results and citation-ready provenance."""

    workspace_id: uuid.UUID
    questionnaire_version_id: uuid.UUID
    questionnaire_version_question_id: uuid.UUID
    normalized_query: str
    search_version: str
    result_limit: int
    status: GroundingStatus
    results: tuple[SearchResult | HybridSearchResult, ...]
    citations: tuple[EvidenceCitation, ...]
