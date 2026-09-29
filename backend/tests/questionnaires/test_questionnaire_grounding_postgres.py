"""PostgreSQL workspace-isolation test for questionnaire grounding."""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Base,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
    User,
    Workspace,
)
from app.questionnaires.grounding.errors import GroundingQuestionNotFoundError
from app.questionnaires.grounding.service import ground_question
from app.questionnaires.grounding.types import GroundingStatus
from app.questionnaires.persistence.models import (
    Questionnaire,
    QuestionnaireQuestion,
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)

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
    reason=_postgres_reason(POSTGRES_URL) or "invalid PostgreSQL test configuration",
)


def _create_question(
    db: Session,
    *,
    user: User,
    workspace: Workspace,
    suffix: str,
) -> tuple[QuestionnaireVersion, QuestionnaireVersionQuestion]:
    questionnaire = Questionnaire(
        workspace_id=workspace.id,
        name=f"Grounding PostgreSQL {suffix}",
        created_by_user_id=user.id,
    )
    db.add(questionnaire)
    db.flush()

    version = QuestionnaireVersion(
        workspace_id=workspace.id,
        questionnaire_id=questionnaire.id,
        version_number=1,
        raw_sha256=hashlib.sha256(f"raw-{suffix}".encode()).hexdigest(),
        canonical_sha256=hashlib.sha256(f"canonical-{suffix}".encode()).hexdigest(),
        source_filename="grounding.xlsx",
        parser_version="xlsx-import-v1",
        normalization_version="question-normalization-v1",
        question_identity_version="question-identity-v2",
        canonical_hash_version="questionnaire-hash-v1",
        question_count=1,
        created_by_user_id=user.id,
    )
    db.add(version)
    db.flush()

    logical_question = QuestionnaireQuestion(
        workspace_id=workspace.id,
        questionnaire_id=questionnaire.id,
        question_id=f"question-{suffix}",
        identity_fingerprint=f"fingerprint-{suffix}",
        identity_kind="explicit",
        normalized_question_text="MFA is required",
        normalized_sheet_name="Security",
        normalized_section_path=["Access"],
        source_question_id=f"Q-{suffix}",
    )
    db.add(logical_question)
    db.flush()

    version_question = QuestionnaireVersionQuestion(
        workspace_id=workspace.id,
        questionnaire_id=questionnaire.id,
        questionnaire_version_id=version.id,
        questionnaire_question_id=logical_question.id,
        ordinal=1,
        source_row=2,
        sheet_name="Security",
        section_path=["Access"],
        normalized_question_text="MFA is required",
        source_question_id=f"Q-{suffix}",
    )
    db.add(version_question)
    db.flush()
    return version, version_question


def _create_chunk(
    db: Session,
    *,
    user: User,
    workspace: Workspace,
    suffix: str,
) -> None:
    content = "MFA is required."
    content_bytes = content.encode()
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    document = EvidenceDocument(
        workspace_id=workspace.id,
        name=f"Grounding PostgreSQL evidence {suffix}",
        source_type="file",
        status="active",
        created_by_user_id=user.id,
    )
    db.add(document)
    db.flush()
    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        normalized_sha256=content_hash,
        raw_sha256=content_hash,
        normalization_version="text-v1",
        original_filename="grounding.md",
        media_type="text/markdown",
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
    db.add(
        EvidenceChunk(
            document_version_id=version.id,
            chunk_index=0,
            content=content,
            content_hash=content_hash,
            normalized_start_byte=0,
            normalized_end_byte=len(content_bytes),
            section_label="Access Control",
            page_number=4,
        )
    )


def test_grounding_is_workspace_scoped_on_postgres() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    suffix = uuid.uuid4().hex
    try:
        with factory() as session:
            user_a = User(
                external_subject=f"grounding-postgres-a:{suffix}",
                email=f"grounding-postgres-a-{suffix}@example.test",
                display_name="Grounding PostgreSQL A",
            )
            user_b = User(
                external_subject=f"grounding-postgres-b:{suffix}",
                email=f"grounding-postgres-b-{suffix}@example.test",
                display_name="Grounding PostgreSQL B",
            )
            session.add_all([user_a, user_b])
            session.flush()
            workspace_a = Workspace(
                name=f"Grounding PostgreSQL A {suffix}",
                created_by_user_id=user_a.id,
            )
            workspace_b = Workspace(
                name=f"Grounding PostgreSQL B {suffix}",
                created_by_user_id=user_b.id,
            )
            session.add_all([workspace_a, workspace_b])
            session.flush()
            version, question = _create_question(
                session,
                user=user_a,
                workspace=workspace_a,
                suffix=suffix,
            )
            _create_chunk(
                session,
                user=user_a,
                workspace=workspace_a,
                suffix=suffix,
            )
            session.commit()

            matched = ground_question(
                session,
                workspace_id=workspace_a.id,
                questionnaire_version_id=version.id,
                questionnaire_version_question_id=question.id,
            )
            assert matched.status is GroundingStatus.MATCHED

            with pytest.raises(GroundingQuestionNotFoundError):
                ground_question(
                    session,
                    workspace_id=workspace_b.id,
                    questionnaire_version_id=version.id,
                    questionnaire_version_question_id=question.id,
                )
    finally:
        engine.dispose()
