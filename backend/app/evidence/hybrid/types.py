"""Immutable domain types for production hybrid retrieval and RRF fusion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.evidence.search.types import SearchChunkCandidate, SearchResult
from app.evidence.semantic.types import SemanticSearchResult


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """One fused evidence candidate with RRF score and component provenance."""

    candidate: SearchChunkCandidate
    rrf_score: float
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    lexical_result: SearchResult | None = None
    semantic_result: SemanticSearchResult | None = None
    workspace_id: uuid.UUID | None = None

    @property
    def chunk_id(self) -> uuid.UUID:
        return self.candidate.chunk_id

    @property
    def document_id(self) -> uuid.UUID:
        return self.candidate.document_id

    @property
    def version_id(self) -> uuid.UUID:
        return self.candidate.version_id

    @property
    def version_number(self) -> int:
        return self.candidate.version_number

    @property
    def chunk_index(self) -> int:
        return self.candidate.chunk_index

    @property
    def content(self) -> str:
        return self.candidate.content

    @property
    def content_hash(self) -> str:
        return self.candidate.content_hash

    @property
    def normalized_start_byte(self) -> int:
        return self.candidate.normalized_start_byte

    @property
    def normalized_end_byte(self) -> int:
        return self.candidate.normalized_end_byte

    @property
    def section_label(self) -> str | None:
        return self.candidate.section_label

    @property
    def page_number(self) -> int | None:
        return self.candidate.page_number

    # Backward-compatible SearchResult properties for downstream APIs
    @property
    def score(self) -> float:
        return self.rrf_score

    @property
    def matched_terms(self) -> tuple[str, ...]:
        return self.lexical_result.matched_terms if self.lexical_result else ()

    @property
    def exact_phrase_match(self) -> bool:
        return self.lexical_result.exact_phrase_match if self.lexical_result else False

    @property
    def occurrence_count(self) -> int:
        return self.lexical_result.occurrence_count if self.lexical_result else 0


@dataclass(frozen=True, slots=True)
class HybridRetrievalReport:
    """Bounded operational telemetry and results for one hybrid retrieval run."""

    workspace_id: uuid.UUID
    normalized_query: str
    lexical_count: int
    semantic_count: int
    union_count: int
    final_count: int
    lexical_success: bool
    semantic_success: bool
    fallback_used: bool
    fallback_reason: str | None
    rrf_k: int
    results: tuple[HybridSearchResult, ...]
