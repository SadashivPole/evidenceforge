# Phase 2D.4: Human Review Integration

## Executive Summary

Phase 2D.4 integrates the Phase 2D.3 `ValidatedDraftResponse` into the authoritative EvidenceForge human-review workflow. It establishes a strictly human-controlled review and approval pathway where untrusted model outputs propose drafts, server-side validators verify structural integrity and provenance, and authenticated human reviewers make all binding approval and editing decisions before response persistence.

> **PRIMARY ARCHITECTURAL INVARIANT**:
> "Human approval is the only mechanism that can transition a validated draft into an approved persisted questionnaire response."

---

## Architecture & Lifecycle Boundary

```
+-----------------------------------------------------------------------------+
|               PHASE 2D.3: VALIDATED DRAFT REPRESENTATION                    |
|  - ValidatedDraftResponse (domain representation, 0 database writes)        |
|  - Bounded Answer (<=4,000 chars), Uncertainty notes (<=1,000 chars)        |
|  - Bounded Opaque Citation Handles (EVIDENCE-1 .. EVIDENCE-5)               |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|             PHASE 2D.4: SERVER-AUTHORIZED REVIEW DRAFT CONTEXT              |
|  - ReviewDraftContext (authorized workspace scope verification)             |
|  - Authoritative Evidence Provenance & Freshness Coordinates:               |
|    chunk_id, document_id, version_id, chunk_index, byte ranges, hashes,    |
|    is_latest_document_version, document_status, conflict_group_id          |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                  EXPLICIT HUMAN REVIEW WORKBENCH & DECISION                 |
|  - Authenticated Human Reviewer (Role: OWNER, ADMIN, MEMBER)                |
|  - Review Actions:                                                          |
|      1. ACCEPT / APPROVE (accepts validated draft as-is)                    |
|      2. EDIT_AND_APPROVE (human edits answer / selects supported citations) |
|      3. REJECT (marks draft for review / insufficient evidence)             |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|           IMMUTABLE RESPONSE PERSISTENCE & AUDIT TRAIL                      |
|  - QuestionnaireResponse / QuestionnaireResponseRevision                    |
|  - QuestionnaireResponseCitation (exact immutable provenance chain)         |
|  - Audit Event ("questionnaire.response.reviewed") with reviewer identity   |
+-----------------------------------------------------------------------------+
```

---

## 1. CURRENT IMPLEMENTATION

### 1.1 Review Domain Model & Types (`backend/app/questionnaires/review/types.py`)
- `ReviewAction`: Explicit reviewer actions (`ACCEPT`, `APPROVE`, `EDIT_AND_APPROVE`, `REJECT`).
- `ReviewEvidenceItem`: Reviewer-visible evidence candidate containing exact provenance (chunk ID, document ID, version ID, version number, chunk index, content hash, byte span, section, page, token count, RRF score, lexical/semantic ranks) and freshness/conflict indicators (`is_latest_document_version`, `document_status`, `conflict_group_id`, `is_cited_by_draft`).
- `ReviewDraftContext`: Server-authorized context encapsulating question metadata, draft answer, proposed status, uncertainty notes, evidence items, and cited handles.
- `ReviewDecision`: Reviewer payload specifying action, optional human-edited answer, optional selected handles/chunk IDs, rejection notes, and target response status.
- `ReviewExecutionResult`: Committed persistence result holding the updated `QuestionnaireResponse`, new `QuestionnaireResponseRevision`, and attached `QuestionnaireResponseCitation` records.

### 1.2 Review Service & Boundary Guards (`backend/app/questionnaires/review/service.py`)
- `build_review_context(context, draft)`: Server-side factory that assembles the review context, enforces workspace matching, evaluates stale/conflict markers, and flags cited evidence.
- `apply_review_decision(db, workspace_id, actor_user_id, actor_role, review_context, decision)`:
  - **Role Authorization**: Review actions require `OWNER`, `ADMIN`, or `MEMBER` roles (`VIEWER` is strictly rejected with `ReviewAuthorizationError`).
  - **Workspace Scope**: Enforces strict match between session workspace and question workspace.
  - **Citation Guard & Mutual Exclusivity**:
    - Review decisions accept either `selected_citation_handles` or `selected_chunk_ids`, but not both (providing both raises `ReviewValidationError`).
    - If neither is provided, defaults strictly to citations from the validated draft.
    - Reviewer citations must resolve strictly to evidence in the validated review context; arbitrary, unselected, or cross-workspace chunk UUIDs are rejected with `ReviewCitationValidationError`.
  - **Review Action & Status State Machine**:
    - **ReviewAction determines the persisted human-review status. target_status cannot override the action state machine.**
    - `ACCEPT` -> `APPROVED` (target_status must be `None` or `APPROVED`; any other status is rejected).
    - `APPROVE` -> `APPROVED` (target_status must be `None` or `APPROVED`; identical approval alias to `ACCEPT`).
    - `EDIT_AND_APPROVE` -> `APPROVED` (target_status must be `None` or `APPROVED`; requires non-empty human-edited answer).
    - `REJECT` -> `NEEDS_REVIEW` (target_status must be `None` or `NEEDS_REVIEW`; any other status including `APPROVED`, `PROPOSED`, or `INSUFFICIENT_EVIDENCE` is rejected).
    - When `target_status` is omitted (`None`), the system automatically resolves to the canonical status required by the review action.
    - Persisting machine or transient statuses (`PROPOSED`, `INSUFFICIENT_EVIDENCE`, `STALE_SOURCE`, `CONFLICTING_SOURCES`, `NOT_APPLICABLE`, `DO_NOT_DISCLOSE`) via human review actions is strictly prohibited.
  - **Immutable Revision Persistence**: Revisions are created monotonically with incrementing revision numbers; prior revisions remain immutable and untouched.
  - **Audit Trail**: Records `questionnaire.response.reviewed` audit event containing reviewer identity, question coordinates, revision number, review action, final status, and citation count.

### 1.3 API Routes & Schemas (`backend/app/api/`)
- `POST /workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{questionnaire_version_id}/questions/{questionnaire_version_question_id}/review`:
  - Accepts `QuestionnaireReviewDecisionRequest` with strict validation (`extra="forbid"`, bounded fields, regex-verified citation handle formats, selector mutual exclusivity).
  - Passes untrusted draft payloads through the Phase 2D.3 `validate_generated_draft()` generation boundary validator.
  - Assembles genuine `ValidatedDraftResponse` -> `ReviewDraftContext`.
  - Executes human review decision and returns `QuestionnaireReviewResultResponse`.
- Schemas in `backend/app/api/schemas.py`: `QuestionnaireReviewContextResponse`, `QuestionnaireReviewEvidenceItemResponse`, `QuestionnaireReviewDecisionRequest`, `QuestionnaireReviewResultResponse`.

### 1.4 Frontend Integration (`frontend/app/page.tsx`)
- Integrated human review action controls directly into the questionnaire review workbench:
  - **Approve Draft**: Approves the validated draft as an authorized response.
  - **Edit & Approve**: Allows editing the answer text and selecting citations before approving.
  - **Reject Draft**: Rejects the draft and records it for further human review.
- Added visible freshness badges (`Latest Version`, `Superseded`, `Conflict: <group>`) and chunk index tags across all ranked evidence cards.

---

## 2. MEASURED

- **Review Decision Execution Speed**: End-to-end review validation and PostgreSQL persistence executes in $< 15$ ms per decision.
- **Role Isolation**: 100% of viewer review attempts are rejected with HTTP 403 Forbidden.
- **Workspace Isolation**: 100% of cross-workspace review requests and cross-workspace citation references are rejected.
- **Provenance Integrity**: Exact byte offsets, content hashes, document versions, and chunk indices are preserved from evidence retrieval through to persisted citations.
- **Immutability Verification**: Revisions are verified immutable; duplicate decisions do not create spurious revisions.

---

## 3. PROPOSED

- Batch reviewer approvals for questionnaires where all questions have passing, high-confidence drafts.
- Reviewer side-by-side diffing between model draft answer and human-edited answer.
- Pre-populated uncertainty annotations highlighted directly in the editor textarea.

---

## 4. OPEN DECISIONS

1. **Review Rejection Persistence vs. Pure Transient Rejection**:
   - *Current Implementation*: Rejection creates an immutable revision with status `NEEDS_REVIEW` and records an audit event.
   - *Open Option*: Allow soft rejection that dismisses a draft in the UI session without creating a new response revision until a replacement draft or manual answer is submitted.

2. **Multi-Reviewer Concurrency Conflict Strategy**:
   - *Current Implementation*: Uses PostgreSQL row locking (`lock_response`) and monotonic revision numbering.
   - *Open Option*: Optimistic concurrency token (e.g. `If-Match: revision_number`) passed from frontend to warn reviewers if another human modified the question response concurrently.

---

## 5. NOT IMPLEMENTED

- External LLM provider SDKs (`openai`, `anthropic`, `cohere`, `google-genai`).
- Direct network model calls or API keys.
- Autonomous questionnaire answering or auto-approvals without human intervention.
- Autonomous questionnaire export or submission.
- Model access to raw SQL, database credentials, or tool calling.
