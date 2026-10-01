"""Unit tests for deterministic production embedding generation and backfill."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.evidence.embeddings import service as embedding_service
from app.evidence.embeddings.config import EmbeddingConfig
from app.evidence.embeddings.errors import (
    EmbeddingContentHashMismatchError,
    EmbeddingDimensionError,
    EmbeddingPersistenceError,
)
from app.evidence.embeddings.service import (
    EmbeddingGenerator,
    backfill_workspace,
    list_workspace_embedding_targets,
    persist_embedding,
)
from app.evidence.embeddings.types import (
    EmbeddingPersistenceStatus,
    EmbeddingTarget,
)
from app.models import (
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceDocumentVersion,
)
from tests.test_evidence_chunk_embeddings import (
    _create_new_version_chunk,
    _create_parent,
)

VECTOR_DIMENSION = 384


class _Tokenizer:
    def encode(self, text: str, **_: object) -> list[int]:
        return list(range(len(text.split())))

    def decode(self, token_ids: list[int], **_: object) -> str:
        return " ".join(f"token-{token_id}" for token_id in token_ids)


class _FakeModel:
    def __init__(self, *, failures_remaining: int = 0, wrong_dimension: bool = False) -> None:
        self.tokenizer = _Tokenizer()
        self.failures_remaining = failures_remaining
        self.wrong_dimension = wrong_dimension
        self.calls = 0
        self.last_kwargs: dict[str, object] = {}

    def get_sentence_embedding_dimension(self) -> int:
        return VECTOR_DIMENSION

    def encode_document(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.calls += 1
        self.last_kwargs = kwargs
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("transient fake encoder failure")

        dimension = VECTOR_DIMENSION - 1 if self.wrong_dimension else VECTOR_DIMENSION
        rows: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimension
            vector = [0.0] * dimension
            vector[index] = 1.0
            rows.append(vector)
        return rows


def _target(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    chunk_id: uuid.UUID,
    content: str,
    content_hash: str,
) -> EmbeddingTarget:
    chunk = session.get(EvidenceChunk, chunk_id)
    assert chunk is not None
    version = session.get(EvidenceDocumentVersion, chunk.document_version_id)
    assert version is not None
    document = session.get(EvidenceDocument, version.document_id)
    assert document is not None
    return EmbeddingTarget(
        workspace_id=workspace_id,
        document_id=document.id,
        version_id=version.id,
        version_number=version.version_number,
        chunk_id=chunk_id,
        chunk_index=0,
        content=content,
        content_hash=content_hash,
        section_label="Evidence",
        page_number=None,
    )


def _make_target(session: Session, *, suffix: str) -> tuple[uuid.UUID, EmbeddingTarget]:
    workspace, chunk = _create_parent(session, suffix=suffix)
    return workspace.id, _target(
        session,
        workspace_id=workspace.id,
        chunk_id=chunk.id,
        content=chunk.content,
        content_hash=chunk.content_hash,
    )


def test_configuration_hash_is_canonical_and_changes_with_generation_inputs() -> None:
    first = EmbeddingConfig()
    second = EmbeddingConfig()
    changed = EmbeddingConfig(max_input_word_pieces=249)

    assert first.configuration_hash == second.configuration_hash
    assert len(first.configuration_hash) == 64
    assert first.configuration_hash != changed.configuration_hash
    assert first.canonical_payload()["model_id"] == (
        "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    )
    assert first.canonical_payload()["embedding_dimension"] == VECTOR_DIMENSION


def test_generation_is_384_dimensional_normalized_and_explicitly_windowed(
    db_session: Session,
) -> None:
    workspace_id, target = _make_target(db_session, suffix=uuid.uuid4().hex[:12])
    long_content = " ".join(f"word-{index}" for index in range(300))
    target = EmbeddingTarget(
        workspace_id=workspace_id,
        document_id=target.document_id,
        version_id=target.version_id,
        version_number=target.version_number,
        chunk_id=target.chunk_id,
        chunk_index=target.chunk_index,
        content=long_content,
        content_hash=hashlib.sha256(long_content.encode()).hexdigest(),
        section_label=target.section_label,
        page_number=target.page_number,
    )
    model = _FakeModel()
    vectors = EmbeddingGenerator(model).generate((target,))

    assert len(vectors) == 1
    assert len(vectors[0]) == VECTOR_DIMENSION
    assert sum(value * value for value in vectors[0]) == 1.0
    assert model.last_kwargs["normalize_embeddings"] is True
    assert model.last_kwargs["show_progress_bar"] is False


def test_content_hash_mismatch_is_rejected(db_session: Session) -> None:
    workspace_id, target = _make_target(db_session, suffix=uuid.uuid4().hex[:12])
    invalid = EmbeddingTarget(
        workspace_id=workspace_id,
        document_id=target.document_id,
        version_id=target.version_id,
        version_number=target.version_number,
        chunk_id=target.chunk_id,
        chunk_index=target.chunk_index,
        content=target.content + " changed",
        content_hash=target.content_hash,
        section_label=target.section_label,
        page_number=target.page_number,
    )

    with pytest.raises(EmbeddingContentHashMismatchError):
        EmbeddingGenerator(_FakeModel()).generate((invalid,))


def test_wrong_generated_dimension_is_rejected(db_session: Session) -> None:
    _, target = _make_target(db_session, suffix=uuid.uuid4().hex[:12])

    with pytest.raises(EmbeddingDimensionError):
        EmbeddingGenerator(_FakeModel(wrong_dimension=True)).generate((target,))


def test_persistence_is_idempotent_and_retains_full_provenance(db_session: Session) -> None:
    workspace_id, target = _make_target(db_session, suffix=uuid.uuid4().hex[:12])
    config = EmbeddingConfig()
    vector = EmbeddingGenerator(_FakeModel(), config).generate((target,))[0]

    first = persist_embedding(db_session, target=target, vector=vector, config=config)
    db_session.commit()
    second = persist_embedding(db_session, target=target, vector=vector, config=config)
    db_session.commit()

    assert first is EmbeddingPersistenceStatus.CREATED
    assert second is EmbeddingPersistenceStatus.SKIPPED_EXISTING
    row = db_session.scalar(
        select(EvidenceChunkEmbedding).where(
            EvidenceChunkEmbedding.evidence_chunk_id == target.chunk_id
        )
    )
    assert row is not None
    assert row.workspace_id == workspace_id
    assert row.evidence_content_hash == target.content_hash
    assert row.model_id == config.model_id
    assert row.model_version == config.model_version
    assert row.configuration_hash == config.configuration_hash
    assert row.embedding_dimension == VECTOR_DIMENSION


def test_workspace_target_selection_is_scoped_and_deterministic(db_session: Session) -> None:
    workspace_id, first = _make_target(db_session, suffix=uuid.uuid4().hex[:12])
    other_workspace_id, _ = _make_target(db_session, suffix=uuid.uuid4().hex[:12])
    targets = list_workspace_embedding_targets(
        db_session,
        workspace_id=workspace_id,
        limit=10,
    )

    assert len(targets) == 1
    assert targets[0] == first
    assert all(target.workspace_id != other_workspace_id for target in targets)


def test_backfill_is_bounded_and_retries_transient_generation_failure(db_session: Session) -> None:
    workspace, first_chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    second_chunk = _create_new_version_chunk(
        db_session,
        existing_chunk=first_chunk,
        content="Second immutable version.",
    )
    _create_new_version_chunk(
        db_session,
        existing_chunk=second_chunk,
        content="Third immutable version.",
    )
    generator = EmbeddingGenerator(_FakeModel(failures_remaining=1))

    report = backfill_workspace(
        db_session,
        workspace_id=workspace.id,
        generator=generator,
        batch_size=2,
        max_chunks=2,
        max_retries=1,
    )

    assert report.requested_count == 2
    assert report.generated_count == 2
    assert report.failed_count == 0
    assert report.batch_count == 1
    assert generator.model.calls == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EvidenceChunkEmbedding)
            .where(EvidenceChunkEmbedding.workspace_id == workspace.id)
        )
        == 2
    )


def test_backfill_rolls_back_entire_batch_on_persistence_failure(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, first_chunk = _create_parent(db_session, suffix=uuid.uuid4().hex[:12])
    second_chunk = _create_new_version_chunk(
        db_session,
        existing_chunk=first_chunk,
        content="Second immutable version.",
    )
    targets = list_workspace_embedding_targets(
        db_session,
        workspace_id=workspace.id,
        limit=10,
    )
    assert {target.chunk_id for target in targets} == {first_chunk.id, second_chunk.id}

    original_persist_embedding = embedding_service.persist_embedding
    calls = 0

    def fail_second_persistence(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise EmbeddingPersistenceError("deterministic batch persistence failure")
        return original_persist_embedding(*args, **kwargs)

    monkeypatch.setattr(embedding_service, "persist_embedding", fail_second_persistence)
    report = backfill_workspace(
        db_session,
        workspace_id=workspace.id,
        generator=EmbeddingGenerator(_FakeModel()),
        batch_size=2,
        max_chunks=2,
        max_retries=1,
    )

    assert calls == 2
    assert report.generated_count == 0
    assert report.skipped_count == 0
    assert report.failed_count == 1
    assert report.failures[0].chunk_id == targets[1].chunk_id
    assert report.failures[0].error_type == "EmbeddingPersistenceError"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EvidenceChunkEmbedding)
            .where(EvidenceChunkEmbedding.workspace_id == workspace.id)
        )
        == 0
    )


def test_backfill_reports_bounded_exhausted_failure(db_session: Session) -> None:
    workspace_id, _ = _make_target(db_session, suffix=uuid.uuid4().hex[:12])
    generator = EmbeddingGenerator(_FakeModel(failures_remaining=5))

    report = backfill_workspace(
        db_session,
        workspace_id=workspace_id,
        generator=generator,
        batch_size=1,
        max_chunks=1,
        max_retries=1,
    )

    assert report.generated_count == 0
    assert report.failed_count == 1
    assert report.failures[0].attempts == 2
    assert report.failures[0].retryable is True
    assert "EmbeddingGenerationError" in report.failures[0].error_type
    assert generator.model.calls == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(EvidenceChunkEmbedding)
            .where(EvidenceChunkEmbedding.workspace_id == workspace_id)
        )
        == 0
    )
