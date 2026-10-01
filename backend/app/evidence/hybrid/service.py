"""Production hybrid retrieval service combining lexical and semantic search with RRF."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.evidence.hybrid.config import (
    DEFAULT_HYBRID_RETRIEVAL_CONFIG,
    HybridRetrievalConfig,
)
from app.evidence.hybrid.errors import (
    HybridAuthorizationError,
    HybridQueryValidationError,
    HybridRetrievalError,
)
from app.evidence.hybrid.rrf import fuse_hybrid_results
from app.evidence.hybrid.types import HybridRetrievalReport, HybridSearchResult
from app.evidence.persistence.search_repository import list_search_candidates
from app.evidence.search.service import normalize_query, search_chunks
from app.evidence.search.types import SearchResult
from app.evidence.semantic.errors import (
    SemanticAuthorizationError,
    SemanticRetrievalError,
    SemanticWorkspaceMismatchError,
)
from app.evidence.semantic.service import SemanticRetriever
from app.evidence.semantic.types import SemanticSearchResult


class HybridRetriever:
    """Production hybrid retrieval service.

    Orchestrates:
    1. Query normalization and workspace authorization.
    2. Bounded workspace-authorized lexical search (top 10).
       If lexical search fails, fails closed immediately with HybridRetrievalError.
    3. Bounded workspace-authorized semantic pgvector search (top 10).
    4. Deterministic Reciprocal Rank Fusion (RRF, k=60).
    5. Fallback handling on semantic failures (bounded lexical-only fallback).
    6. Bounded final candidate ranking (top 10).
    """

    def __init__(
        self,
        semantic_retriever: SemanticRetriever | None = None,
        config: HybridRetrievalConfig = DEFAULT_HYBRID_RETRIEVAL_CONFIG,
    ) -> None:
        self.config = config
        self.semantic_retriever = semantic_retriever or SemanticRetriever(
            config=config.semantic_config
        )

    def search(
        self,
        db: Session,
        *,
        query: str,
        workspace_id: uuid.UUID,
        limit: int | None = None,
        document_id: uuid.UUID | None = None,
        version_id: uuid.UUID | None = None,
    ) -> tuple[HybridSearchResult, ...]:
        """Perform workspace-authorized hybrid search over persisted evidence."""

        report = self.search_with_report(
            db,
            query=query,
            workspace_id=workspace_id,
            limit=limit,
            document_id=document_id,
            version_id=version_id,
        )
        return report.results

    def search_with_report(
        self,
        db: Session,
        *,
        query: str,
        workspace_id: uuid.UUID,
        limit: int | None = None,
        document_id: uuid.UUID | None = None,
        version_id: uuid.UUID | None = None,
    ) -> HybridRetrievalReport:
        """Perform hybrid search and return detailed bounded operational telemetry."""

        if workspace_id is None or not isinstance(workspace_id, uuid.UUID):
            raise HybridAuthorizationError(
                "A valid workspace_id UUID is required for workspace-authorized hybrid search"
            )

        try:
            normalized_query = normalize_query(query, policy=self.config.search_policy)
        except (TypeError, ValueError) as exc:
            raise HybridQueryValidationError(f"Invalid search query: {exc}") from exc

        effective_limit = self.config.final_top_k if limit is None else limit
        if effective_limit < 1 or effective_limit > self.config.max_final_top_k:
            raise ValueError(
                f"Result limit {effective_limit} is outside permitted range "
                f"[1, {self.config.max_final_top_k}]"
            )

        # 1. Lexical retrieval stream (must succeed; lexical failure fails closed)
        try:
            candidates = list_search_candidates(
                db,
                workspace_id=workspace_id,
                document_id=document_id,
                version_id=version_id,
            )
            lexical_results: tuple[SearchResult, ...] = search_chunks(
                candidates,
                normalized_query,
                policy=self.config.search_policy,
                limit=self.config.lexical_top_k,
            )
            lexical_success = True
        except Exception as exc:
            # Policy: if lexical fails, fail closed immediately.
            raise HybridRetrievalError(f"Lexical retrieval failed: {exc}") from exc

        # 2. Semantic retrieval stream
        semantic_results: tuple[SemanticSearchResult, ...] = ()
        semantic_success = False
        fallback_used = False
        fallback_reason: str | None = None

        try:
            semantic_results = self.semantic_retriever.search(
                db,
                query=normalized_query,
                workspace_id=workspace_id,
                top_k=self.config.semantic_top_k,
                document_id=document_id,
                version_id=version_id,
            )
            semantic_success = True
        except (SemanticWorkspaceMismatchError, SemanticAuthorizationError):
            # Security invariant failures must NEVER fall back; fail closed immediately
            raise
        except SemanticRetrievalError as exc:
            semantic_success = False
            if self.config.enable_semantic_fallback:
                fallback_used = True
                fallback_reason = type(exc).__name__
                semantic_results = ()
            else:
                raise HybridRetrievalError(f"Semantic retrieval failed: {exc}") from exc
        except Exception as exc:
            semantic_success = False
            if self.config.enable_semantic_fallback:
                fallback_used = True
                fallback_reason = type(exc).__name__
                semantic_results = ()
            else:
                raise HybridRetrievalError(f"Unexpected semantic retrieval failure: {exc}") from exc

        # 3. Reciprocal Rank Fusion
        effective_config = HybridRetrievalConfig(
            lexical_top_k=self.config.lexical_top_k,
            semantic_top_k=self.config.semantic_top_k,
            rrf_k=self.config.rrf_k,
            final_top_k=effective_limit,
            max_final_top_k=self.config.max_final_top_k,
            min_query_length=self.config.min_query_length,
            max_query_length=self.config.max_query_length,
            search_version=self.config.search_version,
            search_policy=self.config.search_policy,
            semantic_config=self.config.semantic_config,
            enable_semantic_fallback=self.config.enable_semantic_fallback,
        )

        fused_results = fuse_hybrid_results(
            lexical_results,
            semantic_results,
            config=effective_config,
            authorized_workspace_id=workspace_id,
        )

        unique_union_count = len(
            {r.candidate.chunk_id for r in lexical_results} | {r.chunk_id for r in semantic_results}
        )

        return HybridRetrievalReport(
            workspace_id=workspace_id,
            normalized_query=normalized_query,
            lexical_count=len(lexical_results),
            semantic_count=len(semantic_results),
            union_count=unique_union_count,
            final_count=len(fused_results),
            lexical_success=lexical_success,
            semantic_success=semantic_success,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            rrf_k=self.config.rrf_k,
            results=fused_results,
        )
