# Non-Goals and Boundaries

This document prevents scope expansion from weakening the evidence-bound MVP. A non-goal may become a future project, but it is not an implicit commitment.

## Product non-goals

### Generic chatbot

EvidenceForge will not provide an open-ended chat surface whose answers are disconnected from a question, evidence set, state, and citation policy. A future conversational interface would still need to preserve these boundaries.

### Legal or compliance certification

The system will not decide that an organization is compliant, provide legal advice, issue an attestation, or replace a qualified reviewer or auditor. It can help prepare a response draft and expose supporting evidence.

### Autonomous questionnaire submission

The system will not log in to a customer portal, send email, submit a form, or represent that an answer is final without explicit human review and a separate approved process. Export is a controlled output, not submission.

### GRC platform replacement

The MVP will not manage a complete control catalog, risk register, audit program, vendor program, policy lifecycle, or remediation workflow. It consumes a bounded evidence workspace.

### Autonomous agent

The model will not select tools, invoke shell commands, browse the network, modify files, provision infrastructure, or change authorization. The pipeline is a deterministic application workflow around a constrained model call.

## Technical non-goals for the MVP

- OCR or image understanding for scanned PDFs.
- Web crawling, browser automation, or external source discovery.
- Slack, Jira, ticketing, document-management, or GRC integrations.
- MCP or any general-purpose tool protocol.
- Multi-provider routing, automatic fallback among providers, or provider arbitrage.
- SaaS billing, metering for customers, or self-service tenancy management.
- Enterprise SSO, SCIM, directory synchronization, or advanced identity federation.
- Kubernetes, service mesh, or multi-region deployment.
- Fine-tuning, custom model training, or customer-specific model weights.
- Autonomous evidence provisioning or automatic policy updates.
- Real-time collaboration, comments, or presence.
- Spreadsheet formula execution or arbitrary macro support.
- Arbitrary file formats such as DOCX, PPTX, ZIP bundles, images, or archives as evidence.
- Guaranteed offline generation if the selected LLM or embedding provider is remote.
- Automated resolution of conflicting or stale evidence.

## Operational non-goals

- Claiming production security without implementation review and testing.
- Claiming customer validation, cost, latency, or accuracy before measurement.
- Hiding unsupported answers to improve completion rates.
- Logging full source documents, questionnaire rows, prompts, or answers for convenience.
- Treating a model's confidence, citation presence, or schema validity as proof of correctness.
- Using retrieved evidence as an instruction channel.

## Scope rules for future requests

A proposed feature belongs in the MVP only if it:

1. preserves evidence-first drafting and explicit abstention;
2. can be implemented by one developer without obscuring authorization and auditability;
3. has a clear threat model and evaluation case;
4. does not introduce autonomous external side effects; and
5. does not require a deferred integration or identity platform.

Otherwise, record it as a future idea with its own decision record rather than silently adding it to the core.
