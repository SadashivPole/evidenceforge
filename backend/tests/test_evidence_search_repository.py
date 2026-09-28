from __future__ import annotations

import hashlib
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.evidence.ingestion.types import IngestionResult
from app.evidence.persistence import persist_ingestion
from app.evidence.persistence.search_repository import list_search_candidates
from app.models import Base, EvidenceDocument, User, Workspace


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
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
    name: str,
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


def _make_ingestion(
    text: str,
    *,
    filename: str = "security-policy.md",
) -> IngestionResult:
    normalized_bytes = text.encode("utf-8")

    return IngestionResult(
        original_filename=filename,
        extension=".md",
        media_type="text/markdown",
        raw_size_bytes=len(normalized_bytes),
        normalized_size_bytes=len(normalized_bytes),
        raw_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        normalized_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        normalization_version="text-v1",
        normalized_text=text,
    )


def _persist(
    db: Session,
    *,
    workspace: Workspace,
    user: User,
    document: EvidenceDocument,
    text: str,
):
    return persist_ingestion(
        db,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=_make_ingestion(text),
    )


def test_search_repository_enforces_workspace_isolation(
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

    document_a = _create_document(
        db_session,
        workspace=workspace_a,
        user=user_a,
        name="workspace-a.md",
    )
    document_b = _create_document(
        db_session,
        workspace=workspace_b,
        user=user_b,
        name="workspace-b.md",
    )

    _persist(
        db_session,
        workspace=workspace_a,
        user=user_a,
        document=document_a,
        text="Workspace A private evidence.",
    )
    _persist(
        db_session,
        workspace=workspace_b,
        user=user_b,
        document=document_b,
        text="Workspace B private evidence.",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace_a.id,
    )

    assert candidates
    assert {candidate.document_id for candidate in candidates} == {document_a.id}
    assert all(candidate.content == "Workspace A private evidence." for candidate in candidates)


def test_search_repository_filters_by_document(
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

    document_a = _create_document(
        db_session,
        workspace=workspace,
        user=user,
        name="policy-a.md",
    )
    document_b = _create_document(
        db_session,
        workspace=workspace,
        user=user,
        name="policy-b.md",
    )

    _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document_a,
        text="Evidence from policy A.",
    )
    _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document_b,
        text="Evidence from policy B.",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace.id,
        document_id=document_a.id,
    )

    assert candidates
    assert {candidate.document_id for candidate in candidates} == {document_a.id}


def test_search_repository_filters_by_version(
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
        name="security-policy.md",
    )

    first = _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document,
        text="Version one evidence.",
    )
    second = _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document,
        text="Version two evidence.",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace.id,
        version_id=second.version_id,
    )

    assert candidates
    assert {candidate.version_id for candidate in candidates} == {second.version_id}
    assert all(candidate.version_number == 2 for candidate in candidates)
    assert all(candidate.version_id != first.version_id for candidate in candidates)


def test_search_repository_preserves_provenance_metadata(
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
        name="security-policy.md",
    )

    _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document,
        text="# Access Control\n\nMFA is required for privileged access.",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace.id,
    )

    assert candidates
    candidate = candidates[0]

    assert candidate.document_id == document.id
    assert candidate.version_number == 1
    assert candidate.chunk_index == 0
    assert candidate.content_hash
    assert candidate.normalized_start_byte == 0
    assert candidate.normalized_end_byte > candidate.normalized_start_byte
    assert candidate.section_label == "Access Control"
    assert candidate.page_number is None
    assert candidate.content.startswith("# Access Control")


def test_search_repository_order_is_deterministic(
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

    document_a = _create_document(
        db_session,
        workspace=workspace,
        user=user,
        name="policy-a.md",
    )
    document_b = _create_document(
        db_session,
        workspace=workspace,
        user=user,
        name="policy-b.md",
    )

    _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document_b,
        text="Policy B evidence.",
    )
    _persist(
        db_session,
        workspace=workspace,
        user=user,
        document=document_a,
        text="Policy A evidence.",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace.id,
    )

    actual_keys = [
        (
            candidate.document_id.hex,
            candidate.version_number,
            candidate.chunk_index,
            candidate.chunk_id.hex,
        )
        for candidate in candidates
    ]

    assert actual_keys == sorted(actual_keys)


def test_search_repository_returns_empty_tuple_for_empty_workspace(
    db_session: Session,
) -> None:
    user = _create_user(
        db_session,
        external_subject="user-a",
    )
    workspace = _create_workspace(
        db_session,
        user=user,
        name="Empty Workspace",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace.id,
    )

    assert candidates == ()


def test_search_repository_does_not_cross_workspace_for_version_filter(
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
        name="private.md",
    )

    version_b = _persist(
        db_session,
        workspace=workspace_b,
        user=user_b,
        document=document_b,
        text="Private workspace evidence.",
    )

    candidates = list_search_candidates(
        db_session,
        workspace_id=workspace_a.id,
        version_id=version_b.version_id,
    )

    assert candidates == ()
