# Discovery Interview Template

## Purpose

Use this template to learn how questionnaire work is actually performed and to validate EvidenceForge assumptions. Do not collect customer-sensitive evidence into this repository. Replace names with roles or pseudonyms, and record only the minimum information needed for product decisions.

**Interview status:** template only; no interviews completed.

## Interview metadata

- Interview ID:
- Date:
- Participant role:
- Organization type/size band (not name):
- Facilitator:
- Note-taker:
- Consent to record notes: yes / no
- Data classification of notes:
- Follow-up owner:

## Opening script

“We are exploring a local-first workbench that helps prepare security questionnaire drafts from approved organizational evidence. It would show citations, abstain when evidence is insufficient, and require human review before export. It is not intended to certify compliance or submit answers automatically. We are evaluating the workflow, not the participant.”

Confirm that the participant should not share secrets, customer identifiers, personal data, or full confidential documents during the interview. Ask for sanitized examples or field names instead.

## Section 1 — Current workflow

1. Walk me through the last questionnaire you helped answer, from receipt to delivery.
2. What types of questionnaires do you see most often?
3. Which steps consume the most time?
4. Which steps create the most risk or rework?
5. Who drafts, reviews, approves, and exports the final response?
6. What happens when the answer is “not known,” “not applicable,” or “not supported”?
7. How do you track unanswered or follow-up questions?

**Observe / record:** actors, handoffs, systems, approval points, failure points, and terms used by the participant.

## Section 2 — Questionnaire inputs and outputs

1. What file formats do questionnaires arrive in?
2. What columns identify a question, section, response, or comment?
3. Are there formulas, merged cells, hidden sheets, or formatting requirements?
4. Must row order and original identifiers be preserved?
5. What answer types are required: free text, yes/no, maturity scale, attachments, dates, or something else?
6. What must the exported response contain for the recipient to accept it?
7. Is an export considered final, or is there another approval or submission process?

**Capture sample schema only:** column names, not confidential values.

## Section 3 — Evidence sources and freshness

1. Where do analysts look for support today?
2. What makes a document approved for use?
3. What metadata is available: owner, effective date, review date, version, classification, superseded status?
4. How do you know which version is current?
5. What happens when two documents conflict?
6. How are tables, appendices, screenshots, and scanned PDFs handled?
7. What information may not be sent to an external service?
8. How should a reviewer see and explain the source of an answer?

**Ask for sanitized document shapes:** titles, section structure, metadata fields, page/section citation expectations.

## Section 4 — Drafting and review behavior

1. What does a defensible answer look like?
2. How much wording can be inferred from evidence before it becomes overclaiming?
3. Which caveats are important to reviewers?
4. What would make you trust or distrust a generated draft?
5. Would an explicit “insufficient evidence” answer be useful? What should it say next?
6. How should ambiguous or conflicting evidence appear?
7. Can the same person draft and accept an answer?
8. What must be preserved when an answer is edited?

## Section 5 — Security and operational boundary

1. What are the consequences of an incorrect or leaked answer?
2. What data classes are present in questionnaires and evidence?
3. Is a local deployment required to avoid sending content off premises, or is an approved provider acceptable?
4. Who operates the host, database, storage, backups, and secrets?
5. What identity provider or reverse proxy is available?
6. Who should access each workspace, and what roles are needed?
7. What should appear in an audit trail?
8. What are acceptable processing time, resource, and cost limits?
9. What should happen if the model/provider is unavailable?

## Section 6 — Prioritization

Ask the participant to rank these from highest to lowest value:

- faster evidence search;
- better semantic matching;
- visible citations;
- explicit abstention;
- stale/conflict warnings;
- review workflow;
- export preservation;
- audit trail;
- local deployment;
- cost/latency visibility.

Follow up: “What would make this unusable even if the other features worked?”

## Evidence of understanding

After the interview, write a short summary using the participant’s own terms:

- Primary job:
- Current workaround:
- Most expensive failure:
- Evidence approval rule:
- Review/acceptance rule:
- Freshness/conflict rule:
- Required input contract:
- Required output contract:
- Security boundary:
- Operational constraint:

## Assumption review

For each assumption from [discovery.md](discovery.md), mark:

- confirmed by direct evidence;
- contradicted;
- partially supported;
- still unknown; or
- not relevant to this participant.

Do not convert a single participant preference into a universal requirement.

## Follow-up and artifacts

- Sanitized sample questionnaire schema:
- Sanitized evidence metadata schema:
- Open decision:
- Owner:
- Due date:
- Validation method:
- Data deletion/retention action:

## Interviewer cautions

- Do not promise a feature or imply that the system will certify compliance.
- Do not ask participants to upload real evidence into an unapproved environment.
- Do not treat confidence in AI as evidence of correctness.
- Ask for concrete recent examples rather than general opinions.
- Separate “would be useful” from “is required for safe operation.”
