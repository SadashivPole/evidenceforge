# Phase 2E.1: Generation Design & Evaluation Gate

## Executive Summary & Core Principle

EvidenceForge Phase 2E.1 establishes the formal architectural design, server-to-model contracts, evaluation criteria, and failure policies for the future LLM generation layer *prior* to any provider, SDK, or model integration.

```
+----------------------------------------------------------------------------------------------------+
|                                    CORE ARCHITECTURAL RULE                                         |
|                 MEASURE FIRST -> DESIGN SECOND -> IMPLEMENT THIRD -> GENERATE LAST                 |
+----------------------------------------------------------------------------------------------------+
```

Under this gate:
- **Zero Model / Provider Calls**: No LLM SDKs, API keys, network calls, prompt executions, or answer generators are introduced.
- **Fail-Closed Security Invariant**: Untrusted model outputs are projections of data, never instructions. Any validation failure prevents persistence and approval entirely.
- **Human Review Sovereignty**: "Human approval is the only mechanism that can transition a validated draft into an approved persisted questionnaire response. ReviewAction determines the persisted human-review status. target_status cannot override the action state machine."

---

# CURRENT IMPLEMENTATION

### 1.1 Existing Domain & Generation Contracts

EvidenceForge contains a fully verified, hardened pipeline spanning retrieval through human review:

```
+----------------------------------------------------------------------------------------------------+
|                                CURRENT COMPLETED PIPELINE (PHASES 2C - 2D.4)                       |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  [Questionnaire Question]                                                                          |
|            |                                                                                       |
|            v                                                                                       |
|  [Hybrid Retrieval (2C)] ---------> Top K Chunks with RRF Rank Scores                              |
|            |                                                                                       |
|            v                                                                                       |
|  [Freshness / Conflict (2D.1)] ---> Flags stale versions (is_latest=False) & conflict groups       |
|            |                                                                                       |
|            v                                                                                       |
|  [Context Selection (2D.2)] ------> Budgets <= 5 Chunks & <= 4,000 Tokens                          |
|            |                                                                                       |
|            v                                                                                       |
|  [Generation Boundary (2D.3)] ----> Assigns Opaque Handles ("EVIDENCE-1" .. "EVIDENCE-5")          |
|                                     Builds GenerationContext (immutable server domain object)       |
|                                     Validates GeneratedDraftPayload -> ValidatedDraftResponse       |
|            |                                                                                       |
|            v                                                                                       |
|  [Human Review (2D.4)] -----------> ReviewDraftContext -> Human Reviewer -> ReviewDecision         |
|                                     Applies ACCEPT / EDIT_AND_APPROVE / REJECT                     |
|                                     Persists QuestionnaireResponseRevision + Audit Trail           |
+----------------------------------------------------------------------------------------------------+
```

#### Existing Contract Definitions:
1. **`GenerationEvidenceItem` (`app/questionnaires/generation/types.py`)**:
   Immutable server-side candidate containing citation handle (`EVIDENCE-1`..`EVIDENCE-5`), chunk UUID, document UUID, version UUID, version number, chunk index, content hash, byte offsets, section, page, content, token count, RRF scores, and freshness/conflict indicators.
2. **`GenerationContext` (`app/questionnaires/generation/types.py`)**:
   Server-authorized context holding question metadata, workspace UUID, tuple of `GenerationEvidenceItem`, token totals, and version tags.
3. **`GeneratedDraftPayload` (`app/questionnaires/generation/schemas.py`)**:
   Strict Pydantic schema for untrusted model output (`extra="forbid"`, bounded answer $\le 4,000$ chars, bounded notes $\le 1,000$ chars, status strictly restricted to `PROPOSED` or `INSUFFICIENT_EVIDENCE`, citation handles matching `^EVIDENCE-[1-9]\d*$`).
4. **`validate_generated_draft()` (`app/questionnaires/generation/validator.py`)**:
   Deterministic boundary validator enforcing citation handle existence in authorized context, server evidence guard (rejects `PROPOSED` if context has stale/conflicting sources), and non-empty citations for `PROPOSED`.
5. **`ValidatedDraftResponse` (`app/questionnaires/generation/types.py`)**:
   Server-validated draft containing resolved immutable `EvidenceCitation` records and chunk IDs.
6. **`ReviewDraftContext` & `apply_review_decision()` (`app/questionnaires/review/`)**:
   Authoritative human review workflow. Enforces that only human actions (`ACCEPT`, `APPROVE`, `EDIT_AND_APPROVE`, `REJECT`) create persistent revisions, with strict selector mutual exclusivity and audit logging.

### 1.2 Evaluation Smoke Fixture (`backend/app/evaluation/generation.py`)
A deterministic smoke fixture containing **exactly 5 test cases** (one per category: `SUPPORTED`, `AMBIGUOUS`, `INSUFFICIENT_EVIDENCE`, `CONFLICTING_STALE`, `MALICIOUS_INJECTED`) is implemented to verify the generation evaluator mechanics and projection behavior.

---

# MEASURED

### 2.1 Deterministic Security & Contract Test Measurements

The following security and contract characteristics are measured across the repository test suite:

| Dimension | Measured Value | Enforcement Mechanism |
|---|---|---|
| **Context Selection Chunk Limit** | Exactly $\le 5$ chunks | `select_evidence_context()` greedy budgeting |
| **Context Token Limit** | $\le 4,000$ tokens | Exact token accumulator stopping rule |
| **UUID Redaction in Projection** | $100\%$ ($0$ internal UUIDs leaked) | `project_generation_context_to_model_input()` |
| **Conflict Identifier Redaction** | $100\%$ ($0$ raw group IDs leaked; `has_conflict: bool` used) | `project_evidence_item()` |
| **Citation Handle Resolution** | $100\%$ deterministic | Strict map `EVIDENCE-N` $\rightarrow$ `GenerationEvidenceItem` |
| **Cross-Workspace Rejection** | $100\%$ rejected | Workspace boundary check on context and review |
| **Stale/Conflict Guard on PROPOSED** | $100\%$ rejected | `validate_generated_draft()` raises `GenerationStatusValidationError` |
| **Review State Machine Integrity** | $100\%$ deterministic | `ACCEPT`/`APPROVE`/`EDIT_AND_APPROVE` $\rightarrow$ `APPROVED`; `REJECT` $\rightarrow$ `NEEDS_REVIEW` |
| **Persistence Isolation** | $0$ DB writes on generation/validation | Persistence occurs exclusively in `apply_review_decision()` |

### 2.2 Established Phase 2C Hybrid Retrieval Benchmark Metrics

The authoritative Phase 2C hybrid retrieval benchmark measurements on the established corpus are:

- **Recall@5**: $0.8333333333333334$ ($83.33\%$)
- **nDCG@5**: $0.9616266072655447$
- **nDCG@10**: $0.9616266072655447$

### 2.3 Evaluator Smoke Fixture Verification (5 Cases)

Running the deterministic mock smoke fixture through `evaluate_benchmark_dataset()` measures the **evaluator mechanics and metric calculations on deterministic mock outputs** (this is harness verification, NOT real model performance):

- **Fixture Size**: Exactly $5$ cases ($1$ Supported, $1$ Ambiguous, $1$ Insufficient, $1$ Conflicting/Stale, $1$ Malicious/Injected).
- **Harness Schema Validity Rate**: $1.0$ ($100\%$ of compliant mock outputs validated; malformed payloads rejected).
- **Fixture Citation Handle Safety Rate (`fixture_citation_handle_safety_rate`)**: $1.0$ (all emitted handles in mock outputs resolved strictly to available, non-forbidden evidence handles).
- **Fixture Citation Precision (`fixture_citation_precision`)**: $1.0$ on deterministic smoke outputs ($TP / (TP + FP)$ matching expected citations).
- **Fixture Citation Recall (`fixture_citation_recall`)**: $1.0$ on deterministic smoke outputs ($TP / (TP + FN)$ matching expected citations).
- **Fixture Keyword Support Heuristic Rate (`fixture_keyword_support_rate`)**: $1.0$ on the single Supported smoke case (keyword string matching heuristic, NOT semantic claim-level groundedness).
- **Fixture Abstention Accuracy (`fixture_abstention_accuracy`)**: $1.0$ on the 4 abstention smoke cases.
- **Fixture Injection Resistance Check (`fixture_injection_resistance_rate`)**: $1.0$ on the single Injected smoke case.

---

# PROPOSED

### 3.1 Server-to-Model Projection Contract (Part 2)

To prevent leaking internal database topology or group identifiers, the system projects `GenerationContext` into a sanitized `ModelGenerationInput`:

```
+----------------------------------------------------------------------------------------------------+
|                         SERVER-TO-MODEL PROJECTION ARCHITECTURE                                    |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  [GenerationContext (Internal Domain)]                                                             |
|    - authorized_workspace_id: UUID  -----------------+                                            |
|    - question_id: UUID              -----------------+ (REDACTED / STRIPPED)                       |
|    - document_id / version_id: UUID -----------------+                                            |
|    - evidence_chunk_id: UUID        -----------------+                                            |
|    - conflict_group_id: str         -----------------+ (REDACTED -> has_conflict: bool)            |
|    - raw SQL / credentials          -----------------+                                            |
|                                                                                                    |
|    - question_text: str             =================> [ModelGenerationInput (Sanitized)]          |
|    - section_path: tuple[str, ...]  =================>   - question_text: str                      |
|    - evidence_context:                                   - section_path: tuple[str, ...]           |
|        * citation_handle: "EVIDENCE-1" ==============>   - evidence_items:                         |
|        * content: str               =================>       * citation_handle: "EVIDENCE-1"       |
|        * token_count: int           =================>       * content: str                        |
|        * document_name: str         =================>       * token_count: int                    |
|        * document_version_number    =================>       * document_name: str                  |
|        * is_latest_document_version =================>       * is_latest_document_version: bool    |
|        * document_status: str       =================>       * document_status: str                |
|        * conflict_group_id (hidden) ================>       * has_conflict: bool                  |
|        * section_label / page_num   =================>       * section_label / page_number         |
|                                                          - total_evidence_tokens: int              |
|                                                          - has_stale_or_conflicting_evidence: bool |
+----------------------------------------------------------------------------------------------------+
```

### 3.2 Model Output & Validation Contract (Part 3)

The future model output pipeline strictly conforms to Phase 2D.3:

```
  +-------------------------------------------------------------+
  |  Future Model Output (Raw JSON string)                      |
  +------------------------------+------------------------------+
                                 |
                                 v
  +-------------------------------------------------------------+
  |  GeneratedDraftPayload (Pydantic Schema)                    |
  |  - extra="forbid"                                           |
  |  - answer: str (<= 4,000 chars)                             |
  |  - status: PROPOSED | INSUFFICIENT_EVIDENCE                 |
  |  - citation_handles: ["EVIDENCE-1", ...] (<= 5 handles)     |
  |  - uncertainty_notes: str (<= 1,000 chars)                  |
  +------------------------------+------------------------------+
                                 |
                                 v
  +-------------------------------------------------------------+
  |  validate_generated_draft(gen_context, draft_payload)       |
  |  - Verifies handles match authorized gen_context            |
  |  - Verifies stale/conflict guard (rejects PROPOSED)         |
  |  - Resolves exact EvidenceCitation records with chunk IDs   |
  +------------------------------+------------------------------+
                                 |
                                 v
  +-------------------------------------------------------------+
  |  ValidatedDraftResponse (Domain Object)                     |
  |  - validation_passed: True                                  |
  |  - ZERO database persistence                                |
  |  - Passed directly to ReviewDraftContext                    |
  +-------------------------------------------------------------+
```

### 3.3 Proposed Candidate Evaluation Acceptance Gates (Part 4)

Candidate generation models will be evaluated against proposed acceptance gates across 15 objective dimensions (targets to be tested when real generation is introduced):

| Proposed Evaluation Dimension | Proposed Measurement Protocol | Proposed Target Threshold |
|---|---|---|
| **1. Structured JSON Reliability** | Strict conformance to `GeneratedDraftPayload` schema over $N=500$ runs | $100\%$ valid JSON; $0$ unparseable outputs |
| **2. Instruction Adherence** | Follows schema constraints, handle format, and output bounds | $\ge 99.5\%$ format adherence |
| **3. Citation Handle Correctness** | Percentage of cited handles existing in provided prompt context | $100\%$ handle precision; $0\%$ fabricated handles |
| **4. Groundedness** | Every factual assertion in `answer` is supported by cited evidence | $\ge 95.0\%$ verifiable claim support |
| **5. Unsupported Claim Rate** | Frequency of hallucinations or unsupported extrapolated claims | $\le 5.0\%$ unsupported claim rate |
| **6. Correct Abstention Rate** | Returns `INSUFFICIENT_EVIDENCE` when evidence is missing or insufficient | $\ge 98.0\%$ correct abstention |
| **7. Stale/Conflict Handling** | Returns `INSUFFICIENT_EVIDENCE` or flags conflicts when stale sources present | $\ge 98.0\%$ conflict detection/abstention |
| **8. Malicious/Injection Handling** | Treats injected prompt text as inert data; ignores override commands | $100\%$ injection containment; $0$ leaked secrets |
| **9. Answer Completeness** | Addresses all components of multi-part questionnaire questions | $\ge 90.0\%$ facet coverage on supported cases |
| **10. Latency (TTFT & Total)** | Time to First Token and total end-to-end response generation time | $p50 < 3.0$ s, $p95 < 8.0$ s |
| **11. Resource Requirements** | VRAM / compute footprint for self-hosted vs token cost for hosted API | Bounded within deployment operational budget |
| **12. Reproducibility** | Consistency of output across identical inputs at `temperature=0.0` | $\ge 95\%$ semantic equivalence |
| **13. Deployment Complexity** | Containerization, GPU drivers, cold-start latency, model artifact storage | Compatible with standard Docker runtime |
| **14. Privacy & Data Handling** | Zero data retention (ZDR), no training on customer evidence, residency | Full compliance with enterprise governance |
| **15. Failure Behavior** | Clean fail-closed behavior on truncation, rate limits, or context overflow | $100\%$ safe error propagation |

### 3.4 Groundedness & Abstention Protocols (Parts 6 & 7)

- **Groundedness Evaluation Protocol**:
  - *Automated Harness Checks*: Citation handle safety (handles exist in context, no forbidden/stale handles cited), schema conformance, true citation precision/recall ($TP/(TP+FP)$, $TP/(TP+FN)$), and fixture keyword matching heuristics.
  - *Human Claim-Level Verification*: Evaluation of whether chunk text strictly entails every claim in the generated answer, identifying extrapolation and omissions.
- **Abstention Protocol**:
  - `INSUFFICIENT_EVIDENCE`: When evidence is missing or insufficient, model must return `status=INSUFFICIENT_EVIDENCE`, `citation_handles=[]`, and explain missing evidence in `uncertainty_notes`.
  - `CONFLICTING_STALE`: When evidence items are conflicting or superseded, model must abstain with `status=INSUFFICIENT_EVIDENCE` rather than guessing.
  - `MALICIOUS_INJECTED`: Prompt injections remain passive data; instructions embedded in evidence are never executed.

### 3.5 Provider Adapter Boundary & Configuration (Parts 9 & 10)

```python
class GenerationProvider(ABC):
    """Provider-neutral abstract boundary."""
    @abstractmethod
    async def generate(self, model_input: ModelGenerationInput) -> GeneratedDraftPayload:
        """Execute generation and return untrusted structured payload."""
        ...
```

- **Configuration & Secrets**: Loaded strictly via server environment variables (`EVIDENCEFORGE_GENERATION_API_KEY`) or secret managers at runtime. Zero keys in code, prompts, frontend, database, or Git.

---

# OPEN DECISIONS

1. **Self-Hosted vs. Managed Cloud Model Execution**:
   - *Option A (Self-Hosted vLLM / Ollama)*: Complete data residency, zero third-party data transit; higher GPU/operational management.
   - *Option B (Cloud API - Bedrock / Azure OpenAI / Vertex AI)*: Zero infrastructure maintenance, enterprise SLA; recurring token costs and vendor data-processing agreements.
2. **Structured Output Enforcement Technique**:
   - *Option A (Constrained Decoding / BNF Grammar via vLLM/Outlines)*: Guarantees 100% schema validity at engine level; requires grammar-capable engine.
   - *Option B (JSON Mode / Function Calling)*: Supported by cloud providers; requires validator retry on occasional parse errors.
3. **Multi-Chunk Grounding Decomposition**:
   - *Option A (Single-Pass Joint Answer & Citation Generation)*: Lower latency/cost; model simultaneously answers and tags opaque citations.
   - *Option B (Two-Pass: Draft Generation + Post-Hoc Citation Grounding)*: Higher citation precision; increases latency and token consumption.
4. **Token Allocation Policy**: Fixed 4,000-token evidence budget vs. dynamic budgeting adjusted to underlying model context length.

---

# NOT IMPLEMENTED

The following capabilities and metrics are explicitly out of scope and **NOT IMPLEMENTED** / **NOT MEASURED** in Phase 2E.1:

- **Generation Boundary Validation Latency**: Timing microbenchmark is **NOT MEASURED** in this design phase.
- **60-Case Generation Benchmark**: The 60-case retrieval benchmark has NOT been converted into a full 60-case generation dataset. Only the 5-case smoke fixture is implemented in this phase.
- **Real LLM Groundedness / Unsupported Claim Measurements**: Actual model groundedness and hallucination rates are **NOT MEASURED**.
- **Real LLM Citation Precision & Recall**: True model citation performance on real outputs is **NOT MEASURED**.
- **Real LLM Abstention Accuracy & Injection Resistance**: Real model performance on adversarial inputs is **NOT MEASURED**.
- **Real LLM Latency & Resource Footprint**: Live model inference timing and VRAM consumption are **NOT MEASURED**.
- **External LLM Provider SDKs & API Keys**: `openai`, `anthropic`, `google-genai`, `cohere` are NOT installed or configured.
- **Live Model API Calls**: Zero network requests or model executions occur.
- **Autonomous Answering or Submission**: Zero automatic questionnaire answers or persistent mutations occur without human review.
