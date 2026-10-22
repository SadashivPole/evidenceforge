"""PostgreSQL regression coverage for migration 0008's existing-row backfill."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import ArgumentError

POSTGRES_URL = os.getenv("EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL", "").strip()
TEST_DATABASE_NAME = "evidenceforge_migration_backfill_it"
REVISION_0007 = "0007_evidence_chunk_embeddings"
REVISION_0008 = "0008_questionnaire_browser"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _postgres_configuration_reason(database_url: str) -> str | None:
    """Return why a configured migration-test DSN is not safe to mutate."""

    if not database_url:
        return (
            "requires EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL pointing to a dedicated "
            "PostgreSQL test database; it is not configured"
        )

    try:
        parsed_url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError) as exc:
        return (
            "requires EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL to be a valid "
            f"SQLAlchemy URL ({type(exc).__name__})"
        )

    if parsed_url.get_backend_name() != "postgresql":
        return "EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL must use PostgreSQL"

    if not parsed_url.host:
        return "EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL must include a loopback hostname"

    normalized_host = parsed_url.host.lower().rstrip(".")
    if normalized_host not in {"localhost", "127.0.0.1", "::1"}:
        return "EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL must target localhost or loopback"

    if parsed_url.query:
        return (
            "EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL must not include URL query parameters; "
            "connection-target overrides such as host and hostaddr are not permitted"
        )

    if parsed_url.database != TEST_DATABASE_NAME:
        return (
            "EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL must use the dedicated database "
            f"{TEST_DATABASE_NAME}"
        )

    return None


def _validate_postgres_configuration(database_url: str) -> str | None:
    """Fail closed when a non-empty DSN is not the dedicated loopback database."""

    reason = _postgres_configuration_reason(database_url)
    if database_url and reason is not None:
        raise RuntimeError(
            f"Unsafe EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL configuration: {reason}"
        )
    return reason


POSTGRES_CONFIGURATION_REASON = _validate_postgres_configuration(POSTGRES_URL)


@pytest.mark.parametrize(
    ("database_url", "must_raise"),
    [
        ("", False),
        ("sqlite:///:memory:", True),
        (
            "postgresql+psycopg://user:secret@db.example.test:5432/"
            "evidenceforge_migration_backfill_it",
            True,
        ),
        (
            "postgresql+psycopg://user:secret@localhost:5432/evidenceforge_test",
            True,
        ),
        (
            "postgresql+psycopg://user:secret@localhost:5432/"
            "evidenceforge_migration_backfill_it?host=db.example.test",
            True,
        ),
        (
            "postgresql+psycopg://user:secret@<host>:5432/evidenceforge_migration_backfill_it",
            True,
        ),
        ("not a valid SQLAlchemy URL", True),
        (
            "postgresql+psycopg://user:secret@localhost:5432/evidenceforge_migration_backfill_it",
            False,
        ),
    ],
)
def test_migration_database_url_guard(database_url: str, must_raise: bool) -> None:
    """Refuse unsafe database targets while allowing an unset local test URL."""

    if must_raise:
        with pytest.raises(RuntimeError, match="Unsafe EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL"):
            _validate_postgres_configuration(database_url)
        return

    reason = _validate_postgres_configuration(database_url)
    if database_url:
        assert reason is None
    else:
        assert reason is not None and "not configured" in reason


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run_alembic_upgrade(revision: str, database_url: str) -> None:
    """Run the repository's real Alembic upgrade against the guarded test DSN."""

    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if completed.returncode != 0:
        # Keep an accidental DSN echo out of assertion output and CI logs.
        stdout = completed.stdout.replace(database_url, "<redacted database URL>")
        stderr = completed.stderr.replace(database_url, "<redacted database URL>")
        pytest.fail(
            f"Alembic upgrade to {revision} failed with exit code {completed.returncode}.\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )


def _insert_synthetic_question(
    connection: Connection,
    *,
    expected_text: str,
) -> uuid.UUID:
    """Seed the minimum valid 0007-era rows using that revision's SQL schema."""

    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    questionnaire_id = uuid.uuid4()
    version_id = uuid.uuid4()
    question_id = uuid.uuid4()
    version_question_id = uuid.uuid4()
    suffix = uuid.uuid4().hex

    # Connection is a SQLAlchemy Connection; keeping the annotations generic
    # avoids binding this helper to driver-specific connection implementations.
    connection.execute(
        text(
            "INSERT INTO users (id, external_subject, email, display_name) "
            "VALUES (:id, :external_subject, :email, :display_name)"
        ),
        {
            "id": user_id,
            "external_subject": f"migration-0008:{suffix}",
            "email": f"migration-0008-{suffix}@example.test",
            "display_name": "Migration 0008 synthetic test user",
        },
    )
    connection.execute(
        text("INSERT INTO workspaces (id, name, created_by_user_id) VALUES (:id, :name, :user_id)"),
        {
            "id": workspace_id,
            "name": f"Migration 0008 synthetic workspace {suffix}",
            "user_id": user_id,
        },
    )
    connection.execute(
        text(
            "INSERT INTO questionnaires (id, workspace_id, name, created_by_user_id) "
            "VALUES (:id, :workspace_id, :name, :user_id)"
        ),
        {
            "id": questionnaire_id,
            "workspace_id": workspace_id,
            "name": f"Migration 0008 synthetic questionnaire {suffix}",
            "user_id": user_id,
        },
    )

    raw_digest = _sha256(f"raw:{suffix}")
    canonical_digest = _sha256(f"canonical:{suffix}")
    connection.execute(
        text(
            "INSERT INTO questionnaire_versions ("
            "id, workspace_id, questionnaire_id, version_number, raw_sha256, "
            "canonical_sha256, source_filename, parser_version, normalization_version, "
            "question_identity_version, canonical_hash_version, question_count, created_by_user_id"
            ") VALUES ("
            ":id, :workspace_id, :questionnaire_id, 1, :raw_sha256, :canonical_sha256, "
            ":source_filename, :parser_version, :normalization_version, "
            ":question_identity_version, :canonical_hash_version, 1, :user_id)"
        ),
        {
            "id": version_id,
            "workspace_id": workspace_id,
            "questionnaire_id": questionnaire_id,
            "raw_sha256": raw_digest,
            "canonical_sha256": canonical_digest,
            "source_filename": "migration-0008-synthetic.xlsx",
            "parser_version": "migration-test-parser-v1",
            "normalization_version": "migration-test-normalizer-v1",
            "question_identity_version": "migration-test-identity-v1",
            "canonical_hash_version": "migration-test-canonical-v1",
            "user_id": user_id,
        },
    )

    logical_question_text = "Synthetic logical question for migration 0008 backfill."
    connection.execute(
        text(
            "INSERT INTO questionnaire_questions ("
            "id, workspace_id, questionnaire_id, question_id, identity_fingerprint, "
            "identity_kind, normalized_question_text, normalized_sheet_name, "
            "normalized_section_path, source_question_id"
            ") VALUES ("
            ":id, :workspace_id, :questionnaire_id, :question_id, :identity_fingerprint, "
            "'explicit', :normalized_question_text, 'Security', CAST(:section_path AS json), "
            ":source_question_id)"
        ),
        {
            "id": question_id,
            "workspace_id": workspace_id,
            "questionnaire_id": questionnaire_id,
            "question_id": _sha256(f"question-id:{suffix}"),
            "identity_fingerprint": _sha256(f"identity:{suffix}"),
            "normalized_question_text": logical_question_text,
            "section_path": json.dumps(["Access Control"]),
            "source_question_id": "MIGRATION-0008-SYNTHETIC-001",
        },
    )
    connection.execute(
        text(
            "INSERT INTO questionnaire_version_questions ("
            "id, workspace_id, questionnaire_id, questionnaire_version_id, "
            "questionnaire_question_id, ordinal, source_row, sheet_name, section_path, "
            "normalized_question_text, source_question_id"
            ") VALUES ("
            ":id, :workspace_id, :questionnaire_id, :version_id, :question_id, "
            "1, 2, 'Security', CAST(:section_path AS json), :normalized_question_text, "
            ":source_question_id)"
        ),
        {
            "id": version_question_id,
            "workspace_id": workspace_id,
            "questionnaire_id": questionnaire_id,
            "version_id": version_id,
            "question_id": question_id,
            "section_path": json.dumps(["Access Control"]),
            "normalized_question_text": expected_text,
            "source_question_id": "MIGRATION-0008-SYNTHETIC-001",
        },
    )
    return version_question_id


def test_migration_0008_backfills_source_question_text_for_existing_rows() -> None:
    """Prove 0008 preserves existing question wording when upgrading 0007 data."""

    if not POSTGRES_URL:
        pytest.skip(
            "requires EVIDENCEFORGE_MIGRATION_TEST_DATABASE_URL pointing to a dedicated "
            "PostgreSQL database named evidenceforge_migration_backfill_it"
        )

    target_url = make_url(POSTGRES_URL)
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            actual_database = connection.scalar(text("SELECT current_database()"))
        if actual_database != TEST_DATABASE_NAME or actual_database != target_url.database:
            pytest.fail(
                "Refusing migration test: current_database() did not match the exact "
                f"dedicated database name {TEST_DATABASE_NAME}."
            )

        # This test owns the dedicated CI service database. Reset only its public
        # schema; the guard and current_database() check above precede this mutation.
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public AUTHORIZATION CURRENT_USER")

        _run_alembic_upgrade(REVISION_0007, POSTGRES_URL)

        with engine.connect() as connection:
            revision_before = connection.scalar(text("SELECT version_num FROM alembic_version"))
            column_before = connection.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'questionnaire_version_questions' "
                    "AND column_name = 'source_question_text')"
                )
            )
        assert revision_before == REVISION_0007
        assert column_before is False

        expected_text = (
            "Are privileged service accounts re-certified quarterly, including break-glass "
            "accounts? (synthetic — migration-0008-backfill-probe)"
        )
        with engine.begin() as connection:
            question_row_id = _insert_synthetic_question(
                connection,
                expected_text=expected_text,
            )

        _run_alembic_upgrade(REVISION_0008, POSTGRES_URL)

        with engine.connect() as connection:
            migrated_row = (
                connection.execute(
                    text(
                        "SELECT normalized_question_text, source_question_text "
                        "FROM questionnaire_version_questions WHERE id = :id"
                    ),
                    {"id": question_row_id},
                )
                .mappings()
                .one()
            )
            column_nullable = connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'questionnaire_version_questions' "
                    "AND column_name = 'source_question_text'"
                )
            )
            final_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

        assert migrated_row["normalized_question_text"] == expected_text
        assert migrated_row["source_question_text"].encode("utf-8") == expected_text.encode("utf-8")
        assert column_nullable == "NO"
        assert final_revision == REVISION_0008
    finally:
        engine.dispose()
