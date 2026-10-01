"""Focused workspace-authorized semantic retrieval service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.evidence.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingGenerationError,
    EmbeddingModelUnavailableError,
)
from app.evidence.embeddings.service import EmbeddingGenerator, load_embedding_model
from app.evidence.semantic.config import (
    DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    SemanticRetrievalConfig,
)
from app.evidence.semantic.errors import (
    QueryEmbeddingError,
    SemanticConfigurationError,
    SemanticDimensionMismatchError,
    SemanticModelUnavailableError,
    SemanticRetrievalError,
)
from app.evidence.semantic.query import generate_query_embedding, normalize_semantic_query
from app.evidence.semantic.repository import query_semantic_candidates
from app.evidence.semantic.types import SemanticSearchResult


class SemanticRetriever:
    """Workspace-authorized semantic retrieval service over persisted embeddings.

    Encapsulates deterministic query embedding and database pgvector candidate search.
    Enforces that queries are authorized and restricted to a single workspace boundary.
    """

    def __init__(
        self,
        model: Any | None = None,
        generator: EmbeddingGenerator | None = None,
        config: SemanticRetrievalConfig = DEFAULT_SEMANTIC_RETRIEVAL_CONFIG,
    ) -> None:
        self.config = config
        self._model = model
        self._generator = generator

        if model is not None and generator is not None:
            raise ValueError("Provide model or generator, not both")

        if generator is not None:
            if generator.config != config.embedding_config:
                raise SemanticConfigurationError(
                    "Supplied generator configuration does not match "
                    "semantic retrieval configuration"
                )
            self._generator = generator

    def _ensure_generator(self) -> EmbeddingGenerator:
        """Lazily initialize the embedding generator when needed."""

        if self._generator is not None:
            return self._generator

        if self._model is None:
            try:
                self._model = load_embedding_model(self.config.embedding_config)
            except EmbeddingModelUnavailableError as exc:
                raise SemanticModelUnavailableError(str(exc)) from exc
            except (EmbeddingConfigurationError, EmbeddingError) as exc:
                raise SemanticConfigurationError(str(exc)) from exc
            except Exception as exc:
                raise SemanticModelUnavailableError(
                    f"Failed to load embedding model: {exc}"
                ) from exc

        self._generator = EmbeddingGenerator(self._model, self.config.embedding_config)
        return self._generator

    def generate_query_vector(self, query: str) -> tuple[float, ...]:
        """Normalize query text and generate a validated 384-dimensional query vector."""

        normalized_query = normalize_semantic_query(query, config=self.config)

        if self._model is not None and not hasattr(self._model, "tokenizer"):
            # If using a model directly without full generator wrapping
            return generate_query_embedding(self._model, normalized_query, config=self.config)

        generator = self._ensure_generator()
        try:
            return generator.generate_query(normalized_query)
        except EmbeddingModelUnavailableError as exc:
            raise SemanticModelUnavailableError(str(exc)) from exc
        except EmbeddingDimensionError as exc:
            raise SemanticDimensionMismatchError(str(exc)) from exc
        except EmbeddingConfigurationError as exc:
            raise SemanticConfigurationError(str(exc)) from exc
        except EmbeddingGenerationError as exc:
            raise QueryEmbeddingError(str(exc)) from exc
        except SemanticRetrievalError:
            raise
        except Exception as exc:
            raise QueryEmbeddingError(f"Query embedding generation failed: {exc}") from exc

    def search(
        self,
        db: Session,
        *,
        query: str,
        workspace_id: uuid.UUID,
        top_k: int | None = None,
        document_id: uuid.UUID | None = None,
        version_id: uuid.UUID | None = None,
    ) -> tuple[SemanticSearchResult, ...]:
        """Perform workspace-authorized semantic search using a natural language query."""

        query_vector = self.generate_query_vector(query)

        return query_semantic_candidates(
            db,
            workspace_id=workspace_id,
            query_vector=query_vector,
            config=self.config,
            document_id=document_id,
            version_id=version_id,
            top_k=top_k,
        )

    def search_vector(
        self,
        db: Session,
        *,
        query_vector: Sequence[float],
        workspace_id: uuid.UUID,
        top_k: int | None = None,
        document_id: uuid.UUID | None = None,
        version_id: uuid.UUID | None = None,
    ) -> tuple[SemanticSearchResult, ...]:
        """Perform workspace-authorized semantic search using an explicit query vector."""

        return query_semantic_candidates(
            db,
            workspace_id=workspace_id,
            query_vector=query_vector,
            config=self.config,
            document_id=document_id,
            version_id=version_id,
            top_k=top_k,
        )
