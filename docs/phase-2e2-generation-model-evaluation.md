# Phase 2E.2: Real Generation Model Evaluation Report

## Executive Summary & Core Principle

EvidenceForge Phase 2E.2 establishes an isolated, deterministic evaluation harness capable of evaluating candidate generation models against the EvidenceForge generation boundary *without* connecting any model to production questionnaire generation, *without* modifying database state, and *without* performing autonomous approvals.

```
+----------------------------------------------------------------------------------------------------+
|                                    CORE ARCHITECTURAL RULE                                         |
|                 MEASURE FIRST -> COMPARE SECOND -> INTEGRATE LAST                                  |
+----------------------------------------------------------------------------------------------------+
```

### Core Invariants Maintained
- **Zero Production Integration**: No production generation endpoints, automatic questionnaire answering, response mutation, or autonomous approval.
- **Fail-Closed Security**: Every model output must pass strictly through `GeneratedDraftPayload` $\rightarrow$ `validate_generated_draft()` $\rightarrow$ `ValidatedDraftResponse`.
- **Honest Metric Classification**: Only candidates executed in the environment are reported as measured. Unexecuted candidates are explicitly recorded as **NOT MEASURED**.
- **Human Review Authority**: All generated outputs remain transient draft proposals; human approval remains the exclusive mechanism to create approved response revisions.

---

# CURRENT IMPLEMENTATION

### 1.1 Generation Evaluation Harness Architecture

The evaluation harness operates strictly outside the production execution path:

```
+----------------------------------------------------------------------------------------------------+
|                              EVALUATION-ONLY HARNESS PIPELINE                                      |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  [GenerationTestCase (Smoke Fixture N=5)]                                                          |
|            |                                                                                       |
|            v                                                                                       |
|  [ModelGenerationInput] ---------> Sanitized Projection (No raw UUIDs, has_conflict: bool)         |
|            |                                                                                       |
|            v                                                                                       |
|  [EvaluationModelAdapter] -------> Generates untrusted raw output string (EvaluationRawOutput)      |
|            |                                                                                       |
|            v                                                                                       |
|  [normalize_and_validate_output]                                                                   |
|      ├── Step 1: JSON Parsing -------------------------> MALFORMED_JSON                            |
|      ├── Step 2: Pydantic Validation (extra="forbid") -> SCHEMA_VALIDATION_FAILURE                  |
|      ├── Step 3: validate_generated_draft()                                                        |
|      │     ├── Citation handle mismatch ---------------> CITATION_VALIDATION_FAILURE               |
|      │     ├── Stale/conflict PROPOSED violation ------> STATUS_VALIDATION_FAILURE                 |
|      │     └── ValidatedDraftResponse -----------------> SUCCESS (Zero DB Writes)                 |
|            |                                                                                       |
|            v                                                                                       |
|  [EvaluationRunArtifact] --------> Multi-run metrics, failure breakdown, human grounding items     |
+----------------------------------------------------------------------------------------------------+
```

### 1.2 Model Output Failure Classification Hierarchy

Model outputs are categorized into 6 mutually exclusive outcome types:
1. `TRANSPORT_API_FAILURE`: Network timeouts, connection dropouts, or empty HTTP responses.
2. `MALFORMED_JSON`: Non-parseable JSON syntax emitted by the model.
3. `SCHEMA_VALIDATION_FAILURE`: Invalid JSON structure, extra fields, disallowed target status, or length bound violations.
4. `CITATION_VALIDATION_FAILURE`: Emitted citation handles do not exist in authorized context or cite forbidden items.
5. `STATUS_VALIDATION_FAILURE`: Proposed `PROPOSED` on empty context or on context containing stale/conflicting sources.
6. `SUCCESS`: Clean validation into `ValidatedDraftResponse` with 0 persistence.

### 1.3 Human Grounding Review Inspection Item

For every evaluated test case, a reviewable `HumanGroundingReviewItem` is produced containing:
- `case_id`: Benchmark identifier.
- `question_text`: Questionnaire question text.
- `generated_answer`: Draft answer text proposed by the model.
- `cited_handles`: Citation handles attached by the model.
- `expected_citations`: Authoritative gold citations.
- `abstention_decision`: `ABSTAINED` vs. `PROPOSED`.
- `status`: Model proposed status.
- `validation_failure_type`: Normalization/validation outcome.
- `semantic_groundedness_review`: Explicitly tagged as `"NOT MEASURED - Requires Human Adjudication"`.
- `unsupported_claims_review`: Explicitly tagged as `"NOT MEASURED - Requires Human Adjudication"`.

---

# MEASURED

### 2.1 Candidate Models Evaluation Summary

Evaluations were executed across $N=3$ repeated runs (seeds: 42, 43, 44) over the 5-case generation smoke fixture.

| Model Candidate | Environment / Runtime | Execution Status | Runs / Evals | Schema Validity | Citation Handle Safety | Citation Precision | Citation Recall | Keyword Heuristic | Abstention Accuracy | Injection Resistance | Mean Latency (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Deterministic Evaluation Harness** (`v1.0.0`) | In-Memory Python Sandbox | **MEASURED** | 3 runs / 15 evals | $100.0\%$ | $100.0\%$ | $1.00$ | $1.00$ | $1.00$ (heuristic) | $1.00$ | $1.00$ | $< 1.0$ ms |
| **Llama-3.1-8B-Instruct** (`3.1-8b-instruct`) | vLLM / NVIDIA A10G | **NOT MEASURED** | 0 runs / 0 evals | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| **Mistral-7B-Instruct-v0.3** (`v0.3`) | vLLM / NVIDIA RTX 3090 | **NOT MEASURED** | 0 runs / 0 evals | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| **Qwen-2.5-7B-Instruct** (`2.5-7b`) | vLLM / SGLang | **NOT MEASURED** | 0 runs / 0 evals | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| **Claude-3.5-Sonnet** (`20241022`) | Bedrock / Vertex API | **NOT MEASURED** | 0 runs / 0 evals | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| **GPT-4o** (`2024-08-06`) | Azure OpenAI API | **NOT MEASURED** | 0 runs / 0 evals | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

### 2.2 Measured Execution Characteristics (Deterministic Harness)

- **Total Evaluations Executed**: 15 evaluations across 3 runs.
- **Output Schema Validity Rate**: $1.0$ ($100\%$).
- **Output Validation Failure Rate**: $0.0$ ($0\%$).
- **Failure Breakdown**: `SUCCESS: 15`, `TRANSPORT: 0`, `MALFORMED_JSON: 0`, `SCHEMA_FAILURE: 0`, `CITATION_FAILURE: 0`, `STATUS_FAILURE: 0`.
- **Citation Handle Safety Rate**: $1.0$ ($100\%$ emitted handles resolved to available, non-forbidden evidence chunks).
- **Macro Citation Precision**: $1.0$ ($TP / (TP + FP)$).
- **Macro Citation Recall**: $1.0$ ($TP / (TP + FN)$).
- **Fixture Keyword Support Heuristic Rate**: $1.0$ (string match heuristic on Supported smoke cases; NOT semantic groundedness).
- **Fixture Abstention Accuracy**: $1.0$ ($100\%$ on Ambiguous, Insufficient, Stale/Conflict, and Injected cases).
- **Fixture Injection Resistance Check**: $1.0$ (Injected instructions treated strictly as passive data).
- **Run-to-Run Consistency Rate**: $1.0$ ($100\%$ identical status outcomes across seeds).
- **Database Persistence**: Exactly $0$ rows written to `questionnaire_responses` and `questionnaire_response_revisions`.

### 2.3 Why Other Models Are Marked NOT MEASURED

1. **Llama-3.1-8B-Instruct, Mistral-7B-Instruct, Qwen-2.5-7B-Instruct**:
   - *Reason*: Self-hosted local inference requires GPU accelerators (e.g., NVIDIA A10G/RTX 4090, $\ge 24$ GB VRAM) and vLLM/TGI container runtime which are not provisioned in the current environment.
2. **Claude-3.5-Sonnet, GPT-4o**:
   - *Reason*: Managed cloud API calls require external network egress and live provider API keys, which are strictly prohibited in this phase.

---

# PROPOSED

### 3.1 Proposed Model Candidate Profiles

The following candidate profiles are specified for future evaluation when model execution environments are provisioned:

```
+----------------------------------------------------------------------------------------------------+
|                                PROPOSED MODEL CANDIDATE PROFILES                                   |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  1. Meta Llama 3.1 8B Instruct                                                                     |
|     - Architecture: Dense Transformer (8.03B params, bfloat16)                                     |
|     - Context Window: 128,000 tokens                                                               |
|     - Structured Output: Outlines / vLLM JSON grammar-guided decoding                              |
|     - Deployment Target: Self-hosted single-GPU (NVIDIA A10G 24GB)                                 |
|     - Data Privacy: Zero external data transit; 100% on-premises                                   |
|                                                                                                    |
|  2. Mistral 7B Instruct v0.3                                                                       |
|     - Architecture: Dense Transformer with Sliding Window Attention (7.25B params)                 |
|     - Context Window: 32,768 tokens                                                                |
|     - Structured Output: vLLM constrained decoding                                                 |
|     - Deployment Target: Self-hosted single-GPU (NVIDIA RTX 3090 / A10G)                            |
|     - Data Privacy: Zero external data transit; 100% on-premises                                   |
|                                                                                                    |
|  3. Qwen 2.5 7B Instruct                                                                           |
|     - Architecture: Dense Transformer (7.61B params, bfloat16)                                     |
|     - Context Window: 131,072 tokens                                                               |
|     - Structured Output: SGLang / vLLM structured decoding                                         |
|     - Deployment Target: Self-hosted single-GPU                                                    |
|     - Data Privacy: Zero external data transit; 100% on-premises                                   |
|                                                                                                    |
|  4. Anthropic Claude 3.5 Sonnet (20241022)                                                         |
|     - Architecture: Frontier Multimodal Transformer                                               |
|     - Context Window: 200,000 tokens                                                               |
|     - Structured Output: Tool Use / JSON Schema Mode                                               |
|     - Deployment Target: AWS Bedrock / GCP Vertex AI / Direct API                                  |
|     - Data Privacy: Enterprise BAA / Zero Data Retention (ZDR) agreement                           |
|                                                                                                    |
|  5. OpenAI GPT-4o (2024-08-06)                                                                     |
|     - Architecture: Frontier Omnimodal Transformer                                                |
|     - Context Window: 128,000 tokens                                                               |
|     - Structured Output: Structured Outputs (100% JSON Schema adherence guaranteed)                |
|     - Deployment Target: Azure OpenAI / Direct API                                                 |
|     - Data Privacy: Azure Private Link / Enterprise Zero Data Retention                           |
+----------------------------------------------------------------------------------------------------+
```

### 3.2 Multi-Run Evaluation Protocol for Future Executions

When model execution runtimes are provisioned, evaluations must follow this protocol:
- **Run Count ($N$)**: Minimum $N=5$ repeated runs per model.
- **Sampling Parameters**: Fixed `temperature=0.0`, fixed random seeds ($42, 43, 44, 45, 46$), fixed maximum output token budget ($1,000$ tokens).
- **Variance Reporting**: Report mean, standard deviation, minimum, and maximum across latency, citation precision, and keyword heuristic rates.
- **Human Adjudication Sampling**: $100\%$ of test cases undergo human grounding inspection using `HumanGroundingReviewItem` to measure semantic groundedness and unsupported claim rates.

---

# OPEN DECISIONS

1. **Self-Hosted Open Weights vs. Managed Cloud API**:
   - *Trade-off*: Self-hosted models (Llama 3.1 8B, Qwen 2.5 7B) guarantee complete data sovereignty and zero vendor dependency at the cost of GPU infrastructure maintenance. Cloud APIs (Claude 3.5 Sonnet, GPT-4o) offer state-of-the-art reasoning and guaranteed schema adherence via constrained decoding at the cost of external data processing agreements.
2. **Constrained Decoding Engine Selection**:
   - *Option A (vLLM with Outlines / XGrammar)*: Enforces 100% JSON schema adherence during token generation.
   - *Option B (Post-Generation Pydantic Validation with Retries)*: Compatible with all runtimes; introduces latency variance when validation fails.
3. **Generation Benchmark Expansion (5 $\rightarrow$ 60 Cases)**:
   - *Strategy*: Systematically convert the 60-case retrieval baseline corpus into full generation benchmark cases with annotated gold claim breakdowns.

---

# NOT IMPLEMENTED

The following capabilities are explicitly **NOT IMPLEMENTED** and **NOT MEASURED** in Phase 2E.2:

- **Production Questionnaire Generation**: No live questionnaire endpoints or automated response generation.
- **Automatic Persistence / Approval**: No model output can mutate the response database or bypass human review.
- **60-Case Generation Benchmark**: The full 60-case generation dataset remains future work; only the 5-case smoke fixture is active.
- **Real LLM Inference Latency / Token Throughput**: Unexecuted models have no measured latency.
- **Real LLM Semantic Groundedness & Hallucination Rates**: Actual semantic entailment and claim-level hallucinations on unexecuted models are **NOT MEASURED**.
- **External Model SDKs & API Keys**: `openai`, `anthropic`, `google-genai` are not installed or configured.
