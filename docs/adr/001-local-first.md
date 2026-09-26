# ADR 001: Local-First Deployment Boundary

- **Status:** Proposed
- **Date:** 2026-09-26
- **Decision owners:** EvidenceForge maintainers
- **Scope:** MVP deployment and data boundary

## Context

EvidenceForge processes questionnaires, internal policies, control descriptions, and evidence documents that may be sensitive. The product must be understandable and operable by one developer, while reducing unnecessary data movement. A hosted multi-tenant service would add identity, tenancy, billing, backup, incident response, and provider-boundary complexity that is outside the MVP.

“Local-first” is also ambiguous. It can mean operator-controlled application and data, no SaaS control plane, or a fully offline/air-gapped system. The MVP must not imply the strongest meaning if it still calls a remote LLM or embedding provider.

## Decision

Design the MVP to run on operator-controlled infrastructure using Docker-based services, PostgreSQL/pgvector, private local or operator-controlled storage, and an explicit configuration for one approved LLM provider. The application and primary data are local-first; remote provider calls are permitted only after an evidence-class and data-processing review.

The MVP will:

- avoid SaaS billing and hosted multi-tenancy;
- keep original files and primary records in private operator-controlled storage;
- externalize secrets and provider configuration;
- document network connectivity and provider retention assumptions;
- apply workspace authorization even in a single-operator deployment; and
- keep an architecture seam for a future local model or approved provider without designing multi-provider routing now.

## Alternatives considered

### Hosted multi-tenant SaaS

Rejected for MVP because it expands identity, isolation, billing, operations, and compliance scope before the core evidence-bound workflow is validated.

### Fully offline/air-gapped by default

Not selected as the universal MVP promise because the initial LLM/embedding choice is unresolved and may be remote. It remains a deployment option only if approved local models and sufficient resources are available.

### Desktop-only single-user application

Not selected because a small local service with a workspace model better supports review roles, worker isolation, PostgreSQL search, and repeatable Docker deployment. The service must still be usable by a small trusted team.

## Consequences

### Positive

- Primary evidence and audit data can remain under operator control.
- The operational model is smaller than a public SaaS.
- Provider data boundaries are visible rather than hidden.
- Docker makes the MVP reproducible without Kubernetes.

### Negative

- Operators own backups, upgrades, host hardening, secrets, and incident response.
- A remote provider may still receive sensitive excerpts unless a local model is used.
- Local hardware can limit latency, throughput, and embedding/generation quality.
- Authentication may initially require a reverse proxy or a minimal local account system.

## Security implications

Local deployment does not automatically provide confidentiality. The threat model still requires workspace authorization, private volumes, network restrictions, secret handling, worker isolation, rate/token limits, and redacted telemetry. Provider payloads must be minimized and excluded from logs. Host and Docker-daemon compromise are outside the application's complete control.

## Validation required

- Confirm whether remote LLM/embedding processing is allowed by evidence class.
- Define host, database, storage, backup, and secret ownership.
- Test Docker configuration for unintended public exposure.
- Measure local resource needs and provider latency.
- Document deletion and backup restoration behavior.

## Revisit triggers

Revisit this decision if the product needs public multi-tenancy, SSO/SCIM, billing, managed backups, air-gapped deployment, or multiple provider routing. Those changes require new ADRs and an expanded threat model.
