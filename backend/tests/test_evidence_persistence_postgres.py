"""PostgreSQL concurrency tests for evidence ingestion persistence."""

from __future__ import annotations

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.evidence.chunking import chunk_ingestion_result
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

POSTGRES_URL = os.getenv("EVIDENCEFORGE_TEST_DATABASE_URL", "").strip()


def _postgres_configuration_reason(database_url: str) -> str | None:
    """Return an explicit skip reason for an unusable PostgreSQL test URL."""

    if not database_url:
        return (
            "requires EVIDENCEFORGE_TEST_DATABASE_URL pointing to a dedicated "
            "PostgreSQL test database; it is not configured"
        )

    try:
        parsed_url = make_url(database_url)
    except ArgumentError as exc:
        return (
            "requires EVIDENCEFORGE_TEST_DATABASE_URL to be a valid PostgreSQL "
            f"SQLAlchemy URL; the configured value could not be parsed ({exc})"
        )

    if parsed_url.get_backend_name() != "postgresql":
        return (
            "requires EVIDENCEFORGE_TEST_DATABASE_URL to use a PostgreSQL "
            "backend"
        )

    if not parsed_url.host:
        return (
            "requires EVIDENCEFORGE_TEST_DATABASE_URL to include a PostgreSQL "
            "hostname"
        )

    if not parsed_url.host.strip(".") or parsed_url.host.lower() in {
        "<host>",
        "<hostname>",
    }:
        return (
            "requires EVIDENCEFORGE_TEST_DATABASE_URL to contain a real "
            "PostgreSQL hostname; the configured value is a placeholder"
        )

    return None


POSTGRES_CONFIGURATION_REASON = _postgres_configuration_reason(POSTGRES_URL)

pytestmark = pytest.mark.skipif(
    POSTGRES_CONFIGURATION_REASON is not None,
    reason=POSTGRES_CONFIGURATION_REASON or "invalid PostgreSQL test configuration",
)


def _make_ingestion_result(
    text: str,
    *,
    raw_bytes: bytes | None = None,
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
        normalization_version="text-v1",
        normalized_text=text,
    )


def _create_fixture(
    session: Session,
    *,
    suffix: str,
) -> tuple[User, User, Workspace, EvidenceDocument]:
    first_user = User(
        external_subject=f"evidence-postgres-a:{suffix}",
        email=f"evidence-postgres-a-{suffix}@example.test",
        display_name="Evidence PostgreSQL A",
    )
    second_user = User(
        external_subject=f"evidence-postgres-b:{suffix}",
        email=f"evidence-postgres-b-{suffix}@example.test",
        display_name="Evidence PostgreSQL B",
    )
    session.add_all([first_user, second_user])
    session.flush()

    workspace = Workspace(
        name=f"Evidence PostgreSQL Workspace {suffix}",
        created_by_user_id=first_user.id,
    )
    session.add(workspace)
    session.flush()

    document = EvidenceDocument(
        workspace_id=workspace.id,
        name=f"security-policy-{suffix}.md",
        source_type="file",
        status="active",
        created_by_user_id=first_user.id,
    )
    session.add(document)
    session.commit()

    return first_user, second_user, workspace, document


def _test_suffix(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:12]}"


def _persist_concurrently(
    factory: sessionmaker[Session],
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    actor_ids: tuple[uuid.UUID, uuid.UUID],
    ingestions: tuple[IngestionResult, IngestionResult],
) -> list[tuple[IngestionOutcome, uuid.UUID, int, uuid.UUID]]:
    barrier = Barrier(2)

    def worker(
        args: tuple[uuid.UUID, IngestionResult],
    ) -> tuple[IngestionOutcome, uuid.UUID, int, uuid.UUID]:
        actor_id, ingestion = args
        with factory() as session:
            barrier.wait()
            result = persist_ingestion(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_id,
                document_id=document_id,
                ingestion_result=ingestion,
            )
            return (
                result.outcome,
                result.version_id,
                result.version_number,
                result.ingestion_attempt_id,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(worker, zip(actor_ids, ingestions, strict=True)))


def _assert_persisted_chunks(
    session: Session,
    *,
    version_id: uuid.UUID,
    ingestion: IngestionResult,
) -> None:
    persisted_chunks = session.scalars(
        select(EvidenceChunk)
        .where(EvidenceChunk.document_version_id == version_id)
        .order_by(EvidenceChunk.chunk_index)
    ).all()
    expected_chunks = chunk_ingestion_result(ingestion)

    assert len(persisted_chunks) == len(expected_chunks)
    assert [chunk.chunk_index for chunk in persisted_chunks] == list(
        range(len(expected_chunks))
    )

    source_bytes = ingestion.normalized_text.encode("utf-8")

    for persisted, expected in zip(persisted_chunks, expected_chunks, strict=True):
        assert persisted.content == expected.content
        assert persisted.content_hash == expected.content_hash
        assert persisted.normalized_start_byte == expected.normalized_start_byte
        assert persisted.normalized_end_byte == expected.normalized_end_byte
        assert persisted.normalized_end_byte > persisted.normalized_start_byte
        assert persisted.content_hash == hashlib.sha256(
            persisted.content.encode("utf-8")
        ).hexdigest()
        assert source_bytes[
            persisted.normalized_start_byte : persisted.normalized_end_byte
        ] == persisted.content.encode("utf-8")


def _count_for_document(
    session: Session,
    model: type[object],
    *,
    document_id: uuid.UUID,
) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.document_id == document_id)
        )
        or 0
    )


def test_concurrent_identical_ingestion_creates_one_version() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = _test_suffix("identical")
    ingestion = _make_ingestion_result(
        "# Access Control\n\nAuthentication requirements."
    )

    try:
        with factory() as setup:
            first_user, second_user, workspace, document = _create_fixture(
                setup,
                suffix=suffix,
            )

        results = _persist_concurrently(
            factory,
            workspace_id=workspace.id,
            document_id=document.id,
            actor_ids=(first_user.id, second_user.id),
            ingestions=(ingestion, ingestion),
        )

        with factory() as session:
            versions = session.scalars(
                select(EvidenceDocumentVersion)
                .where(EvidenceDocumentVersion.document_id == document.id)
                .order_by(EvidenceDocumentVersion.version_number)
            ).all()
            attempts = session.scalars(
                select(EvidenceIngestionAttempt)
                .where(EvidenceIngestionAttempt.document_id == document.id)
            ).all()
            events = session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.resource_type == "evidence_document_version",
                )
            ).all()

            assert sorted(result[0].value for result in results) == [
                IngestionOutcome.CREATED.value,
                IngestionOutcome.DUPLICATE.value,
            ]
            assert len(versions) == 1
            assert versions[0].version_number == 1
            assert versions[0].normalized_sha256 == ingestion.normalized_sha256
            assert {result[1] for result in results} == {versions[0].id}
            assert {result[2] for result in results} == {1}
            assert _count_for_document(
                session,
                EvidenceDocumentVersion,
                document_id=document.id,
            ) == 1

            _assert_persisted_chunks(
                session,
                version_id=versions[0].id,
                ingestion=ingestion,
            )

            assert len(attempts) == 2
            assert sorted(attempt.outcome for attempt in attempts) == [
                IngestionOutcome.CREATED.value,
                IngestionOutcome.DUPLICATE.value,
            ]
            assert {attempt.id for attempt in attempts} == {
                result[3] for result in results
            }
            assert {attempt.version_id for attempt in attempts} == {versions[0].id}
            assert {attempt.workspace_id for attempt in attempts} == {workspace.id}

            assert len(events) == 2
            assert sorted(event.action for event in events) == [
                "evidence.ingestion.created",
                "evidence.ingestion.duplicate",
            ]
            assert {event.resource_id for event in events} == {str(versions[0].id)}
            assert all(
                event.event_metadata["document_id"] == str(document.id)
                for event in events
            )
    finally:
        engine.dispose()


def test_concurrent_changed_ingestion_allocates_versions_serially() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = _test_suffix("changed")
    first_ingestion = _make_ingestion_result(
        "# Access Control\n\nAuthentication requirements."
    )
    second_ingestion = _make_ingestion_result(
        "# Access Control\n\nPhishing-resistant authentication requirements."
    )

    try:
        with factory() as setup:
            first_user, second_user, workspace, document = _create_fixture(
                setup,
                suffix=suffix,
            )

        results = _persist_concurrently(
            factory,
            workspace_id=workspace.id,
            document_id=document.id,
            actor_ids=(first_user.id, second_user.id),
            ingestions=(first_ingestion, second_ingestion),
        )

        with factory() as session:
            versions = session.scalars(
                select(EvidenceDocumentVersion)
                .where(EvidenceDocumentVersion.document_id == document.id)
                .order_by(EvidenceDocumentVersion.version_number)
            ).all()
            attempts = session.scalars(
                select(EvidenceIngestionAttempt)
                .where(EvidenceIngestionAttempt.document_id == document.id)
            ).all()
            events = session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.resource_type == "evidence_document_version",
                )
            ).all()

            assert [result[0] for result in results] == [
                IngestionOutcome.CREATED,
                IngestionOutcome.CREATED,
            ]
            assert sorted(result[2] for result in results) == [1, 2]
            assert len(versions) == 2
            assert [version.version_number for version in versions] == [1, 2]
            assert len({version.version_number for version in versions}) == 2
            assert {
                version.normalized_sha256 for version in versions
            } == {
                first_ingestion.normalized_sha256,
                second_ingestion.normalized_sha256,
            }

            ingestion_by_hash = {
                first_ingestion.normalized_sha256: first_ingestion,
                second_ingestion.normalized_sha256: second_ingestion,
            }
            for version in versions:
                _assert_persisted_chunks(
                    session,
                    version_id=version.id,
                    ingestion=ingestion_by_hash[version.normalized_sha256],
                )

            assert len(attempts) == 2
            assert {
                attempt.outcome for attempt in attempts
            } == {IngestionOutcome.CREATED.value}
            assert {attempt.id for attempt in attempts} == {
                result[3] for result in results
            }
            assert {attempt.version_id for attempt in attempts} == {
                version.id for version in versions
            }
            assert {attempt.workspace_id for attempt in attempts} == {workspace.id}
            assert len(events) == 2
            assert {
                event.action for event in events
            } == {"evidence.ingestion.created"}
            assert {event.resource_id for event in events} == {
                str(version.id) for version in versions
            }
            assert {event.workspace_id for event in events} == {workspace.id}
    finally:
        engine.dispose()


def test_concurrent_cross_workspace_access_does_not_persist_foreign_document() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = _test_suffix("isolation")
    ingestion = _make_ingestion_result("Workspace-isolated evidence.")

    try:
        with factory() as setup:
            first_user, second_user, workspace, document = _create_fixture(
                setup,
                suffix=suffix,
            )
            other_workspace = Workspace(
                name=f"Other Evidence PostgreSQL Workspace {suffix}",
                created_by_user_id=second_user.id,
            )
            setup.add(other_workspace)
            setup.commit()

        barrier = Barrier(2)

        def valid_worker() -> tuple[str, uuid.UUID]:
            with factory() as session:
                barrier.wait()
                result = persist_ingestion(
                    session,
                    workspace_id=workspace.id,
                    actor_user_id=first_user.id,
                    document_id=document.id,
                    ingestion_result=ingestion,
                )
                return result.outcome.value, result.version_id

        def cross_workspace_worker() -> str:
            with factory() as session:
                barrier.wait()
                with pytest.raises(EvidenceDocumentNotFoundError):
                    persist_ingestion(
                        session,
                        workspace_id=other_workspace.id,
                        actor_user_id=second_user.id,
                        document_id=document.id,
                        ingestion_result=ingestion,
                    )
                return "not_found"

        with ThreadPoolExecutor(max_workers=2) as executor:
            valid_future = executor.submit(valid_worker)
            cross_workspace_future = executor.submit(cross_workspace_worker)
            valid_result = valid_future.result()
            cross_workspace_result = cross_workspace_future.result()

        with factory() as session:
            versions = session.scalars(
                select(EvidenceDocumentVersion)
                .where(EvidenceDocumentVersion.document_id == document.id)
            ).all()
            attempts = session.scalars(
                select(EvidenceIngestionAttempt)
                .where(EvidenceIngestionAttempt.document_id == document.id)
            ).all()
            other_workspace_attempts = session.scalars(
                select(EvidenceIngestionAttempt).where(
                    EvidenceIngestionAttempt.workspace_id == other_workspace.id,
                )
            ).all()

            assert valid_result[0] == IngestionOutcome.CREATED.value
            assert cross_workspace_result == "not_found"
            assert len(versions) == 1
            assert versions[0].id == valid_result[1]
            assert len(attempts) == 1
            assert attempts[0].workspace_id == workspace.id
            assert other_workspace_attempts == []
            _assert_persisted_chunks(
                session,
                version_id=versions[0].id,
                ingestion=ingestion,
            )
    finally:
        engine.dispose()
