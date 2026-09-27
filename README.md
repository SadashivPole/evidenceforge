# EvidenceForge

EvidenceForge is a local-first workbench for drafting security questionnaire responses from approved evidence. It is designed around a deliberately narrow chain:

**evidence first → grounded draft → citations → human review → export**

The project has completed Phase 0 documentation and is now in **Phase 1B: security boundary**. Phase 1A established the API, web, database, migration, configuration, testing, and local Docker boundaries. Phase 1B adds the server-side workspace, membership, role, opaque-token authentication, authorization, isolation, IDOR protections, and audit-event boundary. Evidence ingestion, retrieval, questionnaire processing, and LLM functionality remain unimplemented. Performance, security, and quality values in the documentation remain targets or hypotheses until measured.

## Phase 1B status

The implementation includes:

- workspace and membership persistence with owner, admin, member, and viewer roles;
- database-backed opaque bearer-token authentication with token expiry and revocation checks;
- server-side workspace authorization derived from authenticated memberships;
- protected workspace, membership, and audit-event routes with IDOR and cross-workspace isolation checks;
- immutable-in-practice audit event records for security-sensitive operations and denied access attempts; and
- unit, integration, migration, and security tests covering authentication, role boundaries, IDOR, and cross-workspace access.

Bearer tokens are intentionally provisioned outside the public API. A deployment-specific identity/token provisioning mechanism must create users and token records before protected routes can be used. The Phase 1B API does not add self-service registration or token issuance.

## Phase 1A status

The skeleton includes:

- a FastAPI backend with liveness and database readiness endpoints;
- a Next.js and TypeScript frontend with a deliberately small status page;
- PostgreSQL configuration through SQLAlchemy and `psycopg`;
- Alembic migration configuration with an intentionally empty initial revision;
- Docker Compose services for PostgreSQL, the API, and the web process;
- environment-based configuration with `.env` ignored and `.env.example` committed;
- backend pytest tests plus Ruff and mypy configuration; and
- frontend ESLint, Prettier, and TypeScript configuration.

The empty initial migration is deliberate: Phase 1A establishes the migration mechanism without inventing domain tables before the evidence and questionnaire contracts are validated.

## Local development

Prerequisite: Python 3.11+, Node.js 20+, npm, and Docker Compose.

One-time setup:

```powershell
Copy-Item .env.example .env
```

Start the local environment:

```powershell
docker compose up --build
```

The API is available at `http://localhost:8000`, with liveness at `/health/live` and database readiness at `/health/ready`. The web skeleton is available at `http://localhost:3000`. The API container runs `alembic upgrade head` before starting Uvicorn.

Useful local checks, outside Docker:

```text
python -m pip install -e "backend[dev]"
python -m pytest backend/tests
ruff check backend/app backend/tests backend/migrations
ruff format --check backend/app backend/tests backend/migrations
mypy backend/app
cd frontend; npm ci; npm run lint; npm run typecheck; npm run format:check
```

These commands test and check only the functionality currently implemented; they do not imply application security or production readiness.

## Problem

Security and compliance teams repeatedly answer questionnaires using information spread across approved policies, controls, procedures, and evidence documents. A useful workbench must reduce search and drafting effort without turning unsupported text into a confident answer. When the available evidence is insufficient, the system should say so explicitly rather than guess.

## Product boundary

EvidenceForge is:

- a local workspace/account model for evidence and questionnaire runs;
- an ingestion path for PDF, TXT, and Markdown evidence;
- a single-sheet CSV/XLSX questionnaire importer;
- a PostgreSQL and pgvector-backed evidence index;
- a hybrid lexical/vector retrieval system with reciprocal-rank fusion;
- a typed drafting pipeline using one LLM provider;
- a citation-bearing draft and review workflow;
- an exportable questionnaire result with an audit trail; and
- an evaluation corpus and observability plan.

EvidenceForge is not:

- a generic chatbot or open-ended assistant;
- a legal, compliance, or certification authority;
- an autonomous questionnaire submitter;
- a replacement for GRC platforms such as Vanta or Drata;
- an autonomous agent with shell, network, or write access; or
- a system that can certify that an organization is compliant.

## Bounded workflow

The proposed per-question workflow is:

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

A model may propose structured text only. It does not choose tools, execute commands, access the network, change files, or submit a questionnaire. Human review is required before an answer becomes accepted or exportable.

## Proposed MVP scope

- Local workspace/account and authorization model.
- Evidence ingestion for PDF, TXT, and Markdown, with file, type, and size limits.
- Single-sheet CSV/XLSX questionnaire import.
- Evidence version and provenance tracking.
- PostgreSQL full-text search plus pgvector similarity search.
- Hybrid retrieval and reciprocal-rank fusion.
- One configured LLM provider.
- Strict structured JSON output, typed answer statuses, and citation validation.
- Explicit `INSUFFICIENT_EVIDENCE` handling.
- Human review, edits, rejection, and questionnaire export.
- A 60-case evaluation corpus and measurable quality, safety, and operational metrics.
- Redacted observability, audit events, Docker-based deployment, and documented security controls.

## Explicitly deferred

OCR, web crawling, Slack/Jira integrations, MCP, multi-provider routing, SaaS billing, automatic submission, enterprise SSO/SCIM, Kubernetes, fine-tuning, and autonomous provisioning are outside the MVP. See [docs/non-goals.md](docs/non-goals.md).

## Repository map

| Document | Purpose |
|---|---|
| [docs/discovery.md](docs/discovery.md) | Phase 0 findings, assumptions, hypotheses, and discovery plan |
| [docs/problem-statement.md](docs/problem-statement.md) | Problem framing, users, jobs, and requirements |
| [docs/non-goals.md](docs/non-goals.md) | Explicit product and technical boundaries |
| [docs/threat-model.md](docs/threat-model.md) | Assets, trust boundaries, threats, controls, and residual risk |
| [docs/evaluation-plan.md](docs/evaluation-plan.md) | 60-case corpus, metrics, test method, and targets |
| [docs/architecture.md](docs/architecture.md) | Proposed components, data model, workflows, and deployment |
| [docs/discovery-interview-template.md](docs/discovery-interview-template.md) | Interview script and evidence-gathering template |
| [docs/adr/001-local-first.md](docs/adr/001-local-first.md) | Decision record for local-first deployment |
| [docs/adr/002-retrieval-strategy.md](docs/adr/002-retrieval-strategy.md) | Decision record for hybrid retrieval and RRF |
| [docs/adr/003-question-processing-state-machine.md](docs/adr/003-question-processing-state-machine.md) | Decision record for the bounded workflow |
| [docs/adr/004-observability.md](docs/adr/004-observability.md) | Decision record for redacted telemetry and auditability |

## Status vocabulary

The design uses separate technical and review outcomes:

- `INSUFFICIENT_EVIDENCE`: the available evidence does not support a defensible answer under the configured policy.
- `NEEDS_REVIEW`: a typed draft was generated and passed structural/citation checks, but a human must approve it.
- `ACCEPTED`: a reviewer accepted the generated draft without substantive changes.
- `EDITED_ACCEPTED`: a reviewer edited the draft and accepted the edited answer.
- `REJECTED`: a reviewer rejected the draft.
- `FAILED`: processing failed due to invalid input, infrastructure failure, policy rejection, or an unrecoverable validation error. The reason must be recorded without logging sensitive content.

## Phase 0 completion criteria

Phase 0 is complete when:

1. The proposed workflow and trust boundaries are understandable to a reviewer who did not author the design.
2. Major assumptions and unanswered product/security questions are explicitly listed.
3. The 60-case evaluation corpus design can test support, ambiguity, abstention, conflict/staleness, injection, isolation, and malformed inputs.
4. Every answer-producing path has a citation and human-review policy.
5. The next implementation slice is small enough to test end to end without building deferred features.

These are documentation criteria, not evidence that the future implementation is secure or production-ready.

## Recommended first implementation slice

Implement a non-LLM vertical slice for one workspace: ingest a bounded TXT/Markdown file, create immutable evidence versions and chunks, import a single-sheet CSV questionnaire, perform PostgreSQL lexical retrieval, and display candidate citations. Add authorization and audit events before adding vector retrieval or generation. The suggested sequence and open decisions are recorded in [docs/architecture.md](docs/architecture.md) and [docs/discovery.md](docs/discovery.md).

## Security notice

This repository does not claim production security. The design assumes a trusted local operator, a supported deployment boundary, reviewed configuration, protected database and object storage, and a correctly implemented authorization layer. Threats such as a compromised host, malicious database administrator, compromised LLM provider, OCR-specific attacks, and organizational policy failures remain limitations or deferred work. See [docs/threat-model.md](docs/threat-model.md).
