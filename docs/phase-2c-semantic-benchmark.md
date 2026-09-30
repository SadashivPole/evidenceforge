# Phase 2C Task 4: Real Semantic Embedding Benchmark

- **Status:** Evaluation-only benchmark completed
- **Baseline:** `4ff91c6`
- **Scope:** Local semantic retrieval comparison against the fixed 60-case corpus
- **Model:** `sentence-transformers/multi-qa-MiniLM-L6-cos-v1`
- **sentence-transformers version:** `5.7.0`

## 1. Objective

This benchmark measures local semantic retrieval against the existing fixed 60-case evaluation corpus. It compares:

```text
Lexical-only
versus
Semantic-only
```

The benchmark is evaluation-only. It does not replace production lexical retrieval, implement hybrid retrieval, implement RRF, generate answers, or make reviewer decisions.

**This benchmark measures retrieval quality, not answer accuracy.**

**Correct Abstention remains N/A because the generation layer is not implemented.**

## 2. Model

The benchmark loads this model locally on CPU:

```text
sentence-transformers/multi-qa-MiniLM-L6-cos-v1
```

The initial runtime used:

```text
sentence-transformers: 5.7.0
torch:                2.14.0+cu130
transformers:         5.17.0
embedding dimension: 384
```

The model is an experimental baseline, not a final or optimal production model. No external embedding API/provider, LLM, or second embedding model is used. Model loading is cached once per benchmark process, and model-loading failures raise a clear error rather than falling back to fake embeddings.

## 3. Query/document encoding

The semantic benchmark preserves the model's asymmetric retrieval API:

```text
question  → model.encode_query()
evidence  → model.encode_document()
```

Question text is taken from the evaluation case. Evidence text is taken from each authorized evaluation candidate's content. Both paths request normalized embeddings and validate the expected dimension and finite values.

Semantic ranking uses cosine similarity consistently. Because the model output is normalized, the benchmark's dot product is cosine similarity. Highest similarity ranks first. This is distinct from pgvector cosine distance, where lower distance ranks first.

## 4. Embedding dimensions

The benchmark requires:

```text
384 dimensions
```

The loaded model dimension is checked before evaluation. A dimension mismatch is a hard benchmark failure; it is not silently adapted.

## 5. Similarity metric

The benchmark uses normalized-vector cosine similarity:

```text
cosine similarity = normalized query vector · normalized document vector
```

The output records similarity scores for every returned candidate. Scores are ranking signals only; they are not proof of answer correctness, groundedness, citation validity, or approval.

## 6. Input-window policy

The model has a documented maximum input limit of 512 word pieces and was trained primarily on shorter inputs. The benchmark does not silently rely on arbitrary model truncation.

The explicit deterministic policy is:

```text
max_input_word_pieces = 250
```

For an input within the bound, the original text is encoded. For a longer input, the tokenizer produces a deterministic prefix window of the first 250 word pieces. The benchmark records whether the query or document was windowed and retains the original evidence candidate ID and provenance metadata. The original evidence content is never changed and no second gold label is created.

The policy is configurable through `SemanticBenchmarkConfig`. The window policy is an initial benchmark choice and should be revisited with later long-input evaluation.

## 7. Fixed corpus

The benchmark uses the existing corpus unchanged:

```text
SUPPORTED: 20
AMBIGUOUS: 10
INSUFFICIENT_EVIDENCE: 10
CONFLICTING_STALE: 10
MALICIOUS_INJECTED: 10
TOTAL: 60
```

Case IDs, categories, candidate IDs, gold relevance labels, workspace IDs, claim boundaries, rationales, and malicious content are not changed. Gold labels remain independent of semantic ranking.

## 8. Workspace isolation

For each case, semantic ranking first selects only candidates whose `workspace_id` equals the case's authorized workspace. Cross-workspace candidates never enter the semantic ranking.

The benchmark preserves the target:

```text
workspace leakage = 0 observed
```

The run reported:

```text
unauthorized candidate count: 0
cross-workspace leakage rate: 0.0
```

These leakage metrics are derived by inspecting the observed ranked candidate IDs against their candidate workspace metadata. They are not hardcoded expected-zero values. Workspace filtering remains before semantic ranking, so an unauthorized highly similar candidate cannot enter the semantic result.

Candidate metadata remains separate from embeddings and includes candidate ID, workspace ID, document ID, version ID/number, chunk index, and section label.

## 9. Lexical baseline

The existing production lexical implementation remains unchanged. Its fixed benchmark values are:

```text
Recall@5:  0.8333333333333334
nDCG@5:    0.9219567263864729
nDCG@10:   0.9219567263864729
```

These are benchmark retrieval results, not end-to-end answer accuracy.

## 10. Semantic results

The semantic-only run used:

```text
model:                  sentence-transformers/multi-qa-MiniLM-L6-cos-v1
embedding dimension:    384
query encoder:          encode_query
document encoder:       encode_document
similarity:             cosine
top-k:                  10
window policy:          deterministic prefix, max 250 word pieces
```

Measured semantic-only macro results:

```text
Recall@5:                    0.8333333333333334
nDCG@5:                      0.953101098880283
nDCG@10:                     0.953101098880283
Evidence-state match rate:  1.0
Injection content inert rate: 1.0
Injection candidates surfaced: 10
Cross-workspace leakage count: 0
Cross-workspace leakage rate: 0.0
```

The direct metric comparison is:

| Metric | Lexical-only | Semantic-only |
|---|---:|---:|
| Recall@5 | 0.8333333333333334 | 0.8333333333333334 |
| nDCG@5 | 0.9219567263864729 | 0.953101098880283 |
| nDCG@10 | 0.9219567263864729 | 0.953101098880283 |

The semantic nDCG values are higher than the lexical baseline in this run, but that observation alone does not establish that semantic retrieval is better overall. Category behavior and individual cases must be inspected before any production or hybrid decision.

## 11. Category breakdown

The semantic-only results were:

| Category | Cases | Recall@5 | nDCG@5 | nDCG@10 | Evidence-state match |
|---|---:|---:|---:|---:|---:|
| `SUPPORTED` | 20 | 1.0 | 0.9677497081190781 | 0.9677497081190781 | 1.0 |
| `AMBIGUOUS` | 10 | 1.0 | 0.9708655953922932 | 0.9708655953922932 | 1.0 |
| `INSUFFICIENT_EVIDENCE` | 10 | 0.0 | 0.8839441578296375 | 0.8839441578296375 | 1.0 |
| `CONFLICTING_STALE` | 10 | 1.0 | 0.9371313059523751 | 0.9371313059523751 | 1.0 |
| `MALICIOUS_INJECTED` | 10 | 1.0 | 0.9911661178692359 | 0.9911661178692359 | 1.0 |

Important category observations:

- **Supported:** semantic retrieval found support in the first five results, but its category nDCG was below the lexical category result in this run.
- **Ambiguous:** semantic retrieval surfaced related evidence, but related similarity must not be treated as approval or direct support.
- **Insufficient evidence:** Recall@5 remained `0.0`; the relatively high graded nDCG shows that semantic similarity can surface related context even when the requested claim is unsupported. This requires careful review rather than an accuracy claim.
- **Conflicting/stale:** current and stale/conflicting visibility remained complete at `1.0` in the fixture.
- **Malicious/injected:** instruction-like content remained ordinary evidence content; no execution, tool call, state mutation, or authorization change exists in this benchmark path. Injection candidates surfaced remained measurable at `10`.

For a malicious case, `injection_content_inert` has precise applicability semantics:

```text
no injection-like candidate surfaced
    → None; inertness was not measurable
injection-like candidate(s) surfaced and remained inert
    → 1
injection-like candidate(s) surfaced and inert handling failed
    → 0
```

The aggregate `injection_content_inert_rate` averages only non-null malicious-case measurements. The current 60-case corpus surfaces injection-like candidates and remains at `1.0`.

## 12. Runtime measurements

Development measurements from one local CPU benchmark process were:

```text
Model load time:             8.071664468 seconds
Query embedding time:        0.973717222 seconds
Document embedding time:     1.404323122 seconds
Ranking time:                 0.006712452 seconds
```

These are development measurements only. They are not representative production performance, capacity, latency, or cost benchmarks. The model was loaded once for the process and the corpus used deterministic candidate ordering with no random sampling.

## 13. Limitations

- The model is an experimental baseline and may be replaced after evaluation.
- The benchmark uses local CPU inference and does not establish production resource requirements.
- The 250-word-piece prefix policy is explicit but may lose relevant information from long evidence candidates.
- Similarity scores do not establish entailment, groundedness, citation validity, or answer correctness.
- The fixed 60-case corpus is intentionally small and should not be treated as a general workload sample.
- No answer-generation, response schema, reviewer-decision, export, or true abstention layer exists.
- No hybrid retrieval or RRF comparison has been performed.
- Semantic retrieval must continue to treat instruction-like evidence as inert content.
- Model artifacts and library versions must be recorded for future reproducibility.

## 14. Interpretation

This benchmark reports ranking behavior only. It must not be used to claim:

- semantic retrieval is better solely because macro nDCG increased;
- semantic retrieval is worse solely because one category decreased;
- overall system accuracy;
- answer accuracy;
- groundedness;
- citation correctness; or
- measurable correct abstention.

The retrieval-state metric remains separate from true end-to-end abstention:

```text
Correct Abstention:
N/A — response/generation layer not implemented
```

Any future evaluation decision must inspect supported recall, insufficient-evidence behavior, ambiguity, stale/conflicting visibility, malicious cases, and workspace isolation in addition to aggregate metrics.

## 15. Decision criteria for RRF

RRF is not implemented in this task. The next task will compare:

```text
lexical-only
semantic-only
hybrid/RRF
```

using the same corpus, labels, top-k, and category definitions. Hybrid retrieval should only be considered useful if it improves the intended retrieval behavior without unacceptable regressions in insufficient evidence, ambiguity, stale/conflicting visibility, malicious/injected handling, or workspace isolation.

An aggregate score increase is not sufficient evidence for productionizing hybrid retrieval. Any category-level regression must be investigated before a production-path decision.

## Implementation location

The evaluation-only implementation is under:

```text
backend/app/evaluation/semantic.py
backend/tests/evaluation/test_semantic_benchmark.py
```

The `sentence-transformers` dependency is declared in the backend development extras. No semantic behavior was added to production search services, policies, repositories, or APIs.
