"""SQLite tests for deterministic questionnaire evidence grounding."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.evidence.citations.service import citation_from_candidate
from app.evidence.persistence.search_repository import list_search_candidates
from app.evidence.search.service import search_chunks
from app.models import EvidenceChunk, EvidenceDocument, EvidenceDocumentVersion
from app.questionnaires.grounding.errors import (
    GroundingQueryValidationError,
    GroundingQuestionNotFoundError,
)
from app.questionnaires.grounding.policy import GroundingPolicy
from app.questionnaires.grounding.service import (
    ground_question,
    normalize_grounding_query,
)
from app.questionnaires.grounding.types import GroundingStatus
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.persistence.service import persist_import
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)
from app.questionnaires.service import build_question, build_questionnaire
from app.questionnaires.xlsx.types import XlsxImportResult
from tests.conftest import create_principal, create_workspace_with_owner


def _import_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    question_text: str,
    suffix: str,
) -> tuple[QuestionnaireVersion, QuestionnaireVersionQuestion]:
    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text=question_text,
        section_path=("Access",),
        source_question_id=f"Q-{suffix}",
    )
    result = persist_import(
        db,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        import_result=XlsxImportResult(
            questionnaire=build_questionnaire(
                name=f"Grounding questionnaire {suffix}",
                source_filename="grounding.xlsx",
                questions=(question,),
            ),
            imported_sheets=(),
            ignored_sheets=(),
        ),
        raw_data=f"grounding-{suffix}".encode(),
    )
    version = db.get(QuestionnaireVersion, result.version_id)
    assert version is not None
    version_question = db.scalar(
        select(QuestionnaireVersionQuestion).where(
            QuestionnaireVersionQuestion.questionnaire_version_id == version.id,
        )
    )
    assert version_question is not None
    return version, version_question


def _create_chunk(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    content: str,
    suffix: str,
    section_label: str | None = "Access Control",
    page_number: int | None = 4,
) -> EvidenceChunk:
    content_bytes = content.encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    document = EvidenceDocument(
        workspace_id=workspace_id,
        name=f"Grounding evidence {suffix}",
        source_type="file",
        status="active",
        created_by_user_id=actor_user_id,
    )
    db.add(document)
    db.flush()
    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        normalized_sha256=content_hash,
        raw_sha256=content_hash,
        normalization_version="text-v1",
        original_filename=f"grounding-{suffix}.md",
        media_type="text/markdown",
        raw_size_bytes=len(content_bytes),
        normalized_size_bytes=len(content_bytes),
        extracted_text=content,
        chunking_version="text-chunk-v1",
        chunk_target_bytes=4096,
        chunk_overlap_bytes=512,
        created_by_user_id=actor_user_id,
    )
    db.add(version)
    db.flush()
    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=content,
        content_hash=content_hash,
        normalized_start_byte=0,
        normalized_end_byte=len(content_bytes),
        section_label=section_label,
        page_number=page_number,
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk


def _count(db: Session, model: type[object]) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_grounding_query_normalization_is_deterministic() -> None:
    query = normalize_grounding_query("  MFA\u00a0is\t required  ")

    assert query == "MFA is required"
    assert normalize_grounding_query("  MFA\u00a0is\t required  ") == query


def test_grounding_matches_with_exact_provenance_and_repeats_identically(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-match@example.com",
        display_name="Grounding Match",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding Match Workspace",
    )
    version, question = _import_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required for privileged access",
        suffix="match",
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="MFA is required for privileged access.",
        suffix="match",
    )

    first = ground_question(
        db_session,
        workspace_id=workspace.id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=question.id,
    )
    second = ground_question(
        db_session,
        workspace_id=workspace.id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=question.id,
    )

    assert first == second
    assert first.status is GroundingStatus.MATCHED
    assert first.normalized_query == "MFA is required for privileged access"
    assert first.search_version == "hybrid-rrf-v1"
    assert len(first.results) == 1
    assert first.results[0].candidate.chunk_id == chunk.id
    assert first.citations == (
        citation_from_candidate(first.results[0].candidate, workspace_id=workspace.id),
    )
    assert first.citations[0].chunk_id == chunk.id
    assert first.citations[0].content_hash == chunk.content_hash
    assert first.citations[0].normalized_start_byte == chunk.normalized_start_byte
    assert first.citations[0].normalized_end_byte == chunk.normalized_end_byte
    assert first.citations[0].section_label == chunk.section_label
    assert first.citations[0].page_number == chunk.page_number


def test_grounding_returns_no_matches_without_fabricating_citations(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-empty@example.com",
        display_name="Grounding Empty",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding Empty Workspace",
    )
    version, question = _import_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="Quantum zebra governance control",
        suffix="empty",
    )

    result = ground_question(
        db_session,
        workspace_id=workspace.id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=question.id,
    )

    assert result.status is GroundingStatus.NO_MATCHES
    assert result.search_version == "hybrid-rrf-v1"
    assert result.results == ()
    assert result.citations == ()


def test_grounding_reuses_search_order_and_honors_result_limit(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-limit@example.com",
        display_name="Grounding Limit",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding Limit Workspace",
    )
    version, question = _import_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA required access",
        suffix="limit",
    )
    exact = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="MFA required access",
        suffix="exact",
    )
    _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="MFA is required for access control",
        suffix="partial",
    )

    result = ground_question(
        db_session,
        workspace_id=workspace.id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=question.id,
        policy=GroundingPolicy(default_limit=1),
    )
    candidates = list_search_candidates(db_session, workspace_id=workspace.id)

    assert result.result_limit == 1
    assert len(result.results) == 1
    assert result.results[0].candidate.chunk_id == exact.id
    assert [item.candidate.chunk_id for item in result.results] == [
        item.candidate.chunk_id
        for item in search_chunks(
            candidates,
            result.normalized_query,
            limit=1,
        )
    ]
    assert [citation.chunk_id for citation in result.citations] == [exact.id]

    ordered = ground_question(
        db_session,
        workspace_id=workspace.id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=question.id,
        result_limit=2,
    )
    expected = search_chunks(
        candidates,
        ordered.normalized_query,
        limit=2,
    )
    assert [item.candidate.chunk_id for item in ordered.results] == [
        item.candidate.chunk_id for item in expected
    ]
    assert [citation.chunk_id for citation in ordered.citations] == [
        item.candidate.chunk_id for item in expected
    ]


def test_grounding_rejects_invalid_question_and_version_relationships(
    db_session: Session,
) -> None:
    first = create_principal(
        db_session,
        email="grounding-first@example.com",
        display_name="Grounding First",
    )
    first_workspace = create_workspace_with_owner(
        db_session,
        first,
        name="Grounding First Workspace",
    )
    first_version, first_question = _import_question(
        db_session,
        workspace_id=first_workspace.id,
        actor_user_id=first.user.id,
        question_text="MFA control",
        suffix="first",
    )
    second = create_principal(
        db_session,
        email="grounding-second@example.com",
        display_name="Grounding Second",
    )
    second_workspace = create_workspace_with_owner(
        db_session,
        second,
        name="Grounding Second Workspace",
    )
    second_version, _ = _import_question(
        db_session,
        workspace_id=second_workspace.id,
        actor_user_id=second.user.id,
        question_text="Password control",
        suffix="second",
    )

    with pytest.raises(GroundingQuestionNotFoundError):
        ground_question(
            db_session,
            workspace_id=first_workspace.id,
            questionnaire_version_id=second_version.id,
            questionnaire_version_question_id=first_question.id,
        )

    with pytest.raises(GroundingQuestionNotFoundError):
        ground_question(
            db_session,
            workspace_id=second_workspace.id,
            questionnaire_version_id=first_version.id,
            questionnaire_version_question_id=first_question.id,
        )

    with pytest.raises(GroundingQuestionNotFoundError):
        ground_question(
            db_session,
            workspace_id=first_workspace.id,
            questionnaire_version_id=first_version.id,
            questionnaire_version_question_id=uuid.uuid4(),
        )


def test_invalid_search_query_is_rejected_safely(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-invalid@example.com",
        display_name="Grounding Invalid",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding Invalid Workspace",
    )
    version, question = _import_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="?",
        suffix="invalid",
    )

    with pytest.raises(GroundingQueryValidationError):
        ground_question(
            db_session,
            workspace_id=workspace.id,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=question.id,
        )


def test_grounding_never_writes_response_tables(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="grounding-read-only@example.com",
        display_name="Grounding Read Only",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Grounding Read Only Workspace",
    )
    version, question = _import_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA evidence",
        suffix="read-only",
    )
    _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="MFA evidence is retained.",
        suffix="read-only",
    )

    before = (
        _count(db_session, QuestionnaireResponse),
        _count(db_session, QuestionnaireResponseRevision),
        _count(db_session, QuestionnaireResponseCitation),
    )
    result = ground_question(
        db_session,
        workspace_id=workspace.id,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=question.id,
    )
    after = (
        _count(db_session, QuestionnaireResponse),
        _count(db_session, QuestionnaireResponseRevision),
        _count(db_session, QuestionnaireResponseCitation),
    )

    assert result.status is GroundingStatus.MATCHED
    assert before == after == (0, 0, 0)
