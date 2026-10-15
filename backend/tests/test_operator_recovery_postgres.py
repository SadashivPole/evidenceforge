"""PostgreSQL integration tests using a dedicated, disposable test database only.

Set EVIDENCEFORGE_OPERATOR_RECOVERY_TEST_DATABASE_URL to a dedicated PostgreSQL
TEST database on localhost/loopback named evidenceforge_recovery_test or using one
of the explicit test-only prefixes enforced below. Each test creates and drops its
own randomly named schema; never point this URL at an application database.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

import app.cli.operator_recovery as recovery
from app.auth import hash_token
from app.cli.recovery_identity import RecoveryTargetIdentity
from app.models import ApiToken, AuditEvent, User, Workspace, WorkspaceMembership, WorkspaceRole

POSTGRES_URL = os.getenv("EVIDENCEFORGE_OPERATOR_RECOVERY_TEST_DATABASE_URL", "").strip()
_ALLOWED_TEST_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ALLOWED_TEST_DATABASE_PREFIXES = (
    "evidenceforge_operator_recovery_test_",
    "evidenceforge_recovery_it_",
    "evidenceforge_recovery_ci_",
)
_EXACT_TEST_DATABASE_NAMES = {"evidenceforge_recovery_test"}


def _validated_test_database_url(raw_url: str) -> URL:
    """Fail closed unless the URL names a dedicated local recovery test database."""

    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise ValueError("The operator-recovery test URL is not a valid SQLAlchemy URL.") from exc
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    host = (url.host or "").lower().rstrip(".")
    database = url.database or ""
    allowed_database = database in _EXACT_TEST_DATABASE_NAMES or database.startswith(
        _ALLOWED_TEST_DATABASE_PREFIXES
    )
    if (
        url.get_backend_name() != "postgresql"
        or host not in _ALLOWED_TEST_HOSTS
        or not allowed_database
    ):
        raise ValueError(
            "The operator-recovery test URL must target PostgreSQL on localhost/loopback "
            "and use the exact evidenceforge_recovery_test name or an approved "
            "evidenceforge_operator_recovery_test_*, evidenceforge_recovery_it_*, "
            "or evidenceforge_recovery_ci_* database name."
        )
    return url


_TEST_TABLES = [
    User.__table__,
    ApiToken.__table__,
    Workspace.__table__,
    WorkspaceMembership.__table__,
    AuditEvent.__table__,
]


@pytest.fixture
def isolated_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[sessionmaker[Session], str, str]]:
    if not POSTGRES_URL:
        pytest.skip(
            "Set EVIDENCEFORGE_OPERATOR_RECOVERY_TEST_DATABASE_URL to an isolated "
            "PostgreSQL test database."
        )

    try:
        url = _validated_test_database_url(POSTGRES_URL)
    except ValueError as exc:
        pytest.fail(str(exc))
    database = url.database or ""

    admin_engine = create_engine(url, pool_pre_ping=True)
    actual_database = ""
    try:
        with admin_engine.connect() as conn:
            actual_database = str(conn.scalar(text("SELECT current_database()")) or "")
            if actual_database != database:
                pytest.fail("PostgreSQL connection database did not match the configured test URL.")
        schema = f"efr_{uuid.uuid4().hex}"
        with admin_engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

        test_engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "options": (
                    f"-c search_path={schema} -c evidenceforge.deployment_id=ef-recovery-test-01"
                )
            },
        )
        try:
            with test_engine.begin() as conn:
                conn.exec_driver_sql(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            # Only the five tables used by recovery are created in this isolated schema.
            from app.models import Base

            Base.metadata.create_all(test_engine, tables=_TEST_TABLES)
            monkeypatch.setattr(recovery, "_assert_migration_head", lambda db: None)
            factory = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)
            yield factory, actual_database, schema
        finally:
            test_engine.dispose()
            with admin_engine.begin() as conn:
                conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        admin_engine.dispose()


def _seed_existing_user(factory: sessionmaker[Session], *, live_token: bool = False) -> uuid.UUID:
    with factory() as db:
        user = User(
            external_subject="identity-provider|approved-subject",
            email="existing-user@example.test",
            display_name="Existing User",
            is_active=True,
        )
        db.add(user)
        db.flush()
        now = datetime.now(UTC)
        db.add_all(
            [
                ApiToken(
                    user_id=user.id,
                    token_hash=hash_token("expired-test-credential"),
                    name="expired-test-token",
                    expires_at=now - timedelta(days=2),
                ),
                ApiToken(
                    user_id=user.id,
                    token_hash=hash_token("revoked-test-credential"),
                    name="revoked-test-token",
                    expires_at=now + timedelta(days=2),
                    revoked_at=now,
                ),
            ]
        )
        if live_token:
            db.add(
                ApiToken(
                    user_id=user.id,
                    token_hash=hash_token("live-test-credential"),
                    name="live-test-token",
                    expires_at=now + timedelta(days=2),
                )
            )
        db.commit()
        return user.id


def _counts(factory: sessionmaker[Session]) -> tuple[int, int, int, int, int]:
    with factory() as db:
        return tuple(
            int(db.scalar(select(func.count()).select_from(model)) or 0)
            for model in (User, ApiToken, Workspace, WorkspaceMembership, AuditEvent)
        )  # type: ignore[return-value]


def _target_identity(
    factory: sessionmaker[Session], database: str, schema: str
) -> RecoveryTargetIdentity:
    """Build test-only identity facts from the isolated PostgreSQL instance."""

    with factory() as db:
        row = (
            db.execute(
                text(
                    "SELECT inet_server_addr()::text AS server_address, "
                    "inet_server_port() AS server_port, "
                    "current_setting('evidenceforge.deployment_id', true) AS deployment_id"
                )
            )
            .mappings()
            .one()
        )
    deployment_id = str(row["deployment_id"] or "")
    server_address = str(row["server_address"] or "")
    server_port_raw = row["server_port"]
    if server_port_raw is None:
        pytest.fail("Disposable PostgreSQL did not report its server port.")
    server_port = int(str(server_port_raw))
    if not deployment_id or not server_address:
        pytest.fail("Disposable PostgreSQL did not expose the configured test identity.")
    return RecoveryTargetIdentity(
        created_at_utc=datetime.now(UTC),
        docker_context="disposable-test-context",
        compose_project="evidenceforge-recovery-test",
        compose_service="db",
        container_id="a" * 64,
        deployment_id=deployment_id,
        server_address=server_address,
        server_port=server_port,
        tls_hostname="pg-ef-recovery-test-01.evidenceforge.internal",
        database_name=database,
        schema_name=schema,
    )


def _run_recovery(
    factory: sessionmaker[Session], database: str, schema: str
) -> tuple[str, datetime]:
    with factory() as db:
        return recovery.recover_existing_user(
            db,
            external_subject="identity-provider|approved-subject",
            workspace_name="Recovered owner workspace",
            expected_database_name=database,
            expected_schema_name=schema,
            operator_identity="operator@example.test",
            approval_reference="SEC-1234",
            target_identity=_target_identity(factory, database, schema),
            token_ttl_days=30,
        )


def test_postgres_recovery_creates_workspace_owner_token_and_audit_atomically(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    user_id = _seed_existing_user(factory)
    raw_token, expiry = _run_recovery(factory, database, schema)

    with factory() as db:
        user = db.get(User, user_id)
        assert user is not None and user.is_active
        workspaces = list(db.scalars(select(Workspace)))
        memberships = list(db.scalars(select(WorkspaceMembership)))
        new_token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw_token)))
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "auth.operator_recovery_completed")
        )
        assert len(workspaces) == 1
        assert workspaces[0].created_by_user_id == user_id
        assert len(memberships) == 1
        assert memberships[0].user_id == user_id
        assert memberships[0].workspace_id == workspaces[0].id
        assert memberships[0].role == WorkspaceRole.OWNER
        assert new_token is not None
        assert new_token.token_hash != raw_token
        assert new_token.expires_at == expiry
        assert event is not None
        assert event.actor_user_id is None
        assert event.workspace_id == workspaces[0].id
        assert event.event_metadata["approval_reference"] == "SEC-1234"
        assert event.event_metadata["operator_identity"] == "operator@example.test"
        assert raw_token not in str(event.event_metadata)
        assert db.scalar(select(func.count()).select_from(User)) == 1
        bootstrap_marker_count = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "auth.bootstrap_completed")
        )
        assert bootstrap_marker_count == 0


def test_postgres_recovery_cannot_be_repeated(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    _seed_existing_user(factory)
    _run_recovery(factory, database, schema)
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="marker already exists"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="Do not create a second workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_wrong_database_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    _seed_existing_user(factory)
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="did not match"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No workspace",
                expected_database_name=f"{database}-wrong",
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_wrong_subject_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    _seed_existing_user(factory)
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="external subject"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|different-subject",
                workspace_name="No workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_prior_bootstrap_marker_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    user_id = _seed_existing_user(factory)
    with factory() as db:
        db.add(
            AuditEvent(
                actor_user_id=user_id,
                workspace_id=None,
                action="auth.bootstrap_completed",
                resource_type="bootstrap",
                resource_id=str(user_id),
                event_metadata={},
            )
        )
        db.commit()
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="marker already exists"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_existing_workspace_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    user_id = _seed_existing_user(factory)
    with factory() as db:
        db.add(Workspace(name="Existing workspace", created_by_user_id=user_id))
        db.commit()
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="no existing workspaces"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No new workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_inactive_user_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    user_id = _seed_existing_user(factory)
    with factory() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.is_active = False
        db.commit()
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="sole active user"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_multiple_users_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    _seed_existing_user(factory)
    with factory() as db:
        db.add(
            User(
                external_subject="identity-provider|second-subject",
                email="second-user@example.test",
                display_name="Second User",
                is_active=True,
            )
        )
        db.commit()
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(
            recovery.OperatorRecoveryRefusedError, match="exactly one existing user"
        ):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_when_a_live_token_exists(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    _seed_existing_user(factory, live_token=True)
    before = _counts(factory)
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="currently usable token"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=_target_identity(factory, database, schema),
            )
    assert _counts(factory) == before


def test_postgres_recovery_refuses_wrong_server_port_without_writes(
    isolated_postgres: tuple[sessionmaker[Session], str, str],
) -> None:
    factory, database, schema = isolated_postgres
    _seed_existing_user(factory)
    before = _counts(factory)
    identity = _target_identity(factory, database, schema)
    wrong_port = 1 if identity.server_port == 65535 else identity.server_port + 1
    with factory() as db:
        with pytest.raises(recovery.OperatorRecoveryRefusedError, match="server port"):
            recovery.recover_existing_user(
                db,
                external_subject="identity-provider|approved-subject",
                workspace_name="No workspace",
                expected_database_name=database,
                expected_schema_name=schema,
                operator_identity="operator@example.test",
                approval_reference="SEC-1234",
                target_identity=dataclasses.replace(identity, server_port=wrong_port),
            )
    assert _counts(factory) == before


def test_test_url_guard_accepts_the_local_disposable_recovery_database() -> None:
    url = _validated_test_database_url(
        "postgresql+psycopg://ef_recovery_test@127.0.0.1:55434/evidenceforge_recovery_test"
    )
    assert url.host == "127.0.0.1"
    assert url.port == 55434
    assert url.database == "evidenceforge_recovery_test"


@pytest.mark.parametrize(
    "raw_url",
    [
        "postgresql+psycopg://user@production-db.example.com/evidenceforge_recovery_test",
        "postgresql+psycopg://user@127.0.0.1/production_recovery",
        "postgresql+psycopg://user@127.0.0.1/evidenceforge_recovery_test_prod",
        "sqlite:///evidenceforge_recovery_test.db",
    ],
)
def test_test_url_guard_rejects_nonlocal_or_unapproved_targets(raw_url: str) -> None:
    with pytest.raises(ValueError, match="operator-recovery test URL must target"):
        _validated_test_database_url(raw_url)
