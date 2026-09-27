"""Tests for the Phase 1C evidence data model."""

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

    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        content_hash=hashlib.sha256(b"hello").hexdigest(),
        original_filename="security-policy.md",
        media_type="text/markdown",
        byte_size=5,
        extracted_text="hello",
        created_by_user_id=user.id,
    )
    db_session.add(version)
    db_session.commit()

    assert version.document_id == document.id
    assert version.version_number == 1


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

    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        content_hash=hashlib.sha256(b"evidence").hexdigest(),
        original_filename="policy.txt",
        media_type="text/plain",
        byte_size=8,
        extracted_text="evidence",
        created_by_user_id=user.id,
    )
    db_session.add(version)
    db_session.flush()

    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content="evidence",
        content_hash=hashlib.sha256(b"evidence").hexdigest(),
    )
    db_session.add(chunk)
    db_session.commit()

    assert chunk.document_version_id == version.id
    assert chunk.chunk_index == 0


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

    first = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        content_hash=hashlib.sha256(b"one").hexdigest(),
        original_filename="policy.txt",
        media_type="text/plain",
        byte_size=3,
        extracted_text="one",
        created_by_user_id=user.id,
    )
    second = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        content_hash=hashlib.sha256(b"two").hexdigest(),
        original_filename="policy.txt",
        media_type="text/plain",
        byte_size=3,
        extracted_text="two",
        created_by_user_id=user.id,
    )

    db_session.add(first)
    db_session.commit()

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

    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        content_hash=hashlib.sha256(b"content").hexdigest(),
        original_filename="policy.txt",
        media_type="text/plain",
        byte_size=7,
        extracted_text="content",
        created_by_user_id=user.id,
    )
    db_session.add(version)
    db_session.flush()

    first = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content="first",
        content_hash=hashlib.sha256(b"first").hexdigest(),
    )
    second = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content="second",
        content_hash=hashlib.sha256(b"second").hexdigest(),
    )

    db_session.add(first)
    db_session.commit()

    db_session.add(second)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_sha256_content_hash_is_stored(
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

    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        content_hash=expected_hash,
        original_filename="security-policy.txt",
        media_type="text/plain",
        byte_size=len(content),
        extracted_text=content.decode("utf-8"),
        created_by_user_id=user.id,
    )
    db_session.add(version)
    db_session.commit()

    stored = db_session.get(EvidenceDocumentVersion, version.id)

    assert stored is not None
    assert stored.content_hash == expected_hash
    assert len(stored.content_hash) == 64


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
