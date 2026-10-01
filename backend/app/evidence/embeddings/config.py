"""Frozen, hashable configuration for production embedding generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

DEFAULT_MODEL_ID = "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
DEFAULT_MODEL_VERSION = "multi-qa-MiniLM-L6-cos-v1"
DEFAULT_RUNTIME_VERSION = "5.7.0"
EXPECTED_EMBEDDING_DIMENSION = 384
DEFAULT_MAX_INPUT_WORD_PIECES = 250
DEFAULT_BATCH_SIZE = 32


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Explicit deterministic generation configuration.

    The canonical payload is hashed and persisted with every generated vector.
    No generation-affecting option is read implicitly from process state.
    """

    model_id: str = DEFAULT_MODEL_ID
    model_version: str = DEFAULT_MODEL_VERSION
    runtime_version: str = DEFAULT_RUNTIME_VERSION
    embedding_dimension: int = EXPECTED_EMBEDDING_DIMENSION
    similarity: str = "cosine"
    normalize_embeddings: bool = True
    query_encoder: str = "encode_query"
    document_encoder: str = "encode_document"
    max_input_word_pieces: int = DEFAULT_MAX_INPUT_WORD_PIECES
    batch_size: int = DEFAULT_BATCH_SIZE
    device: str = "cpu"

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if not self.model_version.strip():
            raise ValueError("model_version must not be empty")
        if not self.runtime_version.strip():
            raise ValueError("runtime_version must not be empty")
        if self.embedding_dimension != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError("The production embedding foundation requires 384 dimensions")
        if self.similarity != "cosine":
            raise ValueError("The production embedding foundation supports cosine similarity only")
        if not self.normalize_embeddings:
            raise ValueError("The production embedding foundation requires normalized embeddings")
        if self.query_encoder != "encode_query":
            raise ValueError("The query encoder must remain encode_query")
        if self.document_encoder != "encode_document":
            raise ValueError("The document encoder must remain encode_document")
        if self.max_input_word_pieces < 1:
            raise ValueError("max_input_word_pieces must be positive")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not self.device.strip():
            raise ValueError("device must not be empty")

    def canonical_payload(self) -> dict[str, object]:
        """Return the stable, JSON-serializable configuration payload."""

        return {
            "configuration_schema": "evidenceforge-embedding-config-v1",
            **asdict(self),
        }

    @property
    def configuration_hash(self) -> str:
        """Return the SHA-256 hash of the canonical generation configuration."""

        canonical_json = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical_json).hexdigest()


DEFAULT_EMBEDDING_CONFIG = EmbeddingConfig()
