# ADR 002: Hybrid Retrieval with Reciprocal-Rank Fusion

- **Status:** Proposed
- **Date:** 2026-09-26
- **Decision owners:** EvidenceForge maintainers
- **Scope:** Candidate retrieval for questionnaire questions

## Context

Security questionnaires use exact control names, identifiers, and negations as well as paraphrases and organization-specific terminology. Lexical search is strong for exact language; vector search is useful when question and evidence wording differ. Neither score alone establishes that an answer is supported. The MVP needs an explainable baseline that can be evaluated with PostgreSQL and pgvector.

## Decision

Use two bounded retrieval paths within an already authorized workspace:

1. PostgreSQL full-text search for lexical candidates.
2. pgvector similarity search for semantic candidates.
3. Reciprocal-rank fusion over the two ranked lists to produce a bounded candidate set.

Store component ranks and eligibility flags so a reviewer and evaluator can inspect why a candidate was included. Apply workspace scope before returning results. Apply explicit evidence-status and freshness policy, but retain enough metadata to surface conflicts rather than silently erasing them.

RRF is a ranking mechanism only. Candidate selection, generation, citation validation, and human review remain separate stages.

## Proposed flow

```text
validated question
  → normalize query without removing qualifiers
  → lexical top-k (workspace scoped)
  → vector top-k (workspace scoped)
  → remove failed/ineligible versions according to policy
  → fuse ranks with fixed RRF parameter
  → bound candidates by count and token budget
  → preserve chunk/version/provenance metadata
```

The exact k values, RRF constant, embedding model, chunk size, and filters are configuration under evaluation, not settled performance results.

## Alternatives considered

### PostgreSQL full-text only

Simple and explainable, but likely weak for paraphrase and vocabulary mismatch. Retained as one path and baseline.

### Vector search only

Useful for semantic similarity, but can miss exact identifiers, negation, and uncommon control language and may be harder to explain to reviewers. Rejected as the sole path.

### External search engine

Could provide retrieval features, but adds a service and operational boundary before the PostgreSQL baseline is measured. Deferred.

### LLM-only retrieval or reranking

Would increase cost, latency, provider dependence, and prompt-injection surface. Deferred until deterministic retrieval and evaluation establish a need.

### Weighted score blending

Requires score calibration across incompatible distributions. RRF is easier to explain and less sensitive to scale; weighted blending can be evaluated later.

## Consequences

### Positive

- Covers exact and semantic matching with one primary database.
- Keeps the ranking pipeline inspectable.
- Supports a lexical baseline and controlled ablations.
- Avoids giving the model broad search access.

### Negative

- Chunking and embeddings add ingestion cost and failure modes.
- RRF can rank related but insufficient evidence highly.
- Freshness/conflict policy can hide or over-rank important sources if poorly designed.
- PostgreSQL vector performance and index behavior require load testing.

## Security implications

Authorization predicates must be applied independently to both lexical and vector queries, not after a global result list is assembled. Every candidate carries workspace, evidence version, and chunk identity. The model receives only the bounded candidate set and cannot request more data. Embeddings may still leak information; their storage and provider boundary need the same classification controls as extracted text.

## Evaluation plan

Compare:

- lexical-only;
- vector-only; and
- fused retrieval.

Measure recall@5, nDCG, cross-workspace leakage, candidate token size, latency, and category breakdown for supported, ambiguous, insufficient, conflicting/stale, and injected cases. Evaluate both ranking quality and whether the selected candidates enable correct abstention.

## Open questions

- Which embedding model and data boundary are approved?
- What chunk size and overlap preserve citation context for PDFs?
- Should stale evidence be eligible but flagged, or excluded from answer support?
- Does RRF need a freshness-aware tie break, and can that be explained to reviewers?
- Is a reranker needed after the baseline, and what security/cost evidence would justify it?
