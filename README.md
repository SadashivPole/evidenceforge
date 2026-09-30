# EvidenceForge

EvidenceForge is a local-first workbench for building security questionnaire workflows around approved evidence.

The project is intentionally designed around a bounded target workflow:

**approved evidence → questionnaire → grounded response → citations → human review**

The current implementation provides the evidence, questionnaire, persistence, provenance, retrieval, and evaluation foundations for that workflow. It does not yet provide an end-to-end response-generation, reviewer-decision, or export workflow.

EvidenceForge is designed to reduce the effort required to organize evidence and produce defensible questionnaire responses without turning unsupported information into confident answers.

It is not intended to be a generic chatbot, autonomous compliance authority, autonomous agent, or automatic questionnaire submitter.

---

## Current project status

At commit `aa8858c`, EvidenceForge has completed the security-boundary, evidence, questionnaire, response-persistence, review-foundation, retrieval, and evaluation slices.

The current implementation includes:

- workspace and membership security boundaries;
- bearer authentication;
- authorization and workspace isolation;
- audit events;
- evidence documents, immutable versions, and chunks;
- evidence provenance and normalization;
- deterministic questionnaire XLSX processing;
- questionnaire persistence;
- immutable questionnaire response history;
- citation persistence and provenance;
- PostgreSQL concurrency handling;
- question review/workbench foundations;
- deterministic lexical retrieval;
- Phase 2A retrieval evaluation;
- Phase 2B evaluation hardening; and
- a deterministic 60-case evaluation corpus.

The current implementation measures retrieval and evidence-state behavior. It does not yet implement a response/generation layer that produces answers, reviewer decisions, or true abstention outcomes.

---

## Current implementation

### Security and workspace boundary

The project includes:

- workspace and membership persistence;
- owner, admin, member, and viewer roles;
- database-backed opaque bearer-token authentication;
- token expiry and revocation checks;
- server-side authorization derived from authenticated workspace memberships;
- protected workspace and security-sensitive routes;
- cross-workspace isolation checks;
- IDOR protections;
- audit-event persistence.

Bearer tokens are intentionally provisioned outside the public API. A deployment-specific identity/token provisioning mechanism must create users and token records before protected routes can be used.

The public API does not currently provide self-service registration or token issuance.

---

### Evidence persistence

The evidence layer provides persistent records for:

- evidence documents;
- immutable evidence versions;
- evidence chunks;
- document/version/chunk provenance;
- normalized content metadata;
- evidence chunk offsets;
- content hashes; and
- ingestion-related persistence.

The response layer uses persisted evidence chunks as citation targets.

---

### Deterministic questionnaire processing

The questionnaire layer provides:

- deterministic XLSX questionnaire parsing;
- explicit questionnaire and question identity;
- normalized question representation;
- deterministic canonical questionnaire hashing;
- questionnaire version persistence;
- version-specific question persistence;
- import provenance; and
- import-attempt persistence.

The identity and hashing contracts intentionally avoid using raw XLSX ZIP bytes or filenames as semantic identity.

Question identity is based on deterministic normalized questionnaire content and explicit source identifiers when available.

---

### Retrieval and evaluation foundations

EvidenceForge currently provides deterministic lexical retrieval over authorized evidence candidates. Phase 2A established the retrieval baseline, and Phase 2B hardened the evaluation semantics without changing production retrieval or the 60-case corpus.

The benchmark corpus contains 60 deterministic cases:

```text
SUPPORTED: 20
AMBIGUOUS: 10
INSUFFICIENT_EVIDENCE: 10
CONFLICTING_STALE: 10
MALICIOUS_INJECTED: 10
```

The current measured results are benchmark results for retrieval and evidence-state handling, not overall product accuracy:

```text
Recall@5:                    0.8333333333333334
nDCG@5:                      0.9219567263864729
nDCG@10:                     0.9219567263864729
Evidence-state match rate:  1.0
Conflict/Stale complete rate: 1.0
Injection content inert rate: 1.0
Injection candidates surfaced: 10
Cross-workspace leakage count: 0
Cross-workspace leakage rate: 0.0
```

`evidence_state_match` measures whether the retrieved evidence state matches the expected category-specific condition. It is not answer accuracy and is not an end-to-end abstention metric.

**Correct Abstention: N/A — response/generation layer not implemented**

Injection-like content is evaluated as inert evidence. End-to-end Injection Escape is also unavailable because no generation, tool, agent, or answer layer exists.

---

## Phase 1K — Questionnaire response persistence

Phase 1K adds a persistence-backed response layer on top of immutable questionnaire versions.

The core relationship is:

```text
Workspace
   │
   └── Questionnaire
          │
          └── QuestionnaireVersion
                 │
                 └── QuestionnaireVersionQuestion
                        │
                        └── QuestionnaireResponse
                               │
                               └── QuestionnaireResponseRevision
                                      │
                                      └── QuestionnaireResponseCitation
                                             │
                                             └── EvidenceChunk
```

### Response model

Each questionnaire-version question can have one logical response within a workspace.

Responses support:

- immutable revision history;
- deterministic revision numbering;
- idempotent identical updates;
- workspace isolation;
- typed response statuses;
- multiple evidence citations;
- citation ordering;
- server-resolved evidence provenance;
- transactional persistence; and
- PostgreSQL-safe concurrent revision allocation.

Imports do not automatically create responses.

A response is created only through an explicit response mutation.

---

## Response statuses

The response status vocabulary is:

```text
PROPOSED
APPROVED
NEEDS_REVIEW
INSUFFICIENT_EVIDENCE
STALE_SOURCE
CONFLICTING_SOURCES
NOT_APPLICABLE
DO_NOT_DISCLOSE
```

Status-specific validation is enforced by the response service.

Examples:

- `APPROVED` requires at least one citation.
- `CONFLICTING_SOURCES` requires at least two citations.
- `INSUFFICIENT_EVIDENCE` may have no answer text.
- `NOT_APPLICABLE` may have no answer text.
- `DO_NOT_DISCLOSE` may have no answer text.
- duplicate citation targets are rejected.

---

## Response lifecycle

A response mutation follows a transactional workflow:

```text
authenticate
    ↓
authorize workspace role
    ↓
resolve questionnaire-version question
    ↓
resolve evidence citation targets
    ↓
lock existing response when present
    ↓
detect identical update
    ↓
allocate revision number
    ↓
persist immutable revision
    ↓
persist citations
    ↓
write audit event
    ↓
commit
```

Identical updates do not create unnecessary new revisions.

Changed responses create a new immutable revision.

Failed mutations are rolled back rather than partially committed.

---

## Authorization model

Response writes require one of:

```text
OWNER
ADMIN
MEMBER
```

`VIEWER` is read-only.

`APPROVED` responses additionally require:

```text
OWNER
ADMIN
```

The API verifies workspace membership and role before calling the response persistence layer.

The response persistence service also requires an explicit workspace role for mutation operations.

---

## Evidence citations

Response citations are resolved from the persisted evidence hierarchy:

```text
EvidenceDocument
      │
      └── EvidenceDocumentVersion
              │
              └── EvidenceChunk
```

Each response citation can retain provenance information including:

- evidence document identifier;
- evidence version identifier;
- evidence chunk identifier;
- evidence version number;
- evidence chunk index;
- content hash;
- normalized start offset;
- normalized end offset;
- optional section label; and
- optional page number.

Client callers provide evidence chunk identifiers.

The response service resolves the persisted evidence metadata before creating citation records.

Cross-workspace evidence references and invalid evidence references are rejected.

---

## API

The questionnaire-response API is workspace-scoped.

### Create or revise one response

```text
PUT
/workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{questionnaire_version_id}/questions/{questionnaire_version_question_id}/response
```

### Read one response

```text
GET
/workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{questionnaire_version_id}/questions/{questionnaire_version_question_id}/response
```

### Read response history

```text
GET
/workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{questionnaire_version_id}/questions/{questionnaire_version_question_id}/response/history
```

### List latest responses for a questionnaire version

```text
GET
/workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{questionnaire_version_id}/responses
```

The API returns persisted revision information together with server-resolved citation metadata.

---

## API error handling

The response API distinguishes common failure classes.

Typical responses include:

```text
403
Authorization failure

404
Workspace, questionnaire, questionnaire version,
question, response, or related resource not found

409
Persistence conflict or integrity conflict

422
Validation failure or invalid evidence citation

500
Unexpected server-side failure
```

Error responses should not expose sensitive evidence content or database internals.

---

## Database and migrations

EvidenceForge uses PostgreSQL with SQLAlchemy and Alembic.

The current migration chain is:

```text
0001_initial
    ↓
0002_security_boundary
    ↓
0003_evidence_foundation
    ↓
0004_evidence_persistence
    ↓
0005_questionnaire_persistence
    ↓
0006_questionnaire_responses
```

The current migration head is:

```text
0006_questionnaire_responses
```

### Apply migrations

The supported application path is through the API container:

```powershell
docker compose exec api python -m alembic upgrade head
```

### Check current migration

```powershell
docker compose exec api python -m alembic current
```

The PostgreSQL service is internal to the Docker Compose network and is not currently published as a host port.

---

## Database integrity

The response schema uses database constraints to enforce important invariants.

Examples include:

- one logical response per workspace, questionnaire version, and questionnaire-version question;
- unique workspace-scoped response identifiers;
- unique revision numbers per response;
- positive revision numbers;
- valid response status values;
- unique citation order per revision;
- unique evidence chunk per revision;
- positive citation ordering;
- workspace-scoped foreign-key relationships;
- restrictive evidence-reference deletion behavior.

These database constraints complement application-level validation.

The database is authoritative for transaction ordering and concurrency-sensitive uniqueness.

---

## PostgreSQL concurrency

The response persistence layer includes explicit concurrency handling.

Concurrent writes to the same logical questionnaire response are tested using PostgreSQL transactions and row locking.

The implementation handles:

- concurrent creation of the same logical response;
- deterministic revision allocation;
- unique logical response enforcement;
- transaction conflicts;
- retry after relevant integrity conflicts.

The PostgreSQL response concurrency tests are included in the automated validation suite.

---

## Validation status

The current repository has been validated with:

```text
302 tests passed
8 tests skipped
1 warning
```

Additional validation performed during the questionnaire response work includes:

- PostgreSQL response concurrency tests;
- workspace isolation tests;
- authorization tests;
- response lifecycle tests;
- citation provenance tests;
- rollback tests;
- SQLite foreign-key enforcement;
- Ruff checks;
- Python compile checks;
- Git whitespace checks;
- clean PostgreSQL migration from `0001` through `0006`;
- successful downgrade from `0006` to `0005`;
- successful upgrade from `0005` back to `0006`.

The current test environment reports one non-fatal Starlette/AnyIO deprecation warning from the installed dependency stack.

No GitHub Actions result is asserted here for commit `aa8858c`; the counts above are the locally validated repository state.

Validation results describe the tested repository state and should not be interpreted as a production-security certification.

---

## Local development

### Prerequisites

- Python 3.11+
- Node.js 20+
- npm
- Docker Desktop
- Docker Compose

---

## Python development setup

From the repository root:

```powershell
python -m pip install -e "backend[dev]"
```

---

## Environment configuration

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Configure required environment variables in `.env`.

Do not commit real credentials, access tokens, API keys, or other secrets.

---

## Start the local environment

```powershell
docker compose up --build -d
```

The API is expected at:

```text
http://localhost:8000
```

Health endpoints:

```text
/health/live
/health/ready
```

The frontend is expected at:

```text
http://localhost:3000
```

The API container is configured to apply Alembic migrations before starting the application.

---

## Run the test suite

From the repository root:

```powershell
python -m pytest -q
```

For the current validated repository state:

```text
302 passed
8 skipped
1 warning
```

---

## Run targeted questionnaire response tests

```powershell
python -m pytest -q backend/tests/questionnaires/test_questionnaire_responses.py
```

```powershell
python -m pytest -q backend/tests/questionnaires/test_questionnaire_responses_acceptance.py
```

PostgreSQL integration tests:

```powershell
python -m pytest -q backend/tests/questionnaires/test_questionnaire_responses_postgres.py
```

---

## Run Ruff

```powershell
ruff check backend/app backend/tests backend/migrations
```

---

## Check Ruff formatting

```powershell
ruff format --check backend/app backend/tests backend/migrations
```

---

## Run mypy

```powershell
mypy backend/app
```

---

## Run Python compile checks

```powershell
python -m compileall -q backend
```

---

## Check Git whitespace

```powershell
git diff --check
```

Line-ending conversion warnings from Git on Windows are not whitespace errors.

---

## PostgreSQL integration testing

The PostgreSQL response concurrency tests use a dedicated disposable PostgreSQL test database.

Example test database URL:

```powershell
$env:EVIDENCEFORGE_TEST_DATABASE_URL = "postgresql+psycopg://evidenceforge:EvidenceForgeTest123!@localhost:55432/evidenceforge_test"
```

Start the dedicated PostgreSQL test container when required:

```powershell
docker start evidenceforge-postgres-test
```

Run the PostgreSQL response tests:

```powershell
python -m pytest -q backend/tests/questionnaires/test_questionnaire_responses_postgres.py
```

The dedicated PostgreSQL test database is separate from the normal application database.

Any destructive test-database operation must be limited to that dedicated disposable test database.

---

## Frontend checks

From the frontend directory:

```powershell
cd frontend
npm ci
npm run lint
npm run typecheck
npm run format:check
```

Return to the repository root with:

```powershell
cd ..
```

---

## Repository structure

```text
EvidenceForge/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   └── schemas.py
│   │   ├── questionnaires/
│   │   │   ├── persistence/
│   │   │   ├── responses/
│   │   │   └── ...
│   │   ├── db.py
│   │   └── main.py
│   ├── migrations/
│   │   └── versions/
│   ├── tests/
│   │   └── questionnaires/
│   ├── alembic.ini
│   └── pyproject.toml
├── docs/
├── frontend/
├── compose.yaml
├── .env.example
└── README.md
```

---

## Problem

Security and compliance teams repeatedly answer questionnaires using information distributed across approved policies, controls, procedures, and evidence documents.

A useful workbench should reduce search and drafting effort without converting unsupported information into confident answers.

When the available evidence is insufficient, the system should represent that condition explicitly rather than guessing.

---

## Product boundary

EvidenceForge is:

- a local-first workspace and account model;
- a security evidence persistence system;
- a deterministic questionnaire import and persistence system;
- a questionnaire response and revision system;
- a provenance-aware citation system;
- question review/workbench foundations;
- a deterministic lexical retrieval and evaluation system; and
- a foundation for future grounded drafting and human review workflows.

EvidenceForge is not:

- a generic chatbot;
- a legal, compliance, or certification authority;
- an autonomous questionnaire submitter;
- a replacement for a GRC platform;
- an unrestricted autonomous agent;
- a system that certifies that an organization is compliant.

---

## Design principles

The project prioritizes:

```text
Correctness
    ↓
Security
    ↓
Determinism
    ↓
Database integrity
    ↓
Concurrency safety
    ↓
Reproducibility
    ↓
Developer experience
```

Feature velocity is intentionally secondary to correctness and auditability.

---

## Current architectural boundaries

The current implementation intentionally avoids treating future capabilities as implemented. The following are not yet available as an end-to-end workflow:

- semantic/vector retrieval;
- pgvector integration;
- reciprocal-rank fusion (RRF);
- bounded LLM generation;
- generation schema validation;
- semantic citation or grounding validation;
- true end-to-end correct-abstention measurement;
- end-to-end injection-escape measurement;
- final human-reviewed export workflow;
- hidden LLM calls;
- unrestricted RAG;
- automatic questionnaire submission;
- autonomous tool execution;
- autonomous network access;
- automatic changes to evidence; and
- automatic changes to questionnaire definitions.

The current response layer is focused on:

```text
persistence
    +
provenance
    +
integrity
    +
authorization
    +
revision history
```

The current retrieval layer is deterministic and lexical. It is evaluated as retrieval and evidence-state behavior, not as generated-answer quality or end-to-end reviewer behavior.

---

## Future grounded workflow

The following is a future target workflow, not a claim about the current implementation:

A later end-to-end workflow may evolve toward:

```text
Approved Evidence
        ↓
Questionnaire Import
        ↓
Question Validation
        ↓
Candidate Evidence Retrieval
        ↓
Evidence Selection
        ↓
Grounded Draft
        ↓
Schema + Citation Validation
        ↓
Human Review
        ↓
Accepted / Edited / Rejected
        ↓
Export
```

Any future answer-generation capability should remain bounded by evidence, schema validation, citation requirements, and human review.

---

## Documentation

Existing design documentation includes:

| Document | Purpose |
|---|---|
| `docs/discovery.md` | Discovery findings, assumptions, and open questions |
| `docs/problem-statement.md` | Problem framing, users, jobs, and requirements |
| `docs/non-goals.md` | Explicit product and technical boundaries |
| `docs/threat-model.md` | Assets, trust boundaries, threats, and controls |
| `docs/evaluation-plan.md` | Evaluation corpus, metrics, and test methodology |
| `docs/architecture.md` | Architecture, workflows, data model, and deployment |
| `docs/discovery-interview-template.md` | Interview and evidence-gathering template |
| `docs/adr/001-local-first.md` | Local-first deployment decision |
| `docs/adr/002-retrieval-strategy.md` | Retrieval strategy decision |
| `docs/adr/003-question-processing-state-machine.md` | Question-processing workflow decision |
| `docs/adr/004-observability.md` | Observability and auditability decision |

Some documentation describes proposed or future capabilities.

Documentation describing future capabilities must not be interpreted as proof that those capabilities are already implemented.

---

## Status vocabulary

Technical response statuses currently include:

```text
PROPOSED
APPROVED
NEEDS_REVIEW
INSUFFICIENT_EVIDENCE
STALE_SOURCE
CONFLICTING_SOURCES
NOT_APPLICABLE
DO_NOT_DISCLOSE
```

These represent response-level states and should not be confused with future workflow-level review outcomes.

Future review workflows may introduce additional concepts such as:

```text
ACCEPTED
EDITED_ACCEPTED
REJECTED
FAILED
```

Those concepts should only be treated as implemented once the corresponding workflow is present in the repository.

---

## Explicitly deferred work

The following areas are outside the current implemented slice:

- semantic/vector retrieval;
- pgvector integration;
- reciprocal-rank fusion (RRF);
- bounded LLM generation;
- generation schema validation;
- semantic citation or grounding validation;
- true end-to-end correct-abstention measurement;
- end-to-end injection-escape measurement;
- final human-reviewed export workflow;
- unrestricted web crawling;
- Slack integrations;
- Jira integrations;
- MCP-based autonomous tooling;
- multi-provider LLM routing;
- SaaS billing;
- automatic questionnaire submission;
- enterprise SSO/SCIM;
- Kubernetes deployment;
- model fine-tuning; and
- autonomous provisioning.

The product and technical boundaries are documented in:

```text
docs/non-goals.md
docs/threat-model.md
docs/architecture.md
```

---

## Next phase

The immediate next implementation phase is:

**Phase 2C — Hybrid Retrieval**

The intended comparison is:

```text
lexical-only
semantic-only
lexical + semantic + RRF
```

The purpose is to measure whether semantic retrieval actually improves the 60-case benchmark before productionizing fusion. Phase 2C is planned work; semantic retrieval, pgvector, and RRF are not implemented by this documentation update.

The next implementation areas should be introduced incrementally and validated independently rather than combining retrieval, generation, integrations, and automation into a single large change.

---

## Known limitations

Current known limitations include:

- one non-fatal Starlette/AnyIO deprecation warning in the installed test dependency stack;
- PostgreSQL integration tests require the dedicated PostgreSQL test environment;
- response persistence relies on the trusted API/service boundary for the explicit workspace role passed into mutation operations;
- the evidence document/version/chunk foreign-key structure could be hardened further with stronger cross-table database-level evidence-chain constraints in a future phase;
- semantic/vector retrieval, pgvector, and reciprocal-rank fusion are not implemented;
- bounded LLM generation, generation schema validation, and semantic citation/grounding validation are not implemented;
- true end-to-end correct-abstention and injection-escape measurement are unavailable; and
- the final human-reviewed export workflow is not implemented.

These limitations are documented engineering boundaries, not guarantees about every deployment environment.

---

## Security notice

EvidenceForge does not currently claim production security or organizational compliance certification.

A production deployment requires appropriate protection of:

- credentials;
- PostgreSQL;
- object or file storage;
- network boundaries;
- authentication infrastructure;
- environment configuration;
- audit data; and
- deployment hosts.

Threats such as a compromised host, malicious database administrator, compromised external provider, organizational policy failures, and deployment-specific configuration mistakes remain outside the guarantees of this repository.

Security controls should be reviewed against the actual deployment environment before production use.
