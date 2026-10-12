"""Safe, local-only tests for bootstrap refusal paths."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.cli.bootstrap as bootstrap
from app.cli.bootstrap import BootstrapRefusedError


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated in-memory SQLite session without creating tables."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _invoke(
    db: Session,
    overrides: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Call bootstrap with harmless test-only identity values."""

    arguments: dict[str, Any] = {
        "external_subject": "test:bootstrap-user",
        "email": "bootstrap@example.test",
        "display_name": "Bootstrap Test",
        "workspace_name": "Bootstrap Test Workspace",
    }
    if overrides:
        arguments.update(overrides)
    return bootstrap.bootstrap_first_user(db, **arguments)


def test_non_postgresql_database_is_refused_without_creating_tables(
    db_session: Session,
) -> None:
    engine = db_session.get_bind()
    assert inspect(engine).get_table_names() == []

    with pytest.raises(BootstrapRefusedError, match="requires PostgreSQL"):
        _invoke(db_session)

    assert inspect(engine).get_table_names() == []


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"external_subject": ""}, "External subject must not be empty"),
        (
            {"external_subject": " subject "},
            "leading or trailing whitespace",
        ),
        ({"external_subject": "s" * 256}, "External subject exceeds"),
        ({"email": " "}, "Email must not be empty"),
        ({"email": "e" * 321}, "Email exceeds"),
        ({"display_name": ""}, "Display name must not be empty"),
        ({"display_name": "d" * 256}, "Display name exceeds"),
        ({"workspace_name": " "}, "Workspace name must not be empty"),
        ({"workspace_name": "w" * 256}, "Workspace name exceeds"),
    ],
)
def test_invalid_input_is_rejected_before_database_use(
    db_session: Session,
    overrides: dict[str, Any],
    expected_message: str,
) -> None:
    with pytest.raises(BootstrapRefusedError, match=expected_message):
        _invoke(db_session, overrides)

    assert inspect(db_session.get_bind()).get_table_names() == []


@pytest.mark.parametrize("token_ttl_days", [0, 366])
def test_token_lifetime_outside_allowed_range_is_rejected(
    db_session: Session,
    token_ttl_days: int,
) -> None:
    with pytest.raises(
        BootstrapRefusedError,
        match="between 1 and 365 days",
    ):
        _invoke(db_session, {"token_ttl_days": token_ttl_days})

    assert inspect(db_session.get_bind()).get_table_names() == []


def test_migration_guard_rejects_a_non_head_revision(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a stale revision response without connecting to PostgreSQL."""

    class StaleMigrationContext:
        def get_current_heads(self) -> list[str]:
            return ["0006_questionnaire_responses"]

    monkeypatch.setattr(
        bootstrap.MigrationContext,
        "configure",
        staticmethod(lambda _connection: StaleMigrationContext()),
    )

    with pytest.raises(
        BootstrapRefusedError,
        match="not at the repository head",
    ):
        bootstrap._assert_migration_head(db_session)

    assert inspect(db_session.get_bind()).get_table_names() == []


@pytest.mark.parametrize(
    ("isolation", "allowed"),
    [
        ("read committed", True),
        ("repeatable read", False),
        ("serializable", False),
    ],
)
def test_bootstrap_requires_read_committed_isolation(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    isolation: str,
    allowed: bool,
) -> None:
    """Isolation-policy tests use a mocked result and never contact PostgreSQL."""

    monkeypatch.setattr(db_session, "scalar", lambda _statement: isolation)

    if allowed:
        bootstrap._assert_read_committed(db_session)
    else:
        with pytest.raises(
            BootstrapRefusedError,
            match="requires READ COMMITTED",
        ):
            bootstrap._assert_read_committed(db_session)


@pytest.mark.parametrize(
    ("marker_result", "should_refuse"),
    [
        (None, False),
        ("previous-bootstrap-event", True),
    ],
)
def test_prior_bootstrap_marker_blocks_repeat_bootstrap(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    marker_result: str | None,
    should_refuse: bool,
) -> None:
    """Exercise the marker decision without database access."""

    monkeypatch.setattr(db_session, "scalar", lambda _statement: marker_result)

    if should_refuse:
        with pytest.raises(
            BootstrapRefusedError,
            match="successful bootstrap marker already exists",
        ):
            bootstrap._assert_no_prior_bootstrap(db_session)
    else:
        bootstrap._assert_no_prior_bootstrap(db_session)

    assert inspect(db_session.get_bind()).get_table_names() == []


def test_alembic_config_can_be_found_from_runtime_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed CLI can find an explicitly supplied runtime config."""

    config_path = tmp_path / "alembic.ini"
    config_path.write_text("[alembic]\nscript_location = migrations\n", encoding="utf-8")
    monkeypatch.setenv("EVIDENCEFORGE_ALEMBIC_CONFIG", str(config_path))

    assert bootstrap._resolve_alembic_config_path() == config_path.resolve()


def test_alembic_config_uses_current_directory_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI can locate config when launched from a deployment directory."""

    monkeypatch.delenv("EVIDENCEFORGE_ALEMBIC_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "alembic.ini"
    config_path.write_text("[alembic]\nscript_location = migrations\n", encoding="utf-8")

    assert bootstrap._resolve_alembic_config_path() == config_path.resolve()


def test_invalid_explicit_alembic_config_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-alembic.ini"
    monkeypatch.setenv("EVIDENCEFORGE_ALEMBIC_CONFIG", str(missing))

    with pytest.raises(
        BootstrapRefusedError,
        match="does not point to a readable Alembic configuration file",
    ):
        bootstrap._resolve_alembic_config_path()
