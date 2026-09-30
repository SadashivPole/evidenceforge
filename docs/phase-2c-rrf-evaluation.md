# Phase 2C Task 5: Evaluation-Only RRF Comparison

- **Status:** Evaluation experiment completed
- **Scope:** Lexical-only versus semantic-only versus evaluation-only hybrid RRF
- **Corpus:** Fixed 60-case benchmark
- **Production impact:** No production retrieval or search behavior changed

## 1. Objective

This experiment compares three retrieval configurations against the unchanged 60-case corpus:

```text
A. lexical-only
B. semantic-only
C. lexical + semantic + Reciprocal Rank Fusion (RRF)
```

The purpose is to determine whether hybrid rank fusion improves retrieval quality enough to justify a later production implementation. RRF is implemented only under `backend/app/evaluation/`.

This document records an experiment result, not a production decision already applied to EvidenceForge.

## 2. Existing lexical baseline

The existing production lexical retrieval implementation was evaluated without modification:

```text
Recall@5:  0.8333333333333334
nDCG@5:    0.9219567263864729
nDCG@10:   0.9219567263864729
```

The lexical path, search scoring, search policy, search repository, authorization behavior, and fixed gold corpus were reused as-is.

## 3. Existing semantic baseline

The semantic-only path reused the Phase 2C Task 4 implementation:

```text
Model:              sentence-transformers/multi-qa-MiniLM-L6-cos-v1
Dimension:          384
Query encoder:      encode_query()
Document encoder:   encode_document()
Similarity:         cosine similarity
Input window:       250 word pieces
Top-k:              10
```

Semantic-only results were:

```text
Recall@5:  0.8333333333333334
nDCG@5:    0.953101098880283
nDCG@10:   0.953101098880283
```

## 4. RRF formula

For one-based component ranks, the evaluation-only fusion score is:

```text
RRF(candidate) =
    1 / (rrf_k + lexical_rank)
  + 1 / (rrf_k + semantic_rank)
```

A term is included only when the candidate appears in that component's ranked list. A candidate appearing in only one list receives only that list's contribution.

## 5. RRF configuration

The experiment used:

```text
lexical_top_k:  10
semantic_top_k: 10
rrf_k:          60
final_top_k:    10
```

Ranks start at 1. Candidate identity is the stable evaluation candidate ID, never text content.

## 6. Candidate identity and tie-breaking

Lexical and semantic results are combined by candidate ID. Fused results retain component ranks and RRF scores. Authorized workspace filtering occurs before fusion, and the fused result is checked against candidate workspace metadata.

The deterministic ordering rule is:

1. descending RRF score;
2. candidates present in the lexical list before candidates absent from it;
3. ascending lexical rank, where lower rank is higher rank quality;
4. candidates present in the semantic list before candidates absent from it;
5. ascending semantic rank, where lower rank is higher rank quality; and
6. ascending stable candidate ID.

The final list is bounded to 10 candidates.

## 7. Evaluation methodology

The same fixed corpus, gold relevance labels, workspace boundaries, top-k semantics, and metric definitions were used for all three configurations.

The corpus contains:

```text
SUPPORTED: 20
AMBIGUOUS: 10
INSUFFICIENT_EVIDENCE: 10
CONFLICTING_STALE: 10
MALICIOUS_INJECTED: 10
TOTAL: 60
```

For every configuration the experiment measured:

- Recall@5;
- nDCG@5;
- nDCG@10;
- evidence-state match rate;
- cross-workspace leakage;
- injection content inert rate;
- injection candidates surfaced;
- candidate count; and
- category breakdown.

The true abstention metric remains unavailable:

```text
Correct Abstention:
N/A — response/generation layer not implemented
```

## 8. Aggregate results

| Metric | Lexical-only | Semantic-only | Hybrid RRF |
|---|---:|---:|---:|
| Recall@5 | 0.8333333333333334 | 0.8333333333333334 | 0.8333333333333334 |
| nDCG@5 | 0.9219567263864729 | 0.953101098880283 | 0.9616266072655447 |
| nDCG@10 | 0.9219567263864729 | 0.953101098880283 | 0.9616266072655447 |
| Evidence-state match rate | 1.0 | 1.0 | 1.0 |
| Leakage count | 0 | 0 | 0 |
| Leakage rate | 0.0 | 0.0 | 0.0 |
| Injection inert rate | 1.0 | 1.0 | 1.0 |
| Injection candidates surfaced | 10 | 10 | 10 |
| Total returned candidates | 155 | 177 | 177 |
| Average candidates per case | 2.5833333333333335 | 2.95 | 2.95 |

Hybrid RRF had the highest aggregate nDCG in this run, while Recall@5 and evidence-state match remained unchanged. This result is not treated as sufficient by itself; category behavior and safety conditions are part of the decision.

## 9. Category results

### Recall and graded ranking

| Category | Config | Cases | Recall@5 | nDCG@5 | nDCG@10 | Evidence-state match |
|---|---|---:|---:|---:|---:|---:|
| `SUPPORTED` | Lexical-only | 20 | 1.0 | 0.977840637288149 | 0.977840637288149 | 1.0 |
| `SUPPORTED` | Semantic-only | 20 | 1.0 | 0.9677497081190781 | 0.9677497081190781 | 1.0 |
| `SUPPORTED` | Hybrid RRF | 20 | 1.0 | 0.985825485869144 | 0.985825485869144 | 1.0 |
| `AMBIGUOUS` | Lexical-only | 10 | 1.0 | 0.9068437728341652 | 0.9068437728341652 | 1.0 |
| `AMBIGUOUS` | Semantic-only | 10 | 1.0 | 0.9708655953922932 | 0.9708655953922932 | 1.0 |
| `AMBIGUOUS` | Hybrid RRF | 10 | 1.0 | 0.9766009861291008 | 0.9766009861291008 | 1.0 |
| `INSUFFICIENT_EVIDENCE` | Lexical-only | 10 | 0.0 | 0.775701565950816 | 0.775701565950816 | 1.0 |
| `INSUFFICIENT_EVIDENCE` | Semantic-only | 10 | 0.0 | 0.8839441578296375 | 0.8839441578296375 | 1.0 |
| `INSUFFICIENT_EVIDENCE` | Hybrid RRF | 10 | 0.0 | 0.8839441578296375 | 0.8839441578296375 | 1.0 |
| `CONFLICTING_STALE` | Lexical-only | 10 | 1.0 | 0.9629898969387739 | 0.9629898969387739 | 1.0 |
| `CONFLICTING_STALE` | Semantic-only | 10 | 1.0 | 0.9371313059523751 | 0.9371313059523751 | 1.0 |
| `CONFLICTING_STALE` | Hybrid RRF | 10 | 1.0 | 0.9842828264880937 | 0.9842828264880937 | 1.0 |
| `MALICIOUS_INJECTED` | Lexical-only | 10 | 1.0 | 0.9305238480187846 | 0.9305238480187846 | 1.0 |
| `MALICIOUS_INJECTED` | Semantic-only | 10 | 1.0 | 0.9911661178692359 | 0.9911661178692359 | 1.0 |
| `MALICIOUS_INJECTED` | Hybrid RRF | 10 | 1.0 | 0.9532807014081481 | 0.9532807014081481 | 1.0 |

### Category interpretation

- **Supported:** Hybrid RRF improved graded ranking over both individual configurations while preserving Recall@5 at `1.0`.
- **Ambiguous:** Hybrid RRF improved over lexical-only and remained below semantic-only only by a small amount; similarity remains context, not approval.
- **Insufficient evidence:** Recall@5 remained `0.0` for all configurations. Hybrid RRF matched semantic-only nDCG rather than worsening it, but the higher graded score must not be interpreted as evidence that an answer should be produced.
- **Conflicting/stale:** Hybrid RRF improved over both individual configurations and retained complete current/stale/conflicting visibility at `1.0`.
- **Malicious/injected:** All configurations remained inert with injection content inert rate `1.0`, surfaced 10 injection candidates, and did not introduce workspace leakage. Hybrid RRF's graded score was between lexical-only and semantic-only.

## 10. Safety and isolation results

The observed results for all configurations were:

```text
Cross-workspace leakage count: 0
Cross-workspace leakage rate:  0.0
Injection content inert rate:  1.0
Injection candidates surfaced: 10
Conflict/Stale complete rate:  1.0
```

Workspace leakage is derived from observed ranked candidate IDs and candidate workspace metadata. It is not a hardcoded expected-zero value. Injection inertness uses the hardened `None`/`1`/`0` semantics; the current malicious cases all surfaced injection-like candidates and measured `1`.

RRF does not execute or interpret evidence content, invoke tools, change authorization, mutate evidence, or modify questionnaire definitions.

## 11. Runtime measurements

Development measurements from one local CPU run were:

```text
Lexical evaluation:             0.009370986 seconds
Semantic evaluation:            9.493179884 seconds
RRF fusion:                     0.001904632 seconds
Total comparison:               9.508704128 seconds

Semantic model load:             7.374739831 seconds
Semantic query embedding:       0.828508238 seconds
Semantic document embedding:    1.249850650 seconds
Semantic ranking:                0.005271593 seconds
```

These are development measurements only. They are not production latency, capacity, or cost benchmarks.

## 12. Decision

### Experiment result

On this fixed 60-case corpus, hybrid RRF produced the highest aggregate nDCG and improved or preserved the measured category-level ranking behavior:

- supported Recall@5 did not regress;
- ambiguous Recall@5 and evidence-state match did not regress;
- insufficient-evidence Recall@5 remained unchanged at `0.0` and nDCG did not worsen relative to semantic-only;
- conflict/stale visibility remained complete;
- malicious/injected handling remained inert; and
- workspace leakage remained zero.

### Production boundary

RRF is **not** implemented in production and must not be treated as production-approved from this experiment alone. The result supports proceeding to a separate production hybrid-retrieval design and implementation gate, with authorization, failure fallback, freshness/conflict policy, index behavior, and larger workload evaluation still required.

The next implementation should preserve the lexical path as a fallback and re-run category-level regression tests before any production rollout.

## 13. Limitations

- The corpus contains 60 fixed cases and is not a general production workload sample.
- RRF parameters were not tuned; `rrf_k=60` is an experimental baseline.
- Runtime values are single development observations.
- No confidence interval or statistical significance is inferred.
- Candidate counts are constrained by the small fixture and do not represent production context sizes.
- Correct abstention remains unavailable because no response/generation layer exists.
- No production semantic retrieval or RRF path was added.

## 14. Next implementation recommendation

The next comparison after this experiment is the production-design review of:

```text
lexical-only
semantic-only
hybrid/RRF
```

The measured result supports a follow-up production hybrid-retrieval design review, not an immediate production code change. The next phase should specify:

1. production workspace-scoped lexical and semantic query integration;
2. semantic failure fallback to lexical retrieval;
3. freshness and conflict handling;
4. exact versus approximate vector-index validation;
5. candidate and context bounds;
6. operational model/version provenance; and
7. a production-readiness regression gate against the fixed corpus and broader workload fixtures.

No production hybrid retrieval is implemented by this evaluation task.
