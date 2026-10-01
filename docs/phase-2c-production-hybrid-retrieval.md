# Phase 2C Task 7D: Production Hybrid Retrieval with Reciprocal Rank Fusion (RRF)

- **Status:** Implementation checkpoint for production hybrid retrieval integrating lexical and semantic search with RRF
- **Scope:** Production hybrid retrieval pipeline (`HybridRetriever`) and integration into questionnaire grounding (`ground_question`)
- **Production impact:** Grounding pipeline now uses production hybrid retrieval with reciprocal rank fusion; human review and evidence citation workflows remain unchanged

---

## 1. Executive Summary and Scope Boundaries

Task 7D connects the existing lexical search stream (`app.evidence.search`) and the Task 7C semantic pgvector search stream (`app.evidence.semantic`) into a single, deterministic hybrid retrieval layer using Reciprocal Rank Fusion (RRF, $k=60$).

```text
Question
   ↓
normalize / validate
   ↓
authorized workspace boundary
   ↓
┌───────────────────────┬────────────────────────┐
│                       │                        │
↓                       ↓                        │
Lexical retrieval       Semantic retrieval       │
top 10                  top 10                   │
│                       │                        │
└──────────────┬────────┘
               ↓
        candidate union
          maximum 20
               ↓
        RRF score, k=60
               ↓
   deterministic final ordering
               ↓
         final top 10
               ↓
existing downstream consumer (ground_question)
```

### Strict Non-Goals

As mandated for Phase 2C Task 7D:

```text
HNSW production indexing is not implemented.
LLM generation is not implemented.
Automatic questionnaire answering is not implemented.
Autonomous submission is not implemented.
```

* **No LLM Generation**: Output candidates are structured, immutable evidence chunks only.
* **No Autonomous Actions**: Human review and questionnaire response workflows remain mandatory.
* **No HNSW Production Indexing**: Exact vector search (`ORDER BY embedding <=> :query_vector`) is the reference implementation.
* **No Schema Migrations**: Task 7A/7B/7C database schema is reused without alteration.
* **No Git Operations**: Git operations are prohibited.

---

## 2. Architecture and Data Flow

The hybrid retrieval subsystem consists of five cohesive modules under `app.evidence.hybrid`:

1. **`app.evidence.hybrid.config` (`HybridRetrievalConfig`)**:
   Defines frozen, approved production bounds (`lexical_top_k=10`, `semantic_top_k=10`, `rrf_k=60`, `final_top_k=10`, `max_final_top_k=10`, `search_version="hybrid-rrf-v1"`).
2. **`app.evidence.hybrid.types` (`HybridSearchResult`, `HybridRetrievalReport`)**:
   Provides immutable candidate contracts containing complete chunk provenance, RRF score, component ranks, and backward-compatible properties for downstream consumers.
3. **`app.evidence.hybrid.errors` (`HybridRetrievalError`, `HybridAuthorizationError`, etc.)**:
   Explicit error hierarchy for input validation, security violations, and stream failures.
4. **`app.evidence.hybrid.rrf` (`reciprocal_rank_score`, `fuse_hybrid_results`)**:
   Pure, side-effect-free Reciprocal Rank Fusion algorithm and deterministic 6-tier tie-breaking.
5. **`app.evidence.hybrid.service` (`HybridRetriever`)**:
   Coordinates workspace-scoped lexical search, workspace-authorized pgvector semantic search, fail-closed lexical enforcement, safe semantic fallback handling, and candidate fusion.

---

## 3. Exact RRF Formula

For each unique evidence candidate chunk $d$:

$$\text{RRF}(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$

Where:
* $k = 60$ (`DEFAULT_RRF_K`)
* $r_m(d)$ is the one-based rank ($1, 2, \dots, 10$) of candidate $d$ in stream $m \in \{\text{lexical}, \text{semantic}\}$
* Only streams in which candidate $d$ appears contribute to its score.
* If $d$ appears in lexical rank 1 and semantic rank 2:

$$\text{RRF}(d) = \frac{1}{60 + 1} + \frac{1}{60 + 2} = \frac{1}{61} + \frac{1}{62} \approx 0.016393 + 0.016129 = 0.032522$$

No score normalization, linear weighting, or arbitrary distance-score averaging is introduced.

---

## 4. Candidate Deduplication and Deterministic Ordering

### Deduplication
Fusion uses the immutable `chunk_id` (`EvidenceChunk.id`) as primary key. When a chunk appears in both lexical and semantic streams, it is unified into exactly one `HybridSearchResult` preserving:
* `candidate: SearchChunkCandidate`
* `rrf_score: float`
* `lexical_rank: int | None`
* `semantic_rank: int | None`
* `lexical_result: SearchResult | None`
* `semantic_result: SemanticSearchResult | None`

### Deterministic 6-Tier Tie-Breaking Sequence
When candidate scores or component ranks tie, ordering is strictly deterministic:
1. **Descending RRF score** (`-item.rrf_score`)
2. **Lexical-list presence** (`item.lexical_rank is None` $\to$ present comes first)
3. **Ascending lexical rank** (`item.lexical_rank` or $11$)
4. **Semantic-list presence** (`item.semantic_rank is None` $\to$ present comes first)
5. **Ascending semantic rank** (`item.semantic_rank` or $11$)
6. **Ascending stable candidate ID** (`str(item.candidate.chunk_id)`)

---

## 5. Security Invariant and Workspace Isolation

```text
Unauthorized candidates must never enter:
- RRF candidate union
- RRF scoring
- final candidate list
- downstream context
```

### Defense-in-Depth Enforcement
1. **Lexical Stream**: SQL query explicitly filters `EvidenceDocument.workspace_id == :authorized_workspace_id`.
2. **Semantic Stream**: SQL query explicitly filters `EvidenceChunkEmbedding.workspace_id == :authorized_workspace_id` AND `EvidenceDocument.workspace_id == :authorized_workspace_id`.
3. **Fusion Gate**: `fuse_hybrid_results` verifies that all candidate workspace IDs match the authorized workspace. If any candidate from another workspace is detected, `HybridWorkspaceMismatchError` is raised immediately.
4. **Fail-Closed on Security Errors**: Security violations (`HybridWorkspaceMismatchError`, `HybridAuthorizationError`, `SemanticWorkspaceMismatchError`) are **never** caught or converted into fallback results.

---

## 6. Approved Production Bounds

All retrieval stages enforce strict upper bounds:

```text
Lexical stream:      top_k <= 10
Semantic stream:     top_k <= 10
Candidate union:     <= 20
RRF constant:        rrf_k = 60 (exact)
Final fused output:  top_k <= 10
```

Any attempt to configure `lexical_top_k > 10`, `semantic_top_k > 10`, `final_top_k > 10`, or `rrf_k != 60` is explicitly rejected with a `ValueError`. No hidden unbounded queries or full-table memory loading can occur.

---

## 7. Error Handling and Fallback Policy

Hybrid retrieval enforces explicit fail-closed boundaries:

| Scenario | Behavior | Outcome |
| --- | --- | --- |
| Invalid query (empty, <2 chars, >256 chars) | Reject immediately with `HybridQueryValidationError` | Fail closed |
| Missing / invalid workspace ID | Reject immediately with `HybridAuthorizationError` | Fail closed |
| Unauthorized workspace candidate | Raise `HybridWorkspaceMismatchError` | Fail closed (security error) |
| Lexical succeeds + Semantic succeeds | Execute standard RRF fusion | Fused hybrid results |
| Lexical succeeds + Semantic fails (model down, DB error, config mismatch) | Apply safe lexical fallback (`enable_semantic_fallback=True`) | Bounded lexical-only results (`fallback_used=True`) |
| Semantic succeeds + Lexical fails | Fail closed with `HybridRetrievalError` (no semantic-only fallback) | Raise `HybridRetrievalError` |
| Both streams fail | Fail closed with `HybridRetrievalError` | Raise `HybridRetrievalError` |

---

## 8. Downstream Production Integration Boundary

The hybrid retriever is integrated at the narrowest possible boundary inside `app.questionnaires.grounding.service`:

```text
Previous Flow:
Question → lexical search_chunks() → GroundingResult(search_version="text-search-v1")

Task 7D Production Flow:
Question → HybridRetriever.search() (lexical + semantic + RRF) → GroundingResult(search_version="hybrid-rrf-v1")
```

### Result Compatibility
`HybridSearchResult` wraps `SearchChunkCandidate` and implements all properties required by `SearchResult` (`score`, `matched_terms`, `exact_phrase_match`, `occurrence_count`) and `EvidenceCitation` (`chunk_id`, `document_id`, `version_id`, `content_hash`, byte offsets, `section_label`, `page_number`).

API responses (`QuestionnaireGroundingResponse`) seamlessly serialize the fused candidate and expose `search_version = "hybrid-rrf-v1"`, `rrf_score`, `lexical_rank`, and `semantic_rank`.

---

## 9. Test Coverage Summary

Task 7D includes comprehensive unit, service, and PostgreSQL integration tests:

1. **RRF Formula and Fusion Tests (`backend/tests/test_evidence_hybrid_retrieval.py`)**:
   * Exact RRF score validation for lexical-only, semantic-only, and combined candidates.
   * Input validation for ranks and $k$ parameter.
   * Candidate deduplication when chunk appears in both streams.
   * Deterministic 6-tier tie-breaking sequence.
   * Approved production bounds enforcement (`lexical <= 10`, `semantic <= 10`, `final <= 10`).
   * Workspace isolation regression (unauthorized candidates rejected).
   * End-to-end `HybridRetriever` search execution.
   * Semantic failure fallback to lexical candidates with diagnostic reporting.
   * Lexical failure fail-closed policy (lexical failure + semantic success raises `HybridRetrievalError`).
   * Provenance reporting with `search_version == "hybrid-rrf-v1"`.
   * Rejection of invalid query and missing workspace.

2. **Service and Observability Tests (`backend/tests/evidence/test_hybrid_retriever.py`)**:
   * Initialization defaults and configuration overrides.
   * Document ID and Version ID filtering applied across both streams.
   * Telemetry reporting (`HybridRetrievalReport`).
   * Security invariant fail-closed verification.

3. **PostgreSQL Integration Tests (`backend/tests/test_evidence_hybrid_retrieval_postgres.py`)**:
   * Real PostgreSQL 16 + pgvector 0.8.6 lexical + semantic RRF fusion.
   * Workspace isolation in PostgreSQL across both retrieval streams.
   * Semantic fallback on PostgreSQL.

---

## 10. Operational Limitations

1. **Exact Vector Scan**: Semantic retrieval uses exact vector distance scanning (`<=>`); approximate indexing (HNSW) remains a future phase consideration.
2. **Offline Weights in Tests**: Test suites use deterministic fake models to avoid external model downloads during local CI.
3. **No Generation**: Downstream questionnaire answer drafting remains a human-driven or future Task capability.
