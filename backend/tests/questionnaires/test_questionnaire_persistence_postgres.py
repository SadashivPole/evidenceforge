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

from app.models import Base, User, Workspace
from app.questionnaires.persistence.models import (
    QuestionnaireImportAttempt,
    QuestionnaireVersion,
)
from app.questionnaires.persistence.service import persist_import
from app.questionnaires.service import build_question, build_questionnaire
from app.questionnaires.xlsx.types import XlsxImportResult

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
        return "requires EVIDENCEFORGE_TEST_DATABASE_URL to use a PostgreSQL backend"

    if not parsed_url.host:
        return "requires EVIDENCEFORGE_TEST_DATABASE_URL to include a PostgreSQL hostname"

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


def _build_import(question_text: str) -> XlsxImportResult:
    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text=question_text,
        section_path=("Access",),
        source_question_id="Q-001",
    )

    return XlsxImportResult(
        questionnaire=build_questionnaire(
            name="Concurrent Security",
            source_filename="concurrent.xlsx",
            questions=(question,),
        ),
        imported_sheets=(),
        ignored_sheets=(),
    )


def _principal(
    session: Session,
    *,
    suffix: str,
    workspace_name: str,
) -> tuple[User, Workspace]:
    user = User(
        external_subject=f"postgres:{suffix}",
        email=f"{suffix}@example.test",
        display_name=suffix,
    )
    session.add(user)
    session.flush()

    workspace = Workspace(
        name=workspace_name,
        created_by_user_id=user.id,
    )
    session.add(workspace)
    session.commit()

    return user, workspace


def _count(
    session: Session,
    model: type[object],
    *,
    workspace_id: uuid.UUID,
) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
        or 0
    )


def _test_suffix(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:12]}"


def test_concurrent_identical_import_produces_one_version() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    suffix = _test_suffix("identical")

    try:
        with factory() as setup:
            user, workspace = _principal(
                setup,
                suffix=f"{suffix}-a",
                workspace_name=f"Concurrent Identical Workspace {suffix}",
            )

            second_user = User(
                external_subject=f"postgres:{suffix}-b",
                email=f"{suffix}-b@example.test",
                display_name=f"{suffix}-b",
            )
            setup.add(second_user)
            setup.commit()

        barrier = Barrier(2)
        imported = _build_import("Do you use MFA?")

        def worker(actor_id: uuid.UUID) -> str:
            with factory() as session:
                barrier.wait()

                result = persist_import(
                    session,
                    workspace_id=workspace.id,
                    actor_user_id=actor_id,
                    import_result=imported,
                    raw_data=b"same-concurrent-bytes",
                )

                return result.outcome

        actor_ids = [user.id, second_user.id]

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(worker, actor_ids))

        with factory() as session:
            assert (
                _count(
                    session,
                    QuestionnaireVersion,
                    workspace_id=workspace.id,
                )
                == 1
            )
            assert (
                _count(
                    session,
                    QuestionnaireImportAttempt,
                    workspace_id=workspace.id,
                )
                == 2
            )
            assert sorted(outcomes) == [
                "CREATED",
                "IDENTICAL_DUPLICATE",
            ]
    finally:
        engine.dispose()


def test_concurrent_changed_imports_allocate_versions_serially() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    suffix = _test_suffix("changed")

    try:
        with factory() as setup:
            user, workspace = _principal(
                setup,
                suffix=f"{suffix}-a",
                workspace_name=f"Concurrent Changed Workspace {suffix}",
            )

            second_user = User(
                external_subject=f"postgres:{suffix}-b",
                email=f"{suffix}-b@example.test",
                display_name=f"{suffix}-b",
            )
            setup.add(second_user)
            setup.commit()

        barrier = Barrier(2)

        imports = [
            _build_import("Do you use MFA?"),
            _build_import("Do you require phishing-resistant MFA?"),
        ]

        def worker(
            args: tuple[uuid.UUID, XlsxImportResult, bytes],
        ) -> int:
            actor_id, imported, raw_data = args

            with factory() as session:
                barrier.wait()

                result = persist_import(
                    session,
                    workspace_id=workspace.id,
                    actor_user_id=actor_id,
                    import_result=imported,
                    raw_data=raw_data,
                )

                return result.version_number

        args = [
            (
                user.id,
                imports[0],
                b"changed-concurrent-a",
            ),
            (
                second_user.id,
                imports[1],
                b"changed-concurrent-b",
            ),
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            version_numbers = list(executor.map(worker, args))

        with factory() as session:
            versions = session.scalars(
                select(QuestionnaireVersion).where(
                    QuestionnaireVersion.workspace_id == workspace.id,
                )
            ).all()

            assert len(versions) == 2
            assert sorted(version_numbers) == [1, 2]
            assert sorted(version.version_number for version in versions) == [1, 2]
            assert (
                _count(
                    session,
                    QuestionnaireImportAttempt,
                    workspace_id=workspace.id,
                )
                == 2
            )
    finally:
        engine.dispose()
