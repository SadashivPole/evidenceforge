"""Focused tests for workspace-authorized semantic retrieval (Task 7C)."""

from __future__ import annotations

import hashlib
import math
import uuid

import pytest
from sqlalchemy.orm import Session

from app.evidence.citations.service import citation_from_candidate
from app.evidence.embeddings.config import (
    DEFAULT_EMBEDDING_CONFIG,
    EXPECTED_EMBEDDING_DIMENSION,
    EmbeddingConfig,
)
from app.evidence.embeddings.service import EmbeddingGenerator
from app.evidence.persistence.semantic_repository import query_semantic_candidates
from app.evidence.semantic.config import (
    DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    SemanticRetrievalConfig,
)
from app.evidence.semantic.errors import (
    InvalidQueryVectorError,
    QueryEmbeddingError,
    SemanticConfigurationError,
    SemanticDimensionMismatchError,
    SemanticModelUnavailableError,
    SemanticQueryValidationError,
)
from app.evidence.semantic.query import (
    generate_query_embedding,
    normalize_semantic_query,
    validate_query_vector,
)
from app.evidence.semantic.service import SemanticRetriever
from app.models import (
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceDocumentVersion,
)
from tests.test_evidence_chunk_embeddings import _create_parent


def _unit_vector(index: int, dim: int = EXPECTED_EMBEDDING_DIMENSION) -> list[float]:
    """Create a 384-dimensional unit vector with 1.0 at the given index."""
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


def _mixed_vector(
    index_a: int, index_b: int, dim: int = EXPECTED_EMBEDDING_DIMENSION
) -> list[float]:
    """Create a normalized 384-dimensional vector split across two indices."""
    vec = [0.0] * dim
    val = math.sqrt(0.5)
    vec[index_a % dim] = val
    vec[index_b % dim] = val
    return vec


class _FakeQueryTokenizer:
    def encode(self, text: str, **_: object) -> list[int]:
        return list(range(len(text.split())))

    def decode(self, token_ids: list[int], **_: object) -> str:
        return " ".join(f"token-{token_id}" for token_id in token_ids)


class _FakeSemanticModel:
    """Deterministic fake embedding model for testing query and document encoding."""

    def __init__(
        self,
        *,
        dimension: int = EXPECTED_EMBEDDING_DIMENSION,
        fail_queries: bool = False,
        missing_query_encoder: bool = False,
        return_invalid_norm: bool = False,
        return_nan: bool = False,
        wrong_output_dimension: bool = False,
    ) -> None:
        self.tokenizer = _FakeQueryTokenizer()
        self.dimension = dimension
        self.fail_queries = fail_queries
        self.missing_query_encoder = missing_query_encoder
        self.return_invalid_norm = return_invalid_norm
        self.return_nan = return_nan
        self.wrong_output_dimension = wrong_output_dimension
        self.query_calls: list[list[str]] = []

        if missing_query_encoder:
            # Remove encode_query attribute so getattr fails cleanly
            object.__setattr__(self, "encode_query", None)

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode_query(self, texts: list[str], **_: object) -> list[list[float]]:
        if self.fail_queries:
            raise RuntimeError("Fake encoder failure")
        self.query_calls.append(texts)

        dim = self.dimension - 1 if self.wrong_output_dimension else self.dimension
        rows: list[list[float]] = []
        for text in texts:
            if self.return_nan:
                rows.append([float("nan")] * dim)
                continue
            if self.return_invalid_norm:
                rows.append([10.0] * dim)
                continue
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dim
            vec = [0.0] * dim
            vec[idx] = 1.0
            rows.append(vec)
        return rows

    def encode_document(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        return self.encode_query(texts, **kwargs)


def _persist_chunk_embedding(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    chunk: EvidenceChunk,
    vector: list[float],
    config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
) -> EvidenceChunkEmbedding:
    row = EvidenceChunkEmbedding(
        evidence_chunk_id=chunk.id,
        evidence_content_hash=chunk.content_hash,
        workspace_id=workspace_id,
        model_id=config.model_id,
        model_version=config.model_version,
        configuration_hash=config.configuration_hash,
        embedding_dimension=config.embedding_dimension,
        embedding=vector,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _create_additional_chunk(
    session: Session,
    *,
    document: EvidenceDocument,
    version: EvidenceDocumentVersion,
    chunk_index: int,
    content: str,
    section_label: str | None = None,
    page_number: int | None = None,
) -> EvidenceChunk:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    chunk = EvidenceChunk(
        document_version_id=version.id,
        chunk_index=chunk_index,
        content=content,
        content_hash=content_hash,
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label=section_label,
        page_number=page_number,
    )
    session.add(chunk)
    session.commit()
    session.refresh(chunk)
    return chunk


# ==============================================================================
# Query Normalization & Validation Tests
# ==============================================================================


def test_normalize_semantic_query_validates_and_normalizes() -> None:
    assert normalize_semantic_query("  data   retention  policy  ") == "data retention policy"
    assert normalize_semantic_query("Access\t\nControl") == "Access Control"


def test_normalize_semantic_query_rejects_empty_or_short_query() -> None:
    with pytest.raises(SemanticQueryValidationError, match="too short"):
        normalize_semantic_query("")

    with pytest.raises(SemanticQueryValidationError, match="too short"):
        normalize_semantic_query("   ")

    with pytest.raises(SemanticQueryValidationError, match="too short"):
        normalize_semantic_query("a")


def test_normalize_semantic_query_rejects_overlong_query() -> None:
    long_query = "x" * 257
    with pytest.raises(SemanticQueryValidationError, match="too long"):
        normalize_semantic_query(long_query)


def test_normalize_semantic_query_rejects_non_string() -> None:
    with pytest.raises(SemanticQueryValidationError, match="must be a string"):
        normalize_semantic_query(12345)  # type: ignore[arg-type]


# ==============================================================================
# Query Vector Validation Tests
# ==============================================================================


def test_validate_query_vector_accepts_valid_384_normalized_vector() -> None:
    vec = _unit_vector(0)
    result = validate_query_vector(vec)
    assert len(result) == EXPECTED_EMBEDDING_DIMENSION
    assert math.isclose(math.sqrt(sum(x * x for x in result)), 1.0, rel_tol=1e-3)


def test_validate_query_vector_rejects_wrong_dimension() -> None:
    vec = [1.0] * 383
    with pytest.raises(SemanticDimensionMismatchError, match="383 does not match"):
        validate_query_vector(vec)


def test_validate_query_vector_rejects_non_finite_values() -> None:
    vec = _unit_vector(0)
    vec[5] = float("nan")
    with pytest.raises(InvalidQueryVectorError, match="non-finite"):
        validate_query_vector(vec)

    vec[5] = float("inf")
    with pytest.raises(InvalidQueryVectorError, match="non-finite"):
        validate_query_vector(vec)


def test_validate_query_vector_rejects_unnormalized_vector() -> None:
    vec = [10.0] * EXPECTED_EMBEDDING_DIMENSION
    with pytest.raises(InvalidQueryVectorError, match="not normalized"):
        validate_query_vector(vec)


# ==============================================================================
# Query Embedding Generation Tests
# ==============================================================================


def test_generate_query_embedding_with_fake_model() -> None:
    model = _FakeSemanticModel()
    vector = generate_query_embedding(model, "encryption at rest")
    assert len(vector) == EXPECTED_EMBEDDING_DIMENSION
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-3)
    assert len(model.query_calls) == 1


def test_generate_query_embedding_fails_on_none_model() -> None:
    with pytest.raises(SemanticModelUnavailableError, match="unavailable"):
        generate_query_embedding(None, "encryption at rest")


def test_generate_query_embedding_surfaces_model_dimension_mismatch() -> None:
    model = _FakeSemanticModel(dimension=128)
    with pytest.raises(SemanticDimensionMismatchError, match="128"):
        generate_query_embedding(model, "encryption at rest")


def test_generate_query_embedding_surfaces_encoder_failure() -> None:
    model = _FakeSemanticModel(fail_queries=True)
    with pytest.raises(QueryEmbeddingError, match="failed"):
        generate_query_embedding(model, "encryption at rest")


def test_generate_query_embedding_surfaces_invalid_norm_from_model() -> None:
    model = _FakeSemanticModel(return_invalid_norm=True)
    with pytest.raises(InvalidQueryVectorError, match="not normalized"):
        generate_query_embedding(model, "encryption at rest")


def test_generate_query_embedding_surfaces_nan_from_model() -> None:
    model = _FakeSemanticModel(return_nan=True)
    with pytest.raises(InvalidQueryVectorError, match="non-finite"):
        generate_query_embedding(model, "encryption at rest")


# ==============================================================================
# Semantic Retrieval Configuration Tests
# ==============================================================================


def test_semantic_retrieval_config_defaults_and_properties() -> None:
    config = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG
    assert config.top_k == 10
    assert config.embedding_dimension == 384
    assert config.similarity_metric == "cosine"
    assert config.model_id == DEFAULT_EMBEDDING_CONFIG.model_id
    assert config.configuration_hash == DEFAULT_EMBEDDING_CONFIG.configuration_hash


def test_semantic_retrieval_config_validates_bounds() -> None:
    with pytest.raises(ValueError, match="top_k must be at least 1"):
        SemanticRetrievalConfig(top_k=0)

    with pytest.raises(ValueError, match="top_k must not exceed"):
        SemanticRetrievalConfig(top_k=51)

    with pytest.raises(ValueError, match="cosine"):
        SemanticRetrievalConfig(similarity_metric="euclidean")

    with pytest.raises(ValueError, match="384"):
        SemanticRetrievalConfig(embedding_dimension=512)


# ==============================================================================
# Semantic Repository & Retrieval Tests (SQLite)
# ==============================================================================


def test_query_semantic_candidates_deterministic_ordering_and_distance(
    db_session: Session,
) -> None:
    workspace, chunk_1 = _create_parent(db_session, suffix="sem-1")
    doc = db_session.get(
        EvidenceDocument,
        db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id).document_id,
    )
    version = db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id)

    chunk_2 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=1,
        content="Chunk 2 content",
    )
    chunk_3 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=2,
        content="Chunk 3 content",
    )

    # Chunk 1: distance 0.0 to query vector (unit vector 0)
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_1,
        vector=_unit_vector(0),
    )
    # Chunk 2: distance ~0.2929 to query vector (mixed vector 0 and 1)
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_2,
        vector=_mixed_vector(0, 1),
    )
    # Chunk 3: distance 1.0 to query vector (orthogonal unit vector 1)
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk_3,
        vector=_unit_vector(1),
    )

    query_vec = _unit_vector(0)
    results = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=query_vec,
        top_k=10,
    )

    assert len(results) == 3
    # Ordered by ascending cosine distance
    assert results[0].chunk_id == chunk_1.id
    assert math.isclose(results[0].distance, 0.0, abs_tol=1e-4)
    assert math.isclose(results[0].similarity, 1.0, abs_tol=1e-4)

    assert results[1].chunk_id == chunk_2.id
    assert 0.28 < results[1].distance < 0.30
    assert 0.70 < results[1].similarity < 0.72

    assert results[2].chunk_id == chunk_3.id
    assert math.isclose(results[2].distance, 1.0, abs_tol=1e-4)
    assert math.isclose(results[2].similarity, 0.0, abs_tol=1e-4)


def test_query_semantic_candidates_deterministic_tie_breaking(
    db_session: Session,
) -> None:
    workspace, chunk_1 = _create_parent(db_session, suffix="tie-1")
    doc = db_session.get(
        EvidenceDocument,
        db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id).document_id,
    )
    version = db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id)

    chunk_2 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=1,
        content="Chunk 2 content",
    )
    chunk_3 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=2,
        content="Chunk 3 content",
    )

    # All three have the EXACT same vector (and thus identical cosine distance to query)
    identical_vec = _unit_vector(5)
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_1, vector=identical_vec
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_2, vector=identical_vec
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_3, vector=identical_vec
    )

    results = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=identical_vec,
        top_k=10,
    )

    assert len(results) == 3
    # Secondary ordering must be stable by chunk_id
    expected_chunk_ids = sorted([chunk_1.id, chunk_2.id, chunk_3.id])
    actual_chunk_ids = [r.chunk_id for r in results]
    assert actual_chunk_ids == expected_chunk_ids


def test_query_semantic_candidates_respects_top_k(db_session: Session) -> None:
    workspace, chunk_1 = _create_parent(db_session, suffix="topk-1")
    doc = db_session.get(
        EvidenceDocument,
        db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id).document_id,
    )
    version = db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id)

    chunk_2 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=1,
        content="Chunk 2 content",
    )
    chunk_3 = _create_additional_chunk(
        db_session,
        document=doc,
        version=version,
        chunk_index=2,
        content="Chunk 3 content",
    )

    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_1, vector=_unit_vector(0)
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_2, vector=_unit_vector(1)
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_3, vector=_unit_vector(2)
    )

    results = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=_unit_vector(0),
        top_k=2,
    )
    assert len(results) == 2


def test_query_semantic_candidates_workspace_isolation_regression(
    db_session: Session,
) -> None:
    """Security Invariant: Vector from another workspace is NEVER returned, even if identical."""

    workspace_a, chunk_a = _create_parent(db_session, suffix="iso-a")
    workspace_b, chunk_b = _create_parent(db_session, suffix="iso-b")

    target_query = _unit_vector(0)

    # Workspace B has a vector identical to target_query (distance 0.0)
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace_b.id,
        chunk=chunk_b,
        vector=target_query,
    )

    # Workspace A has a vector orthogonal to target_query (distance 1.0)
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace_a.id,
        chunk=chunk_a,
        vector=_unit_vector(1),
    )

    # Search in Workspace A
    results_a = query_semantic_candidates(
        db_session,
        workspace_id=workspace_a.id,
        query_vector=target_query,
        top_k=10,
    )

    # Must return ONLY Workspace A's chunk, never Workspace B's identical chunk
    assert len(results_a) == 1
    assert results_a[0].chunk_id == chunk_a.id
    assert results_a[0].workspace_id == workspace_a.id
    assert all(r.workspace_id == workspace_a.id for r in results_a)
    assert chunk_b.id not in {r.chunk_id for r in results_a}


def test_query_semantic_candidates_provenance_and_citation_contract(
    db_session: Session,
) -> None:
    workspace, chunk = _create_parent(db_session, suffix="prov-1")
    version = db_session.get(EvidenceDocumentVersion, chunk.document_version_id)
    document = db_session.get(EvidenceDocument, version.document_id)

    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk,
        vector=_unit_vector(0),
    )

    results = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=_unit_vector(0),
        top_k=10,
    )

    assert len(results) == 1
    res = results[0]
    assert res.chunk_id == chunk.id
    assert res.document_id == document.id
    assert res.version_id == version.id
    assert res.version_number == version.version_number
    assert res.chunk_index == chunk.chunk_index
    assert res.content == chunk.content
    assert res.content_hash == chunk.content_hash
    assert res.normalized_start_byte == chunk.normalized_start_byte
    assert res.normalized_end_byte == chunk.normalized_end_byte
    assert res.workspace_id == workspace.id
    assert res.model_id == DEFAULT_SEMANTIC_RETRIEVAL_CONFIG.model_id
    assert res.configuration_hash == DEFAULT_SEMANTIC_RETRIEVAL_CONFIG.configuration_hash
    assert res.embedding_dimension == 384

    # Verify citation construction compatibility
    citation = citation_from_candidate(res.candidate, workspace_id=res.workspace_id)
    assert citation.workspace_id == workspace.id
    assert citation.chunk_id == chunk.id
    assert citation.document_id == document.id
    assert citation.version_id == version.id


def test_query_semantic_candidates_filters_by_document_and_version(
    db_session: Session,
) -> None:
    workspace, chunk_1 = _create_parent(db_session, suffix="filt-1")
    version_1 = db_session.get(EvidenceDocumentVersion, chunk_1.document_version_id)
    doc_1 = db_session.get(EvidenceDocument, version_1.document_id)

    # Additional chunk under same document
    chunk_2 = _create_additional_chunk(
        db_session,
        document=doc_1,
        version=version_1,
        chunk_index=1,
        content="Chunk 2 doc 1",
    )

    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_1, vector=_unit_vector(0)
    )
    _persist_chunk_embedding(
        db_session, workspace_id=workspace.id, chunk=chunk_2, vector=_unit_vector(0)
    )

    # Filter by specific document
    results_doc = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=_unit_vector(0),
        document_id=doc_1.id,
    )
    assert len(results_doc) == 2

    # Filter by non-existent document ID -> returns empty tuple
    results_empty = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=_unit_vector(0),
        document_id=uuid.uuid4(),
    )
    assert results_empty == ()


def test_query_semantic_candidates_ignores_stale_configuration_hash(
    db_session: Session,
) -> None:
    workspace, chunk = _create_parent(db_session, suffix="stale-1")

    # Persist embedding with older/different configuration hash
    stale_config = SemanticRetrievalConfig(
        embedding_config=EmbeddingConfig(model_version="older-model-v0")
    )
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk,
        vector=_unit_vector(0),
        config=stale_config,
    )

    # Searching with current default config does NOT match the stale embedding
    results = query_semantic_candidates(
        db_session,
        workspace_id=workspace.id,
        query_vector=_unit_vector(0),
        config=DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    )
    assert results == ()


# ==============================================================================
# SemanticRetriever Service Tests
# ==============================================================================


def test_semantic_retriever_search_end_to_end(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix="srv-1")
    model = _FakeSemanticModel()
    retriever = SemanticRetriever(model=model)

    # Generate query vector with fake model
    query_text = "What is the encryption standard?"
    query_vec = retriever.generate_query_vector(query_text)

    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk,
        vector=list(query_vec),
    )

    results = retriever.search(
        db_session,
        query=query_text,
        workspace_id=workspace.id,
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].chunk_id == chunk.id
    assert math.isclose(results[0].distance, 0.0, abs_tol=1e-4)
    assert math.isclose(results[0].similarity, 1.0, abs_tol=1e-4)


def test_semantic_retriever_search_vector_direct(db_session: Session) -> None:
    workspace, chunk = _create_parent(db_session, suffix="srv-vec-1")
    retriever = SemanticRetriever()

    vec = _unit_vector(10)
    _persist_chunk_embedding(
        db_session,
        workspace_id=workspace.id,
        chunk=chunk,
        vector=vec,
    )

    results = retriever.search_vector(
        db_session,
        query_vector=vec,
        workspace_id=workspace.id,
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].chunk_id == chunk.id


def test_semantic_retriever_rejects_both_model_and_generator() -> None:
    model = _FakeSemanticModel()
    gen = EmbeddingGenerator(model)
    with pytest.raises(ValueError, match="not both"):
        SemanticRetriever(model=model, generator=gen)


def test_semantic_retriever_rejects_generator_config_mismatch() -> None:
    model = _FakeSemanticModel()
    gen = EmbeddingGenerator(model, config=EmbeddingConfig(batch_size=64))
    with pytest.raises(SemanticConfigurationError, match="does not match"):
        SemanticRetriever(generator=gen, config=DEFAULT_SEMANTIC_RETRIEVAL_CONFIG)
