# ADR 004: Redacted Observability and Auditability

- **Status:** Proposed
- **Date:** 2026-09-26
- **Decision owners:** EvidenceForge maintainers
- **Scope:** logs, metrics, traces, usage records, and audit events

## Context

EvidenceForge needs to explain retrieval, provider, queue, and review behavior, especially when it abstains or fails. The same data is sensitive: questionnaire text, policy excerpts, answers, document names, and provider payloads may contain PII or security details. Logging everything would improve debugging at the cost of creating a new disclosure channel.

Operational telemetry and audit history also serve different purposes. Metrics/logs are for service health; audit events record who did what to a protected object. Combining them encourages either excessive content in logs or insufficient audit detail.

## Decision

Use three deliberately separate channels:

1. **Operational logs:** structured, redacted, short-lived diagnostics with opaque identifiers and reason codes.
2. **Metrics/traces/usage records:** numeric durations, counts, sizes, state outcomes, token counts, and provider/model labels, without raw content.
3. **Audit events:** protected, access-controlled records of security-relevant actions and state transitions, with actor, workspace, object type/ID, outcome, and minimal redacted metadata.

By default, do not log raw questions, evidence, chunks, prompts, answers, provider request/response bodies, file paths, secrets, access tokens, or arbitrary exception payloads. Debug capture, if ever introduced, must be explicit, time-limited, authorized, redacted, and disabled by default.

## Events and fields

### Operational log examples

- `request_completed`: route class, status, duration bucket, workspace-scoped opaque request ID.
- `job_transitioned`: question-run opaque ID, from/to state, reason code, duration.
- `retrieval_completed`: lexical/vector candidate counts, fused count, latency, index version.
- `provider_call_completed`: provider/model identifier, input/output token counts, latency, outcome code.
- `job_failed`: component, safe error code, retryable flag, attempt number.

### Audit event examples

- workspace membership created/removed/role changed;
- evidence upload/version created, quarantined, marked superseded, or deleted;
- questionnaire import accepted/rejected;
- run started/cancelled/completed;
- question state transition;
- draft generated/abstained/rejected by validation;
- reviewer accepted, edited-and-accepted, or rejected;
- export requested/generated/downloaded;
- authorization denied;
- configuration/policy change.

Recommended fields:

- event ID and timestamp;
- actor type and server-derived actor ID or system actor;
- workspace ID;
- object type and opaque object ID;
- event type and outcome;
- previous/new state where relevant;
- policy/schema/configuration version;
- reason code; and
- redacted, bounded metadata.

Do not store the full answer, full evidence, or full prompt in an audit event. If an exact prior value is required for review, store it in a protected revision table with separate retention/access rules and reference it by ID.

## Redaction rules

- Never log credentials, tokens, cookies, database URLs, or provider headers.
- Avoid raw filenames, document titles, email addresses, question text, and answer text.
- Hashing sensitive content is not automatically safe; hashes can still enable correlation or dictionary attacks.
- Prefer enums, counts, lengths, reason codes, and opaque IDs.
- Bound exception messages and map library errors to safe codes.
- Disable provider SDK body logging and HTTP debug traces in normal operation.
- Test logs and traces with synthetic PII and known sensitive strings.

## Metrics

Track at minimum:

- ingestion count and failure rate by safe reason code;
- questionnaire import rejection count;
- queue depth, age, retries, timeouts, and dead-letter count;
- state transition counts;
- retrieval latency, candidate counts, and empty-result rate;
- schema/citation validation pass/fail counts;
- abstention and review outcome rates;
- provider latency, error rate, token usage, and estimated cost;
- p50/p95 end-to-end and component latency;
- storage size and resource pressure; and
- authorization denials and isolation-test outcomes.

Metrics must not use raw user content as labels. Cardinality is bounded.

## Retention and access

Retention values are unresolved and must be chosen with the operator and data policy. Until decided:

- keep normal operational logs short-lived;
- protect audit records more strongly than debug logs;
- restrict telemetry access to operators who need it;
- record export/download activity;
- define deletion behavior for evidence, drafts, and related telemetry; and
- ensure backups inherit the same classification and access controls.

## Consequences

### Positive

- Useful health and cost signals without normal content capture.
- Audit history supports review and incident investigation.
- Provider and parser failures can be diagnosed by reason code and timing.
- The design makes PII leakage a testable property.

### Negative

- Some production bugs will be harder to diagnose without raw payloads.
- Redaction can miss novel PII or sensitive context.
- Audit storage and access control add implementation work.
- Cost estimates may differ by provider and require explicit calculation rules.

## Alternatives considered

### Log full prompts and responses

Rejected due to high confidentiality and retention risk.

### Only collect uptime metrics

Rejected because evidence provenance, review, provider cost, and state failures require audit and structured diagnostics.

### Store immutable raw telemetry forever

Rejected because indefinite retention increases breach impact and conflicts with data minimization.

## Validation required

- Inspect all application and dependency logging configuration.
- Add automated tests asserting sensitive fixtures do not appear in logs/traces.
- Verify audit event authorization and tamper/deletion behavior.
- Measure token/cost records against provider billing or documented estimates.
- Agree retention, backup, deletion, and access policy before handling real evidence.
