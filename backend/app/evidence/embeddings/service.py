"""Deterministic evidence embedding generation and bounded backfill services."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.evidence.embeddings.config import (
    DEFAULT_EMBEDDING_CONFIG,
    EXPECTED_EMBEDDING_DIMENSION,
    EmbeddingConfig,
)
from app.evidence.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingContentHashMismatchError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingGenerationError,
    EmbeddingModelUnavailableError,
    EmbeddingPersistenceError,
)
from app.evidence.embeddings.types import (
    BackfillReport,
    EmbeddingFailure,
    EmbeddingPersistenceStatus,
    EmbeddingTarget,
)
from app.models import (
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceDocumentVersion,
)

MAX_BACKFILL_CHUNKS = 1000


def load_embedding_model(config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> Any:
    """Load the explicitly configured local model on demand.

    Model loading is intentionally not performed at application import or startup.
    A caller must explicitly request generation or backfill.
    """

    try:
        import sentence_transformers
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingModelUnavailableError(
            "sentence-transformers is unavailable for embedding generation"
        ) from exc

    runtime_version = getattr(sentence_transformers, "__version__", None)
    if runtime_version != config.runtime_version:
        raise EmbeddingConfigurationError(
            "Installed sentence-transformers runtime does not match the frozen configuration"
        )

    try:
        model = SentenceTransformer(config.model_id, device=config.device)
    except Exception as exc:
        raise EmbeddingModelUnavailableError(
            "Configured embedding model could not be loaded"
        ) from exc

    _validate_model_dimension(model, config)
    return model


def _validate_model_dimension(model: Any, config: EmbeddingConfig) -> None:
    dimension_reader = getattr(model, "get_sentence_embedding_dimension", None)
    if not callable(dimension_reader):
        raise EmbeddingConfigurationError(
            "Embedding model does not expose its sentence embedding dimension"
        )

    try:
        dimension = int(dimension_reader())
    except Exception as exc:
        raise EmbeddingConfigurationError("Embedding model dimension could not be read") from exc

    if dimension != config.embedding_dimension:
        raise EmbeddingDimensionError(
            f"Embedding model dimension {dimension} does not match "
            f"configured dimension {config.embedding_dimension}"
        )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_target_content_hash(target: EmbeddingTarget) -> None:
    """Reject source content whose stored hash is not the current SHA-256."""

    if _content_hash(target.content) != target.content_hash:
        raise EmbeddingContentHashMismatchError(
            "Evidence chunk content does not match its persisted content hash"
        )


def _prepare_document_text(
    content: str,
    tokenizer: Any,
    *,
    max_word_pieces: int,
) -> str:
    """Apply the explicit deterministic 250-word-piece benchmark policy."""

    try:
        token_ids = tokenizer.encode(
            content,
            add_special_tokens=False,
            truncation=False,
        )
    except Exception as exc:
        raise EmbeddingGenerationError("Evidence tokenizer failed") from exc

    if len(token_ids) <= max_word_pieces:
        return content

    bounded_ids = token_ids[:max_word_pieces]
    try:
        bounded_text = tokenizer.decode(
            bounded_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
    except Exception as exc:
        raise EmbeddingGenerationError("Evidence input window could not be decoded") from exc

    if not bounded_text:
        raise EmbeddingGenerationError("Evidence input window was empty")
    return bounded_text


def _as_rows(output: Any) -> list[list[float]]:
    if hasattr(output, "detach"):
        output = output.detach().cpu().numpy()
    if hasattr(output, "tolist"):
        output = output.tolist()

    if not isinstance(output, (list, tuple)):
        raise EmbeddingGenerationError("Embedding encoder returned an unsupported value")

    if output and isinstance(output[0], (int, float)):
        return [list(output)]

    rows: list[list[float]] = []
    for row in output:
        if not isinstance(row, (list, tuple)):
            raise EmbeddingGenerationError("Embedding encoder returned an invalid matrix")
        rows.append([float(value) for value in row])
    return rows


def _validate_vector(vector: Sequence[float], config: EmbeddingConfig) -> tuple[float, ...]:
    if len(vector) != EXPECTED_EMBEDDING_DIMENSION:
        raise EmbeddingDimensionError(
            f"Generated embedding dimension {len(vector)} does not equal "
            f"{EXPECTED_EMBEDDING_DIMENSION}"
        )

    values = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingGenerationError("Generated embedding contains a non-finite value")

    norm = math.sqrt(sum(value * value for value in values))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise EmbeddingGenerationError("Generated embedding is not normalized")
    return values


class EmbeddingGenerator:
    """Generate deterministic normalized document embeddings with a supplied model."""

    def __init__(self, model: Any, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> None:
        self.model = model
        self.config = config
        _validate_model_dimension(model, config)

    def generate(self, targets: Sequence[EmbeddingTarget]) -> tuple[tuple[float, ...], ...]:
        """Generate one validated vector per target in input order."""

        if not targets:
            return ()

        tokenizer = getattr(self.model, "tokenizer", None)
        if tokenizer is None:
            raise EmbeddingConfigurationError("Embedding model does not expose a tokenizer")

        texts = []
        for target in targets:
            validate_target_content_hash(target)
            texts.append(
                _prepare_document_text(
                    target.content,
                    tokenizer,
                    max_word_pieces=self.config.max_input_word_pieces,
                )
            )

        encoder = getattr(self.model, self.config.document_encoder, None)
        if not callable(encoder):
            raise EmbeddingConfigurationError(
                f"Embedding model does not expose {self.config.document_encoder}"
            )

        try:
            output = encoder(
                texts,
                batch_size=self.config.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=self.config.normalize_embeddings,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingGenerationError("Embedding encoder failed") from exc

        rows = _as_rows(output)
        if len(rows) != len(targets):
            raise EmbeddingGenerationError(
                "Embedding encoder returned a row count different from its input"
            )
        return tuple(_validate_vector(row, self.config) for row in rows)

    def generate_query(self, query: str) -> tuple[float, ...]:
        """Generate one validated vector for a query using the configured query encoder."""

        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not query.strip():
            raise ValueError("query must not be empty")

        tokenizer = getattr(self.model, "tokenizer", None)
        prepared_query = (
            _prepare_document_text(
                query,
                tokenizer,
                max_word_pieces=self.config.max_input_word_pieces,
            )
            if tokenizer is not None
            else query
        )

        encoder = getattr(self.model, self.config.query_encoder, None)
        if not callable(encoder):
            raise EmbeddingConfigurationError(
                f"Embedding model does not expose {self.config.query_encoder}"
            )

        try:
            output = encoder(
                [prepared_query],
                batch_size=self.config.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=self.config.normalize_embeddings,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingGenerationError("Query embedding encoder failed") from exc

        rows = _as_rows(output)
        if len(rows) != 1:
            raise EmbeddingGenerationError(
                "Embedding encoder returned an invalid row count for query"
            )
        return _validate_vector(rows[0], self.config)


def list_workspace_embedding_targets(
    db: Session,
    *,
    workspace_id: Any,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    limit: int = MAX_BACKFILL_CHUNKS,
) -> tuple[EmbeddingTarget, ...]:
    """Select missing current-generation chunks in deterministic workspace order."""

    if limit < 1 or limit > MAX_BACKFILL_CHUNKS:
        raise ValueError(f"limit must be between 1 and {MAX_BACKFILL_CHUNKS}")

    existing_generation = select(EvidenceChunkEmbedding.id).where(
        EvidenceChunkEmbedding.evidence_chunk_id == EvidenceChunk.id,
        EvidenceChunkEmbedding.evidence_content_hash == EvidenceChunk.content_hash,
        EvidenceChunkEmbedding.workspace_id == workspace_id,
        EvidenceChunkEmbedding.model_id == config.model_id,
        EvidenceChunkEmbedding.model_version == config.model_version,
        EvidenceChunkEmbedding.configuration_hash == config.configuration_hash,
    )

    statement = (
        select(
            EvidenceChunk,
            EvidenceDocument.id,
            EvidenceDocumentVersion.id,
            EvidenceDocumentVersion.version_number,
            EvidenceDocument.workspace_id,
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
            EvidenceDocument.workspace_id == workspace_id,
            ~existing_generation.exists(),
        )
        .order_by(
            EvidenceDocument.id,
            EvidenceDocumentVersion.version_number,
            EvidenceChunk.chunk_index,
            EvidenceChunk.id,
        )
        .limit(limit)
    )

    rows = db.execute(statement).all()
    return tuple(
        EmbeddingTarget(
            workspace_id=workspace_id_value,
            document_id=document_id,
            version_id=version_id,
            version_number=version_number,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            content_hash=chunk.content_hash,
            section_label=chunk.section_label,
            page_number=chunk.page_number,
        )
        for chunk, document_id, version_id, version_number, workspace_id_value in rows
    )


def _generation_identity_clause(target: EmbeddingTarget, config: EmbeddingConfig):
    return (
        (EvidenceChunkEmbedding.evidence_chunk_id == target.chunk_id)
        & (EvidenceChunkEmbedding.evidence_content_hash == target.content_hash)
        & (EvidenceChunkEmbedding.workspace_id == target.workspace_id)
        & (EvidenceChunkEmbedding.model_id == config.model_id)
        & (EvidenceChunkEmbedding.model_version == config.model_version)
        & (EvidenceChunkEmbedding.configuration_hash == config.configuration_hash)
    )


def persist_embedding(
    db: Session,
    *,
    target: EmbeddingTarget,
    vector: Sequence[float],
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
) -> EmbeddingPersistenceStatus:
    """Persist one generation without overwriting an existing identity."""

    validate_target_content_hash(target)
    validated_vector = _validate_vector(vector, config)

    existing = db.scalar(
        select(EvidenceChunkEmbedding).where(_generation_identity_clause(target, config))
    )
    if existing is not None:
        return EmbeddingPersistenceStatus.SKIPPED_EXISTING

    row = EvidenceChunkEmbedding(
        evidence_chunk_id=target.chunk_id,
        evidence_content_hash=target.content_hash,
        workspace_id=target.workspace_id,
        model_id=config.model_id,
        model_version=config.model_version,
        configuration_hash=config.configuration_hash,
        embedding_dimension=config.embedding_dimension,
        embedding=list(validated_vector),
    )

    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        existing = db.scalar(
            select(EvidenceChunkEmbedding).where(_generation_identity_clause(target, config))
        )
        if existing is not None:
            return EmbeddingPersistenceStatus.SKIPPED_EXISTING
        raise EmbeddingPersistenceError("Embedding persistence integrity check failed") from exc

    return EmbeddingPersistenceStatus.CREATED


def _is_retryable(error: EmbeddingError) -> bool:
    return isinstance(error, (EmbeddingGenerationError, EmbeddingModelUnavailableError))


def _failure(
    target: EmbeddingTarget,
    error: EmbeddingError,
    *,
    attempts: int,
) -> EmbeddingFailure:
    return EmbeddingFailure(
        workspace_id=target.workspace_id,
        chunk_id=target.chunk_id,
        error_type=type(error).__name__,
        message=str(error),
        attempts=attempts,
        retryable=_is_retryable(error),
    )


def backfill_workspace(
    db: Session,
    *,
    workspace_id: Any,
    model: Any | None = None,
    generator: EmbeddingGenerator | None = None,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    batch_size: int = DEFAULT_EMBEDDING_CONFIG.batch_size,
    max_chunks: int = MAX_BACKFILL_CHUNKS,
    max_retries: int = 1,
) -> BackfillReport:
    """Run bounded deterministic generation for one authorized workspace.

    The selected target snapshot is bounded and ordered before processing. Failed
    model batches receive at most ``max_retries`` retries; failures are reported
    without creating partial embeddings for that failed batch.
    """

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if batch_size > MAX_BACKFILL_CHUNKS:
        raise ValueError(f"batch_size must not exceed {MAX_BACKFILL_CHUNKS}")
    if max_chunks < 1 or max_chunks > MAX_BACKFILL_CHUNKS:
        raise ValueError(f"max_chunks must be between 1 and {MAX_BACKFILL_CHUNKS}")
    if max_retries < 0 or max_retries > 3:
        raise ValueError("max_retries must be between 0 and 3")
    if generator is not None and model is not None:
        raise ValueError("Provide generator or model, not both")

    if generator is None:
        generator = EmbeddingGenerator(model or load_embedding_model(config), config)
    elif generator.config != config:
        raise ValueError("generator configuration must equal the requested config")

    targets = list_workspace_embedding_targets(
        db,
        workspace_id=workspace_id,
        config=config,
        limit=max_chunks,
    )
    failures: list[EmbeddingFailure] = []
    generated_count = 0
    skipped_count = 0
    batch_count = 0

    for start in range(0, len(targets), batch_size):
        batch_count += 1
        batch = targets[start : start + batch_size]
        valid_targets: list[EmbeddingTarget] = []
        for target in batch:
            try:
                validate_target_content_hash(target)
            except EmbeddingError as exc:
                failures.append(_failure(target, exc, attempts=1))
            else:
                valid_targets.append(target)

        if not valid_targets:
            continue

        vectors: tuple[tuple[float, ...], ...] | None = None
        last_error: EmbeddingError | None = None
        attempts = 0
        for attempts in range(1, max_retries + 2):
            try:
                vectors = generator.generate(valid_targets)
                last_error = None
                break
            except EmbeddingError as exc:
                last_error = exc
                if not _is_retryable(exc) or attempts > max_retries:
                    break

        if vectors is None:
            assert last_error is not None
            failures.extend(
                _failure(target, last_error, attempts=attempts) for target in valid_targets
            )
            db.rollback()
            continue

        batch_statuses: list[EmbeddingPersistenceStatus] = []
        current_target = valid_targets[0]
        batch_transaction = db.begin_nested()
        try:
            for target, vector in zip(valid_targets, vectors, strict=True):
                current_target = target
                batch_statuses.append(
                    persist_embedding(
                        db,
                        target=target,
                        vector=vector,
                        config=config,
                    )
                )
            batch_transaction.commit()
            db.commit()
        except EmbeddingError as exc:
            if batch_transaction.is_active:
                batch_transaction.rollback()
            db.rollback()
            failures.append(_failure(current_target, exc, attempts=1))
            continue
        except SQLAlchemyError:
            if batch_transaction.is_active:
                batch_transaction.rollback()
            db.rollback()
            failures.append(
                _failure(
                    current_target,
                    EmbeddingPersistenceError("Embedding batch transaction failed"),
                    attempts=1,
                )
            )
            continue

        generated_count += sum(
            status is EmbeddingPersistenceStatus.CREATED for status in batch_statuses
        )
        skipped_count += sum(
            status is EmbeddingPersistenceStatus.SKIPPED_EXISTING for status in batch_statuses
        )

    return BackfillReport(
        workspace_id=workspace_id,
        configuration_hash=config.configuration_hash,
        requested_count=len(targets),
        generated_count=generated_count,
        skipped_count=skipped_count,
        failed_count=len(failures),
        batch_count=batch_count,
        failures=tuple(failures),
    )
