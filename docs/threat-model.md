# Threat Model

## Status and purpose

This is a design-time threat model for the EvidenceForge MVP. It identifies expected controls; it is not a penetration test, security certification, or claim that an implementation is production-secure. Controls must be implemented, tested, and operated before security conclusions are made.

## Security objectives

1. **Workspace confidentiality:** a user, retrieval query, citation, export, worker, or model request must not cross workspace boundaries.
2. **Evidence integrity and provenance:** a draft can be traced to an immutable evidence version and bounded excerpt.
3. **Safe handling of untrusted content:** files, question text, extracted text, and model output are data, not instructions or code.
4. **Review integrity:** only an authorized human action can move a generated draft to an accepted result.
5. **Privacy-preserving operations:** telemetry and error messages reveal the minimum necessary information.
6. **Bounded resource use:** file parsing, retrieval, model calls, retries, and exports cannot consume unbounded resources.
7. **Accountability:** security-relevant actions and state changes are auditable without copying sensitive content into logs.

## System under consideration

The MVP includes:

- a local web/API process;
- a database containing workspace records, memberships, evidence metadata, extracted chunks, vector/lexical indexes, questions, runs, drafts, citations, reviews, and audit events;
- private file storage for original evidence;
- an isolated ingestion worker;
- a bounded generation worker or job execution path;
- PostgreSQL full-text search and pgvector;
- one configured LLM provider and, if applicable, an embedding provider; and
- a local Docker deployment with operator-managed configuration.

## Assets

| Asset | Confidentiality | Integrity | Availability / audit concern |
|---|---:|---:|---:|
| Original evidence files | High | High | Deletion and version history matter |
| Extracted text and chunks | High | High | Must map to source offsets/pages |
| Embeddings and search indexes | Medium/High | High | Leakage can reveal source content |
| Questionnaire questions | High | High | May contain customer and security details |
| Draft and accepted answers | High | High | Accepted output must be review-authorized |
| Citations and provenance | High | High | Must not point across workspaces |
| Workspace memberships and roles | High | Critical | Authorization source of truth |
| Provider credentials and encryption keys | Critical | Critical | Must never enter logs or repository |
| Audit events | High | High | Need integrity and retention policy |
| Metrics and traces | Medium/High | Medium | Must avoid raw content and identifiers where possible |
| Evaluation corpus and labels | Medium/High | High | May encode sensitive or security facts |

## Trust boundaries

1. **User/browser to application:** user input is untrusted; authentication and authorization are required.
2. **Application to database/storage:** application must use scoped queries and least-privileged credentials; storage keys must not be user-controlled.
3. **Application to file parser/worker:** files are hostile input; processing must be isolated and bounded.
4. **Application to retrieval index:** query and indexed content are data; every query must apply workspace scope before ranking or return.
5. **Application to LLM/embedding provider:** provider is an external or separately trusted boundary; only the minimum bounded content may cross it, and provider retention/training settings must be approved.
6. **Model output to application:** output is untrusted; schema, status, citation, length, and policy validation are mandatory.
7. **Application to export:** exports can become a new disclosure boundary; only authorized reviewed answers may be included.
8. **Operator/host boundary:** local deployment security, Docker daemon, backups, filesystem permissions, and host compromise are outside the application's full control.

## Threats and controls

### T1 — Prompt injection in evidence or questionnaire content

**Scenario:** a PDF or question contains text such as “ignore prior instructions,” requests secrets, or attempts to trigger a tool. Extracted text is placed in a prompt and the model follows it.

**Controls:**

- label question and evidence excerpts as untrusted data in the generation contract;
- use a fixed application-owned instruction template with no model-controlled tools;
- never expose shell, network, filesystem, database-write, or submission tools to the model;
- pass only bounded retrieved excerpts, not arbitrary workspace browsing capability;
- require output schema, allowed statuses, citation IDs, and maximum lengths;
- validate citations against the server-side candidate set, not the model's claims;
- include malicious/injected cases in every evaluation run;
- treat model refusal or instruction-like content in evidence as a possible signal, not as authority.

**Residual risk:** a model can still produce a plausible unsupported answer or be influenced by adversarial text. Injection escape must be measured; prompt instructions alone are not a proof.

### T2 — Cross-workspace data leakage

**Scenario:** an ID, search query, citation, export, background job, or cached result permits a user in workspace A to see workspace B.

**Controls:**

- resolve workspace membership and role server-side for every request;
- scope every database query, full-text query, vector query, citation lookup, file download, cache key, and export by workspace ID;
- do not rely on client-supplied workspace IDs or question IDs as authorization;
- use defense-in-depth database constraints and, if appropriate, PostgreSQL row-level security;
- include workspace isolation tests for direct IDs, search, citations, jobs, failures, and exports;
- avoid shared global caches containing raw content;
- make worker payloads include an authorization context and re-check ownership before processing.

**Residual risk:** a database administrator or compromised host can bypass application authorization; this model does not eliminate host-level compromise.

### T3 — Malicious or malformed files

**Scenario:** a crafted PDF causes parser denial of service, decompression/resource exhaustion, path traversal, active content execution, or unsafe library behavior.

**Controls:**

- allowlist PDF/TXT/MD extensions and validate detected type, not only filename;
- enforce byte, page, extracted-text, and processing-time limits;
- reject archives and macros; do not execute embedded content;
- process files in a separate least-privileged worker/container with no network access where possible;
- mount input read-only and write output to a controlled temporary location;
- use parser timeouts and kill/retry boundaries;
- store originals outside the web root with generated opaque keys;
- quarantine failed files and record a safe reason;
- keep parsing dependencies patched and test malformed corpus files.

**Residual risk:** parser vulnerabilities and host/container escapes remain possible; isolation reduces blast radius but is not a complete sandbox guarantee.

### T4 — Stale, superseded, or conflicting evidence

**Scenario:** retrieval finds a relevant but outdated policy, or two sources disagree; the model states a current control as fact.

**Controls:**

- record evidence version, source, owner, effective date, review date, and superseded status;
- make freshness policy explicit and configurable before generation;
- expose freshness/conflict flags to reviewers and the output schema;
- never silently delete or hide stale evidence; label it and apply policy to eligibility/ranking;
- prefer current approved versions only when metadata supports that decision;
- abstain or request review when conflicts cannot be resolved;
- include conflict/staleness cases in evaluation.

**Residual risk:** metadata can be wrong or missing, and policy-specific freshness cannot be inferred universally.

### T5 — PII and sensitive content leakage through logs or telemetry

**Scenario:** prompts, answers, file names, excerpts, exception messages, or provider payloads enter logs, traces, metrics, crash reports, or evaluation artifacts.

**Controls:**

- prohibit raw question, evidence, answer, prompt, and provider payload logging by default;
- use opaque workspace/run/question IDs only where necessary, with access control;
- redact secrets and common PII patterns at logging boundaries, while recognizing redaction is imperfect;
- keep structured telemetry fields from free-form content;
- separate audit events from debugging logs and define retention/access policies;
- inspect provider SDK logging and disable request-body capture;
- test that representative sensitive strings do not appear in telemetry.

**Residual risk:** PII detection is incomplete; operators can still intentionally copy content into notes, and host-level access can expose process memory or files.

### T6 — Cost, token, and denial-of-service abuse

**Scenario:** large files, huge questionnaires, repeated retries, long prompts, parallel runs, or malicious users exhaust CPU, memory, database, provider quota, or storage.

**Controls:**

- bound file bytes/pages/text, question length/count, candidate count, prompt size, output length, concurrency, queue depth, retries, and run duration;
- rate-limit imports, generation, and exports per workspace/user where applicable;
- use admission control and backpressure for workers;
- estimate token/cost before a model call and enforce per-run/workspace budgets;
- make jobs idempotent and do not retry validation failures;
- cap provider timeouts and circuit-break on repeated failures;
- emit usage metrics without logging content.

**Residual risk:** provider prices, local resource pressure, and adversarial workloads may still cause availability degradation.

### T7 — Authorization and review bypass

**Scenario:** a user changes a status in a request, calls an internal endpoint, downloads an unreviewed export, or edits an accepted answer without a trace.

**Controls:**

- model state transitions as server-side commands with allowed predecessor states;
- derive actor and workspace from authenticated context;
- require reviewer role for acceptance/rejection and re-check at transition time;
- preserve immutable original drafts and append review events;
- export only results satisfying review policy;
- use CSRF protection or equivalent for browser mutations, secure cookies, and request validation as appropriate;
- test direct API calls and IDOR-style access.

**Residual risk:** an operator with database access can alter records unless database-level immutability/monitoring is added.

### T8 — Citation fabrication or overclaiming

**Scenario:** the model cites a nonexistent page, cites an unselected document, or writes claims broader than the excerpt supports.

**Controls:**

- provide stable server-generated citation IDs tied to retrieved chunks;
- reject citations not in the candidate set or not authorized for the run;
- validate source version, chunk bounds, and workspace match;
- show excerpts and metadata to the reviewer;
- require abstention/caveat fields for unsupported portions;
- measure citation precision, recall, and groundedness with independent labels.

**Residual risk:** a valid citation can still fail to support the exact wording; human review and semantic evaluation remain necessary.

### T9 — Provider or dependency compromise

**Scenario:** a provider stores content unexpectedly, a dependency is vulnerable, or a model endpoint returns malicious output.

**Controls:**

- document provider data handling and approved evidence classes;
- minimize payloads and use encrypted transport;
- pin/review dependencies and scan images where practical;
- validate all provider output as untrusted;
- keep model calls isolated from tools and application writes;
- define provider outage and deletion procedures.

**Residual risk:** a remote provider may be outside the operator's full control; no provider is assumed trustworthy by default.

## Security invariants for implementation

- No request may access an object without a server-derived workspace authorization decision.
- No citation may be persisted unless it resolves to an authorized immutable evidence version and chunk.
- No generated draft may become an accepted export without an authorized review transition.
- No model call may have shell, network, filesystem, database-write, or submission capabilities.
- No normal telemetry event may contain raw question, evidence, prompt, answer, or provider payload content.
- No unbounded file, text, model, queue, retry, or export operation is allowed.
- Evidence instructions never override application policy.

## Security limitations and out-of-scope threats

- Host operating system compromise, Docker daemon compromise, malicious administrators, and physical access are not solved by the application.
- Backups, filesystem encryption, key rotation, and incident response require deployment-specific controls.
- The MVP does not include OCR; image-based prompt injection and OCR extraction risk are deferred, not eliminated.
- Authentication strength depends on the selected deployment model and may initially be supplied by an external proxy.
- A reviewer can intentionally accept a wrong answer; the product cannot replace organizational accountability.
- Semantic groundedness is not fully decidable by schema or citation checks.
- Denial of service can be reduced but not completely prevented on a resource-constrained local host.

## Required validation before calling the system secure

- Authorization and isolation tests across all read/write/export/job paths.
- Malformed-file and parser sandbox tests.
- Injection suite and model-output validation tests.
- PII/log leakage tests, including provider SDK behavior.
- Rate, token, size, timeout, and queue-limit tests.
- Dependency/container scans and secret scanning.
- Backup, deletion, and access-control review.
- Independent review of the implementation and threat model.
