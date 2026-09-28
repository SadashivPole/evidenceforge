"""Workspace-scoped evidence document and version routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import (
    EvidenceChunkResponse,
    EvidenceDocumentCreate,
    EvidenceDocumentResponse,
    EvidenceDocumentVersionResponse,
    EvidenceVersionIngestionResponse,
)
from app.audit import record_audit_event
from app.auth import WorkspaceContext, assert_workspace_role, get_workspace_context
from app.db import get_db
from app.evidence.ingestion.errors import (
    FileTooLargeError,
    IngestionValidationError,
    UnsupportedExtensionError,
    UnsupportedMediaTypeError,
)
from app.evidence.ingestion.service import ingest
from app.evidence.ingestion.types import IngestionInput
from app.evidence.persistence.errors import (
    EvidenceDocumentNotFoundError,
    EvidencePersistenceError,
    EvidencePersistenceValidationError,
)
from app.evidence.persistence.ingestion_service import (
    IngestionOutcome,
    persist_ingestion,
)
from app.evidence.persistence.repositories import (
    get_document_version,
    list_document_versions,
    list_version_chunks,
)
from app.models import EvidenceChunk, EvidenceDocument, EvidenceDocumentVersion, WorkspaceRole

router = APIRouter(prefix="/workspaces", tags=["evidence"])

DbSession = Annotated[Session, Depends(get_db)]
WorkspaceAccess = Annotated[WorkspaceContext, Depends(get_workspace_context)]

MAX_UPLOAD_READ_BYTES = 5 * 1024 * 1024 + 1


def _ingestion_http_error(exc: IngestionValidationError) -> HTTPException:
    """Map deterministic ingestion validation failures to safe HTTP responses."""

    if isinstance(exc, FileTooLargeError):
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Evidence file exceeds the maximum permitted size",
        )

    if isinstance(
        exc,
        (UnsupportedExtensionError, UnsupportedMediaTypeError),
    ):
        return HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Evidence file type is not supported",
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Evidence file failed deterministic validation",
    )


@router.post(
    "/{workspace_id}/documents",
    response_model=EvidenceDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_evidence_document(
    payload: EvidenceDocumentCreate,
    context: WorkspaceAccess,
    db: DbSession,
) -> EvidenceDocument:
    """Create a new workspace-scoped evidence document."""

    assert_workspace_role(
        context,
        db,
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
    )

    existing = db.scalar(
        select(EvidenceDocument).where(
            EvidenceDocument.workspace_id == context.workspace.id,
            EvidenceDocument.name == payload.name,
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence document already exists",
        )

    document = EvidenceDocument(
        workspace_id=context.workspace.id,
        name=payload.name,
        source_type="file",
        status="active",
        created_by_user_id=context.user.id,
    )

    db.add(document)
    db.flush()

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="evidence.document.created",
        resource_type="evidence_document",
        resource_id=str(document.id),
        metadata={
            "name": document.name,
            "source_type": document.source_type,
        },
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence document already exists",
        ) from exc

    db.refresh(document)
    return document


@router.get(
    "/{workspace_id}/documents",
    response_model=list[EvidenceDocumentResponse],
)
def list_evidence_documents(
    context: WorkspaceAccess,
    db: DbSession,
) -> list[EvidenceDocument]:
    """List evidence documents inside the authorized workspace only."""

    documents = list(
        db.scalars(
            select(EvidenceDocument)
            .where(EvidenceDocument.workspace_id == context.workspace.id)
            .order_by(EvidenceDocument.created_at, EvidenceDocument.id)
        )
    )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="evidence.document.listed",
        resource_type="evidence_document",
        resource_id=str(context.workspace.id),
        metadata={"count": len(documents)},
    )

    db.commit()
    return documents


@router.get(
    "/{workspace_id}/documents/{document_id}",
    response_model=EvidenceDocumentResponse,
)
def get_evidence_document(
    document_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> EvidenceDocument:
    """Return one evidence document after workspace authorization."""

    document = db.scalar(
        select(EvidenceDocument).where(
            EvidenceDocument.id == document_id,
            EvidenceDocument.workspace_id == context.workspace.id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence document not found",
        )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="evidence.document.read",
        resource_type="evidence_document",
        resource_id=str(document.id),
    )

    db.commit()
    return document


@router.post(
    "/{workspace_id}/documents/{document_id}/versions",
    response_model=EvidenceVersionIngestionResponse,
)
def upload_evidence_version(
    document_id: uuid.UUID,
    context: WorkspaceAccess,
    response: Response,
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
) -> EvidenceVersionIngestionResponse:
    """Upload, normalize, chunk, and persist one evidence document version."""

    assert_workspace_role(
        context,
        db,
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
    )

    filename = file.filename or ""
    media_type = file.content_type

    try:
        raw_bytes = file.file.read(MAX_UPLOAD_READ_BYTES)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to read uploaded evidence file",
        ) from exc
    finally:
        file.file.close()

    try:
        ingestion_result = ingest(
            IngestionInput(
                original_filename=filename,
                media_type=media_type,
                raw_bytes=raw_bytes,
            )
        )

        persistence_result = persist_ingestion(
            db,
            workspace_id=context.workspace.id,
            actor_user_id=context.user.id,
            document_id=document_id,
            ingestion_result=ingestion_result,
        )

    except IngestionValidationError as exc:
        raise _ingestion_http_error(exc) from exc

    except EvidenceDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence document not found",
        ) from exc

    except EvidencePersistenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Evidence persistence validation failed",
        ) from exc

    except EvidencePersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evidence persistence failed",
        ) from exc

    if persistence_result.outcome is IngestionOutcome.DUPLICATE:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED

    return EvidenceVersionIngestionResponse(
        outcome=persistence_result.outcome.value,
        document_id=persistence_result.document_id,
        version_id=persistence_result.version_id,
        version_number=persistence_result.version_number,
        chunk_count=persistence_result.chunk_count,
        ingestion_attempt_id=persistence_result.ingestion_attempt_id,
    )


@router.get(
    "/{workspace_id}/documents/{document_id}/versions",
    response_model=list[EvidenceDocumentVersionResponse],
)
def list_evidence_versions(
    document_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> list[EvidenceDocumentVersion]:
    """List immutable versions for an evidence document."""

    versions = list_document_versions(
        db,
        workspace_id=context.workspace.id,
        document_id=document_id,
    )

    if versions is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence document not found",
        )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="evidence.version.listed",
        resource_type="evidence_document",
        resource_id=str(document_id),
        metadata={"count": len(versions)},
    )

    db.commit()
    return versions


@router.get(
    "/{workspace_id}/documents/{document_id}/versions/{version_id}",
    response_model=EvidenceDocumentVersionResponse,
)
def get_evidence_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> EvidenceDocumentVersion:
    """Return one immutable evidence version."""

    version = get_document_version(
        db,
        workspace_id=context.workspace.id,
        document_id=document_id,
        version_id=version_id,
    )

    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence version not found",
        )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="evidence.version.read",
        resource_type="evidence_document_version",
        resource_id=str(version.id),
        metadata={
            "document_id": str(document_id),
            "version_number": version.version_number,
        },
    )

    db.commit()
    return version


@router.get(
    "/{workspace_id}/documents/{document_id}/versions/{version_id}/chunks",
    response_model=list[EvidenceChunkResponse],
)
def list_evidence_chunks(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> list[EvidenceChunk]:
    """Return deterministic retrieval chunks for one evidence version."""

    chunks = list_version_chunks(
        db,
        workspace_id=context.workspace.id,
        document_id=document_id,
        version_id=version_id,
    )

    if chunks is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence version not found",
        )

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="evidence.chunk.listed",
        resource_type="evidence_document_version",
        resource_id=str(version_id),
        metadata={"count": len(chunks)},
    )

    db.commit()
    return list(chunks)