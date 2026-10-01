"""Deterministic tests for evidence candidate freshness and conflict metadata enrichment."""

from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas import QuestionnaireGroundingResponse
from app.evidence.hybrid.service import HybridRetriever
from app.evidence.persistence.search_repository import list_search_candidates
from app.evidence.search.types import SearchChunkCandidate
from app.evidence.semantic.service import SemanticRetriever
from app.evidence.semantic.types import SemanticSearchResult
from app.models import (
    EvidenceChunk,
    EvidenceDocument,
    EvidenceDocumentVersion,
)
from tests.conftest import auth_headers, create_principal, create_workspace_with_owner
from tests.questionnaires.test_questionnaire_responses import _import_one_question
from tests.test_evidence_semantic_retrieval import (
    _FakeSemanticModel,
    _persist_chunk_embedding,
    _unit_vector,
)


def _create_versioned_evidence(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_name: str,
    versions: list[tuple[int, str]],  # [(version_number, content), ...]
    status: str = "active",
) -> tuple[EvidenceDocument, list[tuple[EvidenceDocumentVersion, EvidenceChunk]]]:
    """Create one evidence document with one or more sequential immutable versions."""

    doc = EvidenceDocument(
        workspace_id=workspace_id,
        name=document_name,
        source_type="file",
        status=status,
        created_by_user_id=actor_user_id,
    )
    db.add(doc)
    db.flush()

    results: list[tuple[EvidenceDocumentVersion, EvidenceChunk]] = []

    for ver_num, content in versions:
        content_bytes = content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        version = EvidenceDocumentVersion(
            document_id=doc.id,
            version_number=ver_num,
            normalized_sha256=content_hash,
            raw_sha256=content_hash,
            normalization_version="text-v1",
            original_filename=f"{document_name}_v{ver_num}.md",
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
            section_label=f"Section v{ver_num}",
            page_number=ver_num,
        )
        db.add(chunk)
        db.flush()
        results.append((version, chunk))

    db.commit()
    for _, chk in results:
        db.refresh(chk)
    db.refresh(doc)
    return doc, results


def test_freshness_single_version_document_is_latest(db_session: Session) -> None:
    """Requirement 4: A document with only Version 1 returns is_latest_document_version = True."""

    principal = create_principal(
        db_session,
        email="freshness-single@example.com",
        display_name="Freshness Single",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Freshness Single Workspace",
    )
    doc, ver_chunks = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Single Version Policy",
        versions=[(1, "Access control requires MFA for all users.")],
    )
    _, chunk_v1 = ver_chunks[0]

    candidates = list_search_candidates(db_session, workspace_id=workspace.id)
    assert len(candidates) == 1
    cand = candidates[0]

    assert cand.chunk_id == chunk_v1.id
    assert cand.document_id == doc.id
    assert cand.document_version_number == 1
    assert cand.version_number == 1
    assert cand.latest_document_version_number == 1
    assert cand.is_latest_document_version is True
    assert cand.document_status == "active"
    assert cand.conflict_group_id is None


def test_freshness_multi_version_document_distinguishes_stale_and_latest(
    db_session: Session,
) -> None:
    """Requirements 1 & 2: Version 1 returns is_latest = False when Version 2 exists."""

    principal = create_principal(
        db_session,
        email="freshness-multi@example.com",
        display_name="Freshness Multi",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Freshness Multi Workspace",
    )
    doc, ver_chunks = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Password Policy",
        versions=[
            (1, "Passwords must be changed every 90 days."),
            (2, "Periodic password rotation is deprecated. MFA is mandatory."),
        ],
    )
    _, chunk_v1 = ver_chunks[0]
    _, chunk_v2 = ver_chunks[1]

    candidates = list_search_candidates(db_session, workspace_id=workspace.id)
    assert len(candidates) == 2

    cand_map = {c.version_number: c for c in candidates}

    cand_1 = cand_map[1]
    assert cand_1.chunk_id == chunk_v1.id
    assert cand_1.document_id == doc.id
    assert cand_1.document_version_number == 1
    assert cand_1.version_number == 1
    assert cand_1.latest_document_version_number == 2
    assert cand_1.is_latest_document_version is False
    assert cand_1.document_status == "active"
    assert cand_1.conflict_group_id is None

    cand_2 = cand_map[2]
    assert cand_2.chunk_id == chunk_v2.id
    assert cand_2.document_id == doc.id
    assert cand_2.document_version_number == 2
    assert cand_2.version_number == 2
    assert cand_2.latest_document_version_number == 2
    assert cand_2.is_latest_document_version is True
    assert cand_2.document_status == "active"
    assert cand_2.conflict_group_id is None


def test_freshness_multiple_documents_independently_evaluated(
    db_session: Session,
) -> None:
    """Requirement 3: Multiple documents in the same workspace are evaluated independently."""

    principal = create_principal(
        db_session,
        email="freshness-indep@example.com",
        display_name="Freshness Independent",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Freshness Independent Workspace",
    )

    # Document A has 3 versions
    doc_a, _ = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Document A",
        versions=[
            (1, "Doc A version 1 content."),
            (2, "Doc A version 2 content."),
            (3, "Doc A version 3 content."),
        ],
    )

    # Document B has 1 version
    doc_b, _ = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Document B",
        versions=[(1, "Doc B version 1 content.")],
    )

    # Document C has 2 versions
    doc_c, _ = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Document C",
        versions=[
            (1, "Doc C version 1 content."),
            (2, "Doc C version 2 content."),
        ],
    )

    candidates = list_search_candidates(db_session, workspace_id=workspace.id)
    assert len(candidates) == 6

    by_doc_and_ver = {(c.document_id, c.version_number): c for c in candidates}

    # Doc A checks
    assert by_doc_and_ver[(doc_a.id, 1)].latest_document_version_number == 3
    assert by_doc_and_ver[(doc_a.id, 1)].is_latest_document_version is False
    assert by_doc_and_ver[(doc_a.id, 2)].latest_document_version_number == 3
    assert by_doc_and_ver[(doc_a.id, 2)].is_latest_document_version is False
    assert by_doc_and_ver[(doc_a.id, 3)].latest_document_version_number == 3
    assert by_doc_and_ver[(doc_a.id, 3)].is_latest_document_version is True

    # Doc B checks
    assert by_doc_and_ver[(doc_b.id, 1)].latest_document_version_number == 1
    assert by_doc_and_ver[(doc_b.id, 1)].is_latest_document_version is True

    # Doc C checks
    assert by_doc_and_ver[(doc_c.id, 1)].latest_document_version_number == 2
    assert by_doc_and_ver[(doc_c.id, 1)].is_latest_document_version is False
    assert by_doc_and_ver[(doc_c.id, 2)].latest_document_version_number == 2
    assert by_doc_and_ver[(doc_c.id, 2)].is_latest_document_version is True


def test_freshness_cross_workspace_metadata_cannot_leak(db_session: Session) -> None:
    """Requirement 6: Documents in workspace B cannot affect workspace A version metadata."""

    owner_a = create_principal(
        db_session,
        email="freshness-ws-a@example.com",
        display_name="Freshness WS A",
    )
    owner_b = create_principal(
        db_session,
        email="freshness-ws-b@example.com",
        display_name="Freshness WS B",
    )
    workspace_a = create_workspace_with_owner(
        db_session,
        owner_a,
        name="Freshness Workspace A",
    )
    workspace_b = create_workspace_with_owner(
        db_session,
        owner_b,
        name="Freshness Workspace B",
    )

    # Workspace A has Document with 1 version
    doc_a, _ = _create_versioned_evidence(
        db_session,
        workspace_id=workspace_a.id,
        actor_user_id=owner_a.user.id,
        document_name="Shared Policy Name",
        versions=[(1, "Workspace A policy version 1.")],
    )

    # Workspace B has Document with 5 versions
    doc_b, _ = _create_versioned_evidence(
        db_session,
        workspace_id=workspace_b.id,
        actor_user_id=owner_b.user.id,
        document_name="Shared Policy Name",
        versions=[(i, f"Workspace B policy version {i}.") for i in range(1, 6)],
    )

    # Querying workspace A candidates
    cands_a = list_search_candidates(db_session, workspace_id=workspace_a.id)
    assert len(cands_a) == 1
    assert cands_a[0].document_id == doc_a.id
    assert cands_a[0].latest_document_version_number == 1
    assert cands_a[0].is_latest_document_version is True

    # Querying workspace B candidates
    cands_b = list_search_candidates(db_session, workspace_id=workspace_b.id)
    assert len(cands_b) == 5
    assert all(c.document_id == doc_b.id for c in cands_b)
    assert all(c.latest_document_version_number == 5 for c in cands_b)


def test_freshness_semantic_search_candidates_carry_freshness(
    db_session: Session,
) -> None:
    """Verify semantic candidates carry authoritative freshness metadata."""

    principal = create_principal(
        db_session,
        email="freshness-sem@example.com",
        display_name="Freshness Semantic",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Freshness Semantic Workspace",
    )
    doc, ver_chunks = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Encryption Policy",
        versions=[
            (1, "Data encryption standard using AES-128 keys."),
            (2, "Data encryption standard updated to AES-256 GCM."),
        ],
    )
    _, chunk_v1 = ver_chunks[0]
    _, chunk_v2 = ver_chunks[1]

    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_v1,
        vector=_unit_vector(0),
    )
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_v2,
        vector=_unit_vector(1),
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)

    sem_results = sem_retriever.search(
        db_session,
        query="Data encryption standard",
        workspace_id=workspace.id,
    )

    assert len(sem_results) == 2
    sem_map = {r.version_number: r for r in sem_results}

    assert sem_map[1].latest_document_version_number == 2
    assert sem_map[1].is_latest_document_version is False
    assert sem_map[1].document_status == "active"
    assert sem_map[1].conflict_group_id is None

    assert sem_map[2].latest_document_version_number == 2
    assert sem_map[2].is_latest_document_version is True
    assert sem_map[2].document_status == "active"
    assert sem_map[2].conflict_group_id is None

    # Check to_search_candidate conversion
    cand_1 = sem_map[1].to_search_candidate()
    assert cand_1.is_latest_document_version is False
    assert cand_1.latest_document_version_number == 2


def test_freshness_hybrid_retriever_preserves_freshness_metadata(
    db_session: Session,
) -> None:
    """Requirements 7 & 8: Hybrid RRF search preserves freshness metadata and ordering."""

    principal = create_principal(
        db_session,
        email="freshness-hyb@example.com",
        display_name="Freshness Hybrid",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Freshness Hybrid Workspace",
    )
    doc, ver_chunks = _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="Access Control Standards",
        versions=[
            (1, "Access control policy with password rules."),
            (2, "Access control policy with biometric rules."),
        ],
    )
    _, chunk_v1 = ver_chunks[0]
    _, chunk_v2 = ver_chunks[1]

    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_v1,
        vector=_unit_vector(0),
    )
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_v2,
        vector=_unit_vector(1),
    )

    model = _FakeSemanticModel()
    sem_retriever = SemanticRetriever(model=model)
    retriever = HybridRetriever(semantic_retriever=sem_retriever)

    report = retriever.search_with_report(
        db_session,
        query="Access control policy",
        workspace_id=workspace.id,
    )

    assert len(report.results) == 2
    res_map = {r.version_number: r for r in report.results}

    assert res_map[1].document_version_number == 1
    assert res_map[1].latest_document_version_number == 2
    assert res_map[1].is_latest_document_version is False
    assert res_map[1].document_status == "active"
    assert res_map[1].conflict_group_id is None

    assert res_map[2].document_version_number == 2
    assert res_map[2].latest_document_version_number == 2
    assert res_map[2].is_latest_document_version is True
    assert res_map[2].document_status == "active"
    assert res_map[2].conflict_group_id is None


def test_freshness_grounding_api_exposes_freshness_metadata(
    client: TestClient,
    db_session: Session,
) -> None:
    """Requirement 10: Grounding API serialization exposes freshness metadata."""

    principal = create_principal(
        db_session,
        email="freshness-api@example.com",
        display_name="Freshness API",
    )
    workspace = create_workspace_with_owner(
        db_session,
        principal,
        name="Freshness API Workspace",
    )
    version, question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        question_text="MFA is required for privileged access",
    )
    _create_versioned_evidence(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=principal.user.id,
        document_name="MFA Document",
        versions=[
            (1, "MFA is required for privileged access v1."),
            (2, "MFA is required for privileged access v2."),
        ],
    )

    url = (
        f"/workspaces/{workspace.id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{question.id}/grounding"
    )

    response = client.get(url, headers=auth_headers(principal))
    assert response.status_code == 200

    body = response.json()
    validated = QuestionnaireGroundingResponse.model_validate(body)

    assert validated.status.value == "MATCHED"
    assert len(validated.results) == 2

    res_map = {r.candidate.version_number: r.candidate for r in validated.results}

    cand_1 = res_map[1]
    assert cand_1.document_version_number == 1
    assert cand_1.latest_document_version_number == 2
    assert cand_1.is_latest_document_version is False
    assert cand_1.document_status == "active"
    assert cand_1.conflict_group_id is None

    cand_2 = res_map[2]
    assert cand_2.document_version_number == 2
    assert cand_2.latest_document_version_number == 2
    assert cand_2.is_latest_document_version is True
    assert cand_2.document_status == "active"
    assert cand_2.conflict_group_id is None


def test_freshness_candidate_fallback_and_properties_without_database() -> None:
    """Requirement 5: Test candidate dataclass behavior and defaults."""

    chunk_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()

    # When latest_document_version_number is supplied, is_latest is derived automatically
    cand = SearchChunkCandidate(
        chunk_id=chunk_id,
        document_id=doc_id,
        version_id=ver_id,
        version_number=1,
        chunk_index=0,
        content="Sample content",
        content_hash="abc",
        normalized_start_byte=0,
        normalized_end_byte=14,
        section_label=None,
        page_number=None,
        latest_document_version_number=2,
    )
    assert cand.document_version_number == 1
    assert cand.is_latest_document_version is False
    assert cand.document_status == "active"
    assert cand.conflict_group_id is None

    # When version_number equals latest_document_version_number
    cand_latest = SearchChunkCandidate(
        chunk_id=chunk_id,
        document_id=doc_id,
        version_id=ver_id,
        version_number=2,
        chunk_index=0,
        content="Sample content",
        content_hash="abc",
        normalized_start_byte=0,
        normalized_end_byte=14,
        section_label=None,
        page_number=None,
        latest_document_version_number=2,
    )
    assert cand_latest.is_latest_document_version is True

    # SemanticSearchResult conversion
    sem_res = SemanticSearchResult(
        chunk_id=chunk_id,
        document_id=doc_id,
        version_id=ver_id,
        version_number=1,
        chunk_index=0,
        content="Sample content",
        content_hash="abc",
        normalized_start_byte=0,
        normalized_end_byte=14,
        section_label=None,
        page_number=None,
        workspace_id=uuid.uuid4(),
        distance=0.1,
        similarity=0.9,
        model_id="model",
        model_version="v1",
        configuration_hash="hash",
        embedding_dimension=384,
        latest_document_version_number=3,
    )
    assert sem_res.is_latest_document_version is False
    assert sem_res.latest_document_version_number == 3
    converted = sem_res.to_search_candidate()
    assert converted.is_latest_document_version is False
    assert converted.latest_document_version_number == 3
