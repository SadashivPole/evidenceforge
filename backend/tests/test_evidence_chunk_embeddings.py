"""SQLite-safe persistence contract tests for EvidenceChunkEmbedding."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceDocumentVersion,
    User,
    Workspace,
)

MODEL_ID = "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
VECTOR_DIMENSION = 384


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _create_parent(
    session: Session,
    *,
    suffix: str,
    content: str = "Immutable evidence content.",
) -> tuple[Workspace, EvidenceChunk]:
    user = User(
        external_subject=f"embedding:{suffix}",
        email=f"embedding-{suffix}@example.test",
        display_name="Embedding Test User",
    )
    session.add(user)
    session.flush()

    workspace = Workspace(
        name=f"Embedding Workspace {suffix}",
        created_by_user_id=user.id,
    )
    session.add(workspace)
    session.flush()

    document = EvidenceDocument(
        workspace_id=workspace.id,
        name=f"embedding-{suffix}.md",
        source_type="file",
        status="active",
        created_by_user_id=user.id,
    )
    session.add(document)
    session.flush()

    normalized_hash = _content_hash(content)
    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        normalized_sha256=normalized_hash,
        raw_sha256=normalized_hash,
        normalization_version="text-v1",
        original_filename=f"embedding-{suffix}.md",
        media_type="text/markdown",
        raw_size_bytes=len(content.encode("utf-8")),
        normalized_size_bytes=len(content.encode("utf-8")),
        extracted_text=content,
        chunking_version="markdown-v1",
        chunk_target_bytes=len(content.encode("utf-8")),
        chunk_overlap_bytes=0,
        created_by_user_id=user.id,
    )
    session.add(version)
    session.flush()

    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=content,
        content_hash=normalized_hash,
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label="Evidence",
        page_number=None,
    )
    session.add(chunk)
    session.commit()
    session.refresh(chunk)
    return workspace, chunk


def _create_new_version_chunk(
    session: Session,
    *,
    existing_chunk: EvidenceChunk,
    content: str,
) -> EvidenceChunk:
    """Create a changed immutable version under the existing document/workspace."""

    existing_version = session.get(EvidenceDocumentVersion, existing_chunk.document_version_id)
    assert existing_version is not None
    document = session.get(EvidenceDocument, existing_version.document_id)
    assert document is not None

    content_hash = _content_hash(content)
    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=existing_version.version_number + 1,
        normalized_sha256=content_hash,
        raw_sha256=content_hash,
        normalization_version="text-v1",
        original_filename=document.name,
        media_type="text/markdown",
        raw_size_bytes=len(content.encode("utf-8")),
        normalized_size_bytes=len(content.encode("utf-8")),
        extracted_text=content,
        chunking_version="markdown-v1",
        chunk_target_bytes=len(content.encode("utf-8")),
        chunk_overlap_bytes=0,
        created_by_user_id=document.created_by_user_id,
    )
    session.add(version)
    session.flush()

    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=content,
        content_hash=content_hash,
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label="Evidence",
        page_number=None,
    )
    session.add(chunk)
    session.commit()
    session.refresh(chunk)
    return chunk


def _embedding(
    *,
    workspace_id: uuid.UUID,
    chunk_id: uuid.UUID,
    content_hash: str,
    model_version: str = "baseline-v1",
    configuration_hash: str = "a" * 64,
) -> EvidenceChunkEmbedding:
    return EvidenceChunkEmbedding(
        evidence_chunk_id=chunk_id,
        evidence_content_hash=content_hash,
        workspace_id=workspace_id,
        model_id=MODEL_ID,
        model_version=model_version,
        configuration_hash=configuration_hash,
        embedding_dimension=VECTOR_DIMENSION,
        embedding=[0.0] * VECTOR_DIMENSION,
    )


def test_embedding_model_declares_provenance_and_384_dimensions() -> None:
    table = EvidenceChunkEmbedding.__table__

    assert table.c.embedding.type.dim == VECTOR_DIMENSION
    assert {
        "evidence_chunk_id",
        "evidence_content_hash",
        "workspace_id",
        "model_id",
        "model_version",
        "configuration_hash",
        "embedding_dimension",
        "embedding",
        "created_at",
    }.issubset(table.c.keys())
    assert "uq_evidence_chunk_embeddings_generation" in {
        constraint.name for constraint in table.constraints
    }
    assert "ck_evidence_chunk_embeddings_dimension" in {
        constraint.name for constraint in table.constraints
    }


def test_embedding_provenance_persists(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])

    row = _embedding(
        workspace_id=workspace.id,
        chunk_id=chunk.id,
        content_hash=chunk.content_hash,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    persisted = db_session.scalar(
        select(EvidenceChunkEmbedding).where(EvidenceChunkEmbedding.id == row.id)
    )
    assert persisted is not None
    assert persisted.evidence_chunk_id == chunk.id
    assert persisted.workspace_id == workspace.id
    assert persisted.evidence_content_hash == chunk.content_hash
    assert persisted.model_id == MODEL_ID
    assert persisted.model_version == "baseline-v1"
    assert persisted.configuration_hash == "a" * 64
    assert persisted.embedding_dimension == VECTOR_DIMENSION


def test_duplicate_generation_identity_is_rejected(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    first = _embedding(
        workspace_id=workspace.id,
        chunk_id=chunk.id,
        content_hash=chunk.content_hash,
    )
    db_session.add(first)
    db_session.commit()

    db_session.add(
        _embedding(
            workspace_id=workspace.id,
            chunk_id=chunk.id,
            content_hash=chunk.content_hash,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EvidenceChunkEmbedding)
            .where(EvidenceChunkEmbedding.evidence_chunk_id == chunk.id)
        )
        == 1
    )


def test_model_and_configuration_generations_coexist(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    db_session.add_all(
        [
            _embedding(
                workspace_id=workspace.id,
                chunk_id=chunk.id,
                content_hash=chunk.content_hash,
                model_version="baseline-v1",
                configuration_hash="a" * 64,
            ),
            _embedding(
                workspace_id=workspace.id,
                chunk_id=chunk.id,
                content_hash=chunk.content_hash,
                model_version="baseline-v2",
                configuration_hash="a" * 64,
            ),
            _embedding(
                workspace_id=workspace.id,
                chunk_id=chunk.id,
                content_hash=chunk.content_hash,
                model_version="baseline-v2",
                configuration_hash="b" * 64,
            ),
        ]
    )
    db_session.commit()

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EvidenceChunkEmbedding)
            .where(EvidenceChunkEmbedding.evidence_chunk_id == chunk.id)
        )
        == 3
    )


def test_changed_content_hash_uses_a_new_immutable_chunk(db_session: Session) -> None:
    workspace, first_chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    second_chunk = _create_new_version_chunk(
        db_session,
        existing_chunk=first_chunk,
        content="Changed immutable evidence content.",
    )
    assert second_chunk.content_hash != first_chunk.content_hash

    db_session.add(
        _embedding(
            workspace_id=workspace.id,
            chunk_id=first_chunk.id,
            content_hash=first_chunk.content_hash,
        )
    )
    db_session.add(
        _embedding(
            workspace_id=workspace.id,
            chunk_id=second_chunk.id,
            content_hash=second_chunk.content_hash,
        )
    )
    db_session.commit()

    rows = db_session.scalars(
        select(EvidenceChunkEmbedding).order_by(EvidenceChunkEmbedding.id)
    ).all()
    assert len(rows) == 2
    assert {row.evidence_content_hash for row in rows} == {
        first_chunk.content_hash,
        second_chunk.content_hash,
    }
    assert {row.workspace_id for row in rows} == {workspace.id}


def test_embedding_foreign_keys_reject_unknown_chunk(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    unknown_chunk_id = uuid.uuid4()

    db_session.add(
        _embedding(
            workspace_id=workspace.id,
            chunk_id=unknown_chunk_id,
            content_hash=chunk.content_hash,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_embedding_dimension_check_rejects_wrong_declared_dimension(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    row = _embedding(
        workspace_id=workspace.id,
        chunk_id=chunk.id,
        content_hash=chunk.content_hash,
    )
    row.embedding_dimension = VECTOR_DIMENSION - 1
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
