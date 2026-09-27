"""Unit and integration coverage for the Phase 1B security boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_token
from app.models import ApiToken, AuditEvent, Workspace, WorkspaceMembership, WorkspaceRole
from tests.conftest import (
    auth_headers,
    create_principal,
    create_workspace_with_owner,
)


def test_protected_routes_require_bearer_auth(client: TestClient) -> None:
    response = client.get("/workspaces")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_revoked_and_expired_tokens_are_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(db_session, email="auth@example.test", display_name="Auth")

    assert (
        client.get(
            "/workspaces",
            headers={"Authorization": "Bearer not-a-real-token"},
        ).status_code
        == 401
    )

    token = db_session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_token(principal.token))
    )
    assert token is not None
    token.revoked_at = datetime.now(UTC)
    db_session.commit()
    assert client.get("/workspaces", headers=auth_headers(principal)).status_code == 401

    fresh = create_principal(db_session, email="expired@example.test", display_name="Expired")
    expired = db_session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_token(fresh.token))
    )
    assert expired is not None
    expired.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert client.get("/workspaces", headers=auth_headers(fresh)).status_code == 401

    assert db_session.query(AuditEvent).filter_by(action="auth.failed").count() == 3


def test_authenticated_user_can_create_and_list_only_owned_workspaces(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(db_session, email="owner@example.test", display_name="Owner")

    create_response = client.post(
        "/workspaces",
        headers=auth_headers(principal),
        json={"name": "Alpha"},
    )
    assert create_response.status_code == 201
    workspace_id = create_response.json()["id"]

    list_response = client.get("/workspaces", headers=auth_headers(principal))
    assert list_response.status_code == 200
    assert [workspace["id"] for workspace in list_response.json()] == [workspace_id]


def test_unknown_workspace_is_denied_without_audit_foreign_key_failure(
    client: TestClient,
    db_session: Session,
) -> None:
    principal = create_principal(db_session, email="unknown@example.test", display_name="Unknown")

    response = client.get(
        "/workspaces/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(principal),
    )

    assert response.status_code == 404
    denial = db_session.query(AuditEvent).filter_by(action="workspace.access_denied").one()
    assert denial.actor_user_id == principal.user.id
    assert denial.workspace_id is None


def test_cross_workspace_read_is_denied_and_audited(
    client: TestClient,
    db_session: Session,
) -> None:
    user_a = create_principal(db_session, email="a@example.test", display_name="A")
    user_b = create_principal(db_session, email="b@example.test", display_name="B")
    workspace_a = create_workspace_with_owner(db_session, user_a, name="A")
    workspace_b = create_workspace_with_owner(db_session, user_b, name="B")

    response = client.get(
        f"/workspaces/{workspace_b.id}",
        headers=auth_headers(user_a),
    )

    assert response.status_code == 404
    denial = db_session.query(AuditEvent).filter_by(action="workspace.access_denied").one()
    assert denial.actor_user_id == user_a.user.id
    assert denial.workspace_id == workspace_b.id

    assert (
        client.get(
            f"/workspaces/{workspace_a.id}",
            headers=auth_headers(user_b),
        ).status_code
        == 404
    )


def test_cross_workspace_update_is_denied_without_mutating_target(
    client: TestClient,
    db_session: Session,
) -> None:
    user_a = create_principal(db_session, email="a2@example.test", display_name="A2")
    user_b = create_principal(db_session, email="b2@example.test", display_name="B2")
    workspace_b = create_workspace_with_owner(db_session, user_b, name="Original")

    response = client.patch(
        f"/workspaces/{workspace_b.id}",
        headers=auth_headers(user_a),
        json={"name": "Tampered"},
    )

    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.get(Workspace, workspace_b.id).name == "Original"


def test_member_listing_and_idor_protection(
    client: TestClient,
    db_session: Session,
) -> None:
    user_a = create_principal(db_session, email="a3@example.test", display_name="A3")
    user_b = create_principal(db_session, email="b3@example.test", display_name="B3")
    workspace_a = create_workspace_with_owner(db_session, user_a, name="A3")
    workspace_b = create_workspace_with_owner(db_session, user_b, name="B3")

    allowed = client.get(
        f"/workspaces/{workspace_a.id}/members",
        headers=auth_headers(user_a),
    )
    assert allowed.status_code == 200
    assert allowed.json()[0]["user_id"] == str(user_a.user.id)

    denied = client.get(
        f"/workspaces/{workspace_b.id}/members",
        headers=auth_headers(user_a),
    )
    assert denied.status_code == 404


def test_owner_can_add_member_and_admin_can_update_non_owner(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(db_session, email="owner2@example.test", display_name="Owner2")
    member = create_principal(db_session, email="member@example.test", display_name="Member")
    workspace = create_workspace_with_owner(db_session, owner, name="Roles")

    add_response = client.post(
        f"/workspaces/{workspace.id}/members",
        headers=auth_headers(owner),
        json={"user_id": str(member.user.id), "role": "admin"},
    )
    assert add_response.status_code == 201
    assert add_response.json()["role"] == "admin"

    update_response = client.patch(
        f"/workspaces/{workspace.id}/members/{member.user.id}",
        headers=auth_headers(owner),
        json={"role": "member"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["role"] == "member"


def test_member_cannot_modify_workspace_or_membership(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(db_session, email="owner3@example.test", display_name="Owner3")
    member = create_principal(db_session, email="member3@example.test", display_name="Member3")
    workspace = create_workspace_with_owner(db_session, owner, name="Protected")
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=member.user.id,
            role=WorkspaceRole.MEMBER,
        )
    )
    db_session.commit()

    assert (
        client.patch(
            f"/workspaces/{workspace.id}",
            headers=auth_headers(member),
            json={"name": "Nope"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/workspaces/{workspace.id}/members",
            headers=auth_headers(member),
            json={"user_id": str(owner.user.id), "role": "viewer"},
        ).status_code
        == 403
    )


def test_admin_cannot_grant_or_modify_owner(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(db_session, email="owner4@example.test", display_name="Owner4")
    admin = create_principal(db_session, email="admin4@example.test", display_name="Admin4")
    member = create_principal(db_session, email="member4@example.test", display_name="Member4")
    workspace = create_workspace_with_owner(db_session, owner, name="Admin")
    db_session.add_all(
        [
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=admin.user.id,
                role=WorkspaceRole.ADMIN,
            ),
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=member.user.id,
                role=WorkspaceRole.MEMBER,
            ),
        ]
    )
    db_session.commit()

    grant_owner = client.patch(
        f"/workspaces/{workspace.id}/members/{member.user.id}",
        headers=auth_headers(admin),
        json={"role": "owner"},
    )
    assert grant_owner.status_code == 403

    modify_owner = client.patch(
        f"/workspaces/{workspace.id}/members/{owner.user.id}",
        headers=auth_headers(admin),
        json={"role": "member"},
    )
    assert modify_owner.status_code == 403


def test_workspace_must_retain_an_owner(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(db_session, email="owner5@example.test", display_name="Owner5")
    workspace = create_workspace_with_owner(db_session, owner, name="Last Owner")

    response = client.delete(
        f"/workspaces/{workspace.id}/members/{owner.user.id}",
        headers=auth_headers(owner),
    )

    assert response.status_code == 409


def test_security_sensitive_operations_create_audit_events(
    client: TestClient,
    db_session: Session,
) -> None:
    owner = create_principal(db_session, email="owner6@example.test", display_name="Owner6")
    member = create_principal(db_session, email="member6@example.test", display_name="Member6")
    workspace = create_workspace_with_owner(db_session, owner, name="Audited")

    response = client.post(
        f"/workspaces/{workspace.id}/members",
        headers=auth_headers(owner),
        json={"user_id": str(member.user.id), "role": "viewer"},
    )
    assert response.status_code == 201

    actions = {
        event.action
        for event in db_session.query(AuditEvent).filter_by(workspace_id=workspace.id).all()
    }
    assert "membership.created" in actions

    audit_response = client.get(
        f"/workspaces/{workspace.id}/audit-events",
        headers=auth_headers(owner),
    )
    assert audit_response.status_code == 200
    assert any(event["action"] == "membership.created" for event in audit_response.json())
