"""Workspace-scoped evidence document metadata routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import EvidenceDocumentCreate, EvidenceDocumentResponse
from app.audit import record_audit_event
from app.auth import WorkspaceContext, assert_workspace_role, get_workspace_context
from app.db import get_db
from app.models import EvidenceDocument, WorkspaceRole

router = APIRouter(prefix="/workspaces", tags=["evidence"])

DbSession = Annotated[Session, Depends(get_db)]
WorkspaceAccess = Annotated[WorkspaceContext, Depends(get_workspace_context)]


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