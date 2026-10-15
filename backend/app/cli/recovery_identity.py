"""Fail-closed Docker/PostgreSQL target identity checks for operator recovery.

The host-side launcher validates Docker metadata and writes a short-lived JSON
attestation. This module independently enforces PostgreSQL TLS hostname
verification and compares the attested endpoint with values reported by the
connected PostgreSQL server before recovery writes can begin.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


class RecoveryTargetIdentityError(RuntimeError):
    """Raised when the database target cannot be independently verified."""


_ATTESTATION_VERSION = 2
_RECOVERY_CA_CERTIFICATE_PATH = "/run/evidenceforge/recovery-ca.pem"
_ATTESTATION_MAX_AGE = timedelta(minutes=2)
_ATTESTATION_MAX_FUTURE_SKEW = timedelta(seconds=30)
_DEPLOYMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


@dataclass(frozen=True, slots=True)
class RecoveryTargetIdentity:
    """Fresh facts captured by the trusted host-side Docker launcher."""

    created_at_utc: datetime
    docker_context: str
    compose_project: str
    compose_service: str
    container_id: str
    deployment_id: str
    server_address: str
    server_port: int
    tls_hostname: str
    database_name: str
    schema_name: str


def _required_text(data: dict[str, Any], key: str, max_length: int = 255) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise RecoveryTargetIdentityError(
            f"The target attestation field '{key}' is missing or invalid."
        )
    if len(value) > max_length or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise RecoveryTargetIdentityError(f"The target attestation field '{key}' is invalid.")
    return value


def _required_port(data: dict[str, Any], key: str) -> int:
    """Read a required TCP port field; fail closed unless it is a valid integer."""

    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise RecoveryTargetIdentityError(
            f"The target attestation field '{key}' is missing or invalid."
        )
    return value


def load_target_identity(path: str | Path) -> RecoveryTargetIdentity:
    """Load and validate a fresh launcher attestation; never log its contents."""

    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise RecoveryTargetIdentityError(
            "A readable, valid target-attestation file is required."
        ) from None

    if not isinstance(data, dict) or data.get("version") != _ATTESTATION_VERSION:
        raise RecoveryTargetIdentityError(
            "The target attestation has an unsupported schema version."
        )

    created_text = _required_text(data, "created_at_utc", 64)
    try:
        created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
    except ValueError:
        raise RecoveryTargetIdentityError("The target-attestation timestamp is invalid.") from None
    if created_at.tzinfo is None:
        raise RecoveryTargetIdentityError(
            "The target-attestation timestamp must include a timezone."
        )
    now = datetime.now(UTC)
    age = now - created_at.astimezone(UTC)
    if age > _ATTESTATION_MAX_AGE or age < -_ATTESTATION_MAX_FUTURE_SKEW:
        raise RecoveryTargetIdentityError(
            "The target attestation is stale or is dated in the future; rerun the host preflight."
        )

    context = _required_text(data, "docker_context", 128)
    project = _required_text(data, "compose_project", 128)
    service = _required_text(data, "compose_service", 128)
    container_id = _required_text(data, "container_id", 64).lower()
    deployment_id = _required_text(data, "deployment_id", 128)
    server_address = _required_text(data, "server_address", 64)
    server_port = _required_port(data, "server_port")
    tls_hostname = _required_text(data, "tls_hostname", 253).lower().rstrip(".")
    database_name = _required_text(data, "database_name", 63)
    schema_name = _required_text(data, "schema_name", 63)

    if not _NAME_RE.fullmatch(project) or not _NAME_RE.fullmatch(service):
        raise RecoveryTargetIdentityError(
            "The target attestation contains an invalid Compose project or service."
        )
    if not _CONTAINER_ID_RE.fullmatch(container_id):
        raise RecoveryTargetIdentityError(
            "The target attestation does not contain a full Docker container ID."
        )
    if not _DEPLOYMENT_ID_RE.fullmatch(deployment_id):
        raise RecoveryTargetIdentityError(
            "The target attestation contains an invalid deployment ID."
        )
    if not _DNS_HOST_RE.fullmatch(tls_hostname):
        raise RecoveryTargetIdentityError(
            "The target attestation must use a deployment-specific DNS TLS hostname."
        )
    try:
        ipaddress.ip_address(server_address)
    except ValueError:
        raise RecoveryTargetIdentityError(
            "The target attestation does not contain a valid PostgreSQL server address."
        ) from None
    if not _NAME_RE.fullmatch(database_name) or not re.fullmatch(
        r"^[a-z_][a-z0-9_]{0,62}$", schema_name
    ):
        raise RecoveryTargetIdentityError(
            "The target attestation contains an invalid database or schema name."
        )

    return RecoveryTargetIdentity(
        created_at_utc=created_at.astimezone(UTC),
        docker_context=context,
        compose_project=project,
        compose_service=service,
        container_id=container_id,
        deployment_id=deployment_id,
        server_address=str(ipaddress.ip_address(server_address)),
        server_port=server_port,
        tls_hostname=tls_hostname,
        database_name=database_name,
        schema_name=schema_name,
    )


def create_recovery_engine(target: RecoveryTargetIdentity) -> Engine:
    """Create a dedicated recovery engine with mandatory TLS hostname checks.

    The general application DATABASE_URL is deliberately not used. The URL is
    taken from EVIDENCEFORGE_RECOVERY_DATABASE_URL and is never logged or echoed.
    """

    raw_url = os.environ.get("EVIDENCEFORGE_RECOVERY_DATABASE_URL", "")
    if not raw_url.strip():
        raise RecoveryTargetIdentityError(
            "EVIDENCEFORGE_RECOVERY_DATABASE_URL is required; the application DSN is not accepted."
        )
    try:
        url = make_url(raw_url)
    except Exception:
        raise RecoveryTargetIdentityError(
            "The dedicated recovery database URL is invalid."
        ) from None

    if url.get_backend_name() != "postgresql":
        raise RecoveryTargetIdentityError("Operator recovery requires a PostgreSQL recovery URL.")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise RecoveryTargetIdentityError(
            "Operator recovery requires the approved psycopg PostgreSQL driver."
        )

    actual_host = (url.host or "").lower().rstrip(".")
    if actual_host != target.tls_hostname:
        raise RecoveryTargetIdentityError(
            "The recovery URL hostname does not match the freshly inspected "
            "deployment TLS hostname."
        )
    if url.database != target.database_name:
        raise RecoveryTargetIdentityError(
            "The recovery URL database name does not match the target attestation."
        )
    effective_port = 5432 if url.port is None else url.port
    if effective_port != target.server_port:
        raise RecoveryTargetIdentityError(
            "The recovery URL port does not match the inspected PostgreSQL container port."
        )

    sslmode = url.query.get("sslmode")
    root_cert = url.query.get("sslrootcert")
    if not isinstance(sslmode, str) or sslmode != "verify-full":
        raise RecoveryTargetIdentityError(
            "PostgreSQL recovery requires sslmode=verify-full; insecure TLS modes are refused."
        )
    if not isinstance(root_cert, str) or not root_cert:
        raise RecoveryTargetIdentityError(
            "PostgreSQL recovery requires a trusted sslrootcert file."
        )
    prohibited_query_overrides = {"options", "service", "servicefile", "hostaddr", "host", "port"}
    for key in url.query:
        if key.lower() in prohibited_query_overrides:
            raise RecoveryTargetIdentityError(
                f"The recovery URL may not contain the PostgreSQL override option '{key}'."
            )
    for name in ("PGOPTIONS", "PGSERVICE", "PGSERVICEFILE", "PGHOST", "PGPORT", "PGHOSTADDR"):
        if os.environ.get(name):
            raise RecoveryTargetIdentityError(
                f"The PostgreSQL environment override {name} must be unset for recovery."
            )

    if root_cert != _RECOVERY_CA_CERTIFICATE_PATH:
        raise RecoveryTargetIdentityError(
            "sslrootcert must use the trusted CA mounted by the verified host launcher."
        )
    cert_path = Path(root_cert)
    if not cert_path.is_absolute() or not cert_path.is_file() or not os.access(cert_path, os.R_OK):
        raise RecoveryTargetIdentityError(
            "The configured PostgreSQL CA certificate path must be readable "
            "in the recovery container."
        )

    try:
        return create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10, "options": ""},
        )
    except Exception:
        raise RecoveryTargetIdentityError(
            "The dedicated recovery database engine could not be configured."
        ) from None


def verify_recovery_target_identity(
    db: Session,
    target: RecoveryTargetIdentity,
    *,
    expected_database_name: str,
    expected_schema_name: str,
) -> None:
    """Cross-check the live SQL endpoint against the fresh Docker attestation.

    This runs inside the same transaction and connection used by recovery, before
    the advisory lock or any recovery writes.
    """

    age = datetime.now(UTC) - target.created_at_utc.astimezone(UTC)
    if age > _ATTESTATION_MAX_AGE or age < -_ATTESTATION_MAX_FUTURE_SKEW:
        raise RecoveryTargetIdentityError(
            "The target attestation expired before recovery began; rerun the host preflight."
        )
    if expected_database_name != target.database_name or expected_schema_name != target.schema_name:
        raise RecoveryTargetIdentityError(
            "The requested database/schema did not match the Docker target attestation."
        )

    row = (
        db.execute(
            text(
                "SELECT current_database() AS database_name, "
                "current_schema() AS schema_name, "
                "current_schemas(false) AS schemas, "
                "inet_server_addr()::text AS server_address, "
                "inet_server_port() AS server_port, "
                "current_setting('evidenceforge.deployment_id', true) AS deployment_id"
            )
        )
        .mappings()
        .one()
    )

    database = str(row.get("database_name") or "")
    schema = str(row.get("schema_name") or "")
    schemas = list(row.get("schemas") or [])
    server_text = row.get("server_address")
    deployment_id = str(row.get("deployment_id") or "")

    if database != target.database_name:
        raise RecoveryTargetIdentityError(
            "The connected PostgreSQL database does not match the target attestation."
        )
    if schema != target.schema_name or schemas != [target.schema_name]:
        raise RecoveryTargetIdentityError(
            "The connected PostgreSQL schema/search path does not match the target attestation."
        )
    try:
        # PostgreSQL renders inet values with a host prefix (for example,
        # 172.17.0.2/32). Parse the interface form and compare its IP address.
        server_address = str(ipaddress.ip_interface(str(server_text)).ip)
        target_server_address = str(ipaddress.ip_interface(target.server_address).ip)
    except ValueError:
        raise RecoveryTargetIdentityError(
            "PostgreSQL did not report a verifiable TCP server address."
        ) from None
    if server_address != target_server_address:
        raise RecoveryTargetIdentityError(
            "The connected PostgreSQL server address differs from the inspected Docker container."
        )
    try:
        server_port = int(str(row.get("server_port")))
    except (TypeError, ValueError):
        raise RecoveryTargetIdentityError(
            "PostgreSQL did not report a verifiable server port."
        ) from None
    if server_port != target.server_port:
        raise RecoveryTargetIdentityError(
            "The connected PostgreSQL server port differs from the inspected Docker container."
        )
    if deployment_id != target.deployment_id:
        raise RecoveryTargetIdentityError(
            "The PostgreSQL deployment ID does not match the inspected Docker deployment."
        )
