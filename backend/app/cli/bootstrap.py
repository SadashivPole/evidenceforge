"""Restricted, one-time first-user bootstrap for PostgreSQL deployments."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth import hash_token
from app.config import get_settings
from app.db import create_db_engine, create_session_factory
from app.models import (
    ApiToken,
    AuditEvent,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)

# One database-wide transaction lock, scoped to this PostgreSQL database.
_BOOTSTRAP_LOCK_ID = 0x45464F524745
_TOKEN_NAME = "first-user-bootstrap"
_REQUIRED_TABLES = {
    "alembic_version",
    "users",
    "api_tokens",
    "workspaces",
    "workspace_memberships",
    "audit_events",
}


class BootstrapRefusedError(RuntimeError):
    """Raised when bootstrap cannot safely proceed."""


def _validated_text(
    value: str,
    label: str,
    max_length: int,
    *,
    preserve_exact: bool = False,
) -> str:
    """Validate required text without silently changing identity subjects."""

    if not value or not value.strip():
        raise BootstrapRefusedError(f"{label} must not be empty.")

    if preserve_exact and value != value.strip():
        raise BootstrapRefusedError(f"{label} must not have leading or trailing whitespace.")

    cleaned = value if preserve_exact else value.strip()
    if len(cleaned) > max_length:
        raise BootstrapRefusedError(
            f"{label} exceeds the maximum length of {max_length} characters."
        )
    return cleaned


def _assert_read_committed(db: Session) -> None:
    """Require fresh statement snapshots for the post-lock eligibility check."""

    isolation = str(db.scalar(text("SHOW transaction_isolation"))).strip().lower()
    if isolation != "read committed":
        raise BootstrapRefusedError(
            "Bootstrap requires READ COMMITTED transaction isolation; "
            f"the current level is {isolation!r}. No records were created."
        )


def _assert_no_prior_bootstrap(db: Session) -> None:
    """Reject repeat provisioning even if the initial records were later removed."""

    previous = db.scalar(
        select(AuditEvent.id).where(AuditEvent.action == "auth.bootstrap_completed").limit(1)
    )
    if previous is not None:
        raise BootstrapRefusedError(
            "A successful bootstrap marker already exists; refusing repeat provisioning."
        )


def _resolve_alembic_config_path() -> Path:
    """Find Alembic configuration for source and installed CLI execution."""

    configured = os.environ.get("EVIDENCEFORGE_ALEMBIC_CONFIG", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise BootstrapRefusedError(
                "EVIDENCEFORGE_ALEMBIC_CONFIG does not point to a readable "
                "Alembic configuration file; refusing bootstrap."
            )
        return path.resolve()

    candidates = (
        Path.cwd() / "alembic.ini",
        Path(__file__).resolve().parents[2] / "alembic.ini",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise BootstrapRefusedError(
        "Alembic configuration could not be located; set "
        "EVIDENCEFORGE_ALEMBIC_CONFIG to the approved configuration path."
    )


def _assert_migration_head(db: Session) -> None:
    """Refuse provisioning unless the database matches every repository head."""

    config_path = _resolve_alembic_config_path()
    config = Config(str(config_path))
    script = ScriptDirectory.from_config(config)
    expected_heads = set(script.get_heads())
    current_heads = set(MigrationContext.configure(db.connection()).get_current_heads())

    if not expected_heads or current_heads != expected_heads:
        expected = ", ".join(sorted(expected_heads)) or "(none)"
        current = ", ".join(sorted(current_heads)) or "(none)"
        raise BootstrapRefusedError(
            "Database migrations are not at the repository head "
            f"(current: {current}; expected: {expected}). "
            "Apply migrations before bootstrapping."
        )


def bootstrap_first_user(
    db: Session,
    *,
    external_subject: str,
    email: str,
    display_name: str,
    workspace_name: str,
    token_ttl_days: int = 30,
) -> tuple[str, datetime]:
    """Create the initial identity, owner workspace, token and audit event atomically.

    PostgreSQL is required because a transaction-level advisory lock protects the
    fresh-database check from concurrent bootstrap attempts. The caller must use
    a fresh SQLAlchemy session. The raw token is returned only after commit.
    """

    external_subject = _validated_text(
        external_subject, "External subject", 255, preserve_exact=True
    )
    email = _validated_text(email, "Email", 320)
    display_name = _validated_text(display_name, "Display name", 255)
    workspace_name = _validated_text(workspace_name, "Workspace name", 255)

    if not 1 <= token_ttl_days <= 365:
        raise BootstrapRefusedError("Token lifetime must be between 1 and 365 days.")

    if db.get_bind().dialect.name != "postgresql":
        raise BootstrapRefusedError("Bootstrap requires PostgreSQL. No records were created.")

    # The lock, eligibility check, record creation and commit share one
    # transaction. Waiting invocations recheck eligibility after the lock.
    with db.begin():
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _BOOTSTRAP_LOCK_ID},
        )
        _assert_read_committed(db)

        tables = set(inspect(db.connection()).get_table_names())
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            raise BootstrapRefusedError(
                "Required database tables are missing: "
                + ", ".join(missing)
                + ". Apply migrations before bootstrapping."
            )

        _assert_migration_head(db)
        _assert_no_prior_bootstrap(db)

        guarded_tables = (
            ("users", User),
            ("api_tokens", ApiToken),
            ("workspaces", Workspace),
            ("workspace_memberships", WorkspaceMembership),
        )
        populated: list[str] = []

        for table_name, model in guarded_tables:
            count = db.scalar(select(func.count()).select_from(model)) or 0
            if count > 0:
                populated.append(table_name)

        # audit_events is intentionally excluded: unauthenticated requests
        # may legitimately create audit records before the first user exists.
        if populated:
            raise BootstrapRefusedError(
                "Bootstrap is permitted only before security-boundary "
                "records exist. Non-empty tables: " + ", ".join(populated)
            )

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=token_ttl_days)

        user = User(
            external_subject=external_subject,
            email=email,
            display_name=display_name,
            is_active=True,
        )
        db.add(user)
        db.flush()

        workspace = Workspace(
            name=workspace_name,
            created_by_user_id=user.id,
        )
        db.add(workspace)
        db.flush()

        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
        )
        token = ApiToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            name=_TOKEN_NAME,
            expires_at=expires_at,
        )
        db.add_all([membership, token])

        record_audit_event(
            db,
            actor_user_id=user.id,
            workspace_id=workspace.id,
            action="auth.bootstrap_completed",
            resource_type="bootstrap",
            resource_id=str(user.id),
            metadata={
                "method": "operator_cli",
                "workspace_role": WorkspaceRole.OWNER.value,
                "token_name": _TOKEN_NAME,
                "token_ttl_days": token_ttl_days,
            },
        )
        db.flush()

    # Reaching this point means the transaction context has committed.
    # The plaintext token is never written to the database or audit metadata.
    return raw_token, expires_at


def main(argv: Sequence[str] | None = None) -> int:
    """Parse operator-supplied identity details and perform one-time bootstrap."""

    parser = argparse.ArgumentParser(
        description=(
            "Provision the first EvidenceForge user and owner workspace "
            "on an eligible PostgreSQL database."
        )
    )
    parser.add_argument(
        "--external-subject",
        required=True,
        help="Explicit identity subject supplied by the operator.",
    )
    parser.add_argument("--email", required=True, help="Initial user's email.")
    parser.add_argument("--display-name", required=True, help="Initial user's display name.")
    parser.add_argument("--workspace-name", required=True, help="Name for the initial workspace.")
    parser.add_argument(
        "--token-ttl-days",
        type=int,
        default=30,
        help="Bearer token lifetime in days (1-365; default: 30).",
    )
    args = parser.parse_args(argv)

    engine = None
    raw_token: str | None = None
    expires_at: datetime | None = None

    try:
        settings = get_settings()
        engine = create_db_engine(settings)
        session_factory = create_session_factory(engine)

        with session_factory() as db:
            raw_token, expires_at = bootstrap_first_user(
                db,
                external_subject=args.external_subject,
                email=args.email,
                display_name=args.display_name,
                workspace_name=args.workspace_name,
                token_ttl_days=args.token_ttl_days,
            )

    except BootstrapRefusedError as exc:
        print(f"Bootstrap refused: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(
            "Bootstrap failed before a token was displayed. Inspect database "
            "connectivity and migration status using approved operator "
            "procedures before retrying; the transaction outcome may require "
            "verification.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Do not print exception details that might reveal configuration values
        # or identity data. Unexpected failures need operator investigation.
        print(
            "Bootstrap failed before a token was displayed. Inspect the "
            "runtime and database state using approved operator procedures "
            "before retrying.",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    if raw_token is None or expires_at is None:
        print(
            "Bootstrap did not return a token; verify database state before retrying.",
            file=sys.stderr,
        )
        return 1

    print("Bootstrap completed.")
    print("Copy this bearer token now. It will not be displayed again:")
    print(raw_token, flush=True)
    print(f"Token expires at (UTC): {expires_at.isoformat()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
