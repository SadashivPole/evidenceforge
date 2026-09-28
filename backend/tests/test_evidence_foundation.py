"""Tests for the EvidenceForge evidence data model."""

from __future__ import annotations

import hashlib
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
    User,
    Workspace,
)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Create an isolated SQLite database for evidence-model tests."""

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
    email: str,
) -> User:
    user = User(
        external_subject=external_subject,
        email=email,
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


def _create_version(
    db: Session,
    *,
    document: EvidenceDocument,
    user: User,
    content: str,
    version_number: int = 1,
    filename: str = "policy.txt",
    media_type: str = "text/plain",
) -> EvidenceDocumentVersion:
    content_bytes = content.encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=version_number,
        normalized_sha256=content_hash,
        raw_sha256=content_hash,
        normalization_version="text-v1",
        original_filename=filename,
        media_type=media_type,
        raw_size_bytes=len(content_bytes),
        normalized_size_bytes=len(content_bytes),
        extracted_text=content,
        chunking_version="text-chunk-v1",
        chunk_target_bytes=4096,
        chunk_overlap_bytes=512,
        created_by_user_id=user.id,
    )

    db.add(version)
    db.flush()

    return version


def test_document_is_bound_to_workspace(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
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

    assert document.workspace_id == workspace.id


def test_version_belongs_to_document(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
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

    version = _create_version(
        db_session,
        document=document,
        user=user,
        content="hello",
        filename="security-policy.md",
        media_type="text/markdown",
    )
    db_session.commit()

    assert version.document_id == document.id
    assert version.version_number == 1
    assert version.normalized_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert version.raw_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert version.normalization_version == "text-v1"
    assert version.raw_size_bytes == 5
    assert version.normalized_size_bytes == 5
    assert version.chunking_version == "text-chunk-v1"
    assert version.chunk_target_bytes == 4096
    assert version.chunk_overlap_bytes == 512


def test_chunk_belongs_to_version(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
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

    version = _create_version(
        db_session,
        document=document,
        user=user,
        content="evidence",
    )

    content = "evidence"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=content,
        content_hash=content_hash,
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label=None,
        page_number=None,
    )
    db_session.add(chunk)
    db_session.commit()

    assert chunk.document_version_id == version.id
    assert chunk.chunk_index == 0
    assert chunk.normalized_start_byte == 0
    assert chunk.normalized_end_byte == 8


def test_duplicate_version_number_is_rejected(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
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

    first = _create_version(
        db_session,
        document=document,
        user=user,
        content="one",
        version_number=1,
    )
    db_session.commit()

    assert first.version_number == 1

    second = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        normalized_sha256=hashlib.sha256(b"two").hexdigest(),
        raw_sha256=hashlib.sha256(b"two").hexdigest(),
        normalization_version="text-v1",
        original_filename="policy.txt",
        media_type="text/plain",
        raw_size_bytes=3,
        normalized_size_bytes=3,
        extracted_text="two",
        chunking_version="text-chunk-v1",
        chunk_target_bytes=4096,
        chunk_overlap_bytes=512,
        created_by_user_id=user.id,
    )

    db_session.add(second)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_duplicate_chunk_index_is_rejected(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
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

    version = _create_version(
        db_session,
        document=document,
        user=user,
        content="content",
    )

    first_content = "first"
    second_content = "second"

    first = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=first_content,
        content_hash=hashlib.sha256(first_content.encode("utf-8")).hexdigest(),
        normalized_start_byte=0,
        normalized_end_byte=len(first_content.encode("utf-8")),
    )

    second = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=second_content,
        content_hash=hashlib.sha256(second_content.encode("utf-8")).hexdigest(),
        normalized_start_byte=0,
        normalized_end_byte=len(second_content.encode("utf-8")),
    )

    db_session.add(first)
    db_session.commit()

    db_session.add(second)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_normalized_and_raw_sha256_are_stored(
    db_session: Session,
) -> None:
    content = b"approved security policy"
    expected_hash = hashlib.sha256(content).hexdigest()

    user = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
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

    version = _create_version(
        db_session,
        document=document,
        user=user,
        content=content.decode("utf-8"),
        filename="security-policy.txt",
    )
    db_session.commit()

    stored = db_session.get(EvidenceDocumentVersion, version.id)

    assert stored is not None
    assert stored.normalized_sha256 == expected_hash
    assert stored.raw_sha256 == expected_hash
    assert len(stored.normalized_sha256) == 64
    assert len(stored.raw_sha256) == 64
    assert stored.raw_size_bytes == len(content)
    assert stored.normalized_size_bytes == len(content)


def test_workspace_ownership_is_distinct(
    db_session: Session,
) -> None:
    user_a = _create_user(
        db_session,
        external_subject="user-a",
        email="a@example.test",
    )
    user_b = _create_user(
        db_session,
        external_subject="user-b",
        email="b@example.test",
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

    document_a = _create_document(
        db_session,
        workspace=workspace_a,
        user=user_a,
        name="a-policy.md",
    )
    document_b = _create_document(
        db_session,
        workspace=workspace_b,
        user=user_b,
        name="b-policy.md",
    )

    assert document_a.workspace_id != document_b.workspace_id
    assert document_a.workspace_id == workspace_a.id
    assert document_b.workspace_id == workspace_b.id
    assert document_a.created_by_user_id == user_a.id
    assert document_b.created_by_user_id == user_b.id
