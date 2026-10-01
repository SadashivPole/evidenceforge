# Phase 2C Task 7B: Deterministic Embedding Generation and Controlled Backfill

- **Status:** Implementation checkpoint for deterministic embedding generation and bounded backfill
- **Scope:** Workspace-scoped document embedding generation persisted through the Task 7A foundation
- **Production retrieval impact:** None; the lexical path remains unchanged

## 1. Objective

Task 7B adds the lifecycle needed to create and persist evidence-chunk embeddings without adding a semantic request path:

```text
immutable EvidenceChunk
        ↓
content-hash validation
        ↓
deterministic embedding configuration
        ↓
384-dimensional document embedding
        ↓
idempotent EvidenceChunkEmbedding persistence
        ↓
bounded workspace backfill report
```

The existing `EvidenceChunkEmbedding` table and migration from Task 7A are reused. No new schema migration is required.

## 2. Deterministic configuration

The production baseline is:

```text
model_id:              sentence-transformers/multi-qa-MiniLM-L6-cos-v1
model_version:         multi-qa-MiniLM-L6-cos-v1
runtime_version:       5.7.0
embedding_dimension:   384
similarity:            cosine
normalize_embeddings:  true
query_encoder:         encode_query
document_encoder:      encode_document
max_input_word_pieces: 250
batch_size:            32
device:                cpu
```

The configuration is represented by a frozen `EmbeddingConfig`. Its canonical JSON payload uses sorted keys and compact separators. The persisted configuration identity is:

```text
SHA-256(canonical configuration JSON)
```

The hash includes the model identity/version, runtime version, dimension, metric, normalization, encoder names, input-window policy, batch size, device, and configuration schema version. Changing any generation-affecting setting produces a different hash and therefore a separately attributable generation.

The `model_version` value is explicit configuration, not an implicit mutable process value. The exact model artifact/revision remains a deployment provenance responsibility before broader production rollout.

## 3. Generation behavior

Model loading is explicit and on demand. Importing the application or starting the API does not download or load a model.

`EmbeddingGenerator`:

1. requires the configured model dimension to be 384;
2. requires the configured tokenizer and `encode_document` encoder;
3. validates each source content hash before encoding;
4. applies the explicit deterministic 250-word-piece prefix window policy;
5. requests normalized NumPy embeddings with progress output disabled;
6. validates row count, dimension, finite values, and normalized vector magnitude; and
7. returns vectors in the same order as the input targets.

The original `EvidenceChunk` content and hash are never rewritten. Windowing only changes the model input text; the parent immutable chunk remains the provenance identity.

## 4. Content-hash validation

Before generation and persistence:

```text
SHA-256(EvidenceChunk.content encoded as UTF-8)
    ==
EvidenceChunk.content_hash
```

A mismatch raises `EmbeddingContentHashMismatchError`. The vector is not generated or persisted.

The embedding row retains the source content hash through the existing `EvidenceChunkEmbedding.evidence_content_hash` field. A changed source must be represented by a new immutable evidence version/chunk and a separately attributable generation.

## 5. Workspace-scoped target selection

Backfill target selection joins:

```text
EvidenceChunk
  → EvidenceDocumentVersion
  → EvidenceDocument
```

and applies:

```text
EvidenceDocument.workspace_id == requested workspace_id
```

The query also excludes an existing row for the exact current generation identity:

```text
evidence_chunk_id
+ evidence_content_hash
+ workspace_id
+ model_id
+ model_version
+ configuration_hash
```

Targets are ordered deterministically by document ID, version number, chunk index, and chunk ID. No global candidate set is loaded before workspace filtering.

## 6. Idempotent persistence

`persist_embedding` reuses the existing `EvidenceChunkEmbedding` relation. It:

- validates the content hash and vector again at the persistence boundary;
- checks the complete generation identity before insert;
- uses the existing database uniqueness constraint for race protection;
- returns `CREATED` for a new row; and
- returns `SKIPPED_EXISTING` without overwriting an existing generation.

It never updates an old generation when content, model, model version, or configuration changes.

## 7. Controlled backfill lifecycle

`backfill_workspace` uses a bounded target snapshot:

```text
maximum selected chunks: 1000
maximum retry count:     3
initial batch size:      32
```

The caller may use smaller limits. Batches are processed in deterministic target order and committed independently after persistence. The service does not create an unbounded queue or background worker.

All persistence attempts for one bounded batch share one database transaction. If any embedding in that batch fails to persist, the service rolls back the entire batch, reports the failure deterministically, and does not increment generated or skipped counts for that batch. Counts are updated only after the complete batch transaction commits successfully.

The returned `BackfillReport` includes:

```text
workspace_id
configuration_hash
requested_count
generated_count
skipped_count
failed_count
batch_count
structured failures
```

A failed encoder batch is retried at most `max_retries` times. Retryable failures include model/encoder availability and generation failures. Configuration, content-hash, dimension, and persistence-integrity failures are not silently retried as if they were transient. Exhausted failures are returned with chunk ID, workspace ID, error type, bounded attempt count, and retryability classification.

Failed batches do not produce partial embeddings. Later bounded batches may continue so one bad chunk does not create an unbounded or opaque run.

## 8. Provenance and security

Every persisted generation remains attributable through:

```text
workspace_id
evidence_chunk_id
evidence_content_hash
model_id
model_version
configuration_hash
embedding_dimension
embedding
created_at
```

The Task 7A database invariant continues to enforce that the embedding workspace and content hash match the immutable parent evidence.

The generation lifecycle:

- does not expose vectors through APIs;
- does not log raw vectors or raw evidence text;
- does not alter evidence ownership;
- does not mutate questionnaire state;
- does not execute retrieved evidence content; and
- does not perform semantic retrieval or request-path authorization changes.

## 9. Tests

Focused unit tests cover:

- canonical configuration hashing;
- 384-dimensional normalized generation;
- explicit long-input windowing;
- content-hash mismatch rejection;
- wrong-dimension rejection;
- provenance retention;
- idempotent persistence;
- workspace-scoped deterministic target selection;
- bounded batch limits;
- transient failure retry;
- exhausted failure reporting; and
- atomic rollback of a multi-target batch when one persistence fails.

The PostgreSQL integration test uses the existing dedicated `EVIDENCEFORGE_TEST_DATABASE_URL` convention and verifies real pgvector persistence, workspace scope, `vector(384)`, and idempotent repeat behavior. It uses a deterministic fake model so the database test does not download model weights.

## 10. Schema and migration boundary

No schema changes are required for Task 7B. The implementation uses the Task 7A migration:

```text
0007_evidence_chunk_embeddings
```

No new migration, vector index, HNSW index, or database table is introduced.

## 11. Explicit non-goals

This task does not implement:

```text
semantic request-path retrieval
RRF
HNSW production indexing
LLM generation
questionnaire auto-answering
```

It also does not add API changes, grounding-service retrieval changes, response generation, or autonomous background workers.

## 12. Next step

After this lifecycle is validated, a separate task may evaluate workspace-authorized exact semantic retrieval over the persisted vectors. That task must preserve the deterministic configuration hash, content-hash checks, provenance, bounded failure behavior, and lexical fallback boundary established here.
