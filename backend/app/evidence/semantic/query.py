"""Deterministic query normalization, validation, and embedding generation."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from app.evidence.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingGenerationError,
    EmbeddingModelUnavailableError,
)
from app.evidence.embeddings.service import (
    _as_rows,
    _prepare_document_text,
    _validate_model_dimension,
)
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

_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_semantic_query(
    query: str,
    *,
    config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
) -> str:
    """Normalize and validate a semantic query string deterministically."""

    if not isinstance(query, str):
        raise SemanticQueryValidationError("Search query must be a string")

    normalized = unicodedata.normalize("NFKC", query)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip()

    if len(normalized) < config.min_query_length:
        raise SemanticQueryValidationError("Search query is too short")

    if len(normalized) > config.max_query_length:
        raise SemanticQueryValidationError("Search query is too long")

    return normalized


def validate_query_vector(
    vector: Sequence[float],
    *,
    config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
) -> tuple[float, ...]:
    """Validate that a query vector is 384-dimensional, finite, and normalized."""

    if len(vector) != config.embedding_dimension:
        raise SemanticDimensionMismatchError(
            f"Query vector dimension {len(vector)} does not match "
            f"expected dimension {config.embedding_dimension}"
        )

    try:
        values = tuple(float(v) for v in vector)
    except (TypeError, ValueError) as exc:
        raise InvalidQueryVectorError("Query vector contains invalid non-numeric values") from exc

    if not all(math.isfinite(v) for v in values):
        raise InvalidQueryVectorError("Query vector contains non-finite values (NaN or Inf)")

    norm = math.sqrt(sum(v * v for v in values))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise InvalidQueryVectorError(
            f"Query vector is not normalized (L2 norm = {norm:.6f}, expected 1.0)"
        )

    return values


def generate_query_embedding(
    model: Any,
    query: str,
    *,
    config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
) -> tuple[float, ...]:
    """Generate a validated 384-dimensional normalized vector for a query string."""

    if model is None:
        raise SemanticModelUnavailableError(
            "Embedding model is unavailable for query embedding generation"
        )

    normalized_query = normalize_semantic_query(query, config=config)

    try:
        _validate_model_dimension(model, config.embedding_config)
    except EmbeddingDimensionError as exc:
        raise SemanticDimensionMismatchError(str(exc)) from exc
    except (EmbeddingConfigurationError, EmbeddingError) as exc:
        raise SemanticConfigurationError(str(exc)) from exc
    except Exception as exc:
        raise SemanticConfigurationError(
            "Failed to validate embedding model configuration"
        ) from exc

    tokenizer = getattr(model, "tokenizer", None)
    prepared_query = (
        _prepare_document_text(
            normalized_query,
            tokenizer,
            max_word_pieces=config.embedding_config.max_input_word_pieces,
        )
        if tokenizer is not None
        else normalized_query
    )

    encoder = getattr(model, config.embedding_config.query_encoder, None)
    if not callable(encoder):
        raise SemanticConfigurationError(
            f"Embedding model does not expose required query encoder "
            f"'{config.embedding_config.query_encoder}'"
        )

    try:
        output = encoder(
            [prepared_query],
            batch_size=config.embedding_config.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=config.embedding_config.normalize_embeddings,
        )
    except EmbeddingModelUnavailableError as exc:
        raise SemanticModelUnavailableError(str(exc)) from exc
    except (EmbeddingDimensionError, SemanticDimensionMismatchError) as exc:
        raise SemanticDimensionMismatchError(str(exc)) from exc
    except (EmbeddingConfigurationError, SemanticConfigurationError) as exc:
        raise SemanticConfigurationError(str(exc)) from exc
    except (EmbeddingGenerationError, QueryEmbeddingError) as exc:
        raise QueryEmbeddingError(str(exc)) from exc
    except Exception as exc:
        raise QueryEmbeddingError("Query embedding encoder failed") from exc

    try:
        rows = _as_rows(output)
    except EmbeddingGenerationError as exc:
        raise QueryEmbeddingError(str(exc)) from exc

    if len(rows) != 1:
        raise QueryEmbeddingError(f"Query encoder returned {len(rows)} rows, expected exactly 1")

    return validate_query_vector(rows[0], config=config)
