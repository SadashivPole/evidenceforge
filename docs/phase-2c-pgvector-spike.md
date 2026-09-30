# Phase 2C Task 3: pgvector Infrastructure Spike

- **Status:** Runtime spike completed successfully in the isolated Docker environment
- **Baseline:** `4be0491`
- **Scope:** Disposable PostgreSQL 16 / pgvector 0.8.6 infrastructure validation

## 1. Objective

This spike isolates the database mechanics needed for a later Phase 2C semantic-retrieval experiment:

```text
PostgreSQL 16
      ↓
pgvector 0.8.6
      ↓
vector(384)
      ↓
384-dimensional data insertion
      ↓
exact cosine search
      ↓
HNSW index creation
      ↓
HNSW query
      ↓
deterministic comparison
```

It does not implement semantic retrieval, hybrid retrieval, RRF, real evidence embeddings, production embedding persistence, or production migrations.

**Synthetic vectors are infrastructure-test vectors only. They do not measure semantic retrieval quality.**

## 2. Spike architecture

The spike uses a dedicated Compose file and a dedicated named volume:

```text
compose.phase2c-pgvector-spike.yaml
        ↓
pgvector/pgvector:0.8.6-pg16
        ↓
disposable evidenceforge_pgvector_spike database
        ↓
phase2c_vector_spike table
        ↓
raw SQL through psycopg
```

The normal application `compose.yaml`, PostgreSQL service, application database, evidence schema, search implementation, and search APIs are not used or modified.

The test refuses to run against a database whose name does not contain `spike`, and it verifies that the isolated public schema contains no application tables before creating the spike table.

## 3. PostgreSQL and pgvector versions

The isolated service is configured with:

```text
PostgreSQL major version: 16
pgvector image:          pgvector/pgvector:0.8.6-pg16
pgvector extension:      0.8.6
```

The automated test verifies both `server_version_num` and the installed `vector` extension version before continuing.

## 4. Isolated database configuration

The Compose file is:

```text
compose.phase2c-pgvector-spike.yaml
```

It uses:

- database: `evidenceforge_pgvector_spike`;
- user: `evidenceforge_spike`;
- host port: `55433` by default, configurable with `EVIDENCEFORGE_PGVECTOR_SPIKE_PORT`;
- volume: `evidenceforge-phase2c-pgvector-spike-data`; and
- healthcheck through `pg_isready`.

The test uses a separate connection variable:

```text
EVIDENCEFORGE_PGVECTOR_SPIKE_DATABASE_URL
```

Example local setup, with the password kept outside the repository:

```powershell
$env:EVIDENCEFORGE_PGVECTOR_SPIKE_PASSWORD = "use-a-local-disposable-password"
$env:EVIDENCEFORGE_PGVECTOR_SPIKE_DATABASE_URL = "postgresql://evidenceforge_spike:use-a-local-disposable-password@localhost:55433/evidenceforge_pgvector_spike"
```

Do not commit the password or a populated `.env` file. Do not point this variable at the normal application database.

## 5. Vector schema

The spike uses only this isolated table; it does not reuse `EvidenceChunk` or any production table:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE phase2c_vector_spike (
    id UUID PRIMARY KEY,
    content_hash TEXT NOT NULL,
    embedding vector(384) NOT NULL
);
```

The test inserts 750 deterministic rows and verifies the PostgreSQL type is `vector(384)`. The table is dropped in a `finally` block after the test.

## 6. Synthetic vector generation

The test is implemented in:

```text
backend/tests/evaluation/test_pgvector_spike.py
```

Each vector is derived from:

```text
candidate UUID + content hash
```

SHA-256 blocks expand the stable seed into exactly 384 finite floating-point values. No random source, model, network provider, or semantic embedding package is used. The same candidate UUID and content hash always produce the same vector.

**Synthetic vectors are infrastructure-test vectors only. They do not measure semantic retrieval quality.**

## 7. Exact cosine search

The test uses the pgvector cosine-distance operator directly in PostgreSQL:

```sql
embedding <=> %s::vector
```

The query vector is exactly the deterministic vector for one stored target candidate. The test verifies that:

- all 750 vectors insert successfully;
- the query returns 10 rows;
- PostgreSQL/pgvector reports a zero distance for the identical target vector;
- the expected target candidate is first;
- repeated exact queries return the same ordered IDs; and
- lower distance is treated as greater similarity.

The test does not calculate cosine similarity in Python as a substitute for the database query.

## 8. HNSW index

After the exact reference query, the test creates:

```sql
CREATE INDEX phase2c_vector_spike_hnsw
ON phase2c_vector_spike
USING hnsw (embedding vector_cosine_ops);
```

The test forces the query plan to use the isolated HNSW index and verifies the index name appears in `EXPLAIN`. It then runs the same query through PostgreSQL/pgvector and repeats it to verify deterministic output for this fixture.

HNSW is only an approximate-index candidate. It is not production-approved by this spike. Its parameters are not tuned here; recall and latency tuning belong to the later semantic benchmark.

## 9. Exact versus HNSW experiment

The fixture contains 750 deterministic vectors, exceeding the preferred 500–1000-vector minimum for this small infrastructure check. Both paths use:

```text
cosine distance
k = 10
```

The test compares:

- exact top-10 IDs;
- HNSW top-10 IDs;
- overlap count and rate;
- repeated-query determinism; and
- single-run query latency for orientation only.

A single small synthetic fixture must not be used to claim statistical significance, production recall, or semantic quality. Lower cosine distance means greater similarity; the output must not label distance as similarity.

## 10. Results

The isolated PostgreSQL/pgvector container started successfully and the database became healthy. The runtime spike verified the extension, vector schema, insertion, exact cosine search, HNSW index creation, HNSW search, and deterministic comparison.

The actual infrastructure-spike measurements were:

```text
PostgreSQL server_version_num: 160015
PostgreSQL version: 16.0.15
pgvector extension version: 0.8.6

Synthetic vector count: 750
Vector dimensions: 384

Exact/HNSW overlap: 10/10 (100.00%)

Exact latency (ms, single run): 46.698
HNSW latency (ms, single run): 46.709
```

The exact and HNSW top-10 results matched completely on this deterministic 750-vector synthetic fixture. The HNSW latency was slightly higher than exact search in this single run; this does not establish that either path is faster.

These are infrastructure-spike measurements using synthetic vectors. They are not semantic retrieval accuracy, production HNSW performance, representative production latency, proof that HNSW is faster, or proof of semantic retrieval quality.

Synthetic vectors are infrastructure-test vectors only. They do not measure semantic retrieval quality.

The targeted pgvector test passed, and the full repository validation completed with:

```text
Targeted pgvector test: 1 passed, 1 warning
Full repository:        303 passed, 8 skipped, 1 warning
Ruff:                   All checks passed!
Ruff format:            106 files already formatted
git diff --check:       PASS
```

## 11. Known limitations

- The vectors are synthetic and do not measure semantic retrieval quality.
- The fixture is small and does not establish production latency, recall, or index tuning.
- The HNSW `ef_search` value is set to `100` only to make this fixture comparison repeatable; it is not a production recommendation.
- No real embedding model is installed or exercised.
- No real EvidenceChunk data is used.
- No production migration, schema, Docker service, or retrieval path is changed.
- Docker and a disposable PostgreSQL service are required for the test to run.
- The reported single-run latencies are diagnostic observations, not a performance benchmark.

## 12. Cleanup procedure

After the spike:

```powershell
docker compose -f compose.phase2c-pgvector-spike.yaml down -v
```

The `-v` removes the named disposable spike volume. This command does not address or remove the normal EvidenceForge PostgreSQL service or its volume.

The test itself drops `phase2c_vector_spike` in a `finally` block, but volume cleanup is still required for deterministic environment teardown.

## 13. Next step

After the infrastructure spike runs successfully, Phase 2C Task 4 can evaluate real local semantic embeddings against the fixed 60-case corpus under:

```text
lexical-only
semantic-only
lexical + semantic + RRF
```

Task 4 must preserve the existing lexical baseline and evaluate category-level behavior, cross-workspace isolation, stale/conflicting visibility, insufficient-evidence behavior, and injection handling. This task does not implement Task 4.
