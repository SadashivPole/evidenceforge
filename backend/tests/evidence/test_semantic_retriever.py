"""Focused service tests for SemanticRetriever."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.evidence.semantic.config import (
    DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    SemanticRetrievalConfig,
)
from app.evidence.semantic.errors import (
    InvalidQueryVectorError,
    SemanticAuthorizationError,
    SemanticConfigurationError,
    SemanticDimensionMismatchError,
    SemanticModelUnavailableError,
    SemanticQueryValidationError,
)
from app.evidence.semantic.service import SemanticRetriever
from tests.test_evidence_chunk_embeddings import _create_parent
from tests.test_evidence_semantic_retrieval import (
    _FakeSemanticModel,
    _unit_vector,
)


def test_semantic_retriever_initialization_defaults() -> None:
    retriever = SemanticRetriever()
    assert retriever.config == DEFAULT_SEMANTIC_RETRIEVAL_CONFIG
    assert retriever._model is None
    assert retriever._generator is None


def test_semantic_retriever_lazy_loads_model_and_fails_safely_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ensure sentence_transformers cannot be imported
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    retriever = SemanticRetriever()
    with pytest.raises(SemanticModelUnavailableError, match="unavailable"):
        retriever.generate_query_vector("data protection")


def test_semantic_retriever_query_validation_error() -> None:
    retriever = SemanticRetriever(model=_FakeSemanticModel())

    with pytest.raises(SemanticQueryValidationError, match="too short"):
        retriever.generate_query_vector("")

    with pytest.raises(SemanticQueryValidationError, match="too long"):
        retriever.generate_query_vector("a" * 300)


def test_semantic_retriever_missing_query_encoder_surfaces_configuration_error() -> None:
    model = _FakeSemanticModel(missing_query_encoder=True)
    retriever = SemanticRetriever(model=model)

    with pytest.raises(SemanticConfigurationError, match="does not expose"):
        retriever.generate_query_vector("data protection")


def test_semantic_retriever_search_requires_valid_workspace_id(
    db_session: Session,
) -> None:
    retriever = SemanticRetriever(model=_FakeSemanticModel())

    with pytest.raises(SemanticAuthorizationError, match="workspace_id"):
        retriever.search(db_session, query="data protection", workspace_id=None)  # type: ignore[arg-type]

    with pytest.raises(SemanticAuthorizationError, match="workspace_id"):
        retriever.search(db_session, query="data protection", workspace_id="not-a-uuid")  # type: ignore[arg-type]


def test_semantic_retriever_search_vector_requires_valid_vector(
    db_session: Session,
) -> None:
    workspace, _ = _create_parent(db_session, suffix="val-vec-1")
    retriever = SemanticRetriever()

    # Dimension mismatch
    with pytest.raises(SemanticDimensionMismatchError, match="does not match"):
        retriever.search_vector(
            db_session,
            query_vector=[1.0] * 100,
            workspace_id=workspace.id,
        )

    # Unnormalized
    with pytest.raises(InvalidQueryVectorError, match="not normalized"):
        retriever.search_vector(
            db_session,
            query_vector=[5.0] * 384,
            workspace_id=workspace.id,
        )

    # Non-finite
    vec = _unit_vector(0)
    vec[0] = float("nan")
    with pytest.raises(InvalidQueryVectorError, match="non-finite"):
        retriever.search_vector(
            db_session,
            query_vector=vec,
            workspace_id=workspace.id,
        )


def test_semantic_retriever_search_empty_workspace_returns_empty_tuple(
    db_session: Session,
) -> None:
    workspace, _ = _create_parent(db_session, suffix="empty-1")
    model = _FakeSemanticModel()
    retriever = SemanticRetriever(model=model)

    results = retriever.search(
        db_session,
        query="data retention",
        workspace_id=workspace.id,
    )
    assert results == ()


def test_semantic_retriever_custom_config_policy(db_session: Session) -> None:
    custom_config = SemanticRetrievalConfig(
        top_k=3,
        min_query_length=5,
        max_query_length=50,
        max_top_k=20,
    )
    model = _FakeSemanticModel()
    retriever = SemanticRetriever(model=model, config=custom_config)

    # Query length 4 is rejected with min_query_length=5
    with pytest.raises(SemanticQueryValidationError, match="too short"):
        retriever.generate_query_vector("data")

    # Query length 5 is accepted
    vec = retriever.generate_query_vector("datas")
    assert len(vec) == 384
