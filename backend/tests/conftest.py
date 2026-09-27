"""Shared fixtures for security-boundary tests."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import hash_token
from app.config import Settings
from app.main import create_app
from app.models import ApiToken, Base, User, Workspace, WorkspaceMembership, WorkspaceRole


@dataclass(frozen=True)
class TestPrincipal:
    user: User
    token: str


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    with TestClient(application) as test_client:
        yield test_client
    application.state.engine.dispose()


@pytest.fixture
def db_session(client: TestClient) -> Iterator[Session]:
    with client.app.state.session_factory() as session:
        yield session


def create_principal(db: Session, *, email: str, display_name: str) -> TestPrincipal:
    """Create a user and an opaque bearer token for test requests."""

    user = User(
        external_subject=f"test:{email}",
        email=email,
        display_name=display_name,
    )
    db.add(user)
    db.flush()
    raw_token = secrets.token_urlsafe(32)
    db.add(
        ApiToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            name="pytest",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()
    db.refresh(user)
    return TestPrincipal(user=user, token=raw_token)


def auth_headers(principal: TestPrincipal) -> dict[str, str]:
    return {"Authorization": f"Bearer {principal.token}"}


def create_workspace_with_owner(
    db: Session,
    principal: TestPrincipal,
    *,
    name: str,
) -> Workspace:
    workspace = Workspace(name=name, created_by_user_id=principal.user.id)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=principal.user.id,
            role=WorkspaceRole.OWNER,
        )
    )
    db.commit()
    db.refresh(workspace)
    return workspace
