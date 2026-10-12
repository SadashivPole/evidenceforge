"""Real PostgreSQL integration coverage for bootstrap and bearer authentication."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.cli.bootstrap as bootstrap
from app.auth import get_current_user, hash_token
from app.models import (
    ApiToken,
    AuditEvent,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)

POSTGRES_URL = os.getenv("EVIDENCEFORGE_BOOTSTRAP_TEST_DATABASE_URL", "").strip()


@dataclass(frozen=True)
class _Identity:
    external_subject: str
    email: str
    display_name: str
    workspace_name: str
    token_ttl_days: int = 1


@dataclass(frozen=True)
class _Outcome:
    identity: _Identity
    raw_token: str | None = None
    expires_at: datetime | None = None
    refusal_reason: str | None = None


def _invoke(
    db: Session,
    identity: _Identity,
) -> tuple[str, datetime]:
    return bootstrap.bootstrap_first_user(
        db,
        external_subject=identity.external_subject,
        email=identity.email,
        display_name=identity.display_name,
        workspace_name=identity.workspace_name,
        token_ttl_days=identity.token_ttl_days,
    )


def _attempt(
    factory: sessionmaker[Session],
    identity: _Identity,
) -> _Outcome:
    """Execute bootstrap in its own session without returning tokens to logs."""

    with factory() as db:
        try:
            raw_token, expires_at = _invoke(db, identity)
        except bootstrap.BootstrapRefusedError as exc:
            return _Outcome(identity=identity, refusal_reason=str(exc))

    return _Outcome(
        identity=identity,
        raw_token=raw_token,
        expires_at=expires_at,
    )


def _record_counts(factory: sessionmaker[Session]) -> dict[str, int]:
    models = {
        "users": User,
        "workspaces": Workspace,
        "memberships": WorkspaceMembership,
        "api_tokens": ApiToken,
        "audit_events": AuditEvent,
    }
    with factory() as db:
        return {
            name: int(db.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models.items()
        }


def _credentials(raw_token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=raw_token,
    )


def test_postgres_bootstrap_rollback_concurrency_and_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise first-use provisioning and authentication against real PostgreSQL."""

    if not POSTGRES_URL:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail("CI must configure EVIDENCEFORGE_BOOTSTRAP_TEST_DATABASE_URL.")
        pytest.skip(
            "Set EVIDENCEFORGE_BOOTSTRAP_TEST_DATABASE_URL to an isolated "
            "PostgreSQL integration database."
        )

    try:
        target_url = make_url(POSTGRES_URL)
    except Exception:
        pytest.fail("EVIDENCEFORGE_BOOTSTRAP_TEST_DATABASE_URL is not a valid SQLAlchemy URL.")

    allowed_names = (
        "evidenceforge_bootstrap_it_",
        "evidenceforge_auth_it_",
    )
    if (
        target_url.get_backend_name() != "postgresql"
        or not target_url.host
        or not target_url.database
        or not target_url.database.startswith(allowed_names)
    ):
        pytest.fail(
            "The bootstrap integration test requires PostgreSQL and an explicitly "
            "named evidenceforge_bootstrap_it_* or evidenceforge_auth_it_* database."
        )

    engine = __import__("sqlalchemy").create_engine(
        target_url,
        pool_pre_ping=True,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    try:
        with engine.connect() as connection:
            actual_database = connection.scalar(text("SELECT current_database()"))
            table_count = connection.scalar(
                text("""
                    SELECT count(*)
                    FROM pg_tables
                    WHERE schemaname NOT IN (
                        'pg_catalog', 'information_schema'
                    )
                """)
            )
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

        if actual_database != target_url.database:
            pytest.fail("Connected database does not match the requested target.")
        if table_count != 19:
            pytest.fail(
                "Expected the 19 migrated application tables; "
                f"found {table_count}. Apply Alembic migrations first."
            )
        if revision is None:
            pytest.fail("The database has no recorded Alembic migration revision.")

        with factory() as db:
            bootstrap._assert_migration_head(db)

        initial_counts = _record_counts(factory)
        if any(initial_counts.values()):
            pytest.fail(
                "Refusing to run bootstrap integration tests against populated "
                f"security-boundary tables: {initial_counts}"
            )

        # Verify rollback when an exception occurs after user/workspace creation.
        rollback_identity = _Identity(
            external_subject="bootstrap-it:rollback",
            email="bootstrap-it-rollback@example.invalid",
            display_name="Rollback Integration Test",
            workspace_name="Rollback Integration Workspace",
        )

        def fail_audit_event(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected audit-write failure")

        with monkeypatch.context() as patcher:
            patcher.setattr(bootstrap, "record_audit_event", fail_audit_event)
            with factory() as db:
                with pytest.raises(RuntimeError, match="injected audit-write failure"):
                    _invoke(db, rollback_identity)

        assert _record_counts(factory) == initial_counts

        # Race two independent first-user attempts. Exactly one may succeed.
        identities = [
            _Identity(
                external_subject="bootstrap-it:concurrent-a",
                email="bootstrap-it-a@example.invalid",
                display_name="Concurrent Bootstrap A",
                workspace_name="Concurrent Bootstrap Workspace A",
            ),
            _Identity(
                external_subject="bootstrap-it:concurrent-b",
                email="bootstrap-it-b@example.invalid",
                display_name="Concurrent Bootstrap B",
                workspace_name="Concurrent Bootstrap Workspace B",
            ),
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_attempt, factory, identity) for identity in identities]
            outcomes = [future.result() for future in futures]

        successes = [outcome for outcome in outcomes if outcome.refusal_reason is None]
        refusals = [outcome for outcome in outcomes if outcome.refusal_reason is not None]

        assert len(successes) == 1, "Exactly one concurrent bootstrap must succeed."
        assert len(refusals) == 1, "The competing bootstrap must be refused."
        assert "bootstrap marker already exists" in (refusals[0].refusal_reason or "")

        winner = successes[0]
        raw_token = winner.raw_token
        assert raw_token is not None
        assert winner.expires_at is not None
        assert winner.expires_at > datetime.now(UTC)

        # Inspect every persisted security-boundary record.
        with factory() as db:
            user = db.scalar(
                select(User).where(User.external_subject == winner.identity.external_subject)
            )
            assert user is not None

            workspace = db.scalar(
                select(Workspace).where(
                    Workspace.created_by_user_id == user.id,
                    Workspace.name == winner.identity.workspace_name,
                )
            )
            assert workspace is not None

            membership = db.scalar(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.user_id == user.id,
                    WorkspaceMembership.workspace_id == workspace.id,
                )
            )
            assert membership is not None
            assert membership.role == WorkspaceRole.OWNER

            token_record = db.scalar(select(ApiToken).where(ApiToken.user_id == user.id))
            assert token_record is not None
            assert token_record.token_hash == hash_token(raw_token)
            assert token_record.token_hash != raw_token
            assert token_record.expires_at == winner.expires_at

            token_id = token_record.id
            user_id = user.id

            marker = db.scalar(
                select(AuditEvent).where(AuditEvent.action == "auth.bootstrap_completed")
            )
            assert marker is not None
            assert marker.actor_user_id == user.id
            assert marker.workspace_id == workspace.id

        # Prove the issued token resolves through the real auth dependency.
        with factory() as db:
            authenticated = get_current_user(db, _credentials(raw_token))
            assert authenticated.id == user_id

        # Explicit repeat must be refused without adding records.
        before_repeat = _record_counts(factory)
        repeat = _attempt(factory, winner.identity)
        assert repeat.raw_token is None
        assert "bootstrap marker already exists" in (repeat.refusal_reason or "")
        assert _record_counts(factory) == before_repeat

        # Invalid token must be rejected.
        with factory() as db:
            with pytest.raises(HTTPException) as invalid:
                get_current_user(db, _credentials("not-a-valid-bootstrap-token"))
            assert invalid.value.status_code == 401

        # Expired token must be rejected.
        with factory() as db:
            token_record = db.get(ApiToken, token_id)
            assert token_record is not None
            token_record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()

        with factory() as db:
            with pytest.raises(HTTPException) as expired:
                get_current_user(db, _credentials(raw_token))
            assert expired.value.status_code == 401
            assert expired.value.detail == "Expired token"

        # A separately revoked token must also be rejected.
        revoked_raw_token = "test-revoked-token-not-a-secret"
        with factory() as db:
            db.add(
                ApiToken(
                    user_id=user_id,
                    token_hash=hash_token(revoked_raw_token),
                    name="integration-revoked-token",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                    revoked_at=datetime.now(UTC),
                )
            )
            db.commit()

        with factory() as db:
            with pytest.raises(HTTPException) as revoked:
                get_current_user(db, _credentials(revoked_raw_token))
            assert revoked.value.status_code == 401
            assert revoked.value.detail == "Invalid or revoked token"

        final_counts = _record_counts(factory)
        assert final_counts["users"] == 1
        assert final_counts["workspaces"] == 1
        assert final_counts["memberships"] == 1
        assert final_counts["api_tokens"] == 2
        assert final_counts["audit_events"] >= 4

    finally:
        engine.dispose()
