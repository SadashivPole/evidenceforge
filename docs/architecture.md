# Proposed Architecture

## Status

This is a proposed MVP architecture for a one-developer project. It is intentionally narrow and explainable. It contains no application implementation and should be revised after the discovery gates in [discovery.md](discovery.md).

## Architectural principles

1. Evidence is data, never an instruction channel.
2. Workspace authorization is applied before retrieval, citation, export, or background processing.
3. A model may draft typed content but cannot take external side effects.
4. A citation is a server-resolved reference to an immutable evidence version and addressable chunk.
5. No evidence support means explicit abstention, not a best-effort guess.
6. Human review is a state transition enforced by the server, not a cosmetic UI step.
7. Every expensive or untrusted operation is bounded.
8. Telemetry is useful without copying sensitive content.
9. One developer should be able to explain and test every major component.

## Context diagram

```text
User / reviewer
      |
      v
Web/API process -----> PostgreSQL + pgvector
      |                       |
      |                       +-- workspace/membership data
      |                       +-- evidence metadata, versions, chunks, indexes
      |                       +-- questions, runs, drafts, citations, reviews
      |                       +-- audit events and usage metadata
      |
      +-----> Private file storage (original evidence)
      |
      +-----> Ingestion worker (isolated, bounded, no model tools)
      |
      +-----> Generation worker/job path
                    |
                    +-----> one approved LLM provider
                    +-----> approved embedding provider or local embedding model
```

The user-facing application should call internal services with relative/local deployment addresses. Browser code must not depend on a hard-coded localhost address when deployed behind a preview or reverse proxy.

## Components

### Web/API process

Responsibilities:

- authenticate or consume a trusted upstream identity, depending on deployment;
- resolve workspace membership and role;
- validate requests and enforce size/rate limits;
- create import and generation jobs;
- serve authorized evidence metadata, candidates, drafts, reviews, and exports;
- perform server-side state transitions; and
- emit redacted operational telemetry and audit events.

It must not parse arbitrary files synchronously or trust client-provided workspace/role/status fields.

### PostgreSQL and pgvector

Stores relational records, full-text indexes, vector embeddings, run state, and audit metadata. Every tenant-scoped table should carry a workspace identity directly or through a constrained relation so scope is testable and hard to omit.

Use PostgreSQL full-text search for lexical retrieval and pgvector for semantic retrieval. Exact database schema, index parameters, embedding model, and PostgreSQL row-level security decision remain implementation questions.

### Private file storage

Stores original evidence objects under generated opaque keys. Files are not served from the web root. Downloads require authorization and should stream only after a server-side ownership check. A database record binds the object to a workspace, evidence item, immutable version, checksum, and ingestion status.

### Ingestion worker

Receives an authorized job reference, re-checks workspace ownership, reads a bounded private object, detects/validates type, extracts text, creates stable chunks and metadata, and updates the evidence version status. It runs in a separate least-privileged process/container with restricted filesystem and network access where practical.

MVP parsers: PDF, TXT, Markdown. Image-only PDFs and unsupported structures should be rejected or marked unsupported rather than silently OCR'd.

### Retrieval service/module

For a validated question and workspace:

1. normalize query text without deleting security-relevant qualifiers;
2. apply an authorization scope before both search paths;
3. retrieve a bounded top-k lexical list using PostgreSQL full-text search;
4. retrieve a bounded top-k vector list using pgvector;
5. filter ineligible evidence according to workspace, ingestion status, and explicit freshness policy;
6. fuse ranked results with reciprocal-rank fusion;
7. preserve source/version/chunk metadata and stable citation IDs; and
8. pass only a bounded candidate set to selection/generation.

Retrieval is not proof of support. Candidates must be inspected by the selection and citation-validation steps.

### Generation worker

Receives an authorized run/question reference and a bounded candidate set or reconstructs it under the same authorization check. It sends a fixed application-owned prompt to one configured provider. The prompt distinguishes:

- application instructions;
- the questionnaire question as untrusted data; and
- each evidence excerpt as untrusted data with citation IDs and provenance.

The provider has no tools. Its output is parsed as JSON, checked against a strict schema, checked for allowed status and lengths, and validated against server-side citations. Invalid output is retried only under a small, explicit policy; validation errors should not loop indefinitely.

### Review/export module

Shows the answer, support excerpts, freshness/conflict signals, and draft metadata to an authorized reviewer. It records accept, edit-and-accept, or reject as an append-only review event or immutable revision. Export queries only reviewed results satisfying the export policy and preserves question IDs, order, answer status, citations, and review metadata appropriate for the output format.

## Data model (logical)

The following is a logical model, not a final schema:

- **Account/User:** identity reference and lifecycle data.
- **Workspace:** authorization and data-isolation boundary.
- **Membership:** user-to-workspace role, status, and timestamps.
- **EvidenceItem:** logical document identity, title, source owner, classification.
- **EvidenceVersion:** immutable file checksum, storage key, source metadata, effective/review/superseded dates, ingestion status, extractor version.
- **EvidenceChunk:** version ID, stable ordinal, extracted text location/page/section where available, searchable text, embedding, and fingerprint.
- **QuestionnaireImport:** workspace, source file metadata, single-sheet validation result, import status.
- **Question:** import ID, stable external ID, row/order, section, text, answer metadata.
- **QuestionnaireRun:** workspace, import, policy/configuration version, state, budgets, timestamps.
- **QuestionRun:** one question's state machine, attempts, failure reason code, and terminal status.
- **RetrievalCandidate:** question run, chunk ID, lexical/vector ranks, fused score, eligibility flags.
- **Draft:** question run, schema version, raw provider response protected from ordinary logs, parsed answer, status, generation metadata.
- **Citation:** draft, chunk/version ID, claim/field reference, excerpt bounds, validation status.
- **ReviewRevision:** draft or prior revision, actor, action, edited fields, reason, timestamp.
- **AuditEvent:** actor/system, workspace, event type, object reference, outcome, redacted metadata, timestamp.
- **UsageRecord:** run/question, token counts, durations, provider/model identifiers, cost estimate, no raw content.

All records holding content or references must have a deliberate workspace scope. Foreign keys and application checks should make orphaned or cross-workspace citations impossible where practical.

## Ingestion flow

```text
upload request
  → authenticate and authorize workspace
  → validate extension, detected type, size, and limits
  → store original privately with checksum
  → create immutable EvidenceVersion + audit event
  → enqueue bounded ingestion job
  → isolated extraction
  → chunk with stable locations and extractor version
  → generate embeddings under approved data boundary
  → index lexical/vector fields
  → mark READY or UNSUPPORTED/FAILED with reason code
```

Important policy: a failed or unsupported version remains visible as a status; it must not be presented as searchable current evidence.

## Questionnaire import flow

```text
upload request
  → authorize workspace
  → validate CSV/XLSX type, size, single sheet, row/column limits
  → parse without evaluating formulas/macros
  → validate required columns and stable identifiers
  → preview errors and row mapping
  → persist validated questions and original row/order
  → audit import result
```

The importer must fail closed on ambiguous row mapping rather than silently importing shifted questions.

## Question processing flow

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

### State semantics

- `QUEUED`: a server-created job exists within the run budget.
- `VALIDATE_QUESTION`: required text/identifier/limits pass; unsafe or malformed input gets `FAILED` with a safe reason.
- `RETRIEVE_CANDIDATES`: same-workspace candidate retrieval completed under limits.
- `SELECT_EVIDENCE`: candidates are filtered/ranked under freshness and evidence policy; no supported set may lead to abstention.
- `GENERATE_TYPED_DRAFT`: one provider call produces candidate JSON, or a budget/provider error occurs.
- `VALIDATE_SCHEMA_AND_CITATIONS`: parse, length, status, workspace, citation, and support checks run server-side.
- `INSUFFICIENT_EVIDENCE`: no defensible supported draft, including approved unresolved ambiguity/conflict policy.
- `NEEDS_REVIEW`: structurally valid draft with validated citations; never an accepted output.
- `FAILED`: processing or policy failure; reason code and safe diagnostics are recorded.
- Review states require authorized server-side commands and immutable history.

## Typed output contract (conceptual)

The output should be narrow enough to validate. A conceptual shape is:

```json
{
  "status": "SUPPORTED | AMBIGUOUS | INSUFFICIENT_EVIDENCE | CONFLICTING_EVIDENCE",
  "answer": "string or null",
  "caveats": ["string"],
  "citations": [
    {
      "citation_id": "server-issued candidate id",
      "supports": "claim or answer field reference",
      "quote": "bounded excerpt or server-rendered excerpt reference"
    }
  ],
  "missing_information": ["string"]
}
```

The final schema and whether `AMBIGUOUS`/`CONFLICTING_EVIDENCE` are external statuses or reasons under `INSUFFICIENT_EVIDENCE` are unresolved. The server, not the model, decides whether a status is permissible and whether a citation resolves.

## Retrieval strategy

The MVP uses two independently inspectable retrieval paths:

- **Lexical:** PostgreSQL full-text search for exact control terms, names, identifiers, negation, and uncommon phrases.
- **Semantic:** pgvector similarity using an approved embedding model to handle paraphrase and terminology variation.
- **Fusion:** reciprocal-rank fusion, with a fixed k constant and bounded candidate lists. Scores are used for ranking, not as truth.

Potential policy adjustments include excluding failed versions, flagging superseded versions, and ranking current approved versions ahead of stale material. These adjustments must be visible in candidate metadata and evaluated so they do not hide relevant conflict evidence.

## Authorization model

The initial role set can be minimal: workspace member/analyst, reviewer, and operator. The exact mapping is unresolved. The server should derive:

- user identity from authenticated context;
- workspace membership from a membership table or trusted upstream identity;
- action permissions from server-side role policy; and
- workspace scope from the authorized object, not from a client-provided filter.

Every retrieval, citation, file, job, draft, review, and export operation repeats or inherits an authorization check that can be tested independently.

## Deployment

Docker Compose is the proposed MVP deployment shape: web/API, worker(s), PostgreSQL/pgvector, private storage volume, and optional reverse proxy. The exact image arrangement is an implementation choice. Configuration and secrets are injected at runtime; no credentials are committed.

Operational requirements include:

- bind services only as needed and protect database/storage from public exposure;
- use a local network between services;
- restrict worker network access, especially file processing;
- define volume permissions and backup/deletion policy;
- health checks and bounded shutdown behavior;
- resource limits for workers and database; and
- documented upgrade and migration process.

“Local-first” does not automatically mean air-gapped: if the LLM or embedding provider is remote, the provider boundary must be approved and disclosed.

## Failure and retry policy

- Validation failures are terminal for the current attempt and are not blindly retried.
- Provider timeouts may retry once or under a small budget, with idempotency keys where available.
- Ingestion failures preserve the version and reason code; they do not create a searchable partial version.
- Worker crashes are retried only within run and cost budgets.
- A terminal failure cannot be mistaken for `INSUFFICIENT_EVIDENCE` or `NEEDS_REVIEW`.
- Audit events must distinguish user action, system transition, provider failure, and policy rejection.

## Architecture risks

- Full-text/vector ranking may return relevant text but miss the exact support needed for a compound question.
- PDF extraction and chunk boundaries may make citations appear precise while omitting table context.
- Staleness and conflict policy could incorrectly suppress evidence or over-trust metadata.
- PostgreSQL row-level security and application predicates can drift if schema access paths expand.
- A single remote provider is a privacy, availability, and cost dependency.
- Local Docker deployments can be misconfigured, exposed, or left unpatched.
- Strict schemas do not ensure semantic groundedness.
- One-developer ownership creates a review bottleneck for security-sensitive changes.

## Implementation sequence after Phase 0

1. Confirm input, role, provider, freshness, and export contracts.
2. Build workspace/membership authorization and audit primitives.
3. Implement bounded TXT/Markdown ingestion, immutable versions, chunks, and citations.
4. Implement single-sheet questionnaire import with validation and row preservation.
5. Implement lexical retrieval and candidate display without generation.
6. Add pgvector retrieval and RRF with retrieval evaluation.
7. Add a fake provider and schema/citation validation tests.
8. Integrate one approved provider under token and cost budgets.
9. Add review transitions and export gating.
10. Run the 60-case corpus, adversarial tests, and load baselines.
