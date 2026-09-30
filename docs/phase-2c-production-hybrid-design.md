# EvidenceForge — Phase 2C Task 6: Production Hybrid Retrieval Design Gate

- **Status:** Design-only gate completed; production implementation not approved
- **Baseline supplied for this gate:** `8f6e331 test: evaluate lexical semantic and RRF retrieval`
- **Scope:** Proposed production architecture for workspace-authorized lexical plus semantic retrieval with bounded RRF fusion
- **Production impact:** Documentation only; production code and behavior remain unchanged
- **Related documents:** [`phase-2c-retrieval-design.md`](phase-2c-retrieval-design.md), [`phase-2c-pgvector-spike.md`](phase-2c-pgvector-spike.md), [`phase-2c-semantic-benchmark.md`](phase-2c-semantic-benchmark.md), [`phase-2c-rrf-evaluation.md`](phase-2c-rrf-evaluation.md)

## Executive decision

```text
Experiment result:
Hybrid RRF improved measured nDCG on the fixed 60-case benchmark.

Design decision:
Proceed to production hybrid-retrieval design review.

Implementation decision:
NOT approved yet. Production code remains unchanged until this design gate
and its subsequent implementation validation are complete.
```

This document is a design specification and review boundary. It does not implement production semantic retrieval, vector persistence, RRF, API changes, generation, or migrations.

## Current implementation / proposed production design / open decisions

The document uses three explicit labels:

- **CURRENT IMPLEMENTATION** describes behavior present in the repository at this gate. It is not a statement about the proposed target.
- **PROPOSED PRODUCTION DESIGN** describes the bounded architecture that a separately reviewed implementation could add later.
- **OPEN DECISION** identifies a choice that must not be silently made by the implementation phase.

The proposed design must retain the current lexical path as a working path and as the semantic failure fallback throughout implementation and rollout.

## 1. Current production baseline

### CURRENT IMPLEMENTATION: request and authorization boundary

The current grounding route is workspace-scoped:

```text
GET /workspaces/{workspace_id}/questionnaires/{questionnaire_id}
    /versions/{questionnaire_version_id}
    /questions/{questionnaire_version_question_id}/grounding
```

The authenticated workspace context is resolved by the server. `get_workspace_context` obtains the workspace from the route identifier and verifies the authenticated user's membership. The route does not trust a client-supplied workspace header or body field. The grounding route separately verifies the questionnaire, questionnaire version, and question chain in the same workspace before calling the grounding service.

The current grounding service loads the normalized questionnaire question through that workspace/version identity chain, normalizes the question with the existing search policy, loads workspace-scoped evidence candidates, and returns deterministic search results plus citation-ready provenance. It performs reads only; it does not create responses, revisions, citations, or other records.

### CURRENT IMPLEMENTATION: lexical retrieval path

The existing production path is:

```text
authenticated workspace context
        ↓
workspace-scoped question/version lookup
        ↓
query normalization and validation
        ↓
workspace-scoped evidence candidate query
        ↓
deterministic lexical scoring
        ↓
bounded result list and citation metadata
```

The relevant production components are:

- `backend/app/questionnaires/grounding/service.py` — loads the authorized question, calls candidate loading and lexical search, builds citations, and returns `MATCHED` or `NO_MATCHES`.
- `backend/app/evidence/search/service.py` — normalizes queries and scores candidate chunks using exact whole-phrase matching, matched-token coverage, token occurrence count, and section-token coverage.
- `backend/app/evidence/search/policy.py` — defines the current lexical policy.
- `backend/app/evidence/persistence/search_repository.py` — loads persisted candidate chunks with a workspace predicate.
- `backend/app/evidence/search/types.py` — defines the candidate and result metadata carried by the lexical path.
- `backend/app/evidence/citations/service.py` — converts a candidate into an immutable citation.

The lexical policy currently is:

```text
min_query_length:  2 characters
max_query_length:  256 characters
default_limit:     10
max_limit:         50
search_version:    text-search-v1
```

The production candidate query joins `EvidenceChunk` to `EvidenceDocumentVersion` and `EvidenceDocument`, and applies:

```text
EvidenceDocument.workspace_id == authorized_workspace_id
```

Optional document and version filters are also applied when supplied by the repository caller. The query has a deterministic source ordering by document ID, version number, chunk index, and chunk ID before the pure Python lexical scoring step.

Lexical scoring is deterministic. Results are ordered by descending score, descending matched-term count, descending occurrence count, document ID, version number, and chunk index. The current lexical path is not to be changed by this design gate.

### CURRENT IMPLEMENTATION: evidence candidate and provenance model

The persisted evidence hierarchy is:

```text
Workspace
  └── EvidenceDocument
        └── EvidenceDocumentVersion
              └── EvidenceChunk
```

`EvidenceDocumentVersion` is immutable and retains version number, normalized and raw hashes, normalization/chunking metadata, extracted text, and creation provenance. `EvidenceChunk` retains its immutable parent version, zero-based chunk index, content, content hash, normalized byte offsets, optional section label, optional page number, and creation time.

The current `SearchChunkCandidate` carries:

```text
chunk_id
document_id
version_id
version_number
chunk_index
content
content_hash
normalized_start_byte
normalized_end_byte
section_label
page_number
```

The candidate type does not carry a workspace field because the current repository query establishes workspace scope from the joined document. The grounding service supplies the authorized workspace when it builds an `EvidenceCitation`. Public grounding results and persisted response citations preserve the document, version, chunk, content hash, byte range, section, and page provenance.

The current retrieval path does not contain production embeddings, vector tables, pgvector ORM models, semantic search, production RRF, or a final response-generation layer. The current search repository also does not apply a latest-version, freshness, or conflict-resolution policy; authorized persisted chunks are candidates according to its workspace and optional document/version predicates.

### CURRENT IMPLEMENTATION: lexical benchmark reference

The unchanged fixed 60-case corpus contains:

```text
SUPPORTED:              20
AMBIGUOUS:               10
INSUFFICIENT_EVIDENCE:   10
CONFLICTING_STALE:       10
MALICIOUS_INJECTED:     10
TOTAL:                   60
```

The measured lexical reference is:

```text
Recall@5:  0.8333333333333334
nDCG@5:    0.9219567263864729
nDCG@10:   0.9219567263864729
```

These are retrieval/evidence-state measurements, not answer accuracy, grounded-response accuracy, reviewer decisions, or true abstention.

## 2. Proposed target architecture

### PROPOSED PRODUCTION DESIGN

The target flow is:

```text
Question
   ↓
Query normalization / validation
   ↓
Authorized workspace boundary
   ├────────────────────────┐
   ↓                        ↓
Lexical retrieval       Semantic retrieval
   ↓                        ↓
bounded top-k            bounded top-k
   └──────────────┬─────────┘
                  ↓
                 RRF
                  ↓
        deterministic final top-k
                  ↓
       freshness/conflict handling
                  ↓
       bounded evidence context
                  ↓
       existing grounded response
```

The phrase **existing grounded response** describes the intended future consumer boundary, not a currently implemented response generator. The repository currently exposes deterministic grounding results and supports persisted, human-managed responses/citations; it does not implement LLM response generation.

The parts of the target that already exist are:

- authenticated workspace context and workspace membership authorization;
- questionnaire/version/question scope validation;
- query normalization and validation boundary;
- workspace-scoped lexical candidate loading;
- deterministic lexical ranking;
- immutable evidence versions and chunks;
- provenance/citation construction; and
- bounded lexical result limits.

The new parts that would require a separately approved implementation are:

- embedding generation and a reproducible embedding lifecycle;
- an `EvidenceChunkEmbedding` persistence relation;
- workspace-authorized semantic retrieval;
- a vector search/index path;
- lexical and semantic candidate diagnostics;
- RRF fusion and its deterministic ordering;
- semantic failure handling with lexical fallback;
- operational telemetry for both retrieval paths; and
- an explicit freshness/conflict-aware evidence-selection boundary.

No part of this target is implemented by this task.

## 3. Authorization ordering

### Security invariant

```text
Unauthorized candidates must never enter semantic retrieval,
RRF fusion, citation selection, or final evidence context.
```

This is a hard security invariant, not an evaluation preference.

### PROPOSED PRODUCTION DESIGN

1. The workspace ID comes from the authenticated request/workspace context. The server derives that context from the authenticated principal's membership in the route workspace. A client-supplied workspace value must not override it.
2. Lexical retrieval remains workspace-scoped in its database query, as it is today.
3. Vector retrieval must be workspace-scoped in its database query before ordering and limiting.
4. Only candidates returned under the authorized workspace predicate may enter RRF.
5. The fused result must be validated against candidate workspace metadata before citation selection and again before construction of final evidence context.
6. Any authorization or workspace mismatch is a hard security failure. It must fail closed and must not be converted into a normal semantic failure or silently handled by returning a potentially unsafe result.

The vector query must not retrieve a global nearest-neighbor list and post-filter it. The database query should enforce the boundary as part of the candidate relation and evidence joins. A conceptual query shape is:

```sql
SELECT
    embedding.evidence_chunk_id,
    chunk.document_version_id,
    version.document_id,
    document.workspace_id,
    chunk.content_hash,
    embedding.embedding,
    embedding.model_id,
    embedding.model_version,
    embedding.embedding_dimension
FROM evidence_chunk_embedding AS embedding
JOIN evidence_chunks AS chunk
  ON chunk.id = embedding.evidence_chunk_id
JOIN evidence_document_versions AS version
  ON version.id = chunk.document_version_id
JOIN evidence_documents AS document
  ON document.id = version.document_id
WHERE embedding.workspace_id = :authorized_workspace_id
  AND document.workspace_id = :authorized_workspace_id
  AND embedding.model_id = :active_model_id
  AND embedding.embedding_dimension = :expected_dimension
ORDER BY embedding.embedding <=> :query_embedding
LIMIT :semantic_top_k;
```

This is a design shape, not a migration or production query implementation. The parent evidence join and both workspace predicates are intentional. The implementation must also verify that the embedding's workspace and joined document workspace agree. If the database supports row-level security or an equivalent additional control, it may be used as defense in depth, but it does not replace application authorization and explicit workspace predicates.

Authorization must also apply to embedding creation, backfill, cache keys, retry jobs, deletion, and diagnostics. Query-time authorization must not depend only on a post-fusion check.

## 4. Embedding lifecycle

### PROPOSED PRODUCTION DESIGN

The logical future relation is:

```text
EvidenceChunkEmbedding
```

At minimum, each embedding row must be attributable through:

```text
evidence_chunk_id
evidence_content_hash
workspace_id
model_id
model_version / configuration
embedding_dimension
embedding
created_at
```

The production implementation may need additional fields for lifecycle state, input-window identity, generation-library version, configuration hash, failure diagnostics, and active/current selection. Those additions require implementation review and must not obscure the minimum provenance above.

A logical relationship is:

```text
EvidenceDocumentVersion
        ↓
EvidenceChunk
        ↓
EvidenceChunkEmbedding
```

The embedding relation is preferred over adding a vector column directly to the immutable evidence record because it permits multiple model/configuration generations, side-by-side validation, explicit stale state, and independent rebuilds while preserving the source evidence identity.

### Immutable evidence-version rule

```text
A content change produces a new immutable chunk/version
and therefore a separately attributable embedding.
```

The current ingestion flow creates a new immutable document version and deterministic chunks when the normalized representation changes. A future embedding job must use the resulting `evidence_chunk_id` and `content_hash` as part of its identity. It must not overwrite an old embedding in place when content, model, model artifact, tokenizer policy, normalization, or input-window configuration changes.

An embedding is current only for the exact source content hash and frozen model/configuration it records. An old embedding remains attributable to its old immutable version and may be marked stale or inactive for a selected model, but it must not be presented as the current embedding for changed content. A model upgrade creates a new attributable embedding generation; it does not silently reinterpret old vectors.

Embedding-derived data is sensitive. Workspace access, backup, deletion, retention, and operational handling must be at least as deliberate as for its source evidence. The design does not authorize exposing raw embeddings through ordinary user-facing APIs.

No table, ORM model, migration, or embedding lifecycle job is added by this task.

## 5. Model strategy

### CURRENT IMPLEMENTATION: experimental baseline

The completed semantic benchmark uses:

```text
model identifier:       sentence-transformers/multi-qa-MiniLM-L6-cos-v1
embedding dimension:    384
similarity:             cosine similarity
question encoder:       encode_query()
evidence encoder:       encode_document()
input policy:           max_input_word_pieces = 250
```

The benchmark documents the observed development stack as `sentence-transformers 5.7.0`, `torch 2.14.0+cu130`, and `transformers 5.17.0`. The model is an experimental baseline, not a final production model recommendation or production certification. No external embedding provider is part of this design.

### PROPOSED PRODUCTION DESIGN

Production reproducibility must freeze and record all of the following for each embedding generation and query execution:

- exact model identifier;
- immutable model artifact/version or digest, not only a mutable registry name;
- embedding dimension;
- tokenizer identity and tokenizer/input policy;
- query/document encoding mode (`encode_query` versus `encode_document`, or an explicitly frozen equivalent);
- normalization behavior and whether stored/query vectors are normalized;
- similarity/distance metric;
- embedding library and runtime versions;
- model configuration and generation configuration hash; and
- the active index/model configuration used for retrieval.

The current MiniLM model remains the experimental starting point because it is the measured repository baseline. Selecting a different production model is an **OPEN DECISION** requiring a separately identified experiment, new vectors, and direct comparison against the fixed corpus and broader workload fixtures.

## 6. Long-input policy

### CURRENT IMPLEMENTATION: benchmark behavior

The semantic benchmark has an explicit, deterministic policy:

```text
max_input_word_pieces = 250
```

For an input longer than the bound, the benchmark uses a deterministic prefix window of the first 250 word pieces and records that windowing occurred. This is a benchmark policy, not a final production requirement. It must not be described as an approved production chunking strategy.

### PROPOSED PRODUCTION DESIGN

Production must not rely on silent tokenizer truncation. The implementation must choose and record a policy for:

- evidence chunking/windowing strategy;
- model input limit and tokenization behavior;
- whether evidence chunks should be bounded before they are stored;
- overlap and window-to-parent-chunk mapping;
- whether queries require windowing; and
- how multiple window results map to one citation-bearing parent chunk.

A future design may use deterministic windows or enforce a bounded stored-chunk contract, but the implementation must reject or explicitly transform oversized input rather than silently dropping content. Every transformation must retain the parent immutable chunk ID, content hash, input-window identity, and configuration provenance.

**OPEN DECISION:** validate whether the current byte-oriented evidence chunking is sufficient for the selected model, whether a separate token-window layer is required, and what token limit/stride/query-window aggregation preserves recall without unbounded expansion.

## 7. Vector search and index design

### CURRENT IMPLEMENTATION / SPIKE EVIDENCE

The isolated pgvector spike validated database mechanics with synthetic vectors using:

```text
PostgreSQL 16
pgvector 0.8.6
vector dimension 384
cosine distance
HNSW index candidate
```

The spike verified vector insertion, exact cosine search, HNSW index creation, and deterministic comparison on a disposable synthetic fixture. Its exact and HNSW top-10 results overlapped 10/10 for that fixture. The spike explicitly does not establish semantic quality, production recall, production latency, or that HNSW is faster.

### PROPOSED PRODUCTION DESIGN

- Exact vector search is the correctness and regression-reference mode.
- HNSW is the likely production index candidate for bounded nearest-neighbor search.
- Exact and HNSW results must be compared against the same real embeddings and workload before HNSW is accepted.
- HNSW index parameters, search parameters, maintenance behavior, and resource requirements remain open implementation decisions.
- The vector query must integrate workspace filtering safely, as specified in the authorization section.
- The application must apply deterministic final ordering after retrieval/fusion; it must not rely on nondeterministic database ordering for tied distances, tied scores, or equivalent candidates.

The vector distance is a component retrieval signal. It is not evidence sufficiency, entailment, approval, freshness, or answer correctness.

No pgvector extension change, production table, ORM model, index, migration, or Docker change is made by this task.

## 8. Semantic failure fallback

### PROPOSED PRODUCTION DESIGN

The semantic path must be optional at request time. If semantic retrieval fails, lexical retrieval remains available:

```text
semantic success:
    lexical + semantic → RRF

semantic failure:
    lexical-only fallback
```

Semantic failure includes at least:

- embedding model unavailable or model load failure;
- query embedding failure;
- vector database or vector-index failure;
- invalid, non-finite, or incorrect-dimension embedding;
- timeout;
- corrupted or missing stored embedding;
- stale embedding for the requested model/content hash;
- long-input/tokenization/windowing failure; and
- incomplete or unavailable semantic index population.

Fallback behavior is bounded:

- use no unbounded retries;
- use a finite timeout and finite retry policy, if any retry is approved;
- do not expand lexical limits to compensate for a semantic failure;
- preserve lexical ordering, workspace scope, candidate limits, and provenance;
- record the semantic failure class and fallback outcome; and
- do not fail the whole request merely because semantic retrieval failed.

A workspace/authorization mismatch is not an ordinary semantic failure. It is a hard security failure that fails closed and must not be hidden by lexical fallback. Similarly, an integrity violation that could make candidate identity or authorization uncertain must be handled as a security/integrity failure rather than silently downgraded.

The implementation must make fallback observable without logging raw evidence content. It must distinguish a genuine semantic success from lexical fallback so evaluation, operations, and rollout metrics cannot incorrectly label fallback as hybrid retrieval.

## 9. RRF production configuration

### PROPOSED PRODUCTION DESIGN: initial measured baseline

The initial production implementation candidate uses the measured evaluation configuration:

```text
lexical_top_k = 10
semantic_top_k = 10
rrf_k = 60
final_top_k = 10
```

For one-based rank `r`, a candidate receives:

```text
RRF(candidate) = Σ 1 / (rrf_k + rank)
```

The sum contains one contribution for every list in which the candidate appears. A candidate present in only one list receives only that list's contribution. A candidate present in both receives both contributions.

Candidate identity is:

```text
stable evidence candidate/chunk ID
```

The implementation must never merge candidates by text content. Evidence chunks with the same text but different chunk IDs, document IDs, versions, or workspaces remain separate candidates until an explicit evidence-selection policy says otherwise.

The required deterministic ordering is:

1. descending RRF score;
2. lexical-list presence before lexical-list absence;
3. ascending lexical rank;
4. semantic-list presence before semantic-list absence;
5. ascending semantic rank; and
6. ascending stable candidate/chunk ID.

Ranks start at one. Missing component ranks sort after present ranks within the relevant presence rule. Database order must not replace this application-level total ordering.

RRF is a ranking mechanism only. It must not be interpreted as a grounding decision, citation decision, answer decision, approval, or abstention decision.

## 10. Freshness and conflicts

### CURRENT IMPLEMENTATION

The current immutable model preserves evidence versions through `EvidenceDocumentVersion.version_number`, normalized/raw hashes, chunking and normalization metadata, and `EvidenceChunk.content_hash`. The current search candidate and citation structures preserve version and chunk identity. The current lexical repository does not silently select only the newest version and does not define a production freshness/conflict ranking policy. The fixed benchmark carries conflict/stale expectations, but those expectations are evaluation semantics rather than a current response-generation policy.

There is no current production `conflict_group`, freshness score, or final conflict-resolution rule in the inspected evidence/search implementation. This gate does not invent one.

### PROPOSED PRODUCTION DESIGN

- Every lexical or semantic candidate remains attributable to its immutable evidence chunk, document, document version, version number, content hash, and workspace.
- Multiple versions remain separate candidates. RRF must not deduplicate them by text, document ID alone, or similarity score.
- A stale candidate is represented by its immutable version and whatever explicitly approved freshness metadata is available. Similarity must never silently make stale evidence current.
- Conflicting evidence remains visible as separate attributable candidates. A later selection stage may organize or label it, but retrieval must not silently collapse current, stale, or conflicting versions into one text record.
- If conflict groups or freshness metadata are added later, they must be carried through the fused candidate and evidence-selection records.
- The post-RRF freshness/conflict boundary may rank, group, flag, or require review, but it must not discard a conflict merely because one candidate has a higher semantic score without an explicit policy.

The proposed target architecture therefore includes:

```text
deterministic final top-k
        ↓
freshness/conflict handling boundary
        ↓
bounded evidence context
```

**OPEN DECISION:** choose the final freshness/conflict policy, including whether stale candidates are filtered, flagged, grouped, or retained alongside current candidates; how conflicting versions are guaranteed visible within bounds; and which role, status, or future response layer consumes that state. Until then, the safe design is preservation and explicit review, not silent resolution.

## 11. Candidate and context bounds

### PROPOSED PRODUCTION DESIGN

Each stage has its own finite bound:

| Stage | Initial bound | Meaning |
|---|---:|---|
| Lexical candidates | `10` | Maximum lexical ranked candidates entering the component result |
| Semantic candidates | `10` | Maximum semantic ranked candidates entering the component result |
| Fused candidate union | `20` maximum | At most `lexical_top_k + semantic_top_k` unique stable candidate IDs before final limiting |
| Deterministic fused result | `10` | `final_top_k` after RRF ordering |
| Final evidence context | finite independent bound | Must be explicitly configured for chunk count and total size; it must not exceed the approved fused result unless a separately bounded selection step is approved |

The first implementation should keep final evidence context no larger than the 10-candidate fused result and must impose an additional finite byte/token budget. The context budget is intentionally separate from component top-k and RRF final top-k because a future response layer may need a smaller context than the ranked result list.

No stage may expand candidates without a fixed maximum. Window-level embeddings, duplicate version handling, conflict visibility, retries, or fallback behavior must not create an unbounded candidate union. If multiple semantic windows map to the same parent chunk, the mapping/aggregation rule must be deterministic and retain the parent provenance.

**OPEN DECISION:** approve the exact final evidence chunk count, byte/token budget, window aggregation bound, and conflict-visibility bound after broader workload validation.

## 12. Provenance and citations

### PROPOSED PRODUCTION DESIGN

Provenance must survive every stage:

```text
embedding candidate
    → fused candidate
    → selected evidence
    → citation
    → grounded response
```

RRF may add component ranks, component membership, component scores, RRF score, model/index identifiers, and fallback state, but it must never remove or replace:

```text
evidence chunk ID
evidence version ID / version number
document ID
workspace ID
chunk index
section
page number
content hash
normalized byte range
```

The stable candidate/chunk ID is the identity used for fusion. If a semantic input window is used, its window ID and hash are diagnostic/derived metadata; the parent `EvidenceChunk` remains the citation-bearing evidence identity unless a separately approved citation model says otherwise.

Citation construction must retain the authorized workspace and exact immutable evidence coordinates. The existing `citation_from_candidate` pattern is the provenance boundary to preserve. A future fused-result type should wrap, rather than replace, the existing candidate metadata.

## 13. Security and injection boundary

### PROPOSED PRODUCTION DESIGN

```text
Evidence content is data, not instructions.
```

Semantic retrieval, embedding generation, and RRF must not:

- execute evidence content;
- call tools or agents because of evidence content;
- modify authorization or workspace scope;
- modify questionnaire definitions or questionnaire state;
- modify evidence;
- submit or approve questionnaire responses; or
- mutate the database based on retrieved text.

Instruction-like or malicious evidence remains ordinary retrieved content. The semantic encoder treats it as text for a ranking calculation. RRF treats it as an opaque candidate with numeric component signals. No evidence content may become executable policy merely because it ranks highly.

The current benchmark's injection semantics remain the boundary for this gate: injected candidates may be surfaced and must remain inert. There is no generation/tool layer in which end-to-end injection escape could be measured.

Embeddings and diagnostic scores are derived sensitive data. Operational systems must avoid exposing them through ordinary APIs and must avoid writing raw evidence content to logs, traces, metrics labels, or exception messages.

## 14. Observability

### PROPOSED PRODUCTION DESIGN

The implementation must emit structured, privacy-conscious telemetry for each retrieval execution or sampled execution. It must include:

- lexical retrieval success/failure and bounded failure class;
- semantic retrieval success/failure and bounded failure class;
- lexical fallback count and fallback reason;
- query-embedding latency;
- evidence/document embedding latency for background work;
- vector query latency;
- lexical ranking latency;
- RRF fusion latency;
- candidate counts at lexical, semantic, union, fused, and final-context stages;
- final context chunk count and size budget outcome;
- active model ID, model artifact/version, dimension, normalization/configuration version;
- PostgreSQL/pgvector and index/version configuration;
- exact versus HNSW retrieval mode;
- authorization rejection or hard security-failure count;
- stale/missing/corrupt embedding counts; and
- timeout and bounded-retry counts.

Telemetry should identify the workspace scope and request/trace correlation without placing raw evidence content in logs. Candidate identifiers, if operationally necessary, should be treated as sensitive opaque identifiers and should not be used as unrestricted metric labels. Error messages must use stable error classes rather than dumping query text, evidence text, embedding values, or database internals.

Fallback and semantic success must be separately measurable. A request that returned lexical-only results after a semantic timeout must not be counted as a successful hybrid request.

## 15. Evaluation and rollout gate

### PROPOSED PRODUCTION DESIGN

The unchanged fixed 60-case corpus becomes a regression suite for any future implementation. The implementation must report and inspect:

```text
Recall@5
nDCG@5
nDCG@10
evidence-state match
workspace leakage
injection inertness
conflict/stale visibility
insufficient-evidence behavior
```

The measured reference values are:

| Configuration | Recall@5 | nDCG@5 | nDCG@10 |
|---|---:|---:|---:|
| Lexical-only | 0.8333333333333334 | 0.9219567263864729 | 0.9219567263864729 |
| Semantic-only | 0.8333333333333334 | 0.953101098880283 | 0.953101098880283 |
| Hybrid RRF | 0.8333333333333334 | 0.9616266072655447 | 0.9616266072655447 |

The observed benchmark safety measurements were:

```text
workspace leakage:       0
injection inert rate:    1.0
conflict/stale complete: 1.0
```

The hybrid category results that informed this gate were:

| Category | Hybrid Recall@5 | Hybrid nDCG@5 | Interpretation boundary |
|---|---:|---:|---|
| `SUPPORTED` | 1.0 | 0.985825485869144 | Improved graded ranking; not answer accuracy |
| `AMBIGUOUS` | 1.0 | 0.9766009861291008 | Similarity is not approval |
| `INSUFFICIENT_EVIDENCE` | 0.0 | 0.8839441578296375 | Higher nDCG does not authorize an answer |
| `CONFLICTING_STALE` | 1.0 | 0.9842828264880937 | Visibility remained complete in the fixture |
| `MALICIOUS_INJECTED` | 1.0 | 0.9532807014081481 | Inertness remained 1.0; content stayed data |

The regression gate must not select a winner solely on aggregate nDCG. It must investigate any category or case-level regression, especially in insufficient-evidence, ambiguity, stale/conflicting, malicious/injected, and authorization behavior. A future production implementation must fail or pause rollout for any observed workspace leakage, any loss of injection inertness, any unexplained evidence-state regression, or any unexplained loss of conflict/stale visibility.

The fixed corpus is necessary but insufficient. Broader workload fixtures, adversarial authorization tests, incomplete-index tests, model/version reproducibility tests, exact-versus-HNSW comparisons, timeout/fallback tests, and load/resource tests are required before production rollout. Fixed-corpus zero leakage is not proof of production safety.

Runtime values from the evaluation are development measurements only and must not be used as production latency or capacity claims.

## 16. Correct abstention boundary

```text
Correct Abstention:
N/A — response/generation layer not implemented
```

The retrieval benchmark's `evidence-state match` is not an end-to-end abstention metric. No retrieval score, semantic similarity, RRF score, `NO_MATCHES` status, or candidate count may be relabeled as correct abstention or answer accuracy.

A future production retrieval path must expose enough state for a future response layer to determine whether evidence is sufficient. At minimum, that state must preserve candidate provenance, evidence-state/category signals where available, freshness/conflict metadata when defined, component/fused ranking diagnostics, and the distinction between semantic success and lexical fallback. The response/generation layer—not this design gate—must decide whether to answer, request review, or abstain according to a separately defined policy.

## 17. Migration and implementation strategy

### PROPOSED PRODUCTION DESIGN

The future implementation should proceed in this order:

```text
1. embedding schema/model
2. deterministic embedding lifecycle
3. backfill/indexing
4. semantic retrieval behind feature flag
5. lexical + semantic candidate collection
6. RRF fusion
7. regression tests
8. fallback/error handling
9. observability
10. controlled rollout
```

Expanded implementation boundary:

1. Add and review the embedding relation, model/artifact manifest, and migration without changing immutable evidence records.
2. Implement deterministic content-hash/model/configuration checks and embedding lifecycle states; preserve old attributable vectors.
3. Build bounded backfill and index jobs with workspace authorization, retry limits, missing/stale/error reporting, and exact reference validation.
4. Add workspace-authorized semantic retrieval behind a feature flag, initially with exact vector search as the correctness reference.
5. Collect bounded lexical and semantic lists without changing the lexical scoring contract.
6. Add candidate-ID-based RRF with the specified deterministic tie-break and provenance retention.
7. Add the fixed-corpus regression suite plus authorization, malformed-index, failure, conflict/stale, and injection tests.
8. Implement bounded semantic failure handling and lexical-only fallback; keep authorization/integrity mismatches fail-closed.
9. Add privacy-conscious telemetry and dashboards for success, fallback, latency, bounds, model/index versions, and errors.
10. Compare exact and HNSW behavior, perform controlled rollout, and retain an immediate feature-flag path back to lexical-only.

Production lexical retrieval must remain available throughout backfill, shadow evaluation, canary rollout, and rollback. None of this sequence is implemented by Task 6.

## 18. Open decisions

The following implementation choices are intentionally unresolved:

- final production embedding model and whether the measured MiniLM baseline is approved;
- immutable model artifact packaging, provenance, and upgrade/coexistence strategy;
- exact embedding runtime/library versions and hardware/resource budget;
- schema details beyond the required `EvidenceChunkEmbedding` provenance fields;
- embedding backfill order, concurrency, retry policy, and partial-population states;
- retention/deletion behavior for embeddings when evidence, workspace membership, or source versions change;
- exact-versus-HNSW activation point and HNSW parameters, including build/search settings and maintenance policy;
- query timeout thresholds, bounded retry limits, circuit-breaking, and fallback SLOs;
- long-input token limit, chunk/window size, overlap, query windowing, and window-to-parent aggregation;
- whether stored evidence chunks must be bounded to the model input policy before embedding;
- freshness metadata and final stale-evidence treatment;
- conflict-group representation and the policy guaranteeing conflict visibility within context bounds;
- exact final evidence-context chunk, token, and byte budgets;
- feature-flag shape, shadow/canary strategy, tenant/workspace rollout order, and rollback trigger;
- operational limits for model loading, embedding queues, vector queries, and concurrent requests; and
- broader workload and production-like performance validation requirements.

No item in this list should be silently decided while implementing the design.

## 19. Non-goals

This design does not implement or approve:

- LLM generation;
- autonomous questionnaire submission;
- autonomous compliance decisions;
- unrestricted RAG;
- production certification;
- automatic reviewer decisions;
- production embedding generation;
- production vector tables or migrations;
- pgvector ORM models;
- production semantic search;
- production RRF;
- API changes; or
- response-generation logic.

## 20. Validation and implementation boundary

This task changes documentation only. The required repository validation is:

```text
python -m compileall -q backend
ruff check backend
ruff format --check backend
python -m pytest -q
```

The validation should report the existing code/test state. No production code, search service, search policy, search repository, schema, migration, Docker configuration, or `.git` data is modified by this design gate.
