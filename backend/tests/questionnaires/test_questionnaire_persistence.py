from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import AuditEvent, Base, User, Workspace
from app.questionnaires.persistence.models import (
    QuestionnaireImportAttempt,
    QuestionnaireQuestion,
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.persistence.service import (
    ImportOutcome,
    persist_import,
)
from app.questionnaires.service import build_question, build_questionnaire
from app.questionnaires.xlsx.types import XlsxImportResult


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        yield session

    engine.dispose()


def _create_principal(db: Session, *, suffix: str) -> tuple[User, Workspace]:
    user = User(
        external_subject=f"questionnaire:{suffix}",
        email=f"{suffix}@example.test",
        display_name=suffix,
    )
    db.add(user)
    db.flush()

    workspace = Workspace(
        name=f"Workspace {suffix}",
        created_by_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    db.commit()
    return user, workspace


def _import_result(
    *,
    source_filename: str = "security.xlsx",
    parser_version: str = "xlsx-import-v1",
    question_texts: tuple[str, ...] = (
        "Do you use MFA?",
        "Do you encrypt backups?",
    ),
    source_rows: tuple[int, ...] = (2, 3),
    source_ids: tuple[str | None, ...] = ("Q-001", "Q-002"),
) -> XlsxImportResult:
    questions = tuple(
        build_question(
            ordinal=index,
            sheet_name="Security",
            source_row=source_rows[index - 1],
            question_text=question_texts[index - 1],
            section_path=("Access",),
            source_question_id=source_ids[index - 1],
        )
        for index in range(1, len(question_texts) + 1)
    )

    questionnaire = build_questionnaire(
        name="Vendor Security",
        source_filename=source_filename,
        questions=questions,
    )

    questionnaire = replace(questionnaire, parser_version=parser_version)

    return XlsxImportResult(
        questionnaire=questionnaire,
        imported_sheets=(),
        ignored_sheets=(),
    )


def _count(db: Session, model: type[object]) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_first_import_creates_parent_version_questions_and_audit(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(db_session, suffix="first")

    result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=_import_result(),
        raw_data=b"xlsx-source-v1",
    )

    assert result.outcome == ImportOutcome.CREATED
    assert result.version_number == 1
    assert _count(db_session, QuestionnaireVersion) == 1
    assert _count(db_session, QuestionnaireQuestion) == 2
    assert _count(db_session, QuestionnaireVersionQuestion) == 2
    assert _count(db_session, QuestionnaireImportAttempt) == 1
    assert _count(db_session, AuditEvent) == 1


def test_identical_duplicate_does_not_create_a_new_version(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="identical",
    )
    first = _import_result()

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"same-source",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"same-source",
    )

    assert first_result.version_id == second_result.version_id
    assert second_result.outcome == ImportOutcome.IDENTICAL_DUPLICATE
    assert _count(db_session, QuestionnaireVersion) == 1
    assert _count(db_session, QuestionnaireImportAttempt) == 2


def test_raw_different_but_canonical_same_is_canonical_duplicate(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="canonical",
    )
    imported = _import_result()

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=imported,
        raw_data=b"xlsx-encoding-a",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=imported,
        raw_data=b"xlsx-encoding-b",
    )

    assert first_result.version_id == second_result.version_id
    assert second_result.outcome == ImportOutcome.CANONICAL_DUPLICATE
    assert _count(db_session, QuestionnaireVersion) == 1


def test_changed_canonical_representation_creates_version_two(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="changed",
    )

    first = _import_result(
        question_texts=(
            "Do you use MFA?",
            "Do you encrypt backups?",
        ),
    )

    second = _import_result(
        question_texts=(
            "Do you use MFA?",
            "Do you encrypt backups monthly?",
        ),
        source_ids=("Q-001", "Q-002"),
    )

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"version-one",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=second,
        raw_data=b"version-two",
    )

    assert first_result.version_number == 1
    assert second_result.version_number == 2
    assert _count(db_session, QuestionnaireVersion) == 2


def test_reordering_questions_preserves_logical_ids_but_creates_new_version(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="reorder",
    )

    first = _import_result(
        question_texts=("Question A", "Question B"),
        source_ids=("Q-001", "Q-002"),
    )

    reordered = _import_result(
        question_texts=("Question B", "Question A"),
        source_ids=("Q-002", "Q-001"),
        source_rows=(20, 30),
    )

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"order-one",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=reordered,
        raw_data=b"order-two",
    )

    assert first_result.version_number == 1
    assert second_result.version_number == 2

    question_rows = db_session.scalars(
        select(QuestionnaireQuestion).order_by(
            QuestionnaireQuestion.question_id,
        )
    ).all()

    assert {row.question_id for row in question_rows} == {
        first.questionnaire.questions[0].question_id,
        first.questionnaire.questions[1].question_id,
    }

    assert _count(db_session, QuestionnaireQuestion) == 2

    snapshots = db_session.scalars(
        select(QuestionnaireVersionQuestion)
        .where(
            QuestionnaireVersionQuestion.questionnaire_version_id
            == second_result.version_id
        )
        .order_by(QuestionnaireVersionQuestion.ordinal)
    ).all()

    assert [row.source_question_id for row in snapshots] == [
        "Q-002",
        "Q-001",
    ]


def test_source_row_change_does_not_create_new_semantic_version(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="source-row",
    )

    first = _import_result(source_rows=(2, 3))
    second = _import_result(source_rows=(200, 300))

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"source-row-a",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=second,
        raw_data=b"source-row-b",
    )

    assert first_result.version_id == second_result.version_id
    assert second_result.outcome == ImportOutcome.CANONICAL_DUPLICATE
    assert _count(db_session, QuestionnaireVersion) == 1


def test_filename_change_does_not_create_new_semantic_version(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="filename",
    )

    first = _import_result(source_filename="security-a.xlsx")
    second = _import_result(source_filename="security-b.xlsx")

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"filename-a",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=second,
        raw_data=b"filename-b",
    )

    assert first_result.version_id == second_result.version_id
    assert second_result.outcome == ImportOutcome.CANONICAL_DUPLICATE


def test_parser_version_change_with_same_canonical_representation_is_duplicate(
    db_session: Session,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="parser",
    )

    first = _import_result(parser_version="xlsx-import-v1")
    second = _import_result(parser_version="xlsx-import-v2")

    assert (
        first.questionnaire.normalized_questionnaire_sha256
        == second.questionnaire.normalized_questionnaire_sha256
    )

    first_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=first,
        raw_data=b"parser-v1",
    )

    second_result = persist_import(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        import_result=second,
        raw_data=b"parser-v2",
    )

    assert first_result.version_id == second_result.version_id
    assert second_result.outcome == ImportOutcome.CANONICAL_DUPLICATE


def test_workspace_isolation_allows_same_name_and_hash_in_different_workspaces(
    db_session: Session,
) -> None:
    user_a, workspace_a = _create_principal(
        db_session,
        suffix="workspace-a",
    )
    user_b, workspace_b = _create_principal(
        db_session,
        suffix="workspace-b",
    )

    imported = _import_result()

    result_a = persist_import(
        db_session,
        workspace_id=workspace_a.id,
        actor_user_id=user_a.id,
        import_result=imported,
        raw_data=b"same-bytes",
    )

    result_b = persist_import(
        db_session,
        workspace_id=workspace_b.id,
        actor_user_id=user_b.id,
        import_result=imported,
        raw_data=b"same-bytes",
    )

    assert result_a.questionnaire_id != result_b.questionnaire_id
    assert result_a.version_id != result_b.version_id
    assert _count(db_session, QuestionnaireVersion) == 2


def test_failed_audit_rolls_back_domain_writes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, workspace = _create_principal(
        db_session,
        suffix="rollback",
    )

    def fail_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(
        "app.questionnaires.persistence.service.record_audit_event",
        fail_audit,
    )

    with pytest.raises(RuntimeError, match="forced audit failure"):
        persist_import(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=user.id,
            import_result=_import_result(),
            raw_data=b"rollback",
        )

    assert _count(db_session, QuestionnaireVersion) == 0
    assert _count(db_session, QuestionnaireQuestion) == 0
    assert _count(db_session, QuestionnaireVersionQuestion) == 0
    assert _count(db_session, QuestionnaireImportAttempt) == 0
    assert _count(db_session, AuditEvent) == 0