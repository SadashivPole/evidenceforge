"""Server-side authentication and workspace authorization helpers."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditEvent, User, Workspace, WorkspaceMembership, WorkspaceRole

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class WorkspaceContext:
    """Authenticated user and the membership that authorizes workspace access."""

    user: User
    workspace: Workspace
    membership: WorkspaceMembership


def hash_token(token: str) -> str:
    """Hash an opaque bearer token before looking it up in the database."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _record_auth_failure(db: Session, reason: str) -> None:
    """Record an authentication failure without storing bearer-token material."""

    db.add(
        AuditEvent(
            workspace_id=None,
            actor_user_id=None,
            action="auth.failed",
            resource_type="authentication",
            resource_id=None,
            event_metadata={"reason": reason},
        )
    )
    db.commit()


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve an active user from a database-backed opaque bearer token.

    Tokens are intentionally provisioned outside the public API. This keeps the
    Phase 1B boundary from introducing a self-service account or token issuance
    endpoint that could bypass deployment-specific identity controls.
    """

    if credentials is None:
        _record_auth_failure(db, "missing_bearer_token")
        raise _unauthorized()
    if credentials.scheme.lower() != "bearer":
        _record_auth_failure(db, "invalid_authentication_scheme")
        raise _unauthorized()

    from app.models import ApiToken  # imported locally to keep auth dependencies explicit

    token = db.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_token(credentials.credentials))
    )
    if token is None or token.revoked_at is not None:
        _record_auth_failure(db, "invalid_or_revoked_token")
        raise _unauthorized("Invalid or revoked token")
    if token.expires_at is not None:
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            _record_auth_failure(db, "expired_token")
            raise _unauthorized("Expired token")

    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        _record_auth_failure(db, "inactive_user")
        raise _unauthorized("Inactive user")
    return user


def _record_denial(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
    action: str,
    reason: str,
) -> None:
    """Record a denied workspace access attempt without exposing tenant state."""

    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type="workspace",
            resource_id=str(workspace_id),
            event_metadata={"reason": reason},
        )
    )
    db.commit()


def get_workspace_context(
    workspace_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> WorkspaceContext:
    """Authorize the current user for the workspace named by the route path.

    The workspace identifier is used only as a server-side lookup key. Access is
    granted from the authenticated user's membership, never from a client-supplied
    workspace header or body field. Unauthorized and unknown workspaces both return
    404 to avoid leaking tenant existence.
    """

    result = db.execute(
        select(Workspace, WorkspaceMembership)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMembership.user_id == user.id)
    ).first()
    if result is None:
        existing_workspace_id = db.scalar(select(Workspace.id).where(Workspace.id == workspace_id))
        _record_denial(
            db,
            actor_user_id=user.id,
            workspace_id=existing_workspace_id,
            action="workspace.access_denied",
            reason="not_a_member_or_workspace_not_found",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    workspace, membership = result
    return WorkspaceContext(user=user, workspace=workspace, membership=membership)


def require_workspace_role(*allowed_roles: WorkspaceRole) -> Callable[..., WorkspaceContext]:
    """Build a dependency that requires one of the supplied workspace roles."""

    def dependency(
        context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkspaceContext:
        if context.membership.role not in allowed_roles:
            _record_denial(
                db,
                actor_user_id=context.user.id,
                workspace_id=context.workspace.id,
                action="workspace.role_denied",
                reason="insufficient_role",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role"
            )
        return context

    return dependency


def assert_workspace_role(
    context: WorkspaceContext,
    db: Session,
    *allowed_roles: WorkspaceRole,
) -> None:
    """Enforce a role check and audit denied privileged operations."""

    if context.membership.role not in allowed_roles:
        _record_denial(
            db,
            actor_user_id=context.user.id,
            workspace_id=context.workspace.id,
            action="workspace.role_denied",
            reason="insufficient_role",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role"
        )
