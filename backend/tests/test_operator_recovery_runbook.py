"""Documentation regression checks for the operator-recovery runbook.

The runbook is an operational safety artifact: the required target-profile
fields, schema versions, rollout ordering, and live-verification behavior must
stay documented in step with the recovery CLI and launcher. These assertions
read the actual document; they fail if the guidance is removed or drifts.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _locate_runbook() -> Path:
    """Resolve docs/operator-recovery-runbook.md relative to this test file."""

    for anchor in Path(__file__).resolve().parents:
        candidate = anchor / "docs" / "operator-recovery-runbook.md"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("docs/operator-recovery-runbook.md not found above this test file")


@pytest.fixture(scope="module")
def runbook_text() -> str:
    return _locate_runbook().read_text(encoding="utf-8")


def _assert_documented(document: str, phrases: list[str]) -> None:
    missing = [phrase for phrase in phrases if phrase not in document]
    assert not missing, f"runbook lost required documentation: {missing}"


def test_runbook_documents_profile_schema_version_and_new_fields(runbook_text: str) -> None:
    _assert_documented(
        runbook_text,
        [
            '"version": 2',
            "database_image_id",
            "database_port",
            "sha256:<64-hex-immutable-image-id>",
        ],
    )


def test_runbook_defines_database_port_as_container_internal_listening_port(
    runbook_text: str,
) -> None:
    _assert_documented(
        runbook_text,
        [
            "listening port inside the database container",
            "not any host-published port",
            "independently verify the real value",
        ],
    )


def test_runbook_documents_attestation_version_and_fail_closed_rollout(runbook_text: str) -> None:
    _assert_documented(
        runbook_text,
        [
            "schema version 2",
            "rejects version-1 attestations",
            "review and update every external trusted profile",
            "/run/evidenceforge/recovery-target.json",
            "fail closed",
            "Do not copy the illustrative values above into a real deployment profile",
        ],
    )


def test_runbook_documents_api_image_refresh_and_live_port_verification(
    runbook_text: str,
) -> None:
    _assert_documented(
        runbook_text,
        [
            "rebuilt API image that contains the current, patched recovery CLI",
            "refresh it whenever the approved CLI image is rebuilt",
            "its immutable image ID",
            "server address, and server port inside the same transaction used for recovery writes",
        ],
    )


def test_runbook_documents_postgres_environment_restrictions(runbook_text: str) -> None:
    _assert_documented(
        runbook_text,
        [
            "PGHOST",
            "PGPORT",
            "PGHOSTADDR",
            "ambient libpq settings cannot influence recovery connection targeting",
        ],
    )


def test_runbook_preserves_existing_safety_warnings(runbook_text: str) -> None:
    _assert_documented(
        runbook_text,
        [
            "approved incident/change request and a verified backup/recovery procedure",
            "sslmode=verify-full",
            "not protection against a compromised Docker host",
            "approved secret manager",
            "Never point test variables at an application database",
            "Do not copy illustrative values blindly.",
            "--identity-verified",
            "disposable PostgreSQL",
        ],
    )
