# Problem Statement

## Summary

Security and compliance teams repeatedly answer questionnaires by searching across policies, standards, procedures, control descriptions, and evidence documents. The work is slow because information is fragmented, terminology varies, document versions conflict, and each answer often needs a source reference. The risk is higher than simple inefficiency: a fluent answer that is not supported by approved evidence can create a false representation of an organization's controls.

EvidenceForge addresses the narrow drafting problem: retrieve relevant approved evidence, produce a typed draft only within the evidence boundary, attach citations, and require human review before export. It does not decide whether an organization is compliant and does not submit answers automatically.

## Users and context

### Analyst

An analyst receives a questionnaire with many questions, often in a spreadsheet, and must prepare defensible draft responses. They need to find relevant evidence quickly and distinguish “not found” from “the organization does not do this.”

### Evidence owner or reviewer

A reviewer knows the organization’s policies and decides whether a generated draft is accurate, current, and appropriate to accept. They need to see provenance, freshness, conflicts, and any caveats.

### Operator

An operator runs the local deployment, manages storage and secrets, and investigates failures. They need bounded resource use and telemetry that does not copy sensitive content into logs.

## User problems

1. **Fragmentation:** relevant facts are distributed across many documents and formats.
2. **Retrieval mismatch:** exact terms, synonyms, and control language require different search approaches.
3. **Version uncertainty:** old or superseded evidence can look relevant while being unsafe to use.
4. **Unsupported completion:** a model can fill gaps with plausible but unverified language.
5. **Traceability burden:** analysts must manually remember which source supports each sentence.
6. **Review ambiguity:** reviewers lack a consistent status model for supported, ambiguous, conflicting, and unsupported questions.
7. **Operational risk:** sensitive questionnaire and evidence content can leak through logs, providers, exports, or authorization mistakes.

## Problem boundaries

The problem is not “answer any question with AI.” It is:

> Given a question and a bounded set of approved evidence in a workspace, help a human prepare a structured, citation-backed draft, or explicitly abstain when the evidence does not justify a draft.

The system must preserve a distinction between:

- evidence that says something directly;
- evidence that supports a limited inference;
- evidence that is ambiguous or conflicting;
- evidence that is stale or superseded; and
- no supporting evidence found.

## Desired outcomes

These are intended outcomes, not measured results:

- less time spent locating candidate evidence;
- fewer unsupported assertions in draft responses;
- a consistent, reviewable citation trail;
- explicit and actionable abstention when evidence is insufficient;
- a reversible and auditable human review process; and
- predictable local operation with bounded cost and resource use.

## Functional requirements

### Evidence

- Ingest PDF, TXT, and Markdown only in the MVP.
- Record immutable source versions, provenance, ownership, dates, and ingestion status.
- Extract searchable text and create addressable chunks with stable citations.
- Reject or quarantine unsupported, malformed, oversized, or unsafe files.
- Preserve the original file privately; do not expose file paths or raw storage keys to the model.

### Questionnaire

- Import one sheet from CSV or XLSX.
- Preserve question identifier, order, section, and original row linkage.
- Validate required columns and report malformed rows without silently shifting questions.
- Export reviewed results while preserving question identity and required output fields.

### Retrieval and drafting

- Run PostgreSQL full-text and pgvector searches within the authorized workspace.
- Fuse ranked candidate lists using reciprocal-rank fusion.
- Apply freshness, source status, and evidence policy signals without treating ranking as proof.
- Give the model only bounded, labeled evidence excerpts and the question.
- Treat all question and evidence text as untrusted data, not instructions.
- Produce strict JSON containing status, answer text when allowed, caveats, and citations.
- Validate that each citation refers to retrieved evidence available to the same workspace.
- Return `INSUFFICIENT_EVIDENCE` when the configured support threshold is not met.

### Review and audit

- Put generated drafts into `NEEDS_REVIEW`.
- Support accept, edit-and-accept, and reject actions with actor, timestamp, and reason where required.
- Preserve original draft, edited answer, evidence versions, retrieval metadata, and review history.
- Prevent unreviewed drafts from being presented as accepted export output.

### Operations and security

- Enforce workspace authorization on every data access path.
- Bound file size, page/text size, questionnaire size, concurrent work, token budgets, and retries.
- Isolate file processing from the web process.
- Use private storage and secret injection rather than checked-in credentials.
- Provide redacted logs, metrics, traces, and audit events.
- Make failures explicit and retryable only when safe.

## Non-functional requirements

| Area | MVP requirement |
|---|---|
| Security | Threat model and controls cover injection, isolation, files, staleness, PII/logging, DoS, and authorization. The design must not claim production security before testing. |
| Correctness | Every answer-bearing draft has validated citations or is rejected/abstained. |
| Explainability | Retrieval candidates, cited chunks, source metadata, and state transitions are inspectable by authorized users. |
| Availability | A failed job does not corrupt questionnaire state; retries are bounded and idempotent where possible. |
| Privacy | Raw evidence, questions, and answers are excluded from normal telemetry; provider boundary is explicit. |
| Portability | Docker deployment works without Kubernetes; configuration is externalized. |
| Performance | p50/p95 latency and token/cost usage are measured against targets after a baseline exists. |
| Maintainability | Major decisions have ADRs; state and output contracts are narrow enough to test. |

## Acceptance hypotheses

A future vertical slice should demonstrate, using synthetic or sanitized data:

1. A user cannot retrieve or cite evidence from another workspace.
2. A question containing an instruction such as “ignore the system” is treated as content and cannot alter the workflow.
3. Evidence containing instructions to the model cannot grant tools, override policy, or create a citation to an un-retrieved document.
4. A question with no support produces `INSUFFICIENT_EVIDENCE` rather than a guessed answer.
5. A stale or conflicting source is visible and cannot silently become a current authoritative claim.
6. A malformed file or spreadsheet produces a bounded validation error rather than a crash or partial silent import.
7. An accepted export contains only reviewed results and preserves source references.

## Out of scope for this problem statement

Autonomous submission, legal conclusions, compliance certification, general enterprise knowledge management, OCR, crawling, integrations, SaaS tenancy/billing, SSO/SCIM, and fine-tuned models are separate problems. See [non-goals.md](non-goals.md).

## Limitations

The problem statement does not establish that the proposed workflow will improve analyst productivity or answer quality. Those claims require representative data, independent labels, security testing, and operational measurements defined in [evaluation-plan.md](evaluation-plan.md).
