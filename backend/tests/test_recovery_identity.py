"""Unit tests for the operator-recovery target identity guard."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import URL

import app.cli.recovery_identity as recovery_identity
from app.cli.recovery_identity import (
    RecoveryTargetIdentity,
    RecoveryTargetIdentityError,
    create_recovery_engine,
    load_target_identity,
    verify_recovery_target_identity,
)


def _attestation(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "version": 2,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "docker_context": "default",
        "compose_project": "evidenceforge-recovery-test",
        "compose_service": "db",
        "container_id": "a" * 64,
        "deployment_id": "ef-recovery-test-01",
        "server_address": "172.20.0.4",
        "server_port": 5432,
        "tls_hostname": "pg-ef-recovery-test-01.evidenceforge.internal",
        "database_name": "evidenceforge_recovery_test",
        "schema_name": "public",
    }
    data.update(overrides)
    return data


def _target(tmp_path: Path, **overrides: object) -> RecoveryTargetIdentity:
    path = tmp_path / "target.json"
    path.write_text(json.dumps(_attestation(**overrides)), encoding="utf-8")
    return load_target_identity(path)


@pytest.mark.parametrize("server_port", ["5432", "5432.0", 5432.0, 0, 65536, True, None])
def test_refuses_invalid_attested_server_port(tmp_path: Path, server_port: object) -> None:
    with pytest.raises(RecoveryTargetIdentityError, match="server_port"):
        _target(tmp_path, server_port=server_port)


def test_loads_fresh_target_attestation(tmp_path: Path) -> None:
    target = _target(tmp_path)
    assert target.compose_service == "db"
    assert target.server_address == "172.20.0.4"
    assert target.deployment_id == "ef-recovery-test-01"


def test_refuses_stale_target_attestation(tmp_path: Path) -> None:
    created_at = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    with pytest.raises(RecoveryTargetIdentityError, match="stale"):
        _target(tmp_path, created_at_utc=created_at)


def test_refuses_non_unique_or_invalid_tls_hostname(tmp_path: Path) -> None:
    with pytest.raises(RecoveryTargetIdentityError, match="TLS hostname"):
        _target(tmp_path, tls_hostname="db")


def test_refuses_invalid_container_id(tmp_path: Path) -> None:
    with pytest.raises(RecoveryTargetIdentityError, match="container ID"):
        _target(tmp_path, container_id="short-id")


def test_engine_requires_dedicated_recovery_url_and_verified_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    ca_path = tmp_path / "recovery-ca.pem"
    ca_path.write_text("test-only placeholder; no connection will be made", encoding="utf-8")
    monkeypatch.setattr(recovery_identity, "_RECOVERY_CA_CERTIFICATE_PATH", str(ca_path))
    url = URL.create(
        "postgresql+psycopg",
        username="test-only",
        password="never-log-this",
        host=target.tls_hostname,
        port=5432,
        database=target.database_name,
        query={"sslmode": "verify-full", "sslrootcert": str(ca_path)},
    )
    monkeypatch.setenv(
        "EVIDENCEFORGE_RECOVERY_DATABASE_URL",
        url.render_as_string(hide_password=False),
    )

    class _EngineStub:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    engine_stub = _EngineStub()
    captured: dict[str, object] = {}

    def fake_create_engine(url: object, **kwargs: object) -> _EngineStub:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return engine_stub

    monkeypatch.setattr(recovery_identity, "create_engine", fake_create_engine)
    engine = create_recovery_engine(target)
    engine.dispose()
    assert engine_stub.disposed
    assert captured["kwargs"] == {
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 10, "options": ""},
    }


def test_engine_refuses_insecure_ssl_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _target(tmp_path)
    ca_path = tmp_path / "recovery-ca.pem"
    ca_path.write_text("test-only placeholder", encoding="utf-8")
    monkeypatch.setattr(recovery_identity, "_RECOVERY_CA_CERTIFICATE_PATH", str(ca_path))
    url = URL.create(
        "postgresql+psycopg",
        username="test-only",
        password="not-a-secret",
        host=target.tls_hostname,
        port=5432,
        database=target.database_name,
        query={"sslmode": "require", "sslrootcert": str(ca_path)},
    )
    monkeypatch.setenv(
        "EVIDENCEFORGE_RECOVERY_DATABASE_URL",
        url.render_as_string(hide_password=False),
    )
    with pytest.raises(RecoveryTargetIdentityError, match="verify-full"):
        create_recovery_engine(target)


def test_engine_refuses_hostname_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _target(tmp_path)
    ca_path = tmp_path / "recovery-ca.pem"
    ca_path.write_text("test-only placeholder", encoding="utf-8")
    monkeypatch.setattr(recovery_identity, "_RECOVERY_CA_CERTIFICATE_PATH", str(ca_path))
    url = URL.create(
        "postgresql+psycopg",
        username="test-only",
        password="not-a-secret",
        host="wrong.evidenceforge.internal",
        port=5432,
        database=target.database_name,
        query={"sslmode": "verify-full", "sslrootcert": str(ca_path)},
    )
    monkeypatch.setenv(
        "EVIDENCEFORGE_RECOVERY_DATABASE_URL",
        url.render_as_string(hide_password=False),
    )
    with pytest.raises(RecoveryTargetIdentityError, match="hostname"):
        create_recovery_engine(target)


def test_engine_refuses_connection_options_that_can_override_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    ca_path = tmp_path / "recovery-ca.pem"
    ca_path.write_text("test-only placeholder", encoding="utf-8")
    monkeypatch.setattr(recovery_identity, "_RECOVERY_CA_CERTIFICATE_PATH", str(ca_path))
    url = URL.create(
        "postgresql+psycopg",
        username="test-only",
        password="not-a-secret",
        host=target.tls_hostname,
        port=5432,
        database=target.database_name,
        query={
            "sslmode": "verify-full",
            "sslrootcert": str(ca_path),
            "options": "-c evidenceforge.deployment_id=spoofed",
        },
    )
    monkeypatch.setenv(
        "EVIDENCEFORGE_RECOVERY_DATABASE_URL",
        url.render_as_string(hide_password=False),
    )
    with pytest.raises(RecoveryTargetIdentityError, match="override option 'options'"):
        create_recovery_engine(target)


def test_engine_refuses_libpq_environment_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    ca_path = tmp_path / "recovery-ca.pem"
    ca_path.write_text("test-only placeholder", encoding="utf-8")
    monkeypatch.setattr(recovery_identity, "_RECOVERY_CA_CERTIFICATE_PATH", str(ca_path))
    url = URL.create(
        "postgresql+psycopg",
        username="test-only",
        password="not-a-secret",
        host=target.tls_hostname,
        port=5432,
        database=target.database_name,
        query={"sslmode": "verify-full", "sslrootcert": str(ca_path)},
    )
    monkeypatch.setenv(
        "EVIDENCEFORGE_RECOVERY_DATABASE_URL",
        url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("PGOPTIONS", "-c evidenceforge.deployment_id=spoofed")
    with pytest.raises(RecoveryTargetIdentityError, match="PGOPTIONS"):
        create_recovery_engine(target)


class _FakeResult:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> _FakeResult:
        return self

    def one(self) -> dict[str, object]:
        return self._row


class _FakeSession:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self.row)


def test_live_sql_identity_must_match_target_attestation(tmp_path: Path) -> None:
    target = _target(tmp_path)
    session = _FakeSession(
        {
            "database_name": target.database_name,
            "schema_name": target.schema_name,
            "schemas": [target.schema_name],
            "server_address": target.server_address,
            "server_port": target.server_port,
            "deployment_id": target.deployment_id,
        }
    )
    verify_recovery_target_identity(
        session,  # type: ignore[arg-type]
        target,
        expected_database_name=target.database_name,
        expected_schema_name=target.schema_name,
    )


def test_live_sql_identity_normalizes_postgres_inet_prefix(tmp_path: Path) -> None:
    target = _target(tmp_path)
    # inet_server_addr()::text commonly returns an address with its netmask.
    address_with_prefix = f"{target.server_address}/32"
    session = _FakeSession(
        {
            "database_name": target.database_name,
            "schema_name": target.schema_name,
            "schemas": [target.schema_name],
            "server_address": address_with_prefix,
            "server_port": target.server_port,
            "deployment_id": target.deployment_id,
        }
    )
    verify_recovery_target_identity(
        session,  # type: ignore[arg-type]
        target,
        expected_database_name=target.database_name,
        expected_schema_name=target.schema_name,
    )


def test_live_sql_identity_refuses_wrong_server_address(tmp_path: Path) -> None:
    target = _target(tmp_path)
    session = _FakeSession(
        {
            "database_name": target.database_name,
            "schema_name": target.schema_name,
            "schemas": [target.schema_name],
            "server_address": "172.20.0.99",
            "server_port": target.server_port,
            "deployment_id": target.deployment_id,
        }
    )
    with pytest.raises(RecoveryTargetIdentityError, match="server address"):
        verify_recovery_target_identity(
            session,  # type: ignore[arg-type]
            target,
            expected_database_name=target.database_name,
            expected_schema_name=target.schema_name,
        )


def test_live_sql_identity_refuses_wrong_deployment_id(tmp_path: Path) -> None:
    target = _target(tmp_path)
    session = _FakeSession(
        {
            "database_name": target.database_name,
            "schema_name": target.schema_name,
            "schemas": [target.schema_name],
            "server_address": target.server_address,
            "server_port": target.server_port,
            "deployment_id": "another-deployment",
        }
    )
    with pytest.raises(RecoveryTargetIdentityError, match="deployment ID"):
        verify_recovery_target_identity(
            session,  # type: ignore[arg-type]
            target,
            expected_database_name=target.database_name,
            expected_schema_name=target.schema_name,
        )


def test_live_sql_identity_refuses_mismatched_expected_database(tmp_path: Path) -> None:
    target = _target(tmp_path)
    session = _FakeSession({})
    with pytest.raises(RecoveryTargetIdentityError, match="did not match"):
        verify_recovery_target_identity(
            session,  # type: ignore[arg-type]
            target,
            expected_database_name="wrong_database",
            expected_schema_name=target.schema_name,
        )


def _install_engine_url_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    port: int | None = 5432,
    extra_query: dict[str, str] | None = None,
) -> None:
    ca_path = tmp_path / "recovery-ca.pem"
    ca_path.write_text("test-only placeholder; no connection will be made", encoding="utf-8")
    monkeypatch.setattr(recovery_identity, "_RECOVERY_CA_CERTIFICATE_PATH", str(ca_path))
    query = {"sslmode": "verify-full", "sslrootcert": str(ca_path)}
    if extra_query:
        query.update(extra_query)
    url = URL.create(
        "postgresql+psycopg",
        username="test-only",
        password="test-only",
        host="pg-ef-recovery-test-01.evidenceforge.internal",
        port=port,
        database="evidenceforge_recovery_test",
        query=query,
    )
    monkeypatch.setenv(
        "EVIDENCEFORGE_RECOVERY_DATABASE_URL",
        url.render_as_string(hide_password=False),
    )


def test_engine_accepts_matching_explicit_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    _install_engine_url_env(tmp_path, monkeypatch, port=5432)

    class _EngineStub:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(recovery_identity, "create_engine", lambda *a, **k: _EngineStub())
    assert create_recovery_engine(target) is not None


def test_engine_refuses_url_port_mismatch_with_attested_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    _install_engine_url_env(tmp_path, monkeypatch, port=5433)
    with pytest.raises(RecoveryTargetIdentityError, match="URL port"):
        create_recovery_engine(target)


@pytest.mark.parametrize(
    "extra_query",
    [
        {"host": "untrusted.example.internal"},
        {"port": "5433"},
        {"HOST": "untrusted.example.internal"},
    ],
)
def test_engine_refuses_url_query_host_and_port_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_query: dict[str, str],
) -> None:
    target = _target(tmp_path)
    _install_engine_url_env(tmp_path, monkeypatch, extra_query=extra_query)
    override = next(iter(extra_query))
    with pytest.raises(RecoveryTargetIdentityError, match=f"override option '{override}'"):
        create_recovery_engine(target)


def test_live_sql_identity_refuses_wrong_server_port(tmp_path: Path) -> None:
    target = _target(tmp_path)
    session = _FakeSession(
        {
            "database_name": target.database_name,
            "schema_name": target.schema_name,
            "schemas": [target.schema_name],
            "server_address": target.server_address,
            "server_port": 5433,
            "deployment_id": target.deployment_id,
        }
    )
    with pytest.raises(RecoveryTargetIdentityError, match="server port"):
        verify_recovery_target_identity(
            session,  # type: ignore[arg-type]
            target,
            expected_database_name=target.database_name,
            expected_schema_name=target.schema_name,
        )


def test_live_sql_identity_refuses_unverifiable_server_port(tmp_path: Path) -> None:
    target = _target(tmp_path)
    session = _FakeSession(
        {
            "database_name": target.database_name,
            "schema_name": target.schema_name,
            "schemas": [target.schema_name],
            "server_address": target.server_address,
            "server_port": None,
            "deployment_id": target.deployment_id,
        }
    )
    with pytest.raises(RecoveryTargetIdentityError, match="verifiable server port"):
        verify_recovery_target_identity(
            session,  # type: ignore[arg-type]
            target,
            expected_database_name=target.database_name,
            expected_schema_name=target.schema_name,
        )


def test_refuses_legacy_version_one_target_attestation(tmp_path: Path) -> None:
    # The patched CLI (schema version 2) must refuse an attestation written by
    # a pre-patch launcher, instead of silently tolerating a missing server_port.
    with pytest.raises(RecoveryTargetIdentityError, match="schema version"):
        _target(tmp_path, version=1)


def test_legacy_loader_constant_refuses_version_two_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Direction check for mixed deployments. The pre-patch loader differs from
    # the patched loader only in the module-level _ATTESTATION_VERSION constant
    # (the version gate line itself is unchanged), so the legacy behavior is
    # faithfully simulated by restoring the previous constant value.
    monkeypatch.setattr(recovery_identity, "_ATTESTATION_VERSION", 1)
    with pytest.raises(RecoveryTargetIdentityError, match="schema version"):
        _target(tmp_path)


def test_engine_refuses_explicit_port_zero_instead_of_treating_it_as_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    _install_engine_url_env(tmp_path, monkeypatch, port=0)
    with pytest.raises(RecoveryTargetIdentityError, match="URL port"):
        create_recovery_engine(target)


def test_engine_accepts_omitted_port_defaulting_to_postgresql_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    _install_engine_url_env(tmp_path, monkeypatch, port=None)

    class _EngineStub:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(recovery_identity, "create_engine", lambda *a, **k: _EngineStub())
    assert create_recovery_engine(target) is not None


@pytest.mark.parametrize("variable", ["PGHOST", "PGPORT", "PGHOSTADDR"])
def test_engine_refuses_postgres_endpoint_environment_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    target = _target(tmp_path)
    _install_engine_url_env(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, "6432" if variable == "PGPORT" else "10.9.9.9")

    def forbid_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("create_recovery_engine must refuse before creating connections")

    monkeypatch.setattr(recovery_identity, "create_engine", forbid_connection)
    with pytest.raises(RecoveryTargetIdentityError, match=f"{variable} must be unset"):
        create_recovery_engine(target)
