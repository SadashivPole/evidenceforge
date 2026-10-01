"""Pure reciprocal-rank fusion (RRF) for production hybrid retrieval."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.evidence.hybrid.config import (
    DEFAULT_HYBRID_RETRIEVAL_CONFIG,
    DEFAULT_RRF_K,
    HybridRetrievalConfig,
)
from app.evidence.hybrid.errors import HybridWorkspaceMismatchError
from app.evidence.hybrid.types import HybridSearchResult
from app.evidence.search.types import SearchChunkCandidate, SearchResult
from app.evidence.semantic.types import SemanticSearchResult


def reciprocal_rank_score(
    lexical_rank: int | None,
    semantic_rank: int | None,
    *,
    rrf_k: int = DEFAULT_RRF_K,
) -> float:
    """Calculate the RRF score using one-based component ranks.

    Formula:
        RRF(d) = sum(1 / (rrf_k + r_m(d))) for each stream m in which d appears.
    """

    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    if lexical_rank is None and semantic_rank is None:
        raise ValueError("At least one component rank is required")
    if lexical_rank is not None and lexical_rank < 1:
        raise ValueError("lexical_rank must be one-based (>= 1)")
    if semantic_rank is not None and semantic_rank < 1:
        raise ValueError("semantic_rank must be one-based (>= 1)")

    score = 0.0
    if lexical_rank is not None:
        score += 1.0 / (rrf_k + lexical_rank)
    if semantic_rank is not None:
        score += 1.0 / (rrf_k + semantic_rank)
    return score


def fuse_hybrid_results(
    lexical_results: Sequence[SearchResult],
    semantic_results: Sequence[SemanticSearchResult],
    *,
    config: HybridRetrievalConfig = DEFAULT_HYBRID_RETRIEVAL_CONFIG,
    authorized_workspace_id: uuid.UUID | None = None,
) -> tuple[HybridSearchResult, ...]:
    """Fuse bounded lexical and semantic search results using Reciprocal Rank Fusion.

    Ordering is strictly deterministic:
    1. descending RRF score
    2. lexical-list presence (present before absent)
    3. ascending lexical rank
    4. semantic-list presence (present before absent)
    5. ascending semantic rank
    6. ascending stable chunk UUID string
    """

    bounded_lexical = tuple(lexical_results[: config.lexical_top_k])
    bounded_semantic = tuple(semantic_results[: config.semantic_top_k])

    lexical_map: dict[uuid.UUID, tuple[int, SearchResult, SearchChunkCandidate]] = {}
    for rank, lex in enumerate(bounded_lexical, start=1):
        if lex.candidate.chunk_id not in lexical_map:
            lexical_map[lex.candidate.chunk_id] = (rank, lex, lex.candidate)

    semantic_map: dict[uuid.UUID, tuple[int, SemanticSearchResult, SearchChunkCandidate]] = {}
    for rank, sem in enumerate(bounded_semantic, start=1):
        if sem.chunk_id not in semantic_map:
            semantic_map[sem.chunk_id] = (rank, sem, sem.to_search_candidate())

    candidate_ids = set(lexical_map.keys()) | set(semantic_map.keys())

    fused_items: list[HybridSearchResult] = []
    for chunk_id in candidate_ids:
        candidate: SearchChunkCandidate | None = None
        lex_rank: int | None = None
        lex_res: SearchResult | None = None
        sem_rank: int | None = None
        sem_res: SemanticSearchResult | None = None

        if chunk_id in lexical_map:
            lex_rank, lex_res, candidate = lexical_map[chunk_id]

        if chunk_id in semantic_map:
            sem_rank, sem_res, sem_cand = semantic_map[chunk_id]
            if candidate is None:
                candidate = sem_cand

            if (
                authorized_workspace_id is not None
                and sem_res.workspace_id != authorized_workspace_id
            ):
                raise HybridWorkspaceMismatchError(
                    f"Candidate {chunk_id} belongs to unauthorized workspace {sem_res.workspace_id}"
                )

        assert candidate is not None

        score = reciprocal_rank_score(
            lex_rank,
            sem_rank,
            rrf_k=config.rrf_k,
        )

        fused_items.append(
            HybridSearchResult(
                candidate=candidate,
                rrf_score=score,
                lexical_rank=lex_rank,
                semantic_rank=sem_rank,
                lexical_result=lex_res,
                semantic_result=sem_res,
                workspace_id=authorized_workspace_id,
            )
        )

    def sort_key(item: HybridSearchResult) -> tuple[float, bool, int, bool, int, str]:
        return (
            -item.rrf_score,
            item.lexical_rank is None,
            item.lexical_rank if item.lexical_rank is not None else config.lexical_top_k + 1,
            item.semantic_rank is None,
            item.semantic_rank if item.semantic_rank is not None else config.semantic_top_k + 1,
            str(item.candidate.chunk_id),
        )

    fused_items.sort(key=sort_key)

    return tuple(fused_items[: config.final_top_k])
