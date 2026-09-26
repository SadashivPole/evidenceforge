# ADR 003: Bounded Question Processing State Machine

- **Status:** Proposed
- **Date:** 2026-09-26
- **Decision owners:** EvidenceForge maintainers
- **Scope:** Per-question processing and review lifecycle

## Context

The product needs model-assisted drafting but must not become an autonomous agent with open-ended tools or side effects. Free-form orchestration makes authorization, retries, cost, auditability, and safe abstention difficult to reason about. The project brief defines a bounded sequence and review states.

## Decision

Implement each question as a server-owned state machine:

```text
QUEUED
  → VALIDATE_QUESTION
  → RETRIEVE_CANDIDATES
  → SELECT_EVIDENCE
  → GENERATE_TYPED_DRAFT
  → VALIDATE_SCHEMA_AND_CITATIONS
  → INSUFFICIENT_EVIDENCE | NEEDS_REVIEW | FAILED

NEEDS_REVIEW
  → ACCEPTED | EDITED_ACCEPTED | REJECTED
```

Transitions are explicit commands with an allowed predecessor state, actor/context, reason code, timestamp, and audit event. The model can propose typed output only; it cannot transition state, call tools, write to storage, or submit an answer.

## State rules

| State | Entry condition | Exit condition |
|---|---|---|
| `QUEUED` | Authorized run created within limits. | Worker claims job or terminal setup failure. |
| `VALIDATE_QUESTION` | Question is bound to an authorized import/run. | Valid question proceeds; invalid/oversized question fails. |
| `RETRIEVE_CANDIDATES` | Valid question and budget remain. | Candidate lists complete or bounded retrieval fails. |
| `SELECT_EVIDENCE` | Candidate set is workspace-scoped and bounded. | Supportable set selected, or abstention/conflict policy applies. |
| `GENERATE_TYPED_DRAFT` | Generation is allowed by evidence policy and budget. | Provider returns a response or bounded failure. |
| `VALIDATE_SCHEMA_AND_CITATIONS` | A response exists. | Valid cited draft becomes reviewable; invalid/unsupported output fails or abstains. |
| `INSUFFICIENT_EVIDENCE` | Support threshold is not met or approved ambiguity/conflict rule applies. | Terminal unless a new run is explicitly created. |
| `NEEDS_REVIEW` | Typed draft and citations pass server validation. | Reviewer accepts, edits and accepts, or rejects. |
| `ACCEPTED` | Authorized reviewer accepts unchanged draft. | New revision/run required to change it. |
| `EDITED_ACCEPTED` | Authorized reviewer edits and accepts. | New revision/run required to change it. |
| `REJECTED` | Authorized reviewer rejects with required reason if policy says so. | New run or explicit follow-up. |
| `FAILED` | Unrecoverable validation, infrastructure, or policy failure. | Retry only through explicit bounded retry/new attempt. |

## Consequences

### Positive

- Security and product behavior can be tested by transition.
- Review is enforceable and auditable.
- Retry and budget behavior is explicit.
- The model has no general-purpose agency.
- `INSUFFICIENT_EVIDENCE` is a first-class outcome rather than a prompt convention.

### Negative

- More persistence and state handling than a single request/response.
- Some real-world ambiguity does not fit a simple terminal state.
- Reviewer edits need careful revision semantics and export rules.
- Provider failures and partial worker progress require idempotency design.

## Output and citation policy

A draft is eligible for `NEEDS_REVIEW` only when:

- JSON parses against the current schema;
- status is allowed for the evidence outcome;
- answer/caveat/missing-information lengths are bounded;
- each citation resolves to a same-workspace candidate and immutable evidence version;
- cited excerpts are within server-known chunk bounds; and
- no policy check requires abstention or failure.

A citation's existence is not proof of semantic support. Review and evaluation remain required.

## Security implications

Client-supplied statuses, workspace IDs, roles, or review actors are never authoritative. The server derives actor and workspace, checks state and permission, then records the transition. Generation output is untrusted. No state transition may be triggered by text in evidence or by an LLM tool call because no model tools exist.

## Alternatives considered

### Free-form agent loop

Rejected because it expands tool, prompt-injection, cost, retry, and audit risk beyond the MVP.

### Synchronous API call with one status

Rejected because it hides partial failure, review, retries, and audit semantics and encourages accidental acceptance.

### Human-only workflow

Safe but does not address retrieval and drafting effort; the product's bounded model call remains useful if controls are enforced.

## Revisit triggers

Revisit if new answer types, collaborative review, asynchronous human clarification, external integrations, or automated submission are introduced. Each would need a new state and threat analysis rather than an implicit exception.
