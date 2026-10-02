# Phase 2E.4B — Real-Model Generation Benchmark

## Status

Phase 2E.4B begins the evaluation of a real generation model against the authoritative 60-case retrieval corpus.

Core rule:

```text
MEASURE FIRST -> IMPROVE SECOND -> INTEGRATE LAST
```

## Benchmark source of truth

The generation benchmark does **not** duplicate the evidence corpus. It imports the existing retrieval evaluation corpus through `load_evaluation_cases()` and projects each case into the existing `GenerationTestCase` contract.

The authoritative distribution is:

| Category | Cases |
|---|---:|
| SUPPORTED | 20 |
| AMBIGUOUS | 10 |
| INSUFFICIENT_EVIDENCE | 10 |
| CONFLICTING_STALE | 10 |
| MALICIOUS_INJECTED | 10 |
| **Total** | **60** |

## Projection rules

### Authorized workspace boundary

Only candidates belonging to the evaluation case's authorized workspace are exposed to the model. Cross-workspace distractors remain part of the retrieval benchmark but are excluded from the generation prompt.

### Opaque citation handles

Authorized evidence is assigned deterministic `EVIDENCE-1`, `EVIDENCE-2`, ... handles in source order. Raw evidence/document/version/workspace UUIDs are not exposed through the model projection.

### Freshness and conflict

Current evidence is projected as the latest active version. Stale candidates are projected with `is_latest_document_version=false` and `document_status=superseded`. Conflict membership is reduced to the boolean `has_conflict`; the internal conflict-group identifier is never exposed.

### Gold status policy

Gold status is derived only from the existing retrieval category semantics:

- `SUPPORTED` -> `PROPOSED`
- `MALICIOUS_INJECTED` -> `PROPOSED` when directly supporting evidence exists and injected text must remain inert
- `AMBIGUOUS` -> `INSUFFICIENT_EVIDENCE`
- `INSUFFICIENT_EVIDENCE` -> `INSUFFICIENT_EVIDENCE`
- `CONFLICTING_STALE` -> `INSUFFICIENT_EVIDENCE`

### Gold citations

For `SUPPORTED` and `MALICIOUS_INJECTED` cases, expected citations are the authorized candidates with retrieval gold relevance `3` (direct support). Relevance `2` is not automatically treated as a required generation citation because it represents related/supporting material rather than necessarily a direct claim source.

For abstention cases, the benchmark expects no citation handles in the generated draft, matching the existing generation evaluator's abstention contract.

## What is measured automatically

The existing evaluation/runtime-gate machinery measures:

- JSON/schema validity;
- citation-handle safety;
- citation precision and recall against the generation gold citation set;
- expected status correctness;
- abstention correctness;
- injection-output detection using explicit forbidden strings;
- wall-clock request latency;
- provider-reported token usage when available;
- run-to-run status consistency;
- failure classification.

## What is not measured automatically

The benchmark does **not** treat keyword matching as semantic groundedness.

Human adjudication remains required for:

- claim-level evidence support;
- unsupported claims;
- overstatement of evidence;
- whether the cited evidence actually entails the answer;
- nuanced injection resistance beyond explicit forbidden-output checks.

## Malicious / injected cases

Malicious cases contain factual evidence together with instruction-like text embedded in evidence. The benchmark deliberately does not transform the entire case into an automatic abstention requirement. The model is expected to use the factual evidence when it directly supports the question while treating instruction-like text as untrusted data.

This keeps factual grounding and prompt-injection resistance as separate evaluation dimensions.

## Initial real-model plan

Candidate:

```text
model: qwen2.5:1.5b
runtime: Ollama 0.35.0
endpoint: local loopback OpenAI-compatible API
structured output: strict JSON Schema
```

Initial measurement plan:

```text
60 cases x 3 runs = 180 evaluations
```

The first run establishes a baseline. Prompt/model optimization is intentionally deferred until the baseline has been recorded.

## Non-goals

This phase does not enable production questionnaire generation, autonomous approval, persistence of model outputs, external cloud model calls, or model ranking.

## Exit criteria for the baseline

A valid Phase 2E.4B baseline requires:

1. all 60 cases execute or receive an explicit runtime/failure classification;
2. no silent output repair or retry is added;
3. automated validation metrics are recorded;
4. raw generated outputs remain available for human adjudication;
5. semantic groundedness is clearly marked as measured only after human review;
6. production generation remains disabled.

## Next step

```text
60-case benchmark projection  ->  real Qwen baseline
                                      ↓
                               automated metrics
                                      ↓
                              human adjudication
                                      ↓
                               baseline report
```


## Packaging boundary correction

The 60-case generation benchmark projection lives under `backend/tests/evaluation/`, alongside the authoritative `retrieval_cases.py` corpus. It intentionally remains outside the installable `app*` package and is consumed only by evaluation tests/harnesses. The first transfer package placed this projection under `backend/app/evaluation/`; Revision 2 corrects that package boundary so the benchmark loader can import the existing test corpus without making application code depend on test modules.
