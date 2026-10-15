"""Unit tests for guarded operator recovery."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cli.operator_recovery import (
    OperatorRecoveryRefusedError,
    _build_parser,
    _parser_option_names,
    _validated_reference,
    _validated_schema_name,
    _validated_text,
    main,
    recover_existing_user,
)


def test_external_subject_preserves_exact_identity_text() -> None:
    assert (
        _validated_text("subject:123", "External subject", 255, preserve_exact=True)
        == "subject:123"
    )


@pytest.mark.parametrize("value", ["", " subject:123", "subject:123 ", "bad\nsubject"])
def test_external_subject_rejects_empty_whitespace_and_control_characters(value: str) -> None:
    with pytest.raises(OperatorRecoveryRefusedError):
        _validated_text(value, "External subject", 255, preserve_exact=True)


def test_reference_accepts_ticket_style_identifier() -> None:
    assert _validated_reference("SEC-1234/REC-8", "Approval reference") == "SEC-1234/REC-8"


@pytest.mark.parametrize("value", ["", "not safe", "ticket;secret", "bad\nref"])
def test_reference_rejects_unapproved_characters(value: str) -> None:
    with pytest.raises(OperatorRecoveryRefusedError):
        _validated_reference(value, "Approval reference")


def test_schema_name_requires_lowercase_postgres_identifier() -> None:
    assert _validated_schema_name("public") == "public"
    with pytest.raises(OperatorRecoveryRefusedError):
        _validated_schema_name("Public; drop schema public")


def test_recovery_refuses_sqlite_before_queries_or_writes() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        with Session(engine) as db:
            with pytest.raises(OperatorRecoveryRefusedError, match="requires PostgreSQL"):
                recover_existing_user(
                    db,
                    external_subject="approved-subject",
                    workspace_name="Recovered workspace",
                    expected_database_name="evidenceforge_test",
                    expected_schema_name="public",
                    operator_identity="operator@example.test",
                    approval_reference="SEC-1234",
                )
    finally:
        engine.dispose()


def test_cli_refuses_without_explicit_identity_verification(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "--external-subject",
            "approved-subject",
            "--workspace-name",
            "Recovered workspace",
            "--expected-database-name",
            "evidenceforge_test",
            "--operator-identity",
            "operator@example.test",
            "--approval-reference",
            "SEC-1234",
            "--confirm-recovery",
        ]
    )
    assert result == 2
    assert "--identity-verified" in capsys.readouterr().err


def test_cli_refuses_without_explicit_recovery_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "--external-subject",
            "approved-subject",
            "--workspace-name",
            "Recovered workspace",
            "--expected-database-name",
            "evidenceforge_test",
            "--operator-identity",
            "operator@example.test",
            "--approval-reference",
            "SEC-1234",
            "--identity-verified",
        ]
    )
    assert result == 2
    assert "--confirm-recovery" in capsys.readouterr().err


def test_cli_refuses_duplicate_target_attestation_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "--target-attestation-file",
            "first.json",
            "--target-attestation-file",
            "second.json",
        ]
    )
    assert result == 2
    assert "duplicate" in capsys.readouterr().err


def test_cli_refuses_equals_form_target_attestation_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["--target-attestation-file=untrusted.json"])
    assert result == 2
    assert "--option=value" in capsys.readouterr().err


@pytest.mark.parametrize(
    "token",
    ["--target-att", "--target-attestation", "--target-att=untrusted.json"],
)
def test_cli_refuses_abbreviated_target_attestation_option(
    capsys: pytest.CaptureFixture[str], token: str
) -> None:
    result = main([token])
    assert result == 2
    assert "abbreviated" in capsys.readouterr().err


def _valid_operator_argv() -> list[str]:
    return [
        "--external-subject",
        "approved-subject",
        "--workspace-name",
        "Recovered workspace",
        "--expected-database-name",
        "evidenceforge_test",
        "--expected-schema-name",
        "public",
        "--operator-identity",
        "operator@example.test",
        "--approval-reference",
        "SEC-1234",
        "--token-ttl-days",
        "30",
        "--identity-verified",
        "--confirm-recovery",
    ]


def test_cli_refuses_duplicate_external_subject_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main([*_valid_operator_argv(), "--external-subject", "different-subject"])
    assert result == 2
    assert "duplicate --external-subject" in capsys.readouterr().err


def test_cli_refuses_duplicate_expected_database_name_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main([*_valid_operator_argv(), "--expected-database-name", "other_database"])
    assert result == 2
    assert "duplicate --expected-database-name" in capsys.readouterr().err


def test_cli_refuses_custom_target_attestation_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main([*_valid_operator_argv(), "--target-attestation-file", "custom.json"])
    assert result == 2
    assert "/run/evidenceforge/recovery-target.json" in capsys.readouterr().err


def test_cli_accepts_launcher_staged_attestation_path_then_reaches_loader(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            *_valid_operator_argv(),
            "--target-attestation-file",
            "/run/evidenceforge/recovery-target.json",
        ]
    )
    # The canonical launcher-staged path passes argument validation; only
    # attestation loading can (and does) fail in this unit-test environment.
    assert result == 2
    err = capsys.readouterr().err
    assert "custom attestation paths" not in err
    assert "target-attestation" in err


@pytest.mark.parametrize(
    ("option", "duplicate"),
    [
        ("--workspace-name", "Duplicate workspace"),
        ("--expected-schema-name", "other_schema"),
        ("--operator-identity", "second@example.test"),
        ("--approval-reference", "SEC-9999"),
        ("--token-ttl-days", "7"),
    ],
)
def test_cli_refuses_repeated_value_options(
    capsys: pytest.CaptureFixture[str], option: str, duplicate: str
) -> None:
    result = main([*_valid_operator_argv(), option, duplicate])
    assert result == 2
    assert f"duplicate {option}" in capsys.readouterr().err


def test_cli_refuses_mixed_space_and_equals_form_duplicate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main([*_valid_operator_argv(), "--workspace-name=Duplicate workspace"])
    assert result == 2
    assert "duplicate --workspace-name" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--identity-verified", "--confirm-recovery"])
def test_cli_refuses_repeated_confirmation_flags(
    capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    result = main([*_valid_operator_argv(), flag])
    assert result == 2
    assert f"duplicate {flag}" in capsys.readouterr().err


def test_duplicate_protection_covers_every_parser_option() -> None:
    value_options, flag_options = _parser_option_names(_build_parser())
    assert value_options == {
        "--target-attestation-file",
        "--external-subject",
        "--workspace-name",
        "--expected-database-name",
        "--expected-schema-name",
        "--operator-identity",
        "--approval-reference",
        "--token-ttl-days",
    }
    assert {"--identity-verified", "--confirm-recovery"} <= flag_options


def test_cli_disables_long_option_abbreviation() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "--external-subject",
                "approved-subject",
                "--workspace-name",
                "Recovered workspace",
                "--expected-database-name",
                "evidenceforge_test",
                "--operator-identity",
                "operator@example.test",
                "--approval-reference",
                "SEC-1234",
                "--identity-verified",
                "--confirm-recovery",
                "--token-ttl",
                "7",
            ]
        )
    assert exit_info.value.code == 2
