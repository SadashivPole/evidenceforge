"""Protected workspace, membership, and audit routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AuditEventResponse,
    MembershipCreate,
    MembershipResponse,
    MembershipUpdate,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.audit import record_audit_event
from app.auth import (
    WorkspaceContext,
    assert_workspace_role,
    get_current_user,
    get_workspace_context,
)
from app.db import get_db
from app.models import AuditEvent, User, Workspace, WorkspaceMembership, WorkspaceRole

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
WorkspaceAccess = Annotated[WorkspaceContext, Depends(get_workspace_context)]


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _role_change_is_allowed(
    context: WorkspaceContext,
    target_membership: WorkspaceMembership,
    requested_role: WorkspaceRole,
) -> None:
    """Apply role hierarchy rules after workspace access is established."""

    actor_role = context.membership.role
    if actor_role == WorkspaceRole.OWNER:
        return
    if actor_role != WorkspaceRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role"
        )
    if target_membership.role == WorkspaceRole.OWNER or requested_role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can manage owners"
        )


def _cannot_remove_last_owner(
    db: Session, workspace_id: uuid.UUID, target: WorkspaceMembership
) -> None:
    """Prevent a workspace from becoming ownerless."""

    if target.role != WorkspaceRole.OWNER:
        return
    owner_count = db.scalar(
        select(func.count())
        .select_from(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.role == WorkspaceRole.OWNER,
        )
    )
    if owner_count == 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Workspace must retain an owner"
        )


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, db: DbSession, user: CurrentUser) -> Workspace:
    """Create a workspace and make the authenticated user its owner."""

    workspace = Workspace(name=payload.name, created_by_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
        )
    )
    record_audit_event(
        db,
        actor_user_id=user.id,
        workspace_id=workspace.id,
        action="workspace.created",
        resource_type="workspace",
        resource_id=str(workspace.id),
        metadata={"role": WorkspaceRole.OWNER.value},
    )
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("", response_model=list[WorkspaceResponse])
def list_workspaces(db: DbSession, user: CurrentUser) -> list[Workspace]:
    """List only workspaces where the authenticated user has a membership."""

    workspaces = list(
        db.scalars(
            select(Workspace)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(Workspace.created_at, Workspace.id)
        )
    )
    for workspace in workspaces:
        record_audit_event(
            db,
            actor_user_id=user.id,
            workspace_id=workspace.id,
            action="workspace.listed",
            resource_type="workspace",
            resource_id=str(workspace.id),
        )
    db.commit()
    return workspaces


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(context: WorkspaceAccess, db: DbSession) -> Workspace:
    """Read a workspace only after membership authorization."""

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="workspace.read",
        resource_type="workspace",
        resource_id=str(context.workspace.id),
    )
    db.commit()
    return context.workspace


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
def update_workspace(
    payload: WorkspaceUpdate,
    context: WorkspaceAccess,
    db: DbSession,
) -> Workspace:
    """Update workspace metadata for owners and admins only."""

    assert_workspace_role(context, db, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    context.workspace.name = payload.name
    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="workspace.updated",
        resource_type="workspace",
        resource_id=str(context.workspace.id),
        metadata={"fields": ["name"]},
    )
    db.commit()
    db.refresh(context.workspace)
    return context.workspace


@router.get("/{workspace_id}/members", response_model=list[MembershipResponse])
def list_members(context: WorkspaceAccess, db: DbSession) -> list[WorkspaceMembership]:
    """List memberships inside the authorized workspace only."""

    members = list(
        db.scalars(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == context.workspace.id)
            .order_by(WorkspaceMembership.created_at, WorkspaceMembership.id)
        )
    )
    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="membership.listed",
        resource_type="workspace_membership",
        resource_id=str(context.workspace.id),
    )
    db.commit()
    return members


@router.post(
    "/{workspace_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    payload: MembershipCreate,
    context: WorkspaceAccess,
    db: DbSession,
) -> WorkspaceMembership:
    """Add an existing user to the authorized workspace."""

    assert_workspace_role(context, db, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    if context.membership.role == WorkspaceRole.ADMIN and payload.role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can add owners"
        )

    target_user = db.get(User, payload.user_id)
    if target_user is None or not target_user.is_active:
        raise _not_found("User not found")
    existing = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == context.workspace.id,
            WorkspaceMembership.user_id == payload.user_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already a member")

    membership = WorkspaceMembership(
        workspace_id=context.workspace.id,
        user_id=payload.user_id,
        role=payload.role,
    )
    db.add(membership)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="membership.created",
        resource_type="workspace_membership",
        resource_id=str(membership.id),
        metadata={"target_user_id": str(payload.user_id), "role": payload.role.value},
    )
    db.commit()
    db.refresh(membership)
    return membership


@router.patch("/{workspace_id}/members/{user_id}", response_model=MembershipResponse)
def update_member(
    payload: MembershipUpdate,
    user_id: uuid.UUID,
    context: WorkspaceAccess,
    db: DbSession,
) -> WorkspaceMembership:
    """Update a member role only inside the authorized workspace."""

    target = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == context.workspace.id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if target is None:
        raise _not_found("Membership not found")
    assert_workspace_role(context, db, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    _role_change_is_allowed(context, target, payload.role)
    if target.role == WorkspaceRole.OWNER and payload.role != WorkspaceRole.OWNER:
        _cannot_remove_last_owner(db, context.workspace.id, target)
    target.role = payload.role
    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="membership.updated",
        resource_type="workspace_membership",
        resource_id=str(target.id),
        metadata={"target_user_id": str(user_id), "role": payload.role.value},
    )
    db.commit()
    db.refresh(target)
    return target


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(user_id: uuid.UUID, context: WorkspaceAccess, db: DbSession) -> None:
    """Remove a member only inside the authorized workspace."""

    target = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == context.workspace.id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if target is None:
        raise _not_found("Membership not found")
    assert_workspace_role(context, db, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    _role_change_is_allowed(context, target, target.role)
    _cannot_remove_last_owner(db, context.workspace.id, target)
    db.delete(target)
    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="membership.deleted",
        resource_type="workspace_membership",
        resource_id=str(target.id),
        metadata={"target_user_id": str(user_id)},
    )
    db.commit()


@router.get("/{workspace_id}/audit-events", response_model=list[AuditEventResponse])
def list_audit_events(context: WorkspaceAccess, db: DbSession) -> list[AuditEvent]:
    """Expose workspace audit events to owners and admins only."""

    assert_workspace_role(context, db, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == context.workspace.id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="audit.read",
        resource_type="audit_event",
        resource_id=str(context.workspace.id),
    )
    db.commit()
    return events
