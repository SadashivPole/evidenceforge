# Phase 2C Task 7A: EvidenceChunkEmbedding Persistence Foundation

- **Status:** Persistence foundation implementation validated against real PostgreSQL 16 + pgvector 0.8.6
- **Migration:** `0007_evidence_chunk_embeddings`
- **Scope:** Additive PostgreSQL/pgvector persistence for attributable evidence-chunk embeddings
- **Production behavior:** Existing lexical retrieval remains unchanged

## 1. Purpose

This task establishes the production persistence boundary for future evidence-chunk embeddings:

```text
EvidenceChunk
     ↓
EvidenceChunkEmbedding
     ↓
PostgreSQL + pgvector persistence
     ↓
provenance + integrity constraints
```

The implementation is intentionally limited to the persistence foundation. It does not create embeddings, load a model, run semantic queries, fuse ranked lists, or change grounding behavior.

## 2. Schema

The new SQLAlchemy model and database table are:

```text
EvidenceChunkEmbedding
→ evidence_chunk_embeddings
```

Fields:

| Field | Type / constraint | Purpose |
|---|---|---|
| `id` | UUID primary key | Stable embedding-row identity |
| `evidence_chunk_id` | UUID, `EvidenceChunk` foreign key | Immutable source chunk identity |
| `evidence_content_hash` | `String(64)`, required | Content hash used to produce the vector |
| `workspace_id` | UUID, `Workspace` foreign key | Explicit workspace provenance and future retrieval boundary |
| `model_id` | `String(255)`, required | Embedding model identity |
| `model_version` | `String(255)`, required | Model artifact/version generation |
| `configuration_hash` | `String(64)`, required | Embedding configuration identity |
| `embedding_dimension` | integer, required, check `= 384` | Explicit configured dimension |
| `embedding` | `vector(384)`, required | Persisted pgvector value |
| `created_at` | timezone-aware timestamp | Persistence timestamp |

The initial model metadata baseline is:

```text
model_id:           sentence-transformers/multi-qa-MiniLM-L6-cos-v1
embedding dimension: 384
similarity:         cosine
```

The model is not loaded or used by the production application in this task.

## 3. Provenance and generation identity

The embedding row is attributable through:

```text
EvidenceChunk
    ↓
evidence_content_hash
    ↓
model_id + model_version
    ↓
configuration_hash
    ↓
embedding_dimension + embedding
```

The database foreign key preserves the source chunk identity. The generation uniqueness constraint is:

```text
evidence_chunk_id
+ evidence_content_hash
+ workspace_id
+ model_id
+ model_version
+ configuration_hash
```

Constraint name:

```text
uq_evidence_chunk_embeddings_generation
```

This prevents accidental duplicate rows for the same generation while allowing side-by-side model and configuration generations. An existing row is never overwritten by this schema when content, model, model version, or configuration changes.

The existing immutable evidence model remains authoritative. A content change must produce a new immutable evidence version/chunk and a separately attributable embedding:

```text
A content change produces a new immutable evidence chunk/version
and therefore a separately attributable embedding.
```

The embedding table retains the content hash so a future embedding lifecycle can compare the stored hash with the current source chunk hash before treating a vector as current.

## 4. Workspace invariant

The existing `EvidenceChunk` table reaches its workspace through:

```text
EvidenceChunk
  → EvidenceDocumentVersion
  → EvidenceDocument.workspace_id
```

`EvidenceChunk` does not currently carry a duplicated `workspace_id`, so adding a composite foreign key would require a broader existing-schema redesign. This task does not add that column or rewrite existing evidence tables.

Instead, migration `0007_evidence_chunk_embeddings` adds two explicit database foreign keys and one narrow PostgreSQL provenance trigger. On insert or relevant update, the trigger checks that:

```text
embedding.workspace_id == parent document.workspace_id
embedding.evidence_content_hash == parent EvidenceChunk.content_hash
```

A mismatch raises a database integrity error. This prevents an embedding row from being attached to a chunk under another workspace or from claiming a different source content hash. The trigger is not a retrieval mechanism and does not alter evidence ownership.

The foreign keys also enforce:

```text
embedding.evidence_chunk_id → evidence_chunks.id
embedding.workspace_id → workspaces.id
```

The future semantic query must still include the explicit workspace predicate on the embedding relation and joined evidence records before nearest-neighbor ordering and limiting. The persistence invariant is defense in depth; it is not permission to post-filter an unauthorized vector result.

## 5. pgvector dependency and environment

The production Python dependency is:

```text
pgvector>=0.5,<1
```

This supplies the SQLAlchemy `Vector(384)` type. It is separate from the PostgreSQL extension version.

The existing application Compose database image was changed minimally from the plain PostgreSQL image to:

```text
pgvector/pgvector:0.8.6-pg16
```

The target environment remains:

```text
PostgreSQL 16
pgvector extension 0.8.6
```

The existing isolated Phase 2C spike Compose configuration already uses the same pgvector image and was not duplicated or replaced.

The earlier synthetic pgvector spike validated infrastructure mechanics only. It does not establish production semantic performance, production index performance, or production retrieval quality.

## 6. Migration

The next migration after `0006_questionnaire_responses` is:

```text
0007_evidence_chunk_embeddings
```

Upgrade behavior:

1. runs `CREATE EXTENSION IF NOT EXISTS vector`;
2. creates `evidence_chunk_embeddings`;
3. creates the 384-dimensional vector column;
4. creates foreign keys to `evidence_chunks` and `workspaces`;
5. creates the dimension check constraint;
6. creates the generation uniqueness constraint;
7. creates B-tree indexes for chunk ID, content hash, workspace ID, and model ID; and
8. creates the narrow provenance trigger described above.

Downgrade removes only the trigger, trigger function, indexes, and embedding table created by the revision. It does not drop the shared `vector` extension, because extension lifecycle is an environment concern and other database objects may depend on it.

The migration is additive. It does not delete or rewrite evidence documents, evidence versions, evidence chunks, questionnaires, responses, or citations.

No HNSW index is created. No production vector similarity query is added.

## 7. Tests and integrity coverage

Focused SQLite-safe model/persistence tests cover:

- declared vector dimension `384`;
- required provenance fields;
- provenance round-trip persistence;
- duplicate generation identity rejection;
- coexistence of model versions and configuration hashes;
- changed content hashes represented by separate immutable chunks;
- nonexistent evidence-chunk foreign-key rejection; and
- declared-dimension check rejection.

A PostgreSQL-specific test uses the existing `EVIDENCEFORGE_TEST_DATABASE_URL` convention when configured. It upgrades the dedicated database to Alembic head and verifies:

- PostgreSQL major version 16;
- pgvector extension version 0.8.6;
- Alembic revision `0007_evidence_chunk_embeddings`;
- the `vector(384)` column type;
- required constraints and indexes;
- no HNSW index on the new table;
- valid provenance persistence;
- model-generation coexistence; and
- database rejection of workspace, content-hash, and foreign-key mismatches.

The PostgreSQL test is skipped with an explicit reason when a dedicated PostgreSQL URL is not configured. A real PostgreSQL 16 + pgvector 0.8.6 database is required for that validation; SQLite does not substitute for the vector migration test.

## 8. Intentionally not implemented

The checkpoint explicitly does not implement:

```text
Embedding persistence exists.
Embedding generation does not exist.
Semantic retrieval does not exist.
HNSW production indexing does not exist.
RRF does not exist in production.
```

Also not implemented:

- model download or application-startup model loading;
- `SentenceTransformer`, `encode`, `encode_query`, or `encode_document` in the production path;
- background embedding workers or backfill jobs;
- semantic query services;
- API changes;
- grounding-service behavior changes;
- search-service or search-policy changes;
- citation behavior changes; and
- response generation.

The existing lexical grounding endpoint and lexical retrieval path remain authoritative and unchanged.

## 9. Security boundary

Embedding vectors are derived sensitive evidence. This foundation:

- preserves workspace and source-chunk provenance;
- prevents cross-workspace embedding rows through the database invariant;
- does not expose vectors through existing APIs;
- does not log vector contents or raw evidence content;
- does not change evidence ownership;
- does not mutate evidence based on embedding state; and
- does not introduce a nearest-neighbor query that could bypass authorization.

Future semantic retrieval must apply workspace filtering on the embedding relation before ordering and limiting. This task does not implement that retrieval path.

## 10. Validation evidence

The persistence foundation was validated against the Windows PostgreSQL Compose service using the dedicated database:

```text
evidenceforge_test
```

The PostgreSQL integration test ran inside the temporary validation container with `EVIDENCEFORGE_TEST_DATABASE_URL` configured:

```text
python -m pytest tests/test_evidence_chunk_embeddings_postgres.py -q
```

Validation results:

```text
PostgreSQL 16:
PASS

pgvector extension:
0.8.6

Alembic revision:
0007_evidence_chunk_embeddings

Embedding column:
vector(384)

Real PostgreSQL migration/integrity validation:
PASS

PostgreSQL migration/integrity test:
1 passed

Full repository suite:
322 passed, 28 skipped, 1 warning

Ruff:
All checks passed

Ruff format:
113 files already formatted

Compileall:
PASS
```

The remaining warning is the non-fatal `Starlette/httpx deprecation warning`. No semantic retrieval, embedding generation, HNSW performance, or RRF result is claimed by this checkpoint.

## 11. Next step

The next implementation step can add a deterministic embedding lifecycle and backfill/indexing process, followed by workspace-authorized exact semantic retrieval. That work must preserve this provenance schema, validate content hashes before treating embeddings as current, keep lexical retrieval available as fallback, and defer HNSW until exact-search correctness and broader validation are complete.
