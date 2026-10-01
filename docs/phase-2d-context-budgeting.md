# Phase 2D.2: Evidence Context Budgeting & Window Selection

- **Document type:** Implementation Checkpoint & Architectural Specification
- **Status:** Context budgeting and window selection layer implemented and validated
- **Current HEAD:** `06b7753` (`feat: add evidence freshness metadata`)
- **Core rule:** `MEASURE FIRST → DECIDE SECOND → IMPLEMENT THIRD → GENERATE LAST`
- **Scope:** Creates a deterministic, bounded context-selection layer (`app.evidence.context`) consuming freshness-enriched hybrid retrieval candidates for future response generation
- **Production impact:** Zero changes to lexical search, semantic search, RRF scoring, database schemas, or API routes; LLM generation remains strictly un-implemented

---

## 1. Executive Summary & Gating Contract

Phase 2D.2 implements the evidence context budgeting and selection foundation.

```text
CURRENT IMPLEMENTATION: Downstream Context Budgeting Flow

Grounding / Hybrid Retrieval Results (Phase 2C & 2D.1)
• Up to 10 ranked candidates (HybridSearchResult)
• Exact RRF scores (k=60), component ranks, and Phase 2D.1 freshness/conflict metadata
            ↓
select_evidence_context(candidates, authorized_workspace_id)
            ↓
┌────────────────────────────────────────────────────────────────────────┐
│ Hard Upper Bounds:                                                     │
│ • max_chunks <= 5                                                      │
│ • max_tokens <= 4000 (counted under active TokenCounter)               │
│                                                                        │
│ Deterministic Algorithm:                                               │
│ 1. Iterate through candidates in exact upstream RRF rank order.        │
│ 2. Count candidate tokens via active TokenCounter.                     │
│ 3. If candidate exceeds 4000 tokens on its own:                        │
│    -> Skip candidate safely; record in diagnostics.                    │
│ 4. If current_tokens + candidate_tokens <= 4000:                       │
│    -> Select candidate; add to selected context; update tokens.        │
│ 5. If candidate exceeds remaining budget:                              │
│    -> Skip candidate; evaluate subsequent smaller candidates.          │
│ 6. Stop when 5 chunks are selected or all candidates evaluated.        │
└────────────────────────────────────────────────────────────────────────┘
            ↓
ContextSelectionResult:
• selected_candidates: tuple[SelectedEvidenceCandidate, ...] (max 5)
• total_selected_chunks: int (<= 5)
• total_input_tokens: int (<= 4000)
• truncated_candidate_count: 0 (Invariant: zero silent truncation)
• skipped_candidate_count: int (oversized + budget-excluded count)
• diagnostics: ContextSelectionDiagnostics
• selection_version: "context-selection-v1"
```

### Strict Non-Goals

```text
NOT IMPLEMENTED:
- No LLM generation or prompt drafting.
- No model API calls or provider integrations.
- No citation synthesis from model output.
- No HNSW production indexing.
- No schema migrations.
- No autonomous conflict resolution or automatic questionnaire answering.
```

---

## 2. Hard Bounds and Invariants

The context budgeting layer enforces strict, immutable upper bounds:

| Boundary Parameter | Approved Value | Enforcement Mechanism | Status |
|---|---|---|---|
| `MAX_CONTEXT_CHUNKS` | `5` | `ContextBudgetConfig.__post_init__` rejects `max_chunks > 5` with `ValueError`. Selection loop terminates when `len(selected_candidates) == max_chunks`. | `CURRENT IMPLEMENTATION` |
| `MAX_CONTEXT_TOKENS` | `4000` | Maximum counted evidence-context tokens under the active `TokenCounter`. `ContextBudgetConfig.__post_init__` rejects `max_tokens > 4000` with `ValueError`. Candidate is rejected if `total_tokens + candidate_tokens > max_tokens`. | `CURRENT IMPLEMENTATION` |
| `truncated_candidate_count` | `0` | Candidates are selected in full or skipped; evidence text is never silently sliced in half. | `CURRENT IMPLEMENTATION` |
| `selection_version` | `"context-selection-v1"` | Explicit version provenance attached to every `ContextSelectionResult`. | `CURRENT IMPLEMENTATION` |

---

## 3. Tokenizer Architecture & Truthfulness

### Explicit Status of Token Accounting

```text
Generation-model token accounting remains OPEN. Current context budgeting
uses the configured retrieval tokenizer only as an interim bounded measurement,
not as a final generation-token guarantee.
```

### Token Counting Implementations

The context selector defines a strict `TokenCounter` abstraction (`app.evidence.context.token_counter`):

1. **`ExactModelTokenCounter` (`CURRENT IMPLEMENTATION`)**:
   * Tokenizer version: `"exact-model-tokenizer-v1"`.
   * `is_exact_model_tokenizer = True`.
   * Delegates directly to an instantiated HuggingFace / SentenceTransformer tokenizer (`tokenizer.encode(text, add_special_tokens=False, truncation=False)`).
   * Used when live model weights/tokenizers are loaded in runtime environments.
2. **`HeuristicWhitespacePunctuationTokenCounter` (`CURRENT IMPLEMENTATION`)**:
   * Tokenizer version: `"heuristic-whitespace-punctuation-v1"`.
   * `is_exact_model_tokenizer = False`.
   * Regex-based whitespace/punctuation splitting (`\w+|[^\w\s]`) with sub-word heuristics for long tokens ($>12$ chars).
   * **Explicit Disclaimer**: This counter is an approximation / test double for offline testing. It does **not** run an exact WordPiece or BPE tokenizer and does **not** claim exact model token counts.

The selection diagnostics explicitly record `is_exact_model_tokenizer: bool` and `tokenizer_version: str` to prevent misrepresenting approximations as exact model tokens.

---

## 4. Deterministic Selection Policy

The selection algorithm strictly preserves upstream relevance:

1. **Order Preservation**: Candidates are evaluated in the exact order returned by hybrid RRF ranking.
2. **No Shortcut Reordering**: A shorter candidate from Rank 8 is never promoted ahead of a Rank 2 candidate simply because it uses fewer tokens.
3. **No Raw Similarity Substitution**: RRF scores and component ranks are immutable during context selection.
4. **No Implicit Freshness Re-ranking**: Freshness metadata is preserved for downstream review; the selector does not demote older versions unless they were already ranked lower by upstream retrieval.

---

## 5. Oversized Candidate Handling

When a candidate exceeds the available budget:

* **Single Candidate $> 4000$ Tokens**:
  * The candidate is **skipped safely** without crashing or throwing an uncaught exception.
  * It is recorded in `diagnostics.oversized_candidates_skipped`.
  * The selection loop continues, allowing subsequent bounded candidates to be selected.
* **Candidate Exceeding Remaining Budget**:
  * If adding a candidate would push total tokens to $4001+$, the candidate is skipped.
  * It is recorded in `diagnostics.budget_exceeded_candidates_skipped`.
  * If subsequent candidates in the ranked list are small enough to fit within the remaining budget, they are evaluated and selected.
* **Exactly 4000 Tokens**: Accepted (`total_tokens == 4000 <= 4000`).
* **4001 Tokens**: Skipped (`total_tokens == 4001 > 4000`).

---

## 6. Provenance & Freshness Preservation

Every selected candidate (`SelectedEvidenceCandidate`) retains complete, attributable metadata:

```python
@dataclass(frozen=True, slots=True)
class SelectedEvidenceCandidate:
    candidate: SearchChunkCandidate
    selection_rank: int            # 1-based order in final selected context
    token_count: int               # Token count under the active TokenCounter
    rrf_score: float | None        # Upstream RRF score
    lexical_rank: int | None       # Upstream lexical stream rank
    semantic_rank: int | None      # Upstream semantic stream rank
    workspace_id: uuid.UUID | None # Authorized workspace identity

    # Provenance Coordinates:
    # chunk_id, document_id, version_id, version_number,
    # content_hash, normalized_start_byte, normalized_end_byte,
    # section_label, page_number

    # Phase 2D.1 Freshness & Conflict Metadata:
    # document_version_number, latest_document_version_number,
    # is_latest_document_version, document_status, conflict_group_id
```

---

## 7. Security & Workspace Boundary Invariants

1. **Workspace Verification**:
   * `select_evidence_context` requires an explicit `authorized_workspace_id: uuid.UUID`.
   * If any candidate in the input list belongs to a different workspace, `ContextSelectionWorkspaceMismatchError` is raised immediately (fail closed).
2. **No Data-Bypassing Retrieval**:
   * The selector does not execute new database queries; it operates strictly on already-authorized candidates.
3. **Privacy in Telemetry**:
   * `ContextSelectionDiagnostics` records counts and bounds metrics only; raw evidence text is never written to logs or telemetry.

---

## 8. 4096-Byte vs 250-Token Issue: Evidence & Status

### Measurement Categorization

```text
MEASURED:
• Fixed 60-case benchmark corpus chunk distribution and token counts.
• All chunks in the 60-case corpus are short synthetic passages (<250 tokens),
  yielding 100.0% embedding prefix window coverage.

NOT MEASURED:
• Real enterprise PDF document corpus.
• NIST SP 800-53, SOC 2, or ISO 27001 production-like evidence collections.
• Long-tail token distribution across multi-page enterprise compliance files.
(No real-document dataset is currently stored in the repository).
```

### Architectural Hypotheses (`PROPOSED` / `OPEN DECISION`)

When enterprise multi-page documents with 4096-byte chunks are ingested in future phases, the tail tokens ($>250$ word pieces) are not encoded by the embedding model's prefix window. Two candidate options exist:

* **Option A (`PROPOSED`)**: Reduce ingestion `CHUNK_TARGET_BYTES` from 4096 to approximately 1024 bytes ($\approx 250$ tokens) so that 1 chunk $\approx$ 1 full embedding window.
* **Option B (`PROPOSED`)**: Retain 4096-byte chunks during ingestion; compute multiple overlapping 250-token window vectors per chunk and pool via `MAX(similarity)`.

### Status: `OPEN DECISION`
Neither option is approved as a production policy yet.

**Required Future Measurement**: An empirical comparison evaluating retrieval quality (nDCG@10, Recall@10), human citation usability, storage overhead, and query latency on a real long-chunk evaluation dataset must be conducted before selecting Option A or Option B.

---

## 9. Evaluation Telemetry & Reporting

Evaluated across the 60-case benchmark corpus (`test_context_budgeting_evaluation_report_across_60_cases`):

```text
================================================================================
Context Budgeting Evaluation Summary (60-Case Corpus)
================================================================================
Average Selected Chunks per Case:    1.95 chunks
Maximum Selected Chunks:             3 chunks (<= 5 hard bound)
Average Selected Tokens per Case:    42.8 tokens (heuristic token counter)
Maximum Selected Tokens:             120 tokens (<= 4000 hard bound)
Oversized Candidates Skipped:        0
Budget-Exceeded Candidates Skipped:  0
Provenance Preservation Rate:        100.0% (Zero loss)
Freshness Preservation Rate:         100.0% (Zero loss)
Truncated Candidate Rate:            0.0% (Zero silent truncation)
================================================================================
```

*(Note: These metrics measure evidence context bounds and provenance fidelity; they do not measure answer accuracy or model generation).*
