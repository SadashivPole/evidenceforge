"""Guarded, one-time operator recovery for an existing EvidenceForge user."""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth import hash_token
from app.cli.bootstrap import (
    _BOOTSTRAP_LOCK_ID,
    _REQUIRED_TABLES,
    BootstrapRefusedError,
    _assert_migration_head,
    _assert_read_committed,
)
from app.cli.recovery_identity import (
    RecoveryTargetIdentity,
    RecoveryTargetIdentityError,
    create_recovery_engine,
    load_target_identity,
    verify_recovery_target_identity,
)
from app.db import create_session_factory
from app.models import (
    ApiToken,
    AuditEvent,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)

_TOKEN_NAME = "operator-recovery"
_RECOVERY_ACTION = "auth.operator_recovery_completed"
_MARKER_ACTIONS = ("auth.bootstrap_completed", _RECOVERY_ACTION)
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$")
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_TARGET_ATTESTATION_OPTION = "--target-attestation-file"
_TARGET_ATTESTATION_PATH = "/run/evidenceforge/recovery-target.json"


class OperatorRecoveryRefusedError(RuntimeError):
    """Raised when operator recovery cannot safely proceed."""


def _validated_text(
    value: str,
    label: str,
    max_length: int,
    *,
    preserve_exact: bool = False,
) -> str:
    """Validate operator-supplied text without silently normalizing identities."""

    if not isinstance(value, str) or not value or not value.strip():
        raise OperatorRecoveryRefusedError(f"{label} must not be empty.")
    if preserve_exact and value != value.strip():
        raise OperatorRecoveryRefusedError(f"{label} must not have leading or trailing whitespace.")
    cleaned = value if preserve_exact else value.strip()
    if len(cleaned) > max_length:
        raise OperatorRecoveryRefusedError(
            f"{label} exceeds the maximum length of {max_length} characters."
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise OperatorRecoveryRefusedError(f"{label} contains control characters.")
    return cleaned


def _validated_reference(value: str, label: str) -> str:
    cleaned = _validated_text(value, label, 128, preserve_exact=True)
    if not _REFERENCE_RE.fullmatch(cleaned):
        raise OperatorRecoveryRefusedError(
            f"{label} must use only letters, numbers, dot, underscore, colon, slash, "
            "hash, or hyphen."
        )
    return cleaned


def _validated_schema_name(value: str) -> str:
    cleaned = _validated_text(value, "Expected schema name", 63, preserve_exact=True)
    if not _SCHEMA_RE.fullmatch(cleaned):
        raise OperatorRecoveryRefusedError(
            "Expected schema name must be a lowercase PostgreSQL identifier."
        )
    return cleaned


def _check_target_attestation_arguments(
    argv: Sequence[str],
    value_options: frozenset[str],
    flag_options: frozenset[str],
) -> str | None:
    """Refuse attempts to weaken the pre-parse argument contract.

    Every value-taking option and every boolean flag registered on the parser
    may occur at most once, in either space-separated or --option=value form:
    argparse's last-one-wins duplicate handling could otherwise silently hide
    an operator or launcher mistake. The protected attestation option must
    additionally use the unabbreviated, space-separated form and reference the
    launcher-staged _TARGET_ATTESTATION_PATH. Called with the option names
    derived from the actual parser, before parsing or any database connection.
    """

    protected_name = _TARGET_ATTESTATION_OPTION[2:]
    single_use_options = value_options | flag_options | {_TARGET_ATTESTATION_OPTION}
    counts = {option: 0 for option in single_use_options}
    attestation_value: str | None = None
    for index, token in enumerate(argv):
        if token in counts:
            counts[token] += 1
            if token == _TARGET_ATTESTATION_OPTION:
                attestation_value = argv[index + 1] if index + 1 < len(argv) else None
            continue
        if not token.startswith("--"):
            continue
        option_name = token[2:].partition("=")[0]
        if not option_name:
            continue
        if option_name == protected_name:
            return (
                f"{_TARGET_ATTESTATION_OPTION} does not accept the --option=value form; "
                "the trusted launcher passes the attestation path as a separate argument."
            )
        if protected_name.startswith(option_name):
            return (
                f"abbreviated forms of {_TARGET_ATTESTATION_OPTION} are refused; "
                "the trusted launcher passes the full option name."
            )
        options_equals_form = "--" + option_name
        if options_equals_form in counts:
            counts[options_equals_form] += 1
    for option in sorted(counts):
        if counts[option] > 1:
            return (
                f"duplicate {option} options are refused; supply each "
                "operator recovery argument at most once."
            )
    if counts[_TARGET_ATTESTATION_OPTION] == 1 and attestation_value != _TARGET_ATTESTATION_PATH:
        return (
            f"{_TARGET_ATTESTATION_OPTION} must reference the launcher-staged "
            f"{_TARGET_ATTESTATION_PATH}; custom attestation paths are refused."
        )
    return None


def recover_existing_user(
    db: Session,
    *,
    external_subject: str,
    workspace_name: str,
    expected_database_name: str,
    expected_schema_name: str = "public",
    operator_identity: str,
    approval_reference: str,
    target_identity: RecoveryTargetIdentity | None = None,
    token_ttl_days: int = 30,
) -> tuple[str, datetime]:
    """Issue one token and create a new owner workspace for the verified existing user.

    The caller must provide a fresh SQLAlchemy session, a fresh target attestation,
    and independently verified identity-provider/approval details. PostgreSQL TLS
    identity is enforced when the dedicated engine is created; live server address,
    deployment ID, database, and schema are rechecked in this transaction before writes.
    All database writes are atomic. The raw token is returned only after commit.
    """

    external_subject = _validated_text(
        external_subject, "External subject", 255, preserve_exact=True
    )
    workspace_name = _validated_text(workspace_name, "Workspace name", 255)
    expected_database_name = _validated_text(
        expected_database_name, "Expected database name", 63, preserve_exact=True
    )
    expected_schema_name = _validated_schema_name(expected_schema_name)
    operator_identity = _validated_text(
        operator_identity, "Operator identity", 128, preserve_exact=True
    )
    approval_reference = _validated_reference(approval_reference, "Approval reference")
    if type(token_ttl_days) is not int or not 1 <= token_ttl_days <= 365:
        raise OperatorRecoveryRefusedError("Token lifetime must be between 1 and 365 days.")

    if db.get_bind().dialect.name != "postgresql":
        raise OperatorRecoveryRefusedError(
            "Operator recovery requires PostgreSQL. No records were created."
        )

    if target_identity is None:
        raise OperatorRecoveryRefusedError(
            "A fresh Docker/PostgreSQL target attestation is required. No records were created."
        )

    raw_token: str
    expires_at: datetime
    try:
        with db.begin():
            # Verify this exact PostgreSQL connection inside the same transaction
            # that will perform recovery writes. There is no preflight/write gap.
            try:
                verify_recovery_target_identity(
                    db,
                    target_identity,
                    expected_database_name=expected_database_name,
                    expected_schema_name=expected_schema_name,
                )
            except RecoveryTargetIdentityError as exc:
                raise OperatorRecoveryRefusedError(str(exc)) from exc

            # Use the same transaction lock as bootstrap so these privileged
            # provisioning paths coordinate instead of racing one another.
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _BOOTSTRAP_LOCK_ID},
            )

            database_name = str(db.scalar(text("SELECT current_database()")) or "")
            if database_name != expected_database_name:
                raise OperatorRecoveryRefusedError(
                    "Connected database did not match --expected-database-name. "
                    "No records were created."
                )

            schema_name = str(db.scalar(text("SELECT current_schema()")) or "")
            schemas = db.scalar(text("SELECT current_schemas(false)"))
            if schema_name != expected_schema_name or list(schemas or []) != [expected_schema_name]:
                raise OperatorRecoveryRefusedError(
                    "Connected schema/search path did not match the expected schema. "
                    "No records were created."
                )

            try:
                _assert_read_committed(db)
            except BootstrapRefusedError as exc:
                raise OperatorRecoveryRefusedError(str(exc)) from None

            tables = set(inspect(db.connection()).get_table_names(schema=expected_schema_name))
            missing = sorted(_REQUIRED_TABLES - tables)
            if missing:
                raise OperatorRecoveryRefusedError(
                    "Required database tables are missing. "
                    "Apply approved migrations before recovery."
                )

            try:
                _assert_migration_head(db)
            except BootstrapRefusedError as exc:
                raise OperatorRecoveryRefusedError(str(exc)) from None

            previous_marker = db.scalar(
                select(AuditEvent.id).where(AuditEvent.action.in_(_MARKER_ACTIONS)).limit(1)
            )
            if previous_marker is not None:
                raise OperatorRecoveryRefusedError(
                    "A bootstrap or operator-recovery marker already exists; "
                    "refusing repeat provisioning."
                )

            user_count = int(db.scalar(select(func.count()).select_from(User)) or 0)
            if user_count != 1:
                raise OperatorRecoveryRefusedError(
                    "Recovery requires exactly one existing user. No records were created."
                )

            user = db.scalar(
                select(User).where(User.external_subject == external_subject).with_for_update()
            )
            if user is None or not user.is_active:
                raise OperatorRecoveryRefusedError(
                    "The exact external subject did not resolve to the sole active user. "
                    "No records were created."
                )

            workspace_count = int(db.scalar(select(func.count()).select_from(Workspace)) or 0)
            membership_count = int(
                db.scalar(select(func.count()).select_from(WorkspaceMembership)) or 0
            )
            if workspace_count or membership_count:
                raise OperatorRecoveryRefusedError(
                    "Recovery requires no existing workspaces or memberships. "
                    "No records were created."
                )

            now = datetime.now(UTC)
            live_token_id = db.scalar(
                select(ApiToken.id)
                .where(
                    ApiToken.revoked_at.is_(None),
                    or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
                )
                .limit(1)
            )
            if live_token_id is not None:
                raise OperatorRecoveryRefusedError(
                    "A currently usable token exists; operator recovery is not permitted."
                )

            raw_token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(days=token_ttl_days)

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
            db.flush()

            record_audit_event(
                db,
                actor_user_id=None,
                workspace_id=workspace.id,
                action=_RECOVERY_ACTION,
                resource_type="operator_recovery",
                resource_id=str(user.id),
                metadata={
                    "method": "operator_cli",
                    "operator_identity": operator_identity,
                    "approval_reference": approval_reference,
                    "workspace_role": WorkspaceRole.OWNER.value,
                    "token_name": _TOKEN_NAME,
                    "token_ttl_days": token_ttl_days,
                    "database_name": database_name,
                    "schema_name": schema_name,
                },
            )
            db.flush()
    except OperatorRecoveryRefusedError:
        raise
    except SQLAlchemyError:
        raise

    # The transaction has committed by this point. Never store the raw token.
    return raw_token, expires_at


def _build_parser() -> argparse.ArgumentParser:
    """Construct the recovery argument parser; the single source of option names."""

    parser = argparse.ArgumentParser(
        description=(
            "One-time, guarded token and owner-workspace recovery for the sole existing "
            "active EvidenceForge user."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--external-subject",
        required=True,
        help="Exact verified identity-provider subject.",
    )
    parser.add_argument(
        "--workspace-name",
        required=True,
        help="Name for the replacement owner workspace.",
    )
    parser.add_argument(
        "--expected-database-name",
        required=True,
        help="Exact PostgreSQL database name to target.",
    )
    parser.add_argument(
        "--expected-schema-name",
        default="public",
        help="Expected schema/search path (default: public).",
    )
    parser.add_argument(
        "--operator-identity",
        required=True,
        help="Approved operator handle; do not put secrets here.",
    )
    parser.add_argument(
        "--approval-reference",
        required=True,
        help="Approved incident/change/ticket reference.",
    )
    parser.add_argument(
        "--token-ttl-days",
        type=int,
        default=30,
        help="Bearer-token lifetime in days (1-365; default: 30).",
    )
    parser.add_argument(
        "--target-attestation-file",
        help=(
            "Fresh, short-lived attestation generated by scripts/operator-recovery.ps1; "
            "direct invocation without a verified target is refused."
        ),
    )
    parser.add_argument(
        "--identity-verified",
        action="store_true",
        help="Acknowledge independent identity-provider verification.",
    )
    parser.add_argument(
        "--confirm-recovery",
        action="store_true",
        help="Explicitly confirm this one-time recovery operation.",
    )
    return parser


def _parser_option_names(
    parser: argparse.ArgumentParser,
) -> tuple[frozenset[str], frozenset[str]]:
    """Partition the parser's long options into value-taking and flag options.

    Reads the parser's registered actions so the pre-parse duplicate guard and
    the parser can never drift apart. The help action is a flag like the two
    confirmation flags; duplicate occurrences of it are refused harmlessly.
    """

    value_options: set[str] = set()
    flag_options: set[str] = set()
    for action in parser._actions:
        for option_string in action.option_strings:
            if not option_string.startswith("--"):
                continue
            if action.nargs == 0:
                flag_options.add(option_string)
            else:
                value_options.add(option_string)
    return frozenset(value_options), frozenset(flag_options)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the one-time operator recovery CLI."""

    parser = _build_parser()
    resolved_argv: Sequence[str] = list(sys.argv[1:] if argv is None else argv)
    value_options, flag_options = _parser_option_names(parser)
    attestation_refusal = _check_target_attestation_arguments(
        resolved_argv, value_options, flag_options
    )
    if attestation_refusal is not None:
        print(f"Operator recovery refused: {attestation_refusal}", file=sys.stderr)
        return 2
    args = parser.parse_args(resolved_argv)

    if not args.identity_verified:
        print(
            "Operator recovery refused: independently verify the identity-provider "
            "subject and pass --identity-verified.",
            file=sys.stderr,
        )
        return 2
    if not args.confirm_recovery:
        print(
            "Operator recovery refused: pass --confirm-recovery only after verifying "
            "the database target and approved recovery request.",
            file=sys.stderr,
        )
        return 2

    if not args.target_attestation_file:
        print(
            "Operator recovery refused: launch through scripts/operator-recovery.ps1 "
            "with a fresh target attestation. No database was contacted.",
            file=sys.stderr,
        )
        return 2

    engine = None
    raw_token: str | None = None
    expires_at: datetime | None = None
    try:
        target_identity = load_target_identity(args.target_attestation_file)
        if (
            target_identity.database_name != args.expected_database_name
            or target_identity.schema_name != args.expected_schema_name
        ):
            raise RecoveryTargetIdentityError(
                "The requested database/schema does not match the target attestation."
            )
        engine = create_recovery_engine(target_identity)
        session_factory = create_session_factory(engine)
        with session_factory() as db:
            raw_token, expires_at = recover_existing_user(
                db,
                external_subject=args.external_subject,
                workspace_name=args.workspace_name,
                expected_database_name=args.expected_database_name,
                expected_schema_name=args.expected_schema_name,
                operator_identity=args.operator_identity,
                approval_reference=args.approval_reference,
                target_identity=target_identity,
                token_ttl_days=args.token_ttl_days,
            )
    except (OperatorRecoveryRefusedError, RecoveryTargetIdentityError) as exc:
        print(f"Operator recovery refused: {exc}", file=sys.stderr)
        return 2
    except BootstrapRefusedError as exc:
        print(f"Operator recovery refused: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(
            "Operator recovery failed before a token was displayed. Inspect database "
            "connectivity and state using approved procedures; do not retry until the "
            "transaction outcome is verified.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "Operator recovery failed before a token was displayed. Inspect runtime "
            "and database state using approved procedures; do not retry until the "
            "transaction outcome is verified.",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    if raw_token is None or expires_at is None:
        print(
            "Operator recovery did not return a token; verify database state before retrying.",
            file=sys.stderr,
        )
        return 1

    print("Operator recovery completed.")
    print(
        "Copy this bearer token now and store it in the approved secret manager. "
        "It will not be displayed again:"
    )
    print(raw_token, flush=True)
    print(f"Token expires at (UTC): {expires_at.isoformat()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
