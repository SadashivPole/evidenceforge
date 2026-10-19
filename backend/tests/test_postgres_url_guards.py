"""Loopback-URL guard regression tests (review findings, 2026-10-19).

Pure parsing/guard tests only: no PostgreSQL server is required or contacted.
They verify that an unsafe EVIDENCEFORGE_TEST_DATABASE_URL fails closed at
module import, before any engine can be created, and that the documented
connection-target overrides (`?host=`, `?hostaddr=`, repeated keys, case
variants) can never pass the loopback guard.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

EVIDENCE_MODULES = [
    "test_evidence_chunk_embeddings_postgres",
    "test_evidence_embedding_generation_postgres",
    "test_evidence_freshness_postgres",
    "test_evidence_hybrid_retrieval_postgres",
    "test_evidence_persistence_postgres",
    "test_evidence_semantic_retrieval_postgres",
]

VALID_URL = "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test"

UNSAFE_URLS = [
    # Non-loopback authority host.
    "postgresql+psycopg://ef_user:redacted@db.internal/evidenceforge_test",
    # Query-string connection-target overrides: SQLAlchemy forwards them to
    # the driver, where 'host' overrides the authority host (documented) and
    # libpq 'hostaddr' takes precedence for the server network address.
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?host=remote.example",
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?hostaddr=203.0.113.7",
    # Repeated routing keys collapse into multi-value tuples; still rejected.
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?host=a.example&host=b.example",
    # Case variants of routing keys must not bypass the guard.
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?HOST=remote.example",
    # Deny-by-default: even non-routing query parameters are rejected, so no
    # query string can ever influence the connection target.
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?application_name=guard-test",
    # Query-string port override: psycopg accepts 'port' as a connect kwarg,
    # so this retargets the listener without touching the authority host.
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?port=6543",
    # Wrong target database name.
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_dev",
]

UNSAFE_ENV_URL = (
    "postgresql+psycopg://ef_user:redacted@localhost/evidenceforge_test?host=remote.example"
)


def _load_module(module_name: str, *, env_url: str | None) -> object:
    """Import one *_postgres test module with a controlled env URL.

    A fresh module name per load re-runs the module-level guard exactly the
    way pytest does when collecting the file.
    """
    path = Path(__file__).with_name(module_name + ".py")
    spec = importlib.util.spec_from_file_location(
        f"ef_review_{module_name}_{uuid.uuid4().hex}", path
    )
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_with_env(monkeypatch: pytest.MonkeyPatch, module_name: str, env_url: str | None):
    if env_url is None:
        monkeypatch.delenv("EVIDENCEFORGE_TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("EVIDENCEFORGE_TEST_DATABASE_URL", env_url)
    return _load_module(module_name, env_url=env_url)


@pytest.mark.parametrize("module_name", EVIDENCE_MODULES)
def test_valid_exact_loopback_url_passes(monkeypatch, module_name):
    module = _load_with_env(monkeypatch, module_name, None)
    assert module._postgres_configuration_reason(VALID_URL) is None


@pytest.mark.parametrize("module_name", EVIDENCE_MODULES)
@pytest.mark.parametrize("unsafe_url", UNSAFE_URLS)
def test_unsafe_url_fails_closed(monkeypatch, module_name, unsafe_url):
    module = _load_with_env(monkeypatch, module_name, None)
    reason = module._postgres_configuration_reason(unsafe_url)
    assert reason is not None, unsafe_url.split("@")[-1].split("/")[0]
    # The reason names the violated rule only; never echo URL content.
    assert "redacted" not in reason
    assert "remote.example" not in reason


@pytest.mark.parametrize("module_name", EVIDENCE_MODULES)
def test_missing_env_url_imports_harmlessly_and_marks_skip(
    monkeypatch,
    module_name,
):
    # With the variable unset, importing the module must NOT raise (the
    # module-level RuntimeError fires only for a present-but-unsafe URL), and
    # the module must carry a truthy skipif mark with a non-empty reason so
    # collection skips intentionally instead of failing.
    # (These properties follow from the module-level lines visible in the
    # reviewed diff: `pytestmark = pytest.mark.skipif(not POSTGRES_URL, ...)`;
    # the exact reason *string* depends on source outside the uploaded hunks
    # and is therefore asserted by shape, not by value.)
    module = _load_with_env(monkeypatch, module_name, None)
    marker = getattr(module, "pytestmark", None)
    assert marker is not None, "module missing pytestmark"
    assert marker.name == "skipif"
    assert marker.args and marker.args[0], "skip mark condition must be truthy"
    reason = marker.kwargs.get("reason")
    assert isinstance(reason, str) and reason, "skip reason must be meaningful"


@pytest.mark.parametrize("module_name", EVIDENCE_MODULES)
def test_unsafe_env_url_raises_before_any_engine_creation(
    monkeypatch,
    module_name,
):
    import sqlalchemy

    engine_calls = []

    def tripwire(*args, **kwargs):
        engine_calls.append((args, kwargs))
        raise AssertionError("create_engine must never run for an unsafe URL")

    monkeypatch.setattr(sqlalchemy, "create_engine", tripwire)
    with pytest.raises(RuntimeError, match="Unsafe EVIDENCEFORGE_TEST_DATABASE_URL"):
        _load_with_env(monkeypatch, module_name, UNSAFE_ENV_URL)
    # Module-level raise occurs before any fixture/test can create an engine.
    assert engine_calls == []
