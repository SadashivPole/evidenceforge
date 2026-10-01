"""Workspace-authorized database repository for persisted evidence vector search."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.evidence.semantic.config import (
    DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    SemanticRetrievalConfig,
)
from app.evidence.semantic.errors import (
    SemanticAuthorizationError,
    SemanticDatabaseError,
    SemanticWorkspaceMismatchError,
)
from app.evidence.semantic.query import validate_query_vector
from app.evidence.semantic.types import SemanticSearchResult
from app.models import (
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceDocumentVersion,
)


def _compute_cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine distance between two normalized vectors."""

    dot_product = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    cosine_sim = dot_product / (norm_a * norm_b)
    return max(0.0, 1.0 - cosine_sim)


def query_semantic_candidates(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    query_vector: Sequence[float],
    config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    top_k: int | None = None,
) -> tuple[SemanticSearchResult, ...]:
    """Execute workspace-authorized pgvector similarity search over persisted embeddings.

    Security Invariant:
    Filtering by authorized workspace_id occurs at the database query boundary
    BEFORE ordering and LIMIT. Unauthorized workspace candidates are never selected,
    ranked, or returned.
    """

    if workspace_id is None or not isinstance(workspace_id, uuid.UUID):
        raise SemanticAuthorizationError(
            "A valid workspace_id UUID is required for workspace-authorized semantic search"
        )

    validated_vector = validate_query_vector(query_vector, config=config)

    effective_top_k = config.top_k if top_k is None else top_k
    if effective_top_k < 1 or effective_top_k > config.max_top_k:
        raise ValueError(f"top_k must be between 1 and {config.max_top_k}")

    # Determine database dialect
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    is_sqlite = bind is not None and bind.dialect.name == "sqlite"

    if is_sqlite:
        return _query_semantic_candidates_sqlite(
            db,
            workspace_id=workspace_id,
            query_vector=validated_vector,
            config=config,
            document_id=document_id,
            version_id=version_id,
            top_k=effective_top_k,
        )

    # Production PostgreSQL / pgvector query path
    dist_expr = EvidenceChunkEmbedding.embedding.cosine_distance(list(validated_vector)).label(
        "distance"
    )

    statement = (
        select(
            EvidenceChunk.id.label("chunk_id"),
            EvidenceDocument.id.label("document_id"),
            EvidenceDocumentVersion.id.label("version_id"),
            EvidenceDocumentVersion.version_number,
            EvidenceChunk.chunk_index,
            EvidenceChunk.content,
            EvidenceChunk.content_hash,
            EvidenceChunk.normalized_start_byte,
            EvidenceChunk.normalized_end_byte,
            EvidenceChunk.section_label,
            EvidenceChunk.page_number,
            EvidenceChunkEmbedding.workspace_id,
            EvidenceChunkEmbedding.model_id,
            EvidenceChunkEmbedding.model_version,
            EvidenceChunkEmbedding.configuration_hash,
            EvidenceChunkEmbedding.embedding_dimension,
            dist_expr,
        )
        .join(
            EvidenceChunk,
            EvidenceChunk.id == EvidenceChunkEmbedding.evidence_chunk_id,
        )
        .join(
            EvidenceDocumentVersion,
            EvidenceDocumentVersion.id == EvidenceChunk.document_version_id,
        )
        .join(
            EvidenceDocument,
            EvidenceDocument.id == EvidenceDocumentVersion.document_id,
        )
        .where(
            EvidenceChunkEmbedding.workspace_id == workspace_id,
            EvidenceDocument.workspace_id == workspace_id,
            EvidenceChunkEmbedding.model_id == config.model_id,
            EvidenceChunkEmbedding.model_version == config.model_version,
            EvidenceChunkEmbedding.configuration_hash == config.configuration_hash,
            EvidenceChunkEmbedding.embedding_dimension == config.embedding_dimension,
        )
    )

    if document_id is not None:
        statement = statement.where(EvidenceDocument.id == document_id)

    if version_id is not None:
        statement = statement.where(EvidenceDocumentVersion.id == version_id)

    statement = statement.order_by(
        dist_expr,
        EvidenceChunkEmbedding.evidence_chunk_id,
    ).limit(effective_top_k)

    try:
        rows = db.execute(statement).all()
    except SQLAlchemyError as exc:
        raise SemanticDatabaseError(f"PostgreSQL vector search query failed: {exc}") from exc

    results: list[SemanticSearchResult] = []
    for row in rows:
        if row.workspace_id != workspace_id:
            raise SemanticWorkspaceMismatchError(
                f"Security violation: candidate workspace {row.workspace_id} "
                f"does not match authorized workspace {workspace_id}"
            )

        distance = float(row.distance)
        similarity = max(-1.0, min(1.0, 1.0 - distance))

        results.append(
            SemanticSearchResult(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                version_id=row.version_id,
                version_number=row.version_number,
                chunk_index=row.chunk_index,
                content=row.content,
                content_hash=row.content_hash,
                normalized_start_byte=row.normalized_start_byte,
                normalized_end_byte=row.normalized_end_byte,
                section_label=row.section_label,
                page_number=row.page_number,
                workspace_id=row.workspace_id,
                distance=distance,
                similarity=similarity,
                model_id=row.model_id,
                model_version=row.model_version,
                configuration_hash=row.configuration_hash,
                embedding_dimension=row.embedding_dimension,
            )
        )

    return tuple(results)


def _query_semantic_candidates_sqlite(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    query_vector: Sequence[float],
    config: SemanticRetrievalConfig,
    document_id: uuid.UUID | None,
    version_id: uuid.UUID | None,
    top_k: int,
) -> tuple[SemanticSearchResult, ...]:
    """Fallback query handler for SQLite test environments without native pgvector operator."""

    statement = (
        select(
            EvidenceChunk.id.label("chunk_id"),
            EvidenceDocument.id.label("document_id"),
            EvidenceDocumentVersion.id.label("version_id"),
            EvidenceDocumentVersion.version_number,
            EvidenceChunk.chunk_index,
            EvidenceChunk.content,
            EvidenceChunk.content_hash,
            EvidenceChunk.normalized_start_byte,
            EvidenceChunk.normalized_end_byte,
            EvidenceChunk.section_label,
            EvidenceChunk.page_number,
            EvidenceChunkEmbedding.workspace_id,
            EvidenceChunkEmbedding.model_id,
            EvidenceChunkEmbedding.model_version,
            EvidenceChunkEmbedding.configuration_hash,
            EvidenceChunkEmbedding.embedding_dimension,
            EvidenceChunkEmbedding.embedding,
        )
        .join(
            EvidenceChunk,
            EvidenceChunk.id == EvidenceChunkEmbedding.evidence_chunk_id,
        )
        .join(
            EvidenceDocumentVersion,
            EvidenceDocumentVersion.id == EvidenceChunk.document_version_id,
        )
        .join(
            EvidenceDocument,
            EvidenceDocument.id == EvidenceDocumentVersion.document_id,
        )
        .where(
            EvidenceChunkEmbedding.workspace_id == workspace_id,
            EvidenceDocument.workspace_id == workspace_id,
            EvidenceChunkEmbedding.model_id == config.model_id,
            EvidenceChunkEmbedding.model_version == config.model_version,
            EvidenceChunkEmbedding.configuration_hash == config.configuration_hash,
            EvidenceChunkEmbedding.embedding_dimension == config.embedding_dimension,
        )
    )

    if document_id is not None:
        statement = statement.where(EvidenceDocument.id == document_id)

    if version_id is not None:
        statement = statement.where(EvidenceDocumentVersion.id == version_id)

    try:
        rows = db.execute(statement).all()
    except SQLAlchemyError as exc:
        raise SemanticDatabaseError(f"Database query failed: {exc}") from exc

    scored_rows: list[tuple[float, uuid.UUID, SemanticSearchResult]] = []

    for row in rows:
        if row.workspace_id != workspace_id:
            raise SemanticWorkspaceMismatchError(
                f"Security violation: candidate workspace {row.workspace_id} "
                f"does not match authorized workspace {workspace_id}"
            )

        raw_embedding = row.embedding
        if isinstance(raw_embedding, (list, tuple)):
            vector = [float(x) for x in raw_embedding]
        else:
            vector = [float(x) for x in raw_embedding]

        distance = _compute_cosine_distance(vector, query_vector)
        similarity = max(-1.0, min(1.0, 1.0 - distance))

        result = SemanticSearchResult(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            version_id=row.version_id,
            version_number=row.version_number,
            chunk_index=row.chunk_index,
            content=row.content,
            content_hash=row.content_hash,
            normalized_start_byte=row.normalized_start_byte,
            normalized_end_byte=row.normalized_end_byte,
            section_label=row.section_label,
            page_number=row.page_number,
            workspace_id=row.workspace_id,
            distance=distance,
            similarity=similarity,
            model_id=row.model_id,
            model_version=row.model_version,
            configuration_hash=row.configuration_hash,
            embedding_dimension=row.embedding_dimension,
        )
        scored_rows.append((distance, row.chunk_id, result))

    # Sort deterministically: primary by ascending distance, secondary by chunk_id
    scored_rows.sort(key=lambda item: (item[0], item[1]))

    return tuple(item[2] for item in scored_rows[:top_k])
