"""Tests for transactional EvidenceForge evidence persistence."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.evidence.chunking import DEFAULT_CHUNKING_POLICY, chunk_ingestion_result
from app.evidence.ingestion.types import IngestionResult
from app.evidence.persistence import (
    EvidenceDocumentNotFoundError,
    IngestionOutcome,
    persist_ingestion,
)
from app.models import (
    AuditEvent,
    Base,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
    EvidenceIngestionAttempt,
    User,
    Workspace,
)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Create an isolated SQLite database for persistence tests."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session

    engine.dispose()


def _create_user(
    db: Session,
    *,
    external_subject: str,
) -> User:
    user = User(
        external_subject=external_subject,
        email=f"{external_subject}@example.test",
        display_name=external_subject,
    )
    db.add(user)
    db.flush()
    return user


def _create_workspace(
    db: Session,
    *,
    user: User,
    name: str,
) -> Workspace:
    workspace = Workspace(
        name=name,
        created_by_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    return workspace


def _create_document(
    db: Session,
    *,
    workspace: Workspace,
    user: User,
    name: str = "security-policy.md",
) -> EvidenceDocument:
    document = EvidenceDocument(
        workspace_id=workspace.id,
        name=name,
        source_type="file",
        status="active",
        created_by_user_id=user.id,
    )
    db.add(document)
    db.flush()
    return document


def _make_ingestion_result(
    text: str,
    *,
    raw_bytes: bytes | None = None,
    normalization_version: str = "text-v1",
) -> IngestionResult:
    normalized_bytes = text.encode("utf-8")
    raw_value = normalized_bytes if raw_bytes is None else raw_bytes

    return IngestionResult(
        original_filename="security-policy.md",
        extension=".md",
        media_type="text/markdown",
        raw_size_bytes=len(raw_value),
        normalized_size_bytes=len(normalized_bytes),
        raw_sha256=hashlib.sha256(raw_value).hexdigest(),
        normalized_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        normalization_version=normalization_version,
        normalized_text=text,
    )


def _count(
    db: Session,
    model: type[object],
) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_created_ingestion_persists_version_chunks_attempt_and_audit(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )
    ingestion = _make_ingestion_result(
        "# Access Control\n\nAuthentication requirements.",
    )

    result = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=ingestion,
    )

    assert result.outcome is IngestionOutcome.CREATED
    assert result.document_id == document.id
    assert result.version_number == 1
    assert result.chunk_count > 0

    version = db_session.get(
        EvidenceDocumentVersion,
        result.version_id,
    )
    assert version is not None
    assert version.normalized_sha256 == ingestion.normalized_sha256
    assert version.raw_sha256 == ingestion.raw_sha256
    assert version.normalization_version == "text-v1"
    assert version.chunking_version == DEFAULT_CHUNKING_POLICY.version
    assert version.raw_size_bytes == ingestion.raw_size_bytes
    assert version.normalized_size_bytes == ingestion.normalized_size_bytes

    chunks = db_session.scalars(
        select(EvidenceChunk)
        .where(EvidenceChunk.document_version_id == version.id)
        .order_by(EvidenceChunk.chunk_index)
    ).all()

    assert len(chunks) == result.chunk_count
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))

    expected_chunks = chunk_ingestion_result(ingestion)

    assert [
        (
            chunk.content,
            chunk.content_hash,
            chunk.normalized_start_byte,
            chunk.normalized_end_byte,
        )
        for chunk in chunks
    ] == [
        (
            chunk.content,
            chunk.content_hash,
            chunk.normalized_start_byte,
            chunk.normalized_end_byte,
        )
        for chunk in expected_chunks
    ]

    attempts = db_session.scalars(select(EvidenceIngestionAttempt)).all()

    assert len(attempts) == 1
    assert attempts[0].outcome == "created"
    assert attempts[0].version_id == version.id
    assert attempts[0].actor_user_id == user.id

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "evidence.ingestion.created")
    ).all()

    assert len(events) == 1
    assert events[0].workspace_id == workspace.id
    assert events[0].actor_user_id == user.id
    assert events[0].resource_id == str(version.id)


def test_duplicate_representation_does_not_create_new_version(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    ingestion = _make_ingestion_result("Approved security policy.")

    first = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=ingestion,
    )

    second = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=ingestion,
    )

    assert first.outcome is IngestionOutcome.CREATED
    assert second.outcome is IngestionOutcome.DUPLICATE
    assert second.version_id == first.version_id
    assert second.version_number == 1

    assert _count(db_session, EvidenceDocumentVersion) == 1
    assert _count(db_session, EvidenceIngestionAttempt) == 2

    duplicate_attempt = db_session.scalars(
        select(EvidenceIngestionAttempt).where(
            EvidenceIngestionAttempt.outcome == "duplicate",
        )
    ).one()

    assert duplicate_attempt.version_id == first.version_id

    duplicate_event = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "evidence.ingestion.duplicate",
        )
    ).one()

    assert duplicate_event.resource_id == str(first.version_id)


def test_same_normalized_content_with_different_raw_provenance_is_duplicate(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    first = _make_ingestion_result(
        "Same normalized evidence",
        raw_bytes=b"RAW-A",
    )
    second = _make_ingestion_result(
        "Same normalized evidence",
        raw_bytes=b"RAW-B",
    )

    first_result = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=first,
    )
    second_result = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=second,
    )

    assert first_result.outcome is IngestionOutcome.CREATED
    assert second_result.outcome is IngestionOutcome.DUPLICATE
    assert second_result.version_id == first_result.version_id

    assert _count(db_session, EvidenceDocumentVersion) == 1


def test_different_representation_gets_version_two(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    first = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=_make_ingestion_result("Version one"),
    )

    second = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=_make_ingestion_result("Version two"),
    )

    assert first.version_number == 1
    assert second.version_number == 2
    assert first.version_id != second.version_id

    assert _count(db_session, EvidenceDocumentVersion) == 2


def test_new_chunking_version_creates_new_representation(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    ingestion = _make_ingestion_result("Same content")

    first = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=ingestion,
    )

    from app.evidence.chunking import ChunkingPolicy

    second = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=ingestion,
        policy=ChunkingPolicy(
            version="text-chunk-v2",
            target_bytes=4096,
            overlap_bytes=512,
            minimum_preferred_break_bytes=1024,
        ),
    )

    assert first.outcome is IngestionOutcome.CREATED
    assert second.outcome is IngestionOutcome.CREATED
    assert first.version_number == 1
    assert second.version_number == 2

    versions = db_session.scalars(
        select(EvidenceDocumentVersion)
        .where(EvidenceDocumentVersion.document_id == document.id)
        .order_by(EvidenceDocumentVersion.version_number)
    ).all()

    assert [version.chunking_version for version in versions] == [
        "text-chunk-v1",
        "text-chunk-v2",
    ]


def test_cross_workspace_document_is_not_accessible(
    db_session: Session,
) -> None:
    user_a = _create_user(
        db_session,
        external_subject="user-a",
    )
    user_b = _create_user(
        db_session,
        external_subject="user-b",
    )

    workspace_a = _create_workspace(
        db_session,
        user=user_a,
        name="Workspace A",
    )
    workspace_b = _create_workspace(
        db_session,
        user=user_b,
        name="Workspace B",
    )

    document_b = _create_document(
        db_session,
        workspace=workspace_b,
        user=user_b,
    )

    with pytest.raises(EvidenceDocumentNotFoundError):
        persist_ingestion(
            db_session,
            workspace_id=workspace_a.id,
            actor_user_id=user_a.id,
            document_id=document_b.id,
            ingestion_result=_make_ingestion_result("Private evidence"),
        )

    assert _count(db_session, EvidenceDocumentVersion) == 0
    assert _count(db_session, EvidenceIngestionAttempt) == 0


def test_audit_failure_rolls_back_the_entire_ingestion(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    def fail_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(
        "app.evidence.persistence.ingestion_service.record_audit_event",
        fail_audit,
    )

    with pytest.raises(RuntimeError, match="forced audit failure"):
        persist_ingestion(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=user.id,
            document_id=document.id,
            ingestion_result=_make_ingestion_result("Rollback test"),
        )

    assert _count(db_session, EvidenceDocumentVersion) == 0
    assert _count(db_session, EvidenceChunk) == 0
    assert _count(db_session, EvidenceIngestionAttempt) == 0
    assert _count(db_session, AuditEvent) == 0


def test_persistence_rejects_tampered_normalized_hash(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    ingestion = _make_ingestion_result("Integrity check")

    tampered = IngestionResult(
        original_filename=ingestion.original_filename,
        extension=ingestion.extension,
        media_type=ingestion.media_type,
        raw_size_bytes=ingestion.raw_size_bytes,
        normalized_size_bytes=ingestion.normalized_size_bytes,
        raw_sha256=ingestion.raw_sha256,
        normalized_sha256="0" * 64,
        normalization_version=ingestion.normalization_version,
        normalized_text=ingestion.normalized_text,
    )

    with pytest.raises(Exception, match="Normalized SHA-256"):
        persist_ingestion(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=user.id,
            document_id=document.id,
            ingestion_result=tampered,
        )

    assert _count(db_session, EvidenceDocumentVersion) == 0


def test_created_chunks_have_exact_source_ranges(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    text = "A" * 9000
    ingestion = _make_ingestion_result(text)

    result = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=ingestion,
    )

    persisted_chunks = db_session.scalars(
        select(EvidenceChunk)
        .where(
            EvidenceChunk.document_version_id == result.version_id,
        )
        .order_by(EvidenceChunk.chunk_index)
    ).all()

    source_bytes = text.encode("utf-8")

    for chunk in persisted_chunks:
        assert source_bytes[
            chunk.normalized_start_byte : chunk.normalized_end_byte
        ] == chunk.content.encode("utf-8")


def test_result_ids_are_real_uuid_values(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Workspace A",
    )
    document = _create_document(
        db_session,
        workspace=workspace,
        user=user,
    )

    result = persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=_make_ingestion_result("UUID check"),
    )

    assert isinstance(result.document_id, uuid.UUID)
    assert isinstance(result.version_id, uuid.UUID)
    assert isinstance(result.ingestion_attempt_id, uuid.UUID)
