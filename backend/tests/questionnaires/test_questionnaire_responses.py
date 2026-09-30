"""SQLite and API tests for Phase 1K questionnaire responses."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.questionnaires.persistence.models import (
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.persistence.service import persist_import
from app.questionnaires.responses.errors import (
    EvidenceChunkCitationNotFoundError,
    ResponseValidationError,
)
from app.questionnaires.responses.models import (
    QuestionnaireResponse,
    QuestionnaireResponseCitation,
    QuestionnaireResponseRevision,
)
from app.questionnaires.responses.service import (
    save_response,
)
from app.questionnaires.service import build_question, build_questionnaire
from app.questionnaires.types import ResponseStatus
from app.questionnaires.xlsx.types import XlsxImportResult
from tests.conftest import auth_headers, create_principal, create_workspace_with_owner


def _import_one_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    questionnaire_name: str = "Response questionnaire",
    question_text: str = "Do you use multi-factor authentication?",
) -> tuple[QuestionnaireVersion, QuestionnaireVersionQuestion]:
    question = build_question(
        ordinal=1,
        sheet_name="Security",
        source_row=2,
        question_text=question_text,
        section_path=("Access",),
        source_question_id="Q-001",
    )
    result = persist_import(
        db,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        import_result=XlsxImportResult(
            questionnaire=build_questionnaire(
                name=questionnaire_name,
                source_filename="response.xlsx",
                questions=(question,),
            ),
            imported_sheets=(),
            ignored_sheets=(),
        ),
        raw_data=b"response-questionnaire",
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
) -> EvidenceChunk:
    document = EvidenceDocument(
        workspace_id=workspace_id,
        name=f"Policy {uuid.uuid4()}",
        source_type="file",
        status="active",
        created_by_user_id=actor_user_id,
    )
    db.add(document)
    db.flush()
    version = EvidenceDocumentVersion(
        document_id=document.id,
        version_number=1,
        normalized_sha256=hashlib.sha256(content.encode()).hexdigest(),
        raw_sha256=hashlib.sha256(content.encode()).hexdigest(),
        normalization_version="text-normalization-v1",
        original_filename="policy.md",
        media_type="text/markdown",
        raw_size_bytes=len(content.encode()),
        normalized_size_bytes=len(content.encode()),
        extracted_text=content,
        chunking_version="chunking-v1",
        chunk_target_bytes=1024,
        chunk_overlap_bytes=0,
        created_by_user_id=actor_user_id,
    )
    db.add(version)
    db.flush()
    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=0,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode()),
        section_label="Authentication",
        page_number=4,
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk


def test_response_revision_history_and_identical_update_are_idempotent(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="response-owner@example.com",
        display_name="Response Owner",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Response Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="Multi-factor authentication is required.",
    )
    second_chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="Authentication reviews are quarterly.",
    )

    first = save_response(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        actor_role=WorkspaceRole.OWNER,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=version_question.id,
        answer="Yes",
        status=ResponseStatus.PROPOSED,
        citation_chunk_ids=(second_chunk.id, chunk.id),
    )
    identical = save_response(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        actor_role=WorkspaceRole.OWNER,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=version_question.id,
        answer="Yes",
        status=ResponseStatus.PROPOSED,
        citation_chunk_ids=(second_chunk.id, chunk.id),
    )
    revised = save_response(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        actor_role=WorkspaceRole.OWNER,
        questionnaire_version_id=version.id,
        questionnaire_version_question_id=version_question.id,
        answer="No",
        status=ResponseStatus.NEEDS_REVIEW,
        citation_chunk_ids=(),
    )

    assert first.revision_created is True
    assert first.response.created_by_user_id == principal.user.id
    assert identical.revision_created is False
    assert identical.response.created_by_user_id == principal.user.id
    assert revised.revision_created is True
    assert revised.response.created_by_user_id == principal.user.id
    assert first.revision.revision_number == 1
    assert identical.revision.revision_number == 1
    assert revised.revision.revision_number == 2
    assert revised.citations == ()
    assert [citation.citation_order for citation in first.citations] == [1, 2]
    assert [citation.evidence_chunk_id for citation in first.citations] == [
        second_chunk.id,
        chunk.id,
    ]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(QuestionnaireResponse)
            .where(QuestionnaireResponse.workspace_id == workspace.id)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(QuestionnaireResponseRevision)
            .where(QuestionnaireResponseRevision.workspace_id == workspace.id)
        )
        == 2
    )


def test_status_citation_rules_and_workspace_chain_are_enforced(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="response-rules@example.com",
        display_name="Response Rules",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Response Rules Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
    )

    with pytest.raises(ResponseValidationError):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=principal.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer=None,
            status=ResponseStatus.PROPOSED,
        )

    with pytest.raises(ResponseValidationError):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=principal.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer="Yes",
            status=ResponseStatus.APPROVED,
        )

    one_chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        content="Only one conflicting source.",
    )
    with pytest.raises(ResponseValidationError):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=principal.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer="Conflicting",
            status=ResponseStatus.CONFLICTING_SOURCES,
            citation_chunk_ids=(one_chunk.id,),
        )

    other = create_principal(
        db_session,
        email="other-response-owner@example.com",
        display_name="Other Response Owner",
    )
    other_workspace = create_workspace_with_owner(
        db_session,
        other,
        name="Other Response Workspace",
    )
    foreign_chunk = _create_chunk(
        db_session,
        workspace_id=other_workspace.id,
        actor_user_id=other.user.id,
        content="Foreign evidence.",
    )

    with pytest.raises(EvidenceChunkCitationNotFoundError):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=principal.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer="Yes",
            status=ResponseStatus.PROPOSED,
            citation_chunk_ids=(foreign_chunk.id,),
        )

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(QuestionnaireResponse)
            .where(QuestionnaireResponse.workspace_id == workspace.id)
        )
        == 0
    )


def test_response_api_enforces_roles_and_returns_server_resolved_citation_metadata(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(
        db_session,
        email="response-api-owner@example.com",
        display_name="Response API Owner",
    )
    workspace = create_workspace_with_owner(
        db_session,
        owner,
        name="Response API Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
    )
    chunk = _create_chunk(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
        content="Policy evidence.",
    )

    path = (
        f"/workspaces/{workspace.id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{version_question.id}/response"
    )
    response = client.put(
        path,
        headers=auth_headers(owner),
        json={
            "answer": "Yes",
            "status": "APPROVED",
            "citation_chunk_ids": [str(chunk.id)],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_by_user_id"] == str(owner.user.id)
    assert body["current_revision"]["revision_number"] == 1
    citation = body["current_revision"]["citations"][0]
    assert citation["evidence_chunk_id"] == str(chunk.id)
    assert citation["citation_order"] == 1
    assert citation["evidence_version_number"] == 1
    assert citation["evidence_chunk_index"] == 0
    assert citation["section_label"] == "Authentication"
    assert citation["page_number"] == 4

    revised = client.put(
        path,
        headers=auth_headers(owner),
        json={
            "answer": "No",
            "status": "NEEDS_REVIEW",
            "citation_chunk_ids": [],
        },
    )
    assert revised.status_code == 200
    assert revised.json()["current_revision"]["revision_number"] == 2
    assert revised.json()["current_revision"]["citations"] == []

    history = client.get(f"{path}/history", headers=auth_headers(owner))
    assert history.status_code == 200
    assert [item["revision_number"] for item in history.json()] == [1, 2]
    assert history.json()[0]["citations"][0]["evidence_chunk_id"] == str(chunk.id)
    assert history.json()[1]["citations"] == []

    collection = client.get(
        f"/workspaces/{workspace.id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/responses",
        headers=auth_headers(owner),
    )
    assert collection.status_code == 200
    assert len(collection.json()) == 1
    assert collection.json()[0]["current_revision"]["revision_number"] == 2

    standalone_citations = client.get(
        f"{path}/citations",
        headers=auth_headers(owner),
    )
    assert standalone_citations.status_code == 404

    viewer = create_principal(
        db_session,
        email="response-api-viewer@example.com",
        display_name="Response API Viewer",
    )
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=viewer.user.id,
            role=WorkspaceRole.VIEWER,
        )
    )
    db_session.commit()

    read = client.get(path, headers=auth_headers(viewer))
    assert read.status_code == 200
    assert len(read.json()["revisions"]) == 2
    assert [item["revision_number"] for item in read.json()["revisions"]] == [1, 2]

    denied = client.put(
        path,
        headers=auth_headers(viewer),
        json={"answer": "No", "status": "NEEDS_REVIEW"},
    )
    assert denied.status_code == 403


def test_failed_citation_mutation_rolls_back_response_and_revision(
    db_session: Session,
) -> None:
    principal = create_principal(
        db_session,
        email="response-rollback@example.com",
        display_name="Response Rollback",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Response Rollback Workspace",
    )
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
    )
    missing_chunk_id = uuid.uuid4()

    with pytest.raises(EvidenceChunkCitationNotFoundError):
        save_response(
            db_session,
            workspace_id=workspace.id,
            actor_user_id=principal.user.id,
            actor_role=WorkspaceRole.OWNER,
            questionnaire_version_id=version.id,
            questionnaire_version_question_id=version_question.id,
            answer="Yes",
            status=ResponseStatus.PROPOSED,
            citation_chunk_ids=(missing_chunk_id,),
        )

    assert db_session.scalar(select(func.count()).select_from(QuestionnaireResponse)) == 0
    assert db_session.scalar(select(func.count()).select_from(QuestionnaireResponseRevision)) == 0
    assert db_session.scalar(select(func.count()).select_from(QuestionnaireResponseCitation)) == 0
