"""Transactional persistence orchestration for EvidenceForge ingestion."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.evidence.chunking import (
    DEFAULT_CHUNKING_POLICY,
    ChunkingPolicy,
    ChunkResult,
    chunk_ingestion_result,
)
from app.evidence.ingestion.types import IngestionResult
from app.evidence.persistence.errors import (
    EvidenceDocumentNotFoundError,
    EvidencePersistenceError,
    EvidencePersistenceValidationError,
)
from app.evidence.persistence.repositories import (
    find_duplicate_version,
    lock_document,
    next_version_number,
)
from app.models import (
    EvidenceChunk,
    EvidenceDocumentVersion,
    EvidenceIngestionAttempt,
)


class IngestionOutcome(enum.StrEnum):
    """Possible successful persistence outcomes."""

    CREATED = "created"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class IngestionPersistenceResult:
    """Result returned after an ingestion transaction commits."""

    outcome: IngestionOutcome
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_count: int
    ingestion_attempt_id: uuid.UUID


def persist_ingestion(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_id: uuid.UUID,
    ingestion_result: IngestionResult,
    policy: ChunkingPolicy = DEFAULT_CHUNKING_POLICY,
) -> IngestionPersistenceResult:
    """Persist one normalized evidence ingestion atomically.

    The caller is responsible for authenticating the actor and authorizing the
    workspace. This service additionally verifies that the target document
    belongs to the authorized workspace.
    """

    try:
        _validate_ingestion_result(ingestion_result)

        document = lock_document(
            db,
            workspace_id=workspace_id,
            document_id=document_id,
        )

        if document is None:
            raise EvidenceDocumentNotFoundError("Evidence document not found")

        duplicate = find_duplicate_version(
            db,
            document_id=document.id,
            normalized_sha256=ingestion_result.normalized_sha256,
            normalization_version=ingestion_result.normalization_version,
            chunking_version=policy.version,
        )

        if duplicate is not None:
            return _persist_duplicate(
                db,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                ingestion_result=ingestion_result,
                version=duplicate,
            )

        chunks = chunk_ingestion_result(
            ingestion_result,
            policy=policy,
        )

        _validate_chunks(
            ingestion_result=ingestion_result,
            chunks=chunks,
        )

        version_number = next_version_number(
            db,
            document_id=document.id,
        )

        version = EvidenceDocumentVersion(
            document_id=document.id,
            version_number=version_number,
            normalized_sha256=ingestion_result.normalized_sha256,
            raw_sha256=ingestion_result.raw_sha256,
            normalization_version=ingestion_result.normalization_version,
            original_filename=ingestion_result.original_filename,
            media_type=ingestion_result.media_type,
            raw_size_bytes=ingestion_result.raw_size_bytes,
            normalized_size_bytes=ingestion_result.normalized_size_bytes,
            extracted_text=ingestion_result.normalized_text,
            chunking_version=policy.version,
            chunk_target_bytes=policy.target_bytes,
            chunk_overlap_bytes=policy.overlap_bytes,
            created_by_user_id=actor_user_id,
        )

        db.add(version)
        db.flush()

        for chunk in chunks:
            db.add(
                EvidenceChunk(
                    document_version_id=version.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    normalized_start_byte=chunk.normalized_start_byte,
                    normalized_end_byte=chunk.normalized_end_byte,
                    section_label=chunk.section_label,
                    page_number=chunk.page_number,
                )
            )

        attempt = EvidenceIngestionAttempt(
            workspace_id=workspace_id,
            document_id=document.id,
            version_id=version.id,
            actor_user_id=actor_user_id,
            outcome=IngestionOutcome.CREATED.value,
            error_code=None,
            original_filename=ingestion_result.original_filename,
            media_type=ingestion_result.media_type,
            raw_sha256=ingestion_result.raw_sha256,
            normalized_sha256=ingestion_result.normalized_sha256,
            normalization_version=ingestion_result.normalization_version,
            raw_size_bytes=ingestion_result.raw_size_bytes,
            normalized_size_bytes=ingestion_result.normalized_size_bytes,
        )
        db.add(attempt)

        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            action="evidence.ingestion.created",
            resource_type="evidence_document_version",
            resource_id=str(version.id),
            metadata={
                "document_id": str(document.id),
                "version_number": version.version_number,
                "chunk_count": len(chunks),
                "raw_sha256": ingestion_result.raw_sha256,
                "normalized_sha256": ingestion_result.normalized_sha256,
                "normalization_version": ingestion_result.normalization_version,
                "chunking_version": policy.version,
            },
        )

        db.commit()

        return IngestionPersistenceResult(
            outcome=IngestionOutcome.CREATED,
            document_id=document.id,
            version_id=version.id,
            version_number=version.version_number,
            chunk_count=len(chunks),
            ingestion_attempt_id=attempt.id,
        )

    except EvidencePersistenceError:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise EvidencePersistenceError("Evidence ingestion persistence failed") from exc
    except Exception:
        db.rollback()
        raise


def _persist_duplicate(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    ingestion_result: IngestionResult,
    version: EvidenceDocumentVersion,
) -> IngestionPersistenceResult:
    """Record duplicate provenance without creating another document version."""

    chunk_count = int(
        db.execute(
            __import__("sqlalchemy")
            .select(__import__("sqlalchemy").func.count(EvidenceChunk.id))
            .where(EvidenceChunk.document_version_id == version.id)
        ).scalar_one()
    )

    attempt = EvidenceIngestionAttempt(
        workspace_id=workspace_id,
        document_id=version.document_id,
        version_id=version.id,
        actor_user_id=actor_user_id,
        outcome=IngestionOutcome.DUPLICATE.value,
        error_code=None,
        original_filename=ingestion_result.original_filename,
        media_type=ingestion_result.media_type,
        raw_sha256=ingestion_result.raw_sha256,
        normalized_sha256=ingestion_result.normalized_sha256,
        normalization_version=ingestion_result.normalization_version,
        raw_size_bytes=ingestion_result.raw_size_bytes,
        normalized_size_bytes=ingestion_result.normalized_size_bytes,
    )
    db.add(attempt)

    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        action="evidence.ingestion.duplicate",
        resource_type="evidence_document_version",
        resource_id=str(version.id),
        metadata={
            "document_id": str(version.document_id),
            "version_number": version.version_number,
            "chunk_count": chunk_count,
            "raw_sha256": ingestion_result.raw_sha256,
            "normalized_sha256": ingestion_result.normalized_sha256,
            "normalization_version": ingestion_result.normalization_version,
            "chunking_version": version.chunking_version,
        },
    )

    db.commit()

    return IngestionPersistenceResult(
        outcome=IngestionOutcome.DUPLICATE,
        document_id=version.document_id,
        version_id=version.id,
        version_number=version.version_number,
        chunk_count=chunk_count,
        ingestion_attempt_id=attempt.id,
    )


def _validate_ingestion_result(
    ingestion_result: IngestionResult,
) -> None:
    """Validate the immutable ingestion contract before persistence."""

    normalized_bytes = ingestion_result.normalized_text.encode("utf-8")

    if not ingestion_result.normalized_text:
        raise EvidencePersistenceValidationError("Normalized evidence text cannot be empty")

    if len(normalized_bytes) != ingestion_result.normalized_size_bytes:
        raise EvidencePersistenceValidationError("Normalized size does not match normalized text")

    expected_normalized_hash = sha256(normalized_bytes).hexdigest()

    if expected_normalized_hash != ingestion_result.normalized_sha256:
        raise EvidencePersistenceValidationError(
            "Normalized SHA-256 does not match normalized text"
        )

    if ingestion_result.raw_size_bytes < 0:
        raise EvidencePersistenceValidationError("Raw size cannot be negative")


def _validate_chunks(
    *,
    ingestion_result: IngestionResult,
    chunks: tuple[ChunkResult, ...],
) -> None:
    """Verify that deterministic chunks match the normalized source exactly."""

    if not chunks:
        raise EvidencePersistenceValidationError(
            "Non-empty evidence must produce at least one chunk"
        )

    source_bytes = ingestion_result.normalized_text.encode("utf-8")

    expected_indexes = list(range(len(chunks)))
    actual_indexes = [chunk.chunk_index for chunk in chunks]

    if actual_indexes != expected_indexes:
        raise EvidencePersistenceValidationError("Chunk indexes must be contiguous and zero-based")

    for chunk in chunks:
        content_bytes = chunk.content.encode("utf-8")

        if chunk.normalized_start_byte < 0:
            raise EvidencePersistenceValidationError("Chunk start offset cannot be negative")

        if chunk.normalized_end_byte <= chunk.normalized_start_byte:
            raise EvidencePersistenceValidationError("Chunk byte range must be positive")

        if chunk.normalized_end_byte > len(source_bytes):
            raise EvidencePersistenceValidationError(
                "Chunk end offset exceeds normalized evidence size"
            )

        if chunk.normalized_end_byte - chunk.normalized_start_byte != len(content_bytes):
            raise EvidencePersistenceValidationError(
                "Chunk byte range does not match chunk content size"
            )

        expected_hash = sha256(content_bytes).hexdigest()

        if chunk.content_hash != expected_hash:
            raise EvidencePersistenceValidationError(
                "Chunk content hash does not match chunk content"
            )

        expected_content = source_bytes[chunk.normalized_start_byte : chunk.normalized_end_byte]

        if expected_content != content_bytes:
            raise EvidencePersistenceValidationError(
                "Chunk content does not match its normalized source range"
            )
