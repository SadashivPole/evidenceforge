"""PostgreSQL concurrency and integrity tests for Phase 1K responses."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, User, Workspace, WorkspaceRole
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.persistence.service import persist_import
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseRevision,
)
from app.questionnaires.responses.service import save_response
from app.questionnaires.service import build_question, build_questionnaire
from app.questionnaires.types import ResponseStatus
from app.questionnaires.xlsx.types import XlsxImportResult

POSTGRES_URL = os.getenv("EVIDENCEFORGE_TEST_DATABASE_URL", "").strip()


def _postgres_reason(database_url: str) -> str | None:
    if not database_url:
        return "requires EVIDENCEFORGE_TEST_DATABASE_URL for PostgreSQL"
    try:
        parsed = make_url(database_url)
    except ArgumentError:
        return "EVIDENCEFORGE_TEST_DATABASE_URL is not a valid SQLAlchemy URL"
    if parsed.get_backend_name() != "postgresql" or not parsed.host:
        return "EVIDENCEFORGE_TEST_DATABASE_URL must identify PostgreSQL"
    return None


pytestmark = pytest.mark.skipif(
    _postgres_reason(POSTGRES_URL) is not None,
    reason=(
        _postgres_reason(POSTGRES_URL)
        or "PostgreSQL test configuration is invalid"
    ),
)


def _create_fixture(
    session: Session,
) -> tuple[User, User, Workspace, QuestionnaireVersionQuestion]:
    suffix = uuid.uuid4().hex
    first = User(
        external_subject=f"response-postgres-a:{suffix}",
        email=f"response-postgres-a-{suffix}@example.test",
        display_name="Response PostgreSQL A",
    )
    second = User(
        external_subject=f"response-postgres-b:{suffix}",
        email=f"response-postgres-b-{suffix}@example.test",
        display_name="Response PostgreSQL B",
    )
    session.add_all([first, second])
    session.flush()
    workspace = Workspace(
        name=f"Response PostgreSQL {suffix}",
        created_by_user_id=first.id,
    )
    session.add(workspace)
    session.commit()

    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text="Do you use MFA?",
        section_path=(),
        source_question_id="Q-001",
    )
    result = persist_import(
        session,
        workspace_id=workspace.id,
        actor_user_id=first.id,
        import_result=XlsxImportResult(
            questionnaire=build_questionnaire(
                name=f"PostgreSQL responses {suffix}",
                source_filename="responses.xlsx",
                questions=(question,),
            ),
            imported_sheets=(),
            ignored_sheets=(),
        ),
        raw_data=b"postgres-response-fixture",
    )
    version = session.get(QuestionnaireVersion, result.version_id)
    assert version is not None
    version_question = session.scalar(
        select(QuestionnaireVersionQuestion).where(
            QuestionnaireVersionQuestion.questionnaire_version_id == version.id,
        )
    )
    assert version_question is not None
    return first, second, workspace, version_question


def test_concurrent_identical_response_writes_create_one_revision() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)

    try:
        with factory() as setup:
            first, second, workspace, question = _create_fixture(setup)

        def worker(actor_id: uuid.UUID) -> int:
            with factory() as session:
                barrier.wait()
                result = save_response(
                    session,
                    workspace_id=workspace.id,
                    actor_user_id=actor_id,
                    actor_role=WorkspaceRole.MEMBER,
                    questionnaire_version_id=question.questionnaire_version_id,
                    questionnaire_version_question_id=question.id,
                    answer="Yes",
                    status=ResponseStatus.PROPOSED,
                )
                return result.revision.revision_number

        with ThreadPoolExecutor(max_workers=2) as executor:
            revision_numbers = list(executor.map(worker, [first.id, second.id]))

        with factory() as session:
            assert sorted(revision_numbers) == [1, 1]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(QuestionnaireResponse)
                    .where(
                        QuestionnaireResponse.workspace_id == workspace.id,
                    )
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(QuestionnaireResponseRevision)
                    .where(
                        QuestionnaireResponseRevision.workspace_id == workspace.id,
                    )
                )
                == 1
            )
    finally:
        engine.dispose()


def test_concurrent_changed_response_writes_allocate_distinct_revisions() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)

    try:
        with factory() as setup:
            first, second, workspace, question = _create_fixture(setup)

        writes = [(first.id, "Yes"), (second.id, "No")]

        def worker(args: tuple[uuid.UUID, str]) -> int:
            actor_id, answer = args
            with factory() as session:
                barrier.wait()
                result = save_response(
                    session,
                    workspace_id=workspace.id,
                    actor_user_id=actor_id,
                    actor_role=WorkspaceRole.MEMBER,
                    questionnaire_version_id=question.questionnaire_version_id,
                    questionnaire_version_question_id=question.id,
                    answer=answer,
                    status=ResponseStatus.PROPOSED,
                )
                return result.revision.revision_number

        with ThreadPoolExecutor(max_workers=2) as executor:
            revision_numbers = list(executor.map(worker, writes))

        with factory() as session:
            assert sorted(revision_numbers) == [1, 2]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(QuestionnaireResponseRevision)
                    .where(
                        QuestionnaireResponseRevision.workspace_id == workspace.id,
                    )
                )
                == 2
            )
    finally:
        engine.dispose()