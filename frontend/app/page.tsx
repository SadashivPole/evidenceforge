"use client";

import { FormEvent, useMemo, useState } from "react";

type ResponseStatus =
  | "PROPOSED"
  | "APPROVED"
  | "NEEDS_REVIEW"
  | "INSUFFICIENT_EVIDENCE"
  | "STALE_SOURCE"
  | "CONFLICTING_SOURCES"
  | "NOT_APPLICABLE"
  | "DO_NOT_DISCLOSE";

type GroundingStatus = "MATCHED" | "NO_MATCHES";

type Selection = {
  workspaceId: string;
  questionnaireId: string;
  versionId: string;
  questionId: string;
};

type GroundingCandidate = {
  chunk_id: string;
  document_id: string;
  version_id: string;
  version_number: number;
  chunk_index: number;
  content: string;
  content_hash: string;
  normalized_start_byte: number;
  normalized_end_byte: number;
  section_label: string | null;
  page_number: number | null;
  document_status?: string | null;
  document_version_number?: number | null;
  latest_document_version_number?: number | null;
  is_latest_document_version?: boolean | null;
  conflict_group_id?: string | null;
};

type GroundingResult = {
  candidate: GroundingCandidate;
  score: number;
  matched_terms: string[];
  exact_phrase_match: boolean;
  occurrence_count: number;
  rrf_score?: number | null;
  lexical_rank?: number | null;
  semantic_rank?: number | null;
};

type GroundingCitation = {
  workspace_id: string;
  document_id: string;
  version_id: string;
  version_number: number;
  chunk_id: string;
  chunk_index: number;
  content_hash: string;
  normalized_start_byte: number;
  normalized_end_byte: number;
  section_label: string | null;
  page_number: number | null;
};

type GroundingResponse = {
  workspace_id: string;
  questionnaire_id: string;
  questionnaire_version_id: string;
  questionnaire_version_question_id: string;
  normalized_query: string;
  search_version: string;
  result_limit: number;
  status: GroundingStatus;
  results: GroundingResult[];
  citations: GroundingCitation[];
};

type ResponseCitation = {
  id: string;
  response_revision_id: string;
  citation_order: number;
  evidence_document_id: string;
  evidence_version_id: string;
  evidence_chunk_id: string;
  evidence_version_number: number;
  evidence_chunk_index: number;
  content_hash: string;
  normalized_start_byte: number;
  normalized_end_byte: number;
  section_label: string | null;
  page_number: number | null;
  created_at: string;
};

type ResponseRevision = {
  id: string;
  response_id: string;
  revision_number: number;
  answer: string | null;
  status: ResponseStatus;
  author_user_id: string | null;
  created_at: string;
  citations: ResponseCitation[];
};

type PersistedResponse = {
  id: string;
  workspace_id: string;
  created_by_user_id: string;
  questionnaire_id: string;
  questionnaire_version_id: string;
  questionnaire_version_question_id: string;
  current_revision: ResponseRevision;
  revisions: ResponseRevision[];
};

const responseStatuses: ResponseStatus[] = [
  "NEEDS_REVIEW",
  "PROPOSED",
  "APPROVED",
  "INSUFFICIENT_EVIDENCE",
  "STALE_SOURCE",
  "CONFLICTING_SOURCES",
  "NOT_APPLICABLE",
  "DO_NOT_DISCLOSE",
];

const initialSelection: Selection = {
  workspaceId: "",
  questionnaireId: "",
  versionId: "",
  questionId: "",
};

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function encodePathPart(value: string): string {
  return encodeURIComponent(value.trim());
}

function resourcePath(
  selection: Selection,
  resource: "grounding" | "response" | "review",
) {
  return [
    "/workspaces",
    encodePathPart(selection.workspaceId),
    "questionnaires",
    encodePathPart(selection.questionnaireId),
    "versions",
    encodePathPart(selection.versionId),
    "questions",
    encodePathPart(selection.questionId),
    resource,
  ].join("/");
}

async function requestApi<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token.trim()) {
    headers.set("Authorization", `Bearer ${token.trim()}`);
  }

  const response = await fetch(`/backend-api${path}`, {
    ...init,
    headers,
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const detail =
      payload && typeof payload.detail === "string"
        ? payload.detail
        : `Request failed with status ${response.status}`;
    throw new ApiError(response.status, detail);
  }

  return payload as T;
}

function formatStatus(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function isComplete(selection: Selection): boolean {
  return Object.values(selection).every((value) => value.trim().length > 0);
}

export default function Home() {
  const [selection, setSelection] = useState<Selection>(initialSelection);
  const [token, setToken] = useState("");
  const [grounding, setGrounding] = useState<GroundingResponse | null>(null);
  const [selectedChunkIds, setSelectedChunkIds] = useState<Set<string>>(
    new Set(),
  );
  const [answer, setAnswer] = useState("");
  const [responseStatus, setResponseStatus] =
    useState<ResponseStatus>("NEEDS_REVIEW");
  const [persistedResponse, setPersistedResponse] =
    useState<PersistedResponse | null>(null);
  const [isGrounding, setIsGrounding] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoadingResponse, setIsLoadingResponse] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedResults = useMemo(
    () =>
      grounding?.results.filter((result) =>
        selectedChunkIds.has(result.candidate.chunk_id),
      ) ?? [],
    [grounding, selectedChunkIds],
  );

  function updateSelection(field: keyof Selection, value: string) {
    setSelection((current) => ({ ...current, [field]: value }));
    setGrounding(null);
    setPersistedResponse(null);
    setSelectedChunkIds(new Set());
    setAnswer("");
    setResponseStatus("NEEDS_REVIEW");
    setNotice("");
    setError("");
  }

  function toggleCitation(chunkId: string) {
    setSelectedChunkIds((current) => {
      const next = new Set(current);
      if (next.has(chunkId)) {
        next.delete(chunkId);
      } else {
        next.add(chunkId);
      }
      return next;
    });
    setNotice("");
  }

  async function runGrounding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    setPersistedResponse(null);

    if (!isComplete(selection)) {
      setError("Enter all four identifiers before running grounding.");
      return;
    }

    setIsGrounding(true);
    try {
      const result = await requestApi<GroundingResponse>(
        resourcePath(selection, "grounding"),
        token,
      );
      setGrounding(result);
      setSelectedChunkIds(new Set());
      setAnswer("");
      setResponseStatus("NEEDS_REVIEW");
      setNotice(
        result.status === "MATCHED"
          ? `${result.results.length} evidence candidate${result.results.length === 1 ? "" : "s"} ready for review.`
          : "Grounding completed without matching evidence.",
      );
    } catch (requestError) {
      setGrounding(null);
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to load deterministic grounding.",
      );
    } finally {
      setIsGrounding(false);
    }
  }

  async function loadResponse() {
    setError("");
    setNotice("");
    if (!isComplete(selection)) {
      setError("Enter all four identifiers before loading a response.");
      return;
    }

    setIsLoadingResponse(true);
    try {
      const result = await requestApi<PersistedResponse>(
        resourcePath(selection, "response"),
        token,
      );
      const revision = result.current_revision;
      setPersistedResponse(result);
      setAnswer(revision.answer ?? "");
      setResponseStatus(revision.status);
      setSelectedChunkIds(
        new Set(
          revision.citations.map((citation) => citation.evidence_chunk_id),
        ),
      );
      setNotice(`Loaded revision ${revision.revision_number}.`);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 404) {
        setPersistedResponse(null);
        setAnswer("");
        setResponseStatus("NEEDS_REVIEW");
        setSelectedChunkIds(new Set());
        setNotice("No persisted response exists for this question yet.");
      } else {
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Unable to load the persisted response.",
        );
      }
    } finally {
      setIsLoadingResponse(false);
    }
  }

  async function executeReview(
    action: "ACCEPT" | "EDIT_AND_APPROVE" | "REJECT",
  ) {
    setError("");
    setNotice("");
    if (!isComplete(selection)) {
      setError("Enter all four identifiers before executing a review action.");
      return;
    }

    setIsSaving(true);
    try {
      await requestApi<{
        response_id: string;
        workspace_id: string;
        questionnaire_id: string;
        questionnaire_version_id: string;
        questionnaire_version_question_id: string;
        action: string;
        is_approved: boolean;
        revision: ResponseRevision;
      }>(resourcePath(selection, "review"), token, {
        method: "POST",
        body: JSON.stringify({
          action,
          edited_answer: answer.trim() || null,
          selected_chunk_ids: [...selectedChunkIds],
        }),
      });

      await loadResponse();
      setNotice(
        action === "ACCEPT"
          ? "Draft approved as response."
          : action === "EDIT_AND_APPROVE"
            ? "Edited response approved."
            : "Draft rejected (status marked for review).",
      );
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to process review action.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function saveResponse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!isComplete(selection)) {
      setError("Enter all four identifiers before saving a response.");
      return;
    }

    setIsSaving(true);
    try {
      const result = await requestApi<PersistedResponse>(
        resourcePath(selection, "response"),
        token,
        {
          method: "PUT",
          body: JSON.stringify({
            answer: answer.trim() || null,
            status: responseStatus,
            citation_chunk_ids: [...selectedChunkIds],
          }),
        },
      );
      setPersistedResponse(result);
      setAnswer(result.current_revision.answer ?? "");
      setResponseStatus(result.current_revision.status);
      setNotice(
        `Revision ${result.current_revision.revision_number} saved with ${result.current_revision.citations.length} citation${result.current_revision.citations.length === 1 ? "" : "s"}.`,
      );
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to save the response.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            EF
          </div>
          <div>
            <p className="brand-name">EvidenceForge</p>
            <p className="brand-context">Evidence-bound review workspace</p>
          </div>
        </div>
        <div className="topbar-title">
          <span className="eyebrow">Phase 1O</span>
          <span>Question Review</span>
        </div>
        <div className="secure-label">
          <span className="secure-dot" aria-hidden="true" />
          Authenticated API boundary
        </div>
      </header>

      <main className="workbench">
        <section className="intro-row" aria-labelledby="page-title">
          <div>
            <p className="eyebrow eyebrow-dark">Review workbench</p>
            <h1 id="page-title">Ground the answer before you write it.</h1>
            <p className="intro-copy">
              Select a question, inspect deterministic evidence, choose the
              citations that support your response, and save an auditable
              revision.
            </p>
          </div>
          <div className="workflow-legend" aria-label="Review workflow">
            <span className="legend-step active">01 Context</span>
            <span className="legend-line" />
            <span className="legend-step">02 Evidence</span>
            <span className="legend-line" />
            <span className="legend-step">03 Response</span>
          </div>
        </section>

        <div className="alert-stack" aria-live="polite">
          {error ? (
            <div className="alert alert-error" role="alert">
              <span className="alert-icon" aria-hidden="true">
                !
              </span>
              <div>
                <strong>Review action could not be completed</strong>
                <p>{error}</p>
              </div>
            </div>
          ) : null}
          {notice ? (
            <div className="alert alert-success" role="status">
              <span className="alert-icon" aria-hidden="true">
                ✓
              </span>
              <p>{notice}</p>
            </div>
          ) : null}
        </div>

        <form className="context-card card" onSubmit={runGrounding}>
          <div className="card-heading">
            <div>
              <p className="section-kicker">Review context</p>
              <h2>Locate the question</h2>
            </div>
            <span className="step-pill">Step 01</span>
          </div>
          <div className="context-grid">
            <label className="field">
              <span>Workspace ID</span>
              <input
                value={selection.workspaceId}
                onChange={(event) =>
                  updateSelection("workspaceId", event.target.value)
                }
                placeholder="Workspace UUID"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span>Questionnaire ID</span>
              <input
                value={selection.questionnaireId}
                onChange={(event) =>
                  updateSelection("questionnaireId", event.target.value)
                }
                placeholder="Questionnaire UUID"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span>Version ID</span>
              <input
                value={selection.versionId}
                onChange={(event) =>
                  updateSelection("versionId", event.target.value)
                }
                placeholder="Version UUID"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span>Question ID</span>
              <input
                value={selection.questionId}
                onChange={(event) =>
                  updateSelection("questionId", event.target.value)
                }
                placeholder="Version question UUID"
                autoComplete="off"
              />
            </label>
          </div>
          <div className="context-actions">
            <label className="token-field">
              <span>Session token</span>
              <input
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="Bearer token, not stored"
                autoComplete="off"
              />
            </label>
            <div className="button-row">
              <button
                className="button button-secondary"
                type="button"
                onClick={loadResponse}
                disabled={isLoadingResponse}
              >
                {isLoadingResponse ? "Loading…" : "Load saved response"}
              </button>
              <button
                className="button button-primary"
                type="submit"
                disabled={isGrounding}
              >
                {isGrounding ? "Grounding…" : "Run grounding"}
                <span aria-hidden="true">→</span>
              </button>
            </div>
          </div>
          <p className="helper-text">
            IDs are sent to the existing authenticated EvidenceForge API. The
            session token stays in this page and is never persisted.
          </p>
        </form>

        <section
          className="question-card card"
          aria-labelledby="question-heading"
        >
          <div className="card-heading compact-heading">
            <div>
              <p className="section-kicker">Question context</p>
              <h2 id="question-heading">
                {grounding?.normalized_query ||
                  "Question text appears after grounding"}
              </h2>
            </div>
            <span className="identity-chip">
              {selection.questionId
                ? `Q · ${selection.questionId}`
                : "Question not selected"}
            </span>
          </div>
          <div className="question-meta">
            <span>
              <b>Questionnaire</b>
              {selection.questionnaireId || "—"}
            </span>
            <span>
              <b>Version</b>
              {selection.versionId || "—"}
            </span>
            <span>
              <b>Grounding search</b>
              {grounding?.search_version || "Not run"}
            </span>
          </div>
        </section>

        <div className="review-grid">
          <section
            className="evidence-card card"
            aria-labelledby="evidence-heading"
          >
            <div className="card-heading">
              <div>
                <p className="section-kicker">Evidence layer</p>
                <div className="heading-with-status">
                  <h2 id="evidence-heading">Ranked evidence</h2>
                  {grounding ? (
                    <span
                      className={`status-badge ${grounding.status === "MATCHED" ? "status-matched" : "status-empty"}`}
                    >
                      {grounding.status === "MATCHED"
                        ? "Matched"
                        : "No matches"}
                    </span>
                  ) : null}
                </div>
              </div>
              <span className="result-count">
                {grounding
                  ? `${grounding.results.length} results`
                  : "Awaiting search"}
              </span>
            </div>

            {!grounding ? (
              <div className="empty-state">
                <div className="empty-icon" aria-hidden="true">
                  ⌕
                </div>
                <h3>Run deterministic grounding</h3>
                <p>
                  Evidence candidates will appear here with their source and
                  byte-level provenance.
                </p>
              </div>
            ) : grounding.status === "NO_MATCHES" ||
              grounding.results.length === 0 ? (
              <div className="empty-state empty-state-small">
                <div className="empty-icon" aria-hidden="true">
                  —
                </div>
                <h3>No evidence matched this question</h3>
                <p>
                  Review the question identifiers or record a response with an
                  appropriate status and no citations.
                </p>
              </div>
            ) : (
              <div className="evidence-list">
                {grounding.results.map((result, index) => {
                  const candidate = result.candidate;
                  const isSelected = selectedChunkIds.has(candidate.chunk_id);
                  return (
                    <label
                      className={`evidence-item ${isSelected ? "evidence-item-selected" : ""}`}
                      key={candidate.chunk_id}
                    >
                      <input
                        className="citation-checkbox"
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleCitation(candidate.chunk_id)}
                        aria-label={`Select evidence chunk ${candidate.chunk_index + 1} as a citation`}
                      />
                      <div className="evidence-item-body">
                        <div className="evidence-item-topline">
                          <span className="rank-number">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="match-label">
                            {result.score} match points
                          </span>
                          {result.exact_phrase_match ? (
                            <span className="phrase-label">Exact phrase</span>
                          ) : null}
                          <span className="chunk-label">
                            Chunk {candidate.chunk_index}
                          </span>
                        </div>
                        <p className="evidence-content">{candidate.content}</p>
                        <div className="evidence-source">
                          <span className="source-document">
                            Document {candidate.document_id}
                          </span>
                          <span>Version {candidate.version_number}</span>
                          {candidate.is_latest_document_version === true ? (
                            <span className="status-badge status-matched">
                              Current v
                              {candidate.document_version_number ??
                                candidate.version_number}
                            </span>
                          ) : candidate.is_latest_document_version === false ? (
                            <span className="status-badge status-empty">
                              Superseded (v
                              {candidate.document_version_number ??
                                candidate.version_number}{" "}
                              of{" "}
                              {candidate.latest_document_version_number ?? "?"})
                            </span>
                          ) : null}
                          {candidate.conflict_group_id ? (
                            <span className="status-badge status-empty">
                              Conflict: {candidate.conflict_group_id}
                            </span>
                          ) : null}
                          <span>
                            Bytes {candidate.normalized_start_byte}–
                            {candidate.normalized_end_byte}
                          </span>
                          {candidate.section_label ? (
                            <span>{candidate.section_label}</span>
                          ) : null}
                          {candidate.page_number !== null ? (
                            <span>Page {candidate.page_number}</span>
                          ) : null}
                        </div>
                        <div className="hash-line">
                          SHA-256 · {candidate.content_hash}
                        </div>
                        <div className="term-line">
                          Matched terms:{" "}
                          {result.matched_terms.length > 0
                            ? result.matched_terms.join(", ")
                            : "phrase context"}
                          {result.occurrence_count > 0
                            ? ` · ${result.occurrence_count} occurrence${result.occurrence_count === 1 ? "" : "s"}`
                            : ""}
                        </div>
                      </div>
                      <span className="select-label">
                        {isSelected ? "Selected" : "Cite"}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </section>

          <aside className="response-column">
            <form className="response-card card" onSubmit={saveResponse}>
              <div className="card-heading">
                <div>
                  <p className="section-kicker">Response layer</p>
                  <h2>Draft your response</h2>
                </div>
                <span className="step-pill">Step 03</span>
              </div>
              <label className="field answer-field">
                <span>Answer</span>
                <textarea
                  value={answer}
                  onChange={(event) => {
                    setAnswer(event.target.value);
                    setNotice("");
                  }}
                  placeholder="Write an evidence-bound answer…"
                  rows={8}
                />
              </label>
              <label className="field">
                <span>Response status</span>
                <select
                  value={responseStatus}
                  onChange={(event) =>
                    setResponseStatus(event.target.value as ResponseStatus)
                  }
                >
                  {responseStatuses.map((status) => (
                    <option key={status} value={status}>
                      {formatStatus(status)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="selection-summary">
                <div className="selection-summary-header">
                  <span>Selected citations</span>
                  <b>{selectedResults.length}</b>
                </div>
                {selectedResults.length > 0 ? (
                  <ul className="selected-list">
                    {selectedResults.map((result) => (
                      <li key={result.candidate.chunk_id}>
                        <span className="selected-check">✓</span>
                        <span>
                          Doc {result.candidate.document_id.slice(0, 8)} · chunk{" "}
                          {result.candidate.chunk_index}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="selection-empty">
                    Select evidence cards to attach provenance.
                  </p>
                )}
              </div>
              <div
                style={{
                  display: "flex",
                  gap: "8px",
                  marginTop: "12px",
                  flexWrap: "wrap",
                }}
              >
                <button
                  className="button button-primary"
                  type="button"
                  disabled={isSaving}
                  onClick={() => executeReview("ACCEPT")}
                >
                  Approve Draft
                </button>
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={isSaving}
                  onClick={() => executeReview("EDIT_AND_APPROVE")}
                >
                  Edit & Approve
                </button>
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={isSaving}
                  onClick={() => executeReview("REJECT")}
                >
                  Reject Draft
                </button>
              </div>
              <button
                className="button button-secondary button-wide"
                type="submit"
                disabled={isSaving}
                style={{ marginTop: "8px" }}
              >
                {isSaving ? "Saving revision…" : "Save custom response"}
                <span aria-hidden="true">↗</span>
              </button>
              <p className="helper-text response-helper">
                Review actions execute server-authorized decisions; saving
                creates an immutable revision.
              </p>
            </form>

            <section
              className="result-card card"
              aria-labelledby="result-heading"
            >
              <div className="card-heading compact-heading">
                <div>
                  <p className="section-kicker">Persisted result</p>
                  <h2 id="result-heading">Revision history</h2>
                </div>
                {persistedResponse ? (
                  <span className="status-badge status-matched">Saved</span>
                ) : null}
              </div>
              {!persistedResponse ? (
                <div className="result-empty">
                  <p>No saved response loaded.</p>
                  <span>
                    Save a draft or use “Load saved response” to inspect the
                    current revision.
                  </span>
                </div>
              ) : (
                <div className="persisted-result">
                  <div className="revision-header">
                    <div>
                      <span className="revision-label">Current revision</span>
                      <strong>
                        #{persistedResponse.current_revision.revision_number}
                      </strong>
                    </div>
                    <span className="status-badge status-neutral">
                      {formatStatus(persistedResponse.current_revision.status)}
                    </span>
                  </div>
                  <p className="persisted-answer">
                    {persistedResponse.current_revision.answer ||
                      "No answer text recorded."}
                  </p>
                  <div className="provenance-heading">
                    <span>Citation provenance</span>
                    <b>{persistedResponse.current_revision.citations.length}</b>
                  </div>
                  {persistedResponse.current_revision.citations.length > 0 ? (
                    <ul className="provenance-list">
                      {persistedResponse.current_revision.citations.map(
                        (citation) => (
                          <li key={citation.id}>
                            <div>
                              <strong>
                                Document {citation.evidence_document_id}
                              </strong>
                              <span>
                                Version {citation.evidence_version_number} ·
                                chunk {citation.evidence_chunk_index}
                              </span>
                            </div>
                            <small>
                              {citation.normalized_start_byte}–
                              {citation.normalized_end_byte} ·{" "}
                              {citation.section_label || "No section"}
                              {citation.page_number !== null
                                ? ` · page ${citation.page_number}`
                                : ""}
                            </small>
                          </li>
                        ),
                      )}
                    </ul>
                  ) : (
                    <p className="selection-empty">
                      This revision has no citations.
                    </p>
                  )}
                  <div className="revision-footnote">
                    Revision saved{" "}
                    {formatDate(persistedResponse.current_revision.created_at)}{" "}
                    · {persistedResponse.revisions.length} total revision
                    {persistedResponse.revisions.length === 1 ? "" : "s"}
                  </div>
                </div>
              )}
            </section>
          </aside>
        </div>
      </main>
      <footer className="page-footer">
        <span>EvidenceForge · deterministic review surface</span>
        <span>Evidence stays authoritative at the API boundary.</span>
      </footer>
    </div>
  );
}
