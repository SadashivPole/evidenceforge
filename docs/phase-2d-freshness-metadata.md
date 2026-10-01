# Phase 2D.1: Freshness & Conflict Metadata Enrichment

- **Status:** Implementation checkpoint for evidence candidate freshness and conflict metadata enrichment
- **Scope:** Enriches retrieved evidence candidates with authoritative document version and freshness metadata across lexical, semantic, hybrid, and grounding layers
- **Production impact:** All candidate contracts (`SearchChunkCandidate`, `SemanticSearchResult`, `HybridSearchResult`, `QuestionnaireGroundingCandidateResponse`) now expose explicit version provenance and freshness flags

---

## 1. Executive Summary and Purpose

Phase 2D.1 enriches retrieved evidence candidates with explicit immutable document-version and freshness metadata.

```text
"Freshness metadata is descriptive state, not an automatic answer policy."
```

The objective is to ensure that downstream context selection, prompt budgeting, and future response generation never have to guess whether a retrieved candidate is from a current, superseded, or archived document version.

```text
Persisted Evidence Ingestion
  ├── EvidenceDocument (status: "active")
  └── EvidenceDocumentVersion (version_number: 1, 2, ...)
        └── EvidenceChunk (chunk_index: 0, ...)
                    ↓
Authoritative SQL Aggregation (Subquery inside authorized workspace)
  latest_document_version_number = MAX(version_number) WHERE document_id = doc.id
                    ↓
Enriched Candidate Contract:
  • document_version_number: int (persisted version number)
  • latest_document_version_number: int (authoritative maximum version)
  • is_latest_document_version: bool (version_number == latest_document_version_number)
  • document_status: str ("active")
  • conflict_group_id: None (explicit deferred field)
```

### Strict Non-Goals

As mandated for Phase 2D.1:
* **No LLM Generation or Prompt Construction**: Output candidates are structured, immutable metadata representations only.
* **No Automatic Conflict Resolution**: The retrieval layer does not discard superseded versions or decide which conflicting version is "true".
* **No Context Budgeting**: Filtering to the top $N$ context items is deferred to Phase 2D.2.
* **No HNSW Indexing or Schema Migrations**: Exact vector scan and existing Task 7A schema are preserved without alteration.
* **No RRF or Ranking Changes**: Lexical, semantic, and RRF scoring and tie-breaking algorithms remain unchanged.

---

## 2. Exact Freshness Semantics

Freshness is derived strictly from authoritative persisted version numbers in the database:

1. **`document_version_number`**:
   The immutable version number ($1, 2, \dots$) of the parent `EvidenceDocumentVersion` from which the `EvidenceChunk` was extracted.
2. **`latest_document_version_number`**:
   The authoritative maximum `version_number` across all versions of that `EvidenceDocument` within the authorized workspace.
3. **`is_latest_document_version`**:
   Boolean flag computed as:
   $$\text{is\_latest\_document\_version} = (\text{document\_version\_number} == \text{latest\_document\_version\_number})$$

### Invariant: What "Latest" Is NOT

"Latest" is **NOT** defined as:
* Highest RRF score
* Highest lexical score
* Highest semantic similarity
* Most recently retrieved candidate
* Most recently generated embedding

Freshness represents immutable repository state, entirely decoupled from search relevance.

---

## 3. Conflict Metadata Representation

* **`document_status`**: Persisted lifecycle status of the parent `EvidenceDocument` (`"active"`).
* **`conflict_group_id`**: Set explicitly to `None`.

### Explicit Deferred Field: `conflict_group_id`
The repository currently models document version trees (`EvidenceDocumentVersion`), but does not have an explicit multi-document semantic conflict grouping table. In strict compliance with the core engineering rule (*"Do not invent a persistence model if the repository does not already have one"*), `conflict_group_id` is preserved as `None` rather than fabricating an ad-hoc heuristic.

---

## 4. Why Stale Evidence Is Preserved Rather Than Discarded

EvidenceForge intentionally retrieves both current and superseded evidence versions when both match a query:

1. **Auditability & Traceability**: Questionnaire responses often need to explain why a previous year's policy differed from the current policy.
2. **Defensive Review**: If a company had a 90-day password rotation policy in Version 1 and eliminated it in Version 2, human reviewers must be alerted that conflicting control language exists in the repository.
3. **Separation of Concerns**: Discarding superseded versions during retrieval would hide historical context. Freshness metadata enables the future selection and generation layers to categorize cases as `CONFLICTING_STALE` and prompt human reviewers accordingly.

---

## 5. Security & Workspace Boundary Invariants

The freshness enrichment implementation is designed with strict multi-tenant isolation:

1. **Workspace-Scoped Subquery**:
   The `MAX(version_number)` subquery joins `EvidenceDocument` and filters `EvidenceDocument.workspace_id == :authorized_workspace_id`.
   A document uploaded in Workspace B cannot influence or leak version numbers to Workspace A.
2. **Single-Query Performance (Zero N+1 Queries)**:
   The latest version subquery is joined directly inside `list_search_candidates` and `query_semantic_candidates`. Freshness metadata is resolved in a single database roundtrip without per-candidate queries.
3. **Defense-in-Depth Fusion Gate**:
   `fuse_hybrid_results` verifies that all candidates match the authorized workspace ID before RRF computation.

---

## 6. Target Candidate Contracts

The metadata is exposed across all retrieval contracts:

```python
# app.evidence.search.types.SearchChunkCandidate
@dataclass(frozen=True, slots=True)
class SearchChunkCandidate:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_index: int
    content: str
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None
    document_status: str = "active"
    latest_document_version_number: int | None = None
    is_latest_document_version: bool | None = None
    conflict_group_id: str | None = None

    @property
    def document_version_number(self) -> int:
        return self.version_number
```

```python
# app.api.schemas.QuestionnaireGroundingCandidateResponse
class QuestionnaireGroundingCandidateResponse(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_index: int
    content: str
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None
    document_status: str | None = "active"
    document_version_number: int | None = None
    latest_document_version_number: int | None = None
    is_latest_document_version: bool | None = None
    conflict_group_id: str | None = None
```
