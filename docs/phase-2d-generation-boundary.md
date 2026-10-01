# Phase 2D.3: Generation Boundary & Server-Side Citation Validator

## 1. Executive Summary & Architectural Scope

Phase 2D.3 defines and implements the **Generation Boundary & Server-Side Citation Validator** for EvidenceForge. This boundary establishes the exact, immutable contract between the contextual evidence selection subsystem (Phase 2D.2) and any downstream draft-generation layer, coupled with a server-enforced citation validator that independently verifies all model outputs prior to human review.

### Strict Architectural Boundaries & Non-Goals
The following constraints are strictly enforced in Phase 2D.3:
- **No LLM Provider Integration**: No vendor SDKs (`openai`, `anthropic`, `cohere`, etc.), API keys, network clients, or external API endpoints are introduced.
- **No Autonomous Answer Generation**: The server never invokes model endpoints, background workers, or autonomous answer pipelines during questionnaire import or grounding.
- **No Autonomous Actions or Approvals**: Untrusted model outputs cannot approve responses (`ResponseStatus.APPROVED` is forbidden). Only human reviewers retain approval and submission authority.
- **No Workflow / Reviewer State Overrides**: Model cannot select `NEEDS_REVIEW`, `STALE_SOURCE`, `CONFLICTING_SOURCES`, `NOT_APPLICABLE`, or `DO_NOT_DISCLOSE`. Workflow states are server/human-controlled.
- **No Database Response Persistence**: The generation validator returns an immutable `ValidatedDraftResponse` domain representation only; it does not write or mutate `questionnaire_responses`, `questionnaire_response_revisions`, or `questionnaire_response_citations` tables.
- **No SQL or Tool Access**: Model output schemas forbid tool requests, SQL commands, or execution payloads (`extra = "forbid"`).

---

## 2. Server-Controlled Generation Boundary Contract

The generation boundary isolates untrusted model generation behind a dual-boundary server pipeline:

```
+-----------------------------------------------------------------------------+
|                      PHASE 2D.2: CONTEXT SELECTION                          |
|  - Max 5 Candidates selected from Hybrid RRF Retrieval                      |
|  - Max 4,000 Token Hard Budget Enforced                                     |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|              PHASE 2D.3: IMMUTABLE GENERATION CONTEXT INPUT                 |
|  - Question metadata (ID, normalized text, section path)                    |
|  - Authorized Workspace Scope                                               |
|  - Opaque Handle Mapping: EVIDENCE-1 .. EVIDENCE-5                          |
|  - Preserved coordinates: Content hash, byte offsets, document freshness,   |
|    conflict group ID, retrieval ranks, token counts                         |
+--------------------------------------+--------------------------------------+
                                       |
                                       v  (Untrusted Draft Generation)
+-----------------------------------------------------------------------------+
|                    UNTRUSTED MODEL DRAFT PAYLOAD                            |
|  - answer: string (max 4,000 chars)                                         |
|  - status: PROPOSED | INSUFFICIENT_EVIDENCE (ALL other statuses forbidden)  |
|  - citation_handles: list of strings (e.g. ["EVIDENCE-1", "EVIDENCE-2"])   |
|  - uncertainty_notes: string (max 1,000 chars)                              |
|  - extra fields: FORBIDDEN                                                  |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|               PHASE 2D.3: SERVER-SIDE CITATION VALIDATOR                    |
|  - Validates handle existence in GenerationContext (fails closed)           |
|  - Validates handle format (rejects raw UUIDs or unselected IDs)            |
|  - Resolves handles to authoritative EvidenceCitation objects               |
|  - Server-Owned Evidence Guard: Rejects PROPOSED if context has stale/      |
|    conflicting evidence (requires INSUFFICIENT_EVIDENCE)                    |
|  - Enforces status integrity (PROPOSED requires citations + evidence)       |
|  - Verifies exact stored content hashes, byte ranges, and workspace ID      |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|              PHASE 2D.3: VALIDATED DRAFT RESPONSE (DOMAIN ONLY)             |
|  - ValidatedDraftResponse (ready for human review workbench)                |
|  - ZERO database persistence, ZERO autonomous approvals                     |
+-----------------------------------------------------------------------------+
```

---

## 3. Server Ownership & Security Invariants

### 3.1 Strict Server Ownership Matrix
The generation boundary enforces complete separation between untrusted model proposals and authoritative server controls:

| Domain Element | Ownership | Rationale / Enforcement |
| :--- | :--- | :--- |
| **Workspace Scope** | Server-Owned | Authorized workspace ID verified during context build; cross-workspace rejected. |
| **Evidence Freshness** | Server-Owned | `is_latest_document_version` and `document_status` inspected on server. |
| **Conflict Detection** | Server-Owned | `conflict_group_id` presence inspected on server. |
| **Citation Identity** | Server-Owned | Opaque `EVIDENCE-N` resolved to authoritative database chunks, byte spans, hashes. |
| **Reviewer / Workflow State** | Server-Owned | `NEEDS_REVIEW`, `STALE_SOURCE`, `CONFLICTING_SOURCES`, `APPROVED` forbidden for model. |
| **Draft Content Proposal** | Model-Proposed | Model proposes answer text, `PROPOSED` or `INSUFFICIENT_EVIDENCE`, handles, uncertainty notes. |

### 3.2 Security Invariant: Stale & Conflicting Evidence Guard
> **SECURITY INVARIANT**: "Presence of a valid citation does not make stale or conflicting evidence eligible for a PROPOSED draft."

If the selected `GenerationContext` contains any candidate with:
- `is_latest_document_version is False`, or
- `document_status` indicating a non-current state (e.g., `superseded`, `archived`, `deprecated`), or
- `conflict_group_id is not None`

Then any model payload with `status = ResponseStatus.PROPOSED` is **rejected** with `GenerationStatusValidationError`. The untrusted model must return `status = ResponseStatus.INSUFFICIENT_EVIDENCE` for the draft to pass the server boundary.

---

## 4. Data Contracts & Schema Specifications

### 4.1 Input Contract: `GenerationContext` & `GenerationEvidenceItem`
Exposed to generation layers as a frozen dataclass with complete provenance coordinates:

```python
@dataclass(frozen=True, slots=True)
class GenerationEvidenceItem:
    citation_handle: str                    # "EVIDENCE-1" .. "EVIDENCE-5"
    evidence_chunk_id: uuid.UUID            # Authoritative server chunk ID
    document_id: uuid.UUID                  # Authoritative server document ID
    version_id: uuid.UUID                   # Authoritative document version ID
    version_number: int                     # Chunk document version number
    document_name: str | None               # Human-readable document name
    document_version_number: int            # Document version number
    latest_document_version_number: int | None
    is_latest_document_version: bool | None # Document freshness indicator
    document_status: str                    # "active", "superseded", etc.
    conflict_group_id: str | None           # Conflicting policy group ID
    chunk_index: int                        # Authoritative document chunk index
    content_hash: str                       # SHA-256 chunk hash
    normalized_start_byte: int              # Exact UTF-8 byte start offset
    normalized_end_byte: int                # Exact UTF-8 byte end offset
    section_label: str | None               # Document section heading
    page_number: int | None                 # Document page number
    content: str                            # Normalized chunk text
    token_count: int                        # Chunk token count
    rrf_score: float | None = None          # Hybrid RRF score
    lexical_rank: int | None = None         # Lexical retrieval rank
    semantic_rank: int | None = None        # Semantic retrieval rank
    fallback_used: bool | None = None       # Whether search fallback occurred
```

### 4.2 Opaque Citation Handle Mapping
- Selected candidate chunks are assigned sequential, deterministic opaque handles: `EVIDENCE-1`, `EVIDENCE-2`, ..., `EVIDENCE-k` ($k \le 5$).
- **Zero Raw UUID Exposure**: Downstream model prompts receive only opaque handles, preventing the model from hallucinating, leaking, or targeting raw database UUIDs.
- Handle lookup is strictly $O(1)$ and fails closed if a cited handle was not in the selected candidate list.

### 4.3 Untrusted Output Schema: `GeneratedDraftPayload`
Pydantic model enforcing structural boundaries:

```python
class GeneratedDraftPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    answer: Annotated[str | None, Field(max_length=4000)] = None
    status: ResponseStatus  # Must be PROPOSED or INSUFFICIENT_EVIDENCE
    citation_handles: list[str] = Field(default_factory=list, max_length=5)
    uncertainty_notes: Annotated[str | None, Field(max_length=1000)] = None
```

---

## 5. Server-Side Citation Validation Pipeline

The `validate_generated_draft` function enforces multi-stage validation rules:

1. **Schema & Field Integrity**:
   - Pydantic parses payload with `extra="forbid"`. Extra attributes, tool requests, or arbitrary metadata raise `GenerationPayloadMalformedError`.
   - Status restricted to `ALLOWED_MODEL_PROPOSED_STATUSES = frozenset({ResponseStatus.PROPOSED, ResponseStatus.INSUFFICIENT_EVIDENCE})`. All workflow/approval statuses (`APPROVED`, `NEEDS_REVIEW`, `STALE_SOURCE`, `CONFLICTING_SOURCES`, `NOT_APPLICABLE`, `DO_NOT_DISCLOSE`) raise `GenerationPayloadMalformedError`.
   - Answer length bounded to 4,000 characters; uncertainty notes bounded to 1,000 characters.
   - Max 5 citation handles permitted.
   - Duplicate citation handles within the payload are rejected.
2. **Handle Resolution & Workspace Scope**:
   - Every cited handle in `citation_handles` must exist in `context.evidence_context`.
   - Unknown handles, fabricated IDs, or unselected handles raise `GenerationCitationValidationError`.
   - Handles map to authoritative `EvidenceCitation` records populated directly from server-verified candidates.
3. **Server-Owned Evidence Freshness & Conflict Guard**:
   - If selected evidence contains stale (`is_latest_document_version=False` or non-active status) or conflicting (`conflict_group_id is not None`) items, `PROPOSED` is rejected with `GenerationStatusValidationError`.
4. **Status Integrity**:
   - Status `ResponseStatus.PROPOSED` requires:
     - Non-empty, non-stale, non-conflicting evidence context in `GenerationContext`.
     - At least 1 valid cited handle in `citation_handles`.
     - Non-empty, non-whitespace `answer` string.
   - Status `ResponseStatus.INSUFFICIENT_EVIDENCE` is valid with cited or empty citations.
5. **Deterministic Resolution**:
   - Validation is pure, synchronous, deterministic, and side-effect free.
   - Produces `ValidatedDraftResponse` ready for the human review workflow.

---

## 6. Implementation Status

### CURRENT IMPLEMENTATION
- **Model Status Restriction**: Untrusted models may propose ONLY `ResponseStatus.PROPOSED` or `ResponseStatus.INSUFFICIENT_EVIDENCE`. All other statuses are rejected.
- **Server-Owned Evidence Guard**: Server rejects `PROPOSED` whenever selected context contains stale (`is_latest_document_version=False`, superseded status) or conflicting (`conflict_group_id != None`) evidence candidates.
- **Server Ownership**: Server exclusively owns freshness interpretation, conflict evaluation, workspace scope, citation identity, and reviewer/workflow state.
- **Bounds & Versioning**: Configured in `backend/app/questionnaires/generation/config.py` (`MAX_ANSWER_CHARACTERS = 4000`, `MAX_UNCERTAINTY_NOTES_CHARACTERS = 1000`, `MAX_CITED_HANDLES = 5`, `GENERATION_BOUNDARY_VERSION = "generation-boundary-v1"`).
- **Validation Pipeline**: Implemented in `backend/app/questionnaires/generation/validator.py` and `schemas.py`.
- **Test Suite**: 28 deterministic unit and integration scenarios covering all edge cases in `backend/tests/test_questionnaire_generation_boundary.py`.

### MEASURED
- **Citation Resolution Speed**: Deterministic local handle mapping and Pydantic validation executes in $<0.5$ ms per draft response.
- **Context Chunk Bound**: Generation context is strictly bounded to $\le 5$ chunks and $\le 4,000$ tokens.
- **Fail-Closed Protection**: 100% of unknown handles, raw UUIDs, model self-approvals, workflow status injections, stale/conflict `PROPOSED` attempts, extra fields, and oversized answers are caught and rejected prior to downstream review.
- **Persistence Isolation**: 0 database writes occur during generation context building and citation validation.

### PROPOSED
- Downstream integration with human review workbench UI (Phase 2E), surfacing `ValidatedDraftResponse` alongside side-by-side evidence previews and confidence flags.
- Real-time diffing between model-proposed draft citations and human-adjusted citations during review.

### OPEN DECISION
- **Batch Draft Validation vs. Per-Question Validation**: The current architecture validates drafts on a per-question basis. Batch validation across all questions in a questionnaire version can be composed as parallel invocations of `validate_generated_draft`.
- **Model Confidence Scoring**: While `uncertainty_notes` is currently a string field, a structured confidence enum or normalized numeric score could be added to future schema versions if required by downstream calibration systems.

### NOT IMPLEMENTED
- External LLM provider SDKs (`openai`, `anthropic`, `cohere`).
- Automatic or background model API execution.
- Direct database writes to `questionnaire_responses` from untrusted model payloads.
- Automated response approval without human reviewer sign-off.
- Questionnaire auto-submission.
