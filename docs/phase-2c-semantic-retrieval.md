# Phase 2C Task 7C: Workspace-Authorized Semantic Retrieval

- **Status:** Implementation checkpoint for workspace-authorized semantic retrieval layer over persisted pgvector embeddings
- **Scope:** Pure semantic retrieval layer over `EvidenceChunkEmbedding` vectors; lexical path remains unchanged
- **Production impact:** Semantic retrieval service added; existing lexical grounding and response behavior untouched

---

## 1. Executive Summary and Scope Boundaries

Task 7C implements the authorized semantic retrieval layer over persisted `EvidenceChunkEmbedding` vectors. It connects query normalization, query vector generation, workspace authorization, and exact pgvector cosine distance search into a deterministic candidate retrieval pipeline.

```text
Question
  ↓
normalize / validate
  ↓
workspace authorization
  ↓
query embedding
  ↓
authorized pgvector search
  ↓
bounded semantic top-k
  ↓
deterministic ordering
  ↓
attributable evidence candidates
```

### Explicit Non-Goals

As specified for Phase 2C Task 7C:

```text
RRF is not implemented.
HNSW production indexing is not implemented.
LLM generation is not implemented.
Automatic questionnaire answering is not implemented.
```

* RRF fusion across lexical and semantic streams is deferred to Task 7D.
* Production HNSW indexing is deferred to a future dedicated indexing task; exact search (`ORDER BY embedding <=> :query_vector`) is the reference implementation.
* No public unrestricted semantic search API is exposed.
* No schema redesign or database migration is introduced.

---

## 2. Architecture and Data Flow

The semantic retrieval layer consists of four decoupled components:

1. **Query Normalization and Validation (`app.evidence.semantic.query`)**:
   Normalizes input strings using Unicode `NFKC`, collapses whitespace, and enforces existing repository query limits (min 2, max 256 characters).
2. **Query Vector Generation (`app.evidence.embeddings.service` / `app.evidence.semantic.query`)**:
   Uses the Task 7B frozen embedding configuration and model encoder (`encode_query`) to produce a 384-dimensional L2-normalized vector.
3. **Database Vector Query Repository (`app.evidence.persistence.semantic_repository`)**:
   Executes exact cosine distance search (`<=>`) over `evidence_chunk_embeddings` joined to parent documents, enforcing authorized workspace predicates at the SQL boundary before ordering and limit.
4. **Semantic Retrieval Service (`app.evidence.semantic.service.SemanticRetriever`)**:
   Coordinates normalization, query embedding, and authorized candidate retrieval while returning structured, provenance-preserving `SemanticSearchResult` candidates.

---

## 3. Query Embedding and Configuration Compatibility

Query embedding reuses the Task 7B frozen `EmbeddingConfig` foundation:

```text
model_id:              sentence-transformers/multi-qa-MiniLM-L6-cos-v1
model_version:         multi-qa-MiniLM-L6-cos-v1
runtime_version:       5.7.0
embedding_dimension:   384
similarity:            cosine
normalize_embeddings:  true
query_encoder:         encode_query
max_input_word_pieces: 250
batch_size:            32
device:                cpu
```

Configuration constants are not duplicated. `SemanticRetrievalConfig` references `EmbeddingConfig` directly.

### Safety Invariants

* **Dimension Validation**: Generated vectors must be exactly 384 dimensions.
* **Vector Normalization**: Vectors must have an L2 norm of 1.0 (within `1e-3` tolerance) and contain only finite numbers (`isfinite`).
* **Configuration Integrity**: If persisted embeddings were generated under a different configuration hash or model version, the query filters on the active configuration hash, ensuring generations are never silently mixed.
* **Lazy Loading**: Model weights are loaded on demand and never at module import time.

---

## 4. Security Invariant and Workspace Isolation

### Hard Security Rule

```text
Unauthorized evidence must never enter semantic candidate retrieval,
ranking, the final candidate list, or downstream context.
```

The semantic retrieval query enforces workspace isolation at the PostgreSQL database query boundary **before** ordering and `LIMIT`.

The database query joins `evidence_chunk_embeddings` to `evidence_chunks`, `evidence_document_versions`, and `evidence_documents`, enforcing:

```sql
SELECT
    evidence_chunks.id,
    evidence_documents.id AS document_id,
    evidence_document_versions.id AS version_id,
    evidence_document_versions.version_number,
    evidence_chunks.chunk_index,
    evidence_chunks.content,
    evidence_chunks.content_hash,
    evidence_chunks.normalized_start_byte,
    evidence_chunks.normalized_end_byte,
    evidence_chunks.section_label,
    evidence_chunks.page_number,
    evidence_chunk_embeddings.workspace_id,
    evidence_chunk_embeddings.model_id,
    evidence_chunk_embeddings.model_version,
    evidence_chunk_embeddings.configuration_hash,
    evidence_chunk_embeddings.embedding_dimension,
    evidence_chunk_embeddings.embedding <=> :query_vector AS distance
FROM evidence_chunk_embeddings
JOIN evidence_chunks
  ON evidence_chunks.id = evidence_chunk_embeddings.evidence_chunk_id
JOIN evidence_document_versions
  ON evidence_document_versions.id = evidence_chunks.document_version_id
JOIN evidence_documents
  ON evidence_documents.id = evidence_document_versions.document_id
WHERE evidence_chunk_embeddings.workspace_id = :authorized_workspace_id
  AND evidence_documents.workspace_id = :authorized_workspace_id
  AND evidence_chunk_embeddings.model_id = :model_id
  AND evidence_chunk_embeddings.model_version = :model_version
  AND evidence_chunk_embeddings.configuration_hash = :configuration_hash
  AND evidence_chunk_embeddings.embedding_dimension = 384
ORDER BY distance,
         evidence_chunk_embeddings.evidence_chunk_id
LIMIT :top_k;
```

### Defense-in-Depth

1. The SQL query strictly restricts `workspace_id` on both the embedding and the parent document tables.
2. Even if another workspace contains a vector with distance 0.0 (an exact duplicate of the query), that vector cannot appear in the candidate list or displace any authorized candidates within `top_k`.
3. In-memory validation post-query verifies `row.workspace_id == authorized_workspace_id`, raising `SemanticWorkspaceMismatchError` if any invariant is violated.

---

## 5. Deterministic Ranking and Tie-Breaking

To eliminate database nondeterminism when vectors produce identical cosine distances:

* **Primary Sort**: Ascending cosine distance (`distance = embedding <=> query_vector`).
* **Secondary Sort (Tie-Breaker)**: Stable candidate ID (`evidence_chunk_id` / `EvidenceChunk.id`).

Distance and similarity are computed as:

$$\text{distance} = 1.0 - (\vec{u} \cdot \vec{v})$$
$$\text{similarity} = \max(-1.0, \min(1.0, 1.0 - \text{distance}))$$

---

## 6. Attributable Candidate Metadata and Provenance

Every returned `SemanticSearchResult` preserves complete provenance back to immutable evidence:

* `chunk_id`: persisted evidence chunk UUID
* `document_id`: parent document UUID
* `version_id`: parent document version UUID
* `version_number`: document version number
* `chunk_index`: zero-based chunk index
* `content`: raw normalized chunk text
* `content_hash`: SHA-256 chunk content hash
* `normalized_start_byte`: chunk start byte offset
* `normalized_end_byte`: chunk end byte offset
* `section_label`: optional section header
* `page_number`: optional page number
* `workspace_id`: authorized workspace UUID
* `distance`: cosine distance (float)
* `similarity`: cosine similarity (float)
* `model_id`: embedding model identifier
* `model_version`: model version identifier
* `configuration_hash`: SHA-256 generation configuration hash
* `embedding_dimension`: 384

Raw embedding vectors are not returned to client or downstream layers. Each `SemanticSearchResult` provides `.candidate` and `.to_search_candidate()` helpers that map directly to `SearchChunkCandidate` and `EvidenceCitation`.

---

## 7. Bounded Retrieval Policy

All search limits are strictly bounded:

```text
min_query_length:      2
max_query_length:      256
default_top_k:         10
max_top_k:             50
similarity_metric:     cosine
embedding_dimension:   384
```

Requests for `top_k < 1` or `top_k > 50` are rejected immediately. Unbounded candidate searches cannot occur.

---

## 8. Error Behavior and Lexical Fallback Boundary

All semantic retrieval errors inherit from `SemanticRetrievalError`, providing a clean, explicit boundary for future hybrid/fallback callers (e.g., Task 7D RRF):

| Error Class | Trigger | Safe Fallback Action |
| --- | --- | --- |
| `SemanticQueryValidationError` | Query is empty, too short (<2 chars), or too long (>256 chars) | Reject immediately; do not search |
| `SemanticAuthorizationError` | Workspace ID is missing or invalid | Fail closed (security error) |
| `SemanticWorkspaceMismatchError` | Retrieved row has unexpected workspace | Fail closed (security error) |
| `SemanticModelUnavailableError` | `sentence-transformers` or model missing | Caller may choose lexical fallback |
| `QueryEmbeddingError` | Query encoder failed or generated non-finite values | Caller may choose lexical fallback |
| `SemanticDimensionMismatchError` | Model output or query vector is not 384-dim | Caller may choose lexical fallback |
| `InvalidQueryVectorError` | Query vector is NaN, Inf, or unnormalized | Caller may choose lexical fallback |
| `SemanticDatabaseError` | PostgreSQL/pgvector database query failure | Caller may choose lexical fallback |
| `SemanticConfigurationError` | Configuration hash or runtime version mismatch | Caller may choose lexical fallback |

No indefinite retries or unhandled exceptions are allowed.

---

## 9. Test Coverage and Validation Matrix

Task 7C includes 35 new focused tests across unit, service, repository, and PostgreSQL integration boundaries:

1. **Query Embedding Unit Tests (`backend/tests/test_evidence_semantic_retrieval.py`)**:
   * Valid 384-dimensional normalized vector generation
   * Dimension mismatch rejection (<384, >384)
   * Non-finite vector rejection (NaN, Inf)
   * Unnormalized vector rejection
   * Query normalization, length boundaries, and whitespace handling
   * Model unavailable and encoder failure handling

2. **Semantic Retrieval and Repository Tests (`backend/tests/test_evidence_semantic_retrieval.py`)**:
   * Cosine distance calculation accuracy
   * Deterministic ordering and secondary ID tie-breaking
   * Bounded `top_k` enforcement
   * Workspace isolation regression (cross-workspace identical vector excluded)
   * Optional document and version filtering
   * Provenance preservation and citation compatibility
   * Stale configuration hash exclusion

3. **Service Boundary Tests (`backend/tests/evidence/test_semantic_retriever.py`)**:
   * `SemanticRetriever` initialization and configuration overrides
   * Lazy model loading error handling
   * `search()` and `search_vector()` execution paths
   * Empty workspace returning explicit `()`
   * Authorization validation

4. **PostgreSQL Integration Tests (`backend/tests/test_evidence_semantic_retrieval_postgres.py`)**:
   * Real PostgreSQL 16 + pgvector 0.8.6 `<=>` distance execution
   * Database-boundary workspace isolation before LIMIT
   * Deterministic tie-breaking on identical distance vectors in PostgreSQL
   * Configuration hash isolation in PostgreSQL

---

## 10. Operational Limitations

1. **Index Optimization**: Semantic search uses exact scanning (`<=>`) over authorized workspace rows. HNSW index construction and tuning remain a later phase decision.
2. **Offline Weights**: Tests use deterministic fake models or pre-computed unit vectors to avoid network downloads during automated test execution.
3. **Hybrid Fusion**: Lexical and semantic fusion via Reciprocal Rank Fusion (RRF) is deferred to Task 7D.
