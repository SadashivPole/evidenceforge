"""Deterministic evidence context selection and budgeting service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.evidence.context.config import (
    DEFAULT_CONTEXT_BUDGET_CONFIG,
    ContextBudgetConfig,
)
from app.evidence.context.errors import (
    ContextSelectionAuthorizationError,
    ContextSelectionWorkspaceMismatchError,
)
from app.evidence.context.token_counter import (
    DEFAULT_TOKEN_COUNTER,
    TokenCounter,
)
from app.evidence.context.types import (
    ContextSelectionDiagnostics,
    ContextSelectionResult,
    SelectedEvidenceCandidate,
)
from app.evidence.hybrid.types import HybridSearchResult
from app.evidence.search.types import SearchChunkCandidate


def select_evidence_context(
    candidates: Sequence[HybridSearchResult | SearchChunkCandidate],
    *,
    authorized_workspace_id: uuid.UUID,
    config: ContextBudgetConfig = DEFAULT_CONTEXT_BUDGET_CONFIG,
    token_counter: TokenCounter | None = None,
) -> ContextSelectionResult:
    """Select a deterministic, bounded evidence context from already-ranked candidates.

    Hard Invariants:
    1. total_selected_chunks <= config.max_chunks (max 5)
    2. total_input_tokens <= config.max_tokens (max 4000)
    3. Preserves upstream RRF candidate order.
    4. Preserves all chunk coordinates, hashes, and Phase 2D.1 freshness/conflict metadata.
    5. Rejects candidates belonging to unauthorized workspaces (fail-closed).
    6. Never silently truncates evidence content; oversized candidates are skipped.
    """

    if authorized_workspace_id is None or not isinstance(authorized_workspace_id, uuid.UUID):
        raise ContextSelectionAuthorizationError(
            "A valid authorized_workspace_id UUID is required for context selection"
        )

    counter = token_counter or DEFAULT_TOKEN_COUNTER

    selected_list: list[SelectedEvidenceCandidate] = []
    total_tokens = 0
    oversized_skipped = 0
    budget_skipped = 0
    considered = 0

    for item in candidates:
        considered += 1

        # Extract underlying SearchChunkCandidate and ranking metadata
        if isinstance(item, HybridSearchResult):
            cand: SearchChunkCandidate = item.candidate
            rrf_score = item.rrf_score
            lex_rank = item.lexical_rank
            sem_rank = item.semantic_rank
            item_ws_id = item.workspace_id
        elif isinstance(item, SearchChunkCandidate):
            cand = item
            rrf_score = None
            lex_rank = None
            sem_rank = None
            item_ws_id = authorized_workspace_id
        else:
            cand = getattr(item, "candidate", item)
            rrf_score = getattr(item, "rrf_score", None)
            lex_rank = getattr(item, "lexical_rank", None)
            sem_rank = getattr(item, "semantic_rank", None)
            item_ws_id = getattr(item, "workspace_id", authorized_workspace_id)

        # Defense-in-depth workspace isolation check
        if item_ws_id is not None and item_ws_id != authorized_workspace_id:
            raise ContextSelectionWorkspaceMismatchError(
                f"Candidate {cand.chunk_id} belongs to unauthorized workspace {item_ws_id}"
            )

        token_count = counter.count_tokens(cand.content)

        # Oversized candidate check (single candidate exceeds total budget limit)
        if token_count > config.max_tokens:
            if config.allow_oversized_skip:
                oversized_skipped += 1
                continue
            break

        # Check if candidate fits in remaining budget
        if total_tokens + token_count <= config.max_tokens:
            selection_rank = len(selected_list) + 1
            selected_item = SelectedEvidenceCandidate(
                candidate=cand,
                selection_rank=selection_rank,
                token_count=token_count,
                rrf_score=rrf_score,
                lexical_rank=lex_rank,
                semantic_rank=sem_rank,
                workspace_id=authorized_workspace_id,
            )
            selected_list.append(selected_item)
            total_tokens += token_count

            if len(selected_list) == config.max_chunks:
                break
        else:
            # Does not fit within remaining budget
            budget_skipped += 1
            if not config.allow_oversized_skip:
                break

    diagnostics = ContextSelectionDiagnostics(
        total_candidates_considered=considered,
        total_chunks_selected=len(selected_list),
        total_tokens_selected=total_tokens,
        remaining_token_budget=max(0, config.max_tokens - total_tokens),
        oversized_candidates_skipped=oversized_skipped,
        budget_exceeded_candidates_skipped=budget_skipped,
        tokenizer_version=counter.tokenizer_version,
        is_exact_model_tokenizer=counter.is_exact_model_tokenizer,
        selection_version=config.selection_version,
    )

    return ContextSelectionResult(
        workspace_id=authorized_workspace_id,
        selected_candidates=tuple(selected_list),
        total_selected_chunks=len(selected_list),
        total_input_tokens=total_tokens,
        truncated_candidate_count=0,
        skipped_candidate_count=oversized_skipped + budget_skipped,
        selection_version=config.selection_version,
        diagnostics=diagnostics,
    )


class EvidenceContextSelector:
    """Service wrapping evidence context budgeting with a configured policy."""

    def __init__(
        self,
        config: ContextBudgetConfig = DEFAULT_CONTEXT_BUDGET_CONFIG,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.config = config
        self.token_counter = token_counter or DEFAULT_TOKEN_COUNTER

    def select(
        self,
        candidates: Sequence[HybridSearchResult | SearchChunkCandidate],
        *,
        authorized_workspace_id: uuid.UUID,
        max_chunks: int | None = None,
        max_tokens: int | None = None,
    ) -> ContextSelectionResult:
        """Select evidence context with optional runtime bound overrides."""

        effective_config = self.config
        if max_chunks is not None or max_tokens is not None:
            effective_config = ContextBudgetConfig(
                max_chunks=max_chunks if max_chunks is not None else self.config.max_chunks,
                max_tokens=max_tokens if max_tokens is not None else self.config.max_tokens,
                selection_version=self.config.selection_version,
                allow_oversized_skip=self.config.allow_oversized_skip,
            )

        return select_evidence_context(
            candidates,
            authorized_workspace_id=authorized_workspace_id,
            config=effective_config,
            token_counter=self.token_counter,
        )
