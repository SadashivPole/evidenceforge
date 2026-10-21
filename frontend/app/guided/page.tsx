"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";
import styles from "./guided.module.css";

type ResponseStatus =
  | "PROPOSED"
  | "APPROVED"
  | "NEEDS_REVIEW"
  | "INSUFFICIENT_EVIDENCE"
  | "STALE_SOURCE"
  | "CONFLICTING_SOURCES"
  | "NOT_APPLICABLE"
  | "DO_NOT_DISCLOSE";

type Selection = {
  workspaceId: string;
  questionnaireId: string;
  versionId: string;
  questionId: string;
};

type WorkspaceOption = { id: string; name: string };
type QuestionnaireOption = {
  id: string;
  name: string;
  latest_version_id: string | null;
  latest_version_number: number | null;
  source_filename: string | null;
  imported_at: string | null;
  question_count: number;
};
type VersionOption = {
  id: string;
  questionnaire_id: string;
  version_number: number;
  source_filename: string;
  question_count: number;
  created_at: string;
};
type QuestionOption = {
  id: string;
  ordinal: number;
  source_row: number;
  sheet_name: string;
  section_path: string[];
  question_text: string;
  source_question_id: string | null;
  stable_question_id: string;
};
type EvidenceDocument = { id: string; name: string; status: string };
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
};
type GroundingResponse = {
  normalized_query: string;
  search_version: string;
  status: "MATCHED" | "NO_MATCHES";
  results: GroundingResult[];
};
type ResponseCitation = {
  id: string;
  evidence_document_id: string;
  evidence_chunk_id: string;
  evidence_version_number: number;
  evidence_chunk_index: number;
  section_label: string | null;
  page_number: number | null;
};
type ResponseRevision = {
  id: string;
  revision_number: number;
  answer: string | null;
  status: ResponseStatus;
  created_at: string;
  citations: ResponseCitation[];
};
type PersistedResponse = {
  id: string;
  current_revision: ResponseRevision;
  revisions: ResponseRevision[];
};
type QuestionnaireImportResult = {
  questionnaire_id: string;
  version_id: string;
  outcome: string;
};

const emptySelection: Selection = {
  workspaceId: "",
  questionnaireId: "",
  versionId: "",
  questionId: "",
};

const statuses: ResponseStatus[] = [
  "NEEDS_REVIEW",
  "PROPOSED",
  "APPROVED",
  "INSUFFICIENT_EVIDENCE",
  "STALE_SOURCE",
  "CONFLICTING_SOURCES",
  "NOT_APPLICABLE",
  "DO_NOT_DISCLOSE",
];

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function pathPart(value: string): string {
  return encodeURIComponent(value.trim());
}

function resourcePath(
  selection: Selection,
  resource: "grounding" | "response" | "review",
) {
  return [
    "/workspaces",
    pathPart(selection.workspaceId),
    "questionnaires",
    pathPart(selection.questionnaireId),
    "versions",
    pathPart(selection.versionId),
    "questions",
    pathPart(selection.questionId),
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
  if (init.body) headers.set("Content-Type", "application/json");
  if (token.trim()) headers.set("Authorization", `Bearer ${token.trim()}`);

  const response = await fetch(`/backend-api${path}`, { ...init, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `Request failed (${response.status})`,
    );
  }
  return payload as T;
}

function formatStatus(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function isComplete(selection: Selection): boolean {
  return Object.values(selection).every((value) => value.trim().length > 0);
}

export default function GuidedQuestionReview() {
  const [token, setToken] = useState("");
  const [connected, setConnected] = useState(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([]);
  const [questionnaires, setQuestionnaires] = useState<QuestionnaireOption[]>(
    [],
  );
  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [questions, setQuestions] = useState<QuestionOption[]>([]);
  const [documents, setDocuments] = useState<EvidenceDocument[]>([]);
  const [selection, setSelection] = useState<Selection>(emptySelection);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [grounding, setGrounding] = useState<GroundingResponse | null>(null);
  const [selectedChunkIds, setSelectedChunkIds] = useState<Set<string>>(
    new Set(),
  );
  const [answer, setAnswer] = useState("");
  const [responseStatus, setResponseStatus] =
    useState<ResponseStatus>("NEEDS_REVIEW");
  const [persistedResponse, setPersistedResponse] =
    useState<PersistedResponse | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isLoadingQuestions, setIsLoadingQuestions] = useState(false);
  const [isGrounding, setIsGrounding] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoadingResponse, setIsLoadingResponse] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [questionnaireFilter, setQuestionnaireFilter] = useState("");
  const [questionFilter, setQuestionFilter] = useState("");

  const selectedQuestion =
    questions.find((question) => question.id === selection.questionId) ?? null;
  const selectedQuestionnaire =
    questionnaires.find((item) => item.id === selection.questionnaireId) ??
    null;
  const selectedVersion =
    versions.find((item) => item.id === selection.versionId) ?? null;
  const selectedResults = useMemo(
    () =>
      grounding?.results.filter((result) =>
        selectedChunkIds.has(result.candidate.chunk_id),
      ) ?? [],
    [grounding, selectedChunkIds],
  );
  const filteredQuestionnaires = useMemo(() => {
    const term = questionnaireFilter.trim().toLowerCase();
    if (!term) return questionnaires;
    return questionnaires.filter(
      (item) =>
        item.id === selection.questionnaireId ||
        item.name.toLowerCase().includes(term) ||
        (item.source_filename ?? "").toLowerCase().includes(term),
    );
  }, [questionnaires, questionnaireFilter, selection.questionnaireId]);
  const filteredQuestions = useMemo(() => {
    const term = questionFilter.trim().toLowerCase();
    if (!term) return questions;
    return questions.filter(
      (item) =>
        item.id === selection.questionId ||
        item.question_text.toLowerCase().includes(term) ||
        (item.source_question_id ?? "").toLowerCase().includes(term) ||
        item.sheet_name.toLowerCase().includes(term) ||
        item.section_path.join(" / ").toLowerCase().includes(term),
    );
  }, [questions, questionFilter, selection.questionId]);

  // Browser-level unsaved-work guard: closing or reloading the tab with a dirty
  // draft must prompt, not silently discard. In-component navigation paths are
  // already guarded by confirmDiscard().
  useEffect(() => {
    if (!isDirty) return undefined;
    function handleBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  function confirmDiscard(): boolean {
    if (!isDirty) return true;
    return window.confirm(
      "You have unsaved changes to your answer, status, or citations. Discard them and continue?",
    );
  }

  function clearQuestionState() {
    setGrounding(null);
    setSelectedChunkIds(new Set());
    setAnswer("");
    setResponseStatus("NEEDS_REVIEW");
    setPersistedResponse(null);
    setIsDirty(false);
    setNotice("");
    setError("");
  }

  async function loadQuestionnaires(
    workspaceId: string,
  ): Promise<QuestionnaireOption[]> {
    const result = await requestApi<QuestionnaireOption[]>(
      `/workspaces/${pathPart(workspaceId)}/questionnaires`,
      token,
    );
    setQuestionnaires(result);
    return result;
  }

  async function loadDocuments(workspaceId: string): Promise<void> {
    const result = await requestApi<EvidenceDocument[]>(
      `/workspaces/${pathPart(workspaceId)}/documents`,
      token,
    );
    setDocuments(result);
  }

  async function loadQuestions(
    workspaceId: string,
    questionnaireId: string,
    versionId: string,
  ): Promise<QuestionOption[]> {
    setIsLoadingQuestions(true);
    try {
      const result = await requestApi<QuestionOption[]>(
        `/workspaces/${pathPart(workspaceId)}/questionnaires/${pathPart(questionnaireId)}/versions/${pathPart(versionId)}/questions`,
        token,
      );
      setQuestions(result);
      return result;
    } finally {
      setIsLoadingQuestions(false);
    }
  }

  async function connectToEvidenceForge() {
    setError("");
    setNotice("");
    if (!token.trim()) {
      setError(
        "Enter your session token to connect. The token is kept only in this page's memory.",
      );
      return;
    }
    setIsConnecting(true);
    try {
      const available = await requestApi<WorkspaceOption[]>(
        "/workspaces",
        token,
      );
      setWorkspaces(available);
      setConnected(true);
      setNotice(
        `Connected. ${available.length} workspace${available.length === 1 ? "" : "s"} available.`,
      );
      if (available.length === 1) await chooseWorkspace(available[0].id, true);
    } catch (caught) {
      setConnected(false);
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not connect to EvidenceForge.",
      );
    } finally {
      setIsConnecting(false);
    }
  }

  async function chooseWorkspace(workspaceId: string, skipGuard = false) {
    if (!skipGuard && !confirmDiscard()) return;
    clearQuestionState();
    setSelection({
      workspaceId,
      questionnaireId: "",
      versionId: "",
      questionId: "",
    });
    setQuestionnaires([]);
    setVersions([]);
    setQuestions([]);
    setDocuments([]);
    setQuestionnaireFilter("");
    setQuestionFilter("");
    if (!workspaceId) return;
    try {
      const [items] = await Promise.all([
        loadQuestionnaires(workspaceId),
        loadDocuments(workspaceId),
      ]);
      setNotice(
        items.length
          ? `Loaded ${items.length} questionnaire${items.length === 1 ? "" : "s"}.`
          : "No questionnaires yet. Upload an XLSX questionnaire to get started.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to load this workspace.",
      );
    }
  }

  async function chooseQuestionnaire(questionnaireId: string) {
    if (!confirmDiscard()) return;
    clearQuestionState();
    setSelection((current) => ({
      ...current,
      questionnaireId,
      versionId: "",
      questionId: "",
    }));
    setVersions([]);
    setQuestions([]);
    setQuestionFilter("");
    if (!questionnaireId || !selection.workspaceId) return;
    try {
      const available = await requestApi<VersionOption[]>(
        `/workspaces/${pathPart(selection.workspaceId)}/questionnaires/${pathPart(questionnaireId)}/versions`,
        token,
      );
      setVersions(available);
      if (!available.length) {
        setNotice("This questionnaire has no imported versions yet.");
        return;
      }
      // The endpoint sorts latest-first. Selecting the questionnaire defaults to latest;
      // after this, the selected version remains explicit until the user changes it.
      const latest = available[0];
      setSelection((current) => ({
        ...current,
        questionnaireId,
        versionId: latest.id,
        questionId: "",
      }));
      await loadQuestions(selection.workspaceId, questionnaireId, latest.id);
      setNotice(
        `Loaded version ${latest.version_number}. Choose the question you want to answer.`,
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to load questionnaire versions.",
      );
    }
  }

  async function chooseVersion(versionId: string) {
    if (!confirmDiscard()) return;
    clearQuestionState();
    setSelection((current) => ({ ...current, versionId, questionId: "" }));
    setQuestions([]);
    setQuestionFilter("");
    if (!versionId || !selection.workspaceId || !selection.questionnaireId)
      return;
    try {
      await loadQuestions(
        selection.workspaceId,
        selection.questionnaireId,
        versionId,
      );
      const version = versions.find((item) => item.id === versionId);
      setNotice(
        version
          ? `Version ${version.version_number} selected. Choose a question.`
          : "Version selected. Choose a question.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to load questions for this version.",
      );
    }
  }

  async function chooseQuestion(questionId: string) {
    if (!confirmDiscard()) return;
    clearQuestionState();
    const next = { ...selection, questionId };
    setSelection(next);
    if (!questionId) return;
    // Load the saved answer automatically; grounding remains an explicit next step.
    await loadResponse(next);
  }

  async function importQuestionnaire() {
    setError("");
    setNotice("");
    if (!selection.workspaceId) {
      setError("Choose a workspace before uploading a questionnaire.");
      return;
    }
    if (!uploadFile) {
      setError("Choose an .xlsx questionnaire file first.");
      return;
    }
    if (!uploadFile.name.toLowerCase().endsWith(".xlsx")) {
      setError("Only .xlsx questionnaire files are supported.");
      return;
    }
    if (!confirmDiscard()) return;

    setIsImporting(true);
    try {
      const form = new FormData();
      form.append("file", uploadFile);
      const response = await fetch(
        `/backend-api/workspaces/${pathPart(selection.workspaceId)}/questionnaires/import`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${token.trim()}`,
          },
          body: form,
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = payload?.detail;
        throw new ApiError(
          response.status,
          typeof detail === "string"
            ? detail
            : detail
              ? JSON.stringify(detail)
              : `Upload failed (${response.status})`,
        );
      }
      const imported = payload as QuestionnaireImportResult;
      const available = await loadQuestionnaires(selection.workspaceId);
      if (!available.some((item) => item.id === imported.questionnaire_id)) {
        throw new Error(
          "Import completed, but the questionnaire did not appear in the workspace list.",
        );
      }
      const nextVersions = await requestApi<VersionOption[]>(
        `/workspaces/${pathPart(selection.workspaceId)}/questionnaires/${pathPart(imported.questionnaire_id)}/versions`,
        token,
      );
      setVersions(nextVersions);
      setQuestions([]);
      setSelection((current) => ({
        ...current,
        questionnaireId: imported.questionnaire_id,
        versionId: imported.version_id,
        questionId: "",
      }));
      const nextQuestions = await loadQuestions(
        selection.workspaceId,
        imported.questionnaire_id,
        imported.version_id,
      );
      setUploadFile(null);
      setNotice(
        `${imported.outcome === "IDENTICAL_DUPLICATE" ? "This identical questionnaire was already imported." : "Questionnaire imported."} ${nextQuestions.length} question${nextQuestions.length === 1 ? "" : "s"} available. Choose a question below.`,
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Questionnaire import failed.",
      );
    } finally {
      setIsImporting(false);
    }
  }

  async function runGrounding() {
    setError("");
    setNotice("");
    if (!isComplete(selection)) {
      setError(
        "Choose a workspace, questionnaire, version, and question first.",
      );
      return;
    }
    if (isDirty && !confirmDiscard()) return;
    if (isDirty) {
      setAnswer(persistedResponse?.current_revision.answer ?? "");
      setResponseStatus(
        persistedResponse?.current_revision.status ?? "NEEDS_REVIEW",
      );
      setSelectedChunkIds(
        new Set(
          persistedResponse?.current_revision.citations.map(
            (citation) => citation.evidence_chunk_id,
          ) ?? [],
        ),
      );
      setIsDirty(false);
    }
    setIsGrounding(true);
    try {
      const result = await requestApi<GroundingResponse>(
        resourcePath(selection, "grounding"),
        token,
      );
      setGrounding(result);
      const resultIds = new Set(
        result.results.map((item) => item.candidate.chunk_id),
      );
      setSelectedChunkIds(
        (current) => new Set([...current].filter((id) => resultIds.has(id))),
      );
      setNotice(
        result.status === "MATCHED"
          ? `${result.results.length} evidence passage${result.results.length === 1 ? "" : "s"} found. Check each passage before citing it.`
          : "No supporting evidence matched this question.",
      );
    } catch (caught) {
      setGrounding(null);
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to find supporting evidence.",
      );
    } finally {
      setIsGrounding(false);
    }
  }

  async function loadResponse(forSelection: Selection = selection) {
    if (isDirty && !confirmDiscard()) return;
    setError("");
    setNotice("");
    if (!isComplete(forSelection)) return;
    setIsLoadingResponse(true);
    try {
      const result = await requestApi<PersistedResponse>(
        resourcePath(forSelection, "response"),
        token,
      );
      setPersistedResponse(result);
      setAnswer(result.current_revision.answer ?? "");
      setResponseStatus(result.current_revision.status);
      setSelectedChunkIds(
        new Set(
          result.current_revision.citations.map(
            (citation) => citation.evidence_chunk_id,
          ),
        ),
      );
      setIsDirty(false);
      setNotice(
        `Loaded saved revision ${result.current_revision.revision_number}. Find supporting evidence to inspect its citations.`,
      );
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setPersistedResponse(null);
        setAnswer("");
        setResponseStatus("NEEDS_REVIEW");
        setSelectedChunkIds(new Set());
        setIsDirty(false);
        setNotice("No saved response exists for this question yet.");
      } else {
        setError(
          caught instanceof Error
            ? caught.message
            : "Unable to load the saved response.",
        );
      }
    } finally {
      setIsLoadingResponse(false);
    }
  }

  function toggleCitation(chunkId: string) {
    setSelectedChunkIds((current) => {
      const next = new Set(current);
      if (next.has(chunkId)) next.delete(chunkId);
      else next.add(chunkId);
      return next;
    });
    setIsDirty(true);
    setNotice("");
  }

  async function saveResponse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!isComplete(selection)) {
      setError("Choose a question before saving a response.");
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
      setIsDirty(false);
      setNotice(
        `Revision ${result.current_revision.revision_number} saved with ${result.current_revision.citations.length} citation${result.current_revision.citations.length === 1 ? "" : "s"}.`,
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to save this response.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function executeReview(
    action: "ACCEPT" | "EDIT_AND_APPROVE" | "REJECT",
  ) {
    setError("");
    setNotice("");
    if (!isComplete(selection)) {
      setError("Choose a question before reviewing a response.");
      return;
    }
    if (
      isDirty &&
      !window.confirm(
        "You have unsaved changes. Continue with the current review action?",
      )
    )
      return;
    setIsSaving(true);
    try {
      await requestApi(resourcePath(selection, "review"), token, {
        method: "POST",
        body: JSON.stringify({
          action,
          edited_answer: answer.trim() || null,
          selected_chunk_ids: [...selectedChunkIds],
        }),
      });
      setIsDirty(false);
      await loadResponse(selection);
      setNotice(
        action === "ACCEPT"
          ? "Response approved."
          : action === "EDIT_AND_APPROVE"
            ? "Edited response approved."
            : "Draft rejected and marked for review.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Review action failed.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.brand}>
          <span className={styles.mark}>EF</span>
          <span>
            <strong>EvidenceForge</strong>
            <small>Evidence-bound review workspace</small>
          </span>
        </div>
        <span className={styles.secure}>
          <span /> Authenticated API boundary
        </span>
      </header>
      <main className={styles.main}>
        <section className={styles.intro}>
          <div>
            <p className={styles.eyebrow}>Guided review</p>
            <h1>
              Choose a question.
              <br />
              Check the evidence.
              <br />
              Save your answer.
            </h1>
            <p>
              EvidenceForge finds relevant passages and lets an authorized
              reviewer decide what supports the response.
            </p>
          </div>
          <div className={styles.steps}>
            <span>01 · Question</span>
            <span>02 · Evidence</span>
            <span>03 · Response</span>
          </div>
        </section>

        <div className={styles.alerts} aria-live="polite">
          {error ? (
            <div className={styles.error} role="alert">
              <strong>We couldn&apos;t complete that action</strong>
              <span>{error}</span>
            </div>
          ) : null}
          {notice ? (
            <div className={styles.notice} role="status">
              {notice}
            </div>
          ) : null}
        </div>

        <section className={styles.card} aria-labelledby="connection-heading">
          <div className={styles.cardHeading}>
            <div>
              <p className={styles.kicker}>Connection</p>
              <h2 id="connection-heading">Connect to EvidenceForge</h2>
              <p className={styles.help}>
                Use your authorized session token. It stays in this page&apos;s
                memory and is not saved to browser storage.
              </p>
            </div>
            <span className={connected ? styles.connected : styles.pill}>
              {connected ? "Connected" : "Not connected"}
            </span>
          </div>
          <div className={styles.connectionRow}>
            <label className={styles.field}>
              <span>Session token</span>
              <input
                type="password"
                value={token}
                onChange={(event) => {
                  if (!confirmDiscard()) return;
                  setToken(event.target.value);
                  setConnected(false);
                  setWorkspaces([]);
                  setQuestionnaires([]);
                  setVersions([]);
                  setQuestions([]);
                  setDocuments([]);
                  setSelection(emptySelection);
                  clearQuestionState();
                }}
                placeholder="Paste your temporary session token"
                autoComplete="off"
              />
            </label>
            <button
              className={styles.primary}
              type="button"
              onClick={() => void connectToEvidenceForge()}
              disabled={isConnecting}
            >
              {isConnecting
                ? "Connecting…"
                : connected
                  ? "Reconnect"
                  : "Connect"}
            </button>
          </div>
        </section>

        <section
          className={styles.card}
          aria-labelledby="question-select-heading"
        >
          <div className={styles.cardHeading}>
            <div>
              <p className={styles.kicker}>Step 01</p>
              <h2 id="question-select-heading">
                Find the question you need to answer
              </h2>
              <p className={styles.help}>
                Choose names and question text. IDs are handled internally by
                EvidenceForge.
              </p>
            </div>
          </div>
          <div className={styles.selectGrid}>
            <label className={styles.field}>
              <span>Team or project</span>
              <select
                value={selection.workspaceId}
                disabled={!connected}
                onChange={(event) => void chooseWorkspace(event.target.value)}
              >
                <option value="">Choose a workspace</option>
                {workspaces.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.field}>
              <span>Questionnaire</span>
              <input
                type="search"
                value={questionnaireFilter}
                placeholder="Filter questionnaires by name or file"
                aria-label="Filter questionnaires"
                disabled={!connected || !selection.workspaceId}
                onChange={(event) => setQuestionnaireFilter(event.target.value)}
              />
              <select
                value={selection.questionnaireId}
                disabled={!connected || !selection.workspaceId}
                onChange={(event) =>
                  void chooseQuestionnaire(event.target.value)
                }
              >
                <option value="">Choose a questionnaire</option>
                {filteredQuestionnaires.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.question_count} question
                    {item.question_count === 1 ? "" : "s"}
                  </option>
                ))}
              </select>
              <small>
                {selectedQuestionnaire?.source_filename
                  ? `Source: ${selectedQuestionnaire.source_filename}`
                  : "Questionnaire names from this workspace"}
              </small>
            </label>
            <label className={styles.field}>
              <span>Version</span>
              <select
                value={selection.versionId}
                disabled={!selection.questionnaireId || versions.length === 0}
                onChange={(event) => void chooseVersion(event.target.value)}
              >
                <option value="">Choose a version</option>
                {versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    Version {item.version_number} · {item.question_count}{" "}
                    questions
                  </option>
                ))}
              </select>
              <small>
                {selectedVersion
                  ? `${selectedVersion.source_filename} · ${formatDate(selectedVersion.created_at)}`
                  : "Latest is selected by default"}
              </small>
            </label>
            <label className={styles.field}>
              <span>Question</span>
              <input
                type="search"
                value={questionFilter}
                placeholder="Filter questions by text, ID, or section"
                aria-label="Filter questions"
                disabled={!selection.versionId || isLoadingQuestions}
                onChange={(event) => setQuestionFilter(event.target.value)}
              />
              <select
                value={selection.questionId}
                disabled={!selection.versionId || isLoadingQuestions}
                onChange={(event) => void chooseQuestion(event.target.value)}
              >
                <option value="">
                  {isLoadingQuestions
                    ? "Loading questions…"
                    : "Choose a question"}
                </option>
                {filteredQuestions.map((item) => (
                  <option key={item.id} value={item.id}>
                    Q{item.ordinal} ·{" "}
                    {item.section_path.join(" / ") || item.sheet_name} ·{" "}
                    {item.question_text}
                  </option>
                ))}
              </select>
              <small>
                {questions.length
                  ? questionFilter.trim()
                    ? `${filteredQuestions.length} of ${questions.length} questions match`
                    : `${questions.length} questions in this version`
                  : "Select a questionnaire version first"}
              </small>
            </label>
          </div>
          <div className={styles.uploadRow}>
            <label className={styles.fileField}>
              <span>Need to add a questionnaire?</span>
              <input
                key={uploadFile ? uploadFile.name : "empty"}
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                disabled={!connected || !selection.workspaceId || isImporting}
                onChange={(event) =>
                  setUploadFile(event.target.files?.[0] ?? null)
                }
              />
            </label>
            <button
              className={styles.secondary}
              type="button"
              onClick={() => void importQuestionnaire()}
              disabled={
                !connected ||
                !selection.workspaceId ||
                !uploadFile ||
                isImporting
              }
            >
              {isImporting ? "Uploading…" : "Upload questionnaire"}
            </button>
          </div>
          <div className={styles.actions}>
            <button
              className={styles.primary}
              type="button"
              onClick={() => void runGrounding()}
              disabled={!connected || !isComplete(selection) || isGrounding}
            >
              {isGrounding ? "Finding evidence…" : "Find supporting evidence"}{" "}
              <span aria-hidden="true">→</span>
            </button>
            <button
              className={styles.secondary}
              type="button"
              onClick={() => void loadResponse()}
              disabled={
                !connected || !isComplete(selection) || isLoadingResponse
              }
            >
              {isLoadingResponse ? "Loading…" : "Load saved response"}
            </button>
          </div>
          <details className={styles.advanced}>
            <summary>Advanced developer settings</summary>
            <p className={styles.help}>
              Use only for debugging. Normal review should use the selectors
              above.
            </p>
            <div className={styles.selectGrid}>
              {(
                [
                  "workspaceId",
                  "questionnaireId",
                  "versionId",
                  "questionId",
                ] as const
              ).map((field) => (
                <label className={styles.field} key={field}>
                  <span>
                    {field === "workspaceId"
                      ? "Workspace UUID"
                      : field === "questionnaireId"
                        ? "Questionnaire UUID"
                        : field === "versionId"
                          ? "Questionnaire version UUID"
                          : "Version-question UUID"}
                  </span>
                  <input
                    value={selection[field]}
                    onChange={(event) => {
                      if (!confirmDiscard()) return;
                      clearQuestionState();
                      setSelection((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }));
                    }}
                    autoComplete="off"
                  />
                </label>
              ))}
            </div>
          </details>
        </section>

        <section
          className={styles.questionCard}
          aria-labelledby="question-heading"
        >
          <p className={styles.kicker}>Your selected question</p>
          <h2 id="question-heading">
            {selectedQuestion?.question_text ??
              "Choose a question above to see its wording here."}
          </h2>
          {selectedQuestion ? (
            <div className={styles.questionMeta}>
              <span>
                {selectedQuestion.section_path.join(" / ") ||
                  selectedQuestion.sheet_name}
              </span>
              <span>Q{selectedQuestion.ordinal}</span>
              <span>Version {selectedVersion?.version_number ?? "—"}</span>
              <span>
                {grounding?.search_version ?? "Evidence not searched yet"}
              </span>
            </div>
          ) : (
            <p className={styles.help}>
              The imported wording is shown before evidence search so you can
              verify you selected the right question.
            </p>
          )}
        </section>

        <div className={styles.reviewGrid}>
          <section className={styles.card} aria-labelledby="evidence-heading">
            <div className={styles.cardHeading}>
              <div>
                <p className={styles.kicker}>Step 02 · Evidence</p>
                <h2 id="evidence-heading">
                  Which passages support your answer?
                </h2>
                <p className={styles.help}>
                  These passages may be relevant. Check that each one actually
                  supports your answer.
                </p>
              </div>
              {grounding ? (
                <span
                  className={
                    grounding.status === "MATCHED"
                      ? styles.connected
                      : styles.pill
                  }
                >
                  {grounding.status === "MATCHED"
                    ? "Matches found"
                    : "No matches"}
                </span>
              ) : null}
            </div>
            {!grounding ? (
              <div className={styles.empty}>
                <strong>Evidence has not been searched yet</strong>
                <span>
                  Choose a question, then select “Find supporting evidence.”
                </span>
              </div>
            ) : grounding.results.length === 0 ? (
              <div className={styles.empty}>
                <strong>No supporting passages found</strong>
                <span>
                  Do not guess. Check the question and evidence library, or save
                  the answer as needing review.
                </span>
              </div>
            ) : (
              <div className={styles.evidenceList}>
                {grounding.results.map((result, index) => {
                  const candidate = result.candidate;
                  const selected = selectedChunkIds.has(candidate.chunk_id);
                  const document = documents.find(
                    (item) => item.id === candidate.document_id,
                  );
                  return (
                    <label
                      className={`${styles.evidenceItem} ${selected ? styles.evidenceSelected : ""}`}
                      key={candidate.chunk_id}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggleCitation(candidate.chunk_id)}
                        aria-label={`Use passage ${index + 1} as a source`}
                      />
                      <div className={styles.evidenceBody}>
                        <div className={styles.evidenceTopline}>
                          <strong>Passage {index + 1}</strong>
                          {result.exact_phrase_match ? (
                            <span className={styles.tag}>Exact phrase</span>
                          ) : null}
                          {candidate.is_latest_document_version === false ? (
                            <span className={styles.warningTag}>
                              Superseded version
                            </span>
                          ) : null}
                          {candidate.conflict_group_id ? (
                            <span className={styles.warningTag}>
                              Conflicting sources
                            </span>
                          ) : null}
                        </div>
                        <p className={styles.excerpt}>{candidate.content}</p>
                        <div className={styles.evidenceMeta}>
                          <span>{document?.name ?? "Evidence document"}</span>
                          <span>Version {candidate.version_number}</span>
                          {candidate.section_label ? (
                            <span>{candidate.section_label}</span>
                          ) : null}
                          {candidate.page_number !== null ? (
                            <span>Page {candidate.page_number}</span>
                          ) : null}
                        </div>
                        <details className={styles.technical}>
                          <summary>Technical details and provenance</summary>
                          <p>Chunk ID: {candidate.chunk_id}</p>
                          <p>SHA-256: {candidate.content_hash}</p>
                          <p>
                            Normalized bytes: {candidate.normalized_start_byte}–
                            {candidate.normalized_end_byte}
                          </p>
                          <p>
                            Retrieval score: {result.score} · Matched terms:{" "}
                            {result.matched_terms.join(", ") ||
                              "phrase context"}
                          </p>
                        </details>
                      </div>
                      <span className={styles.useLabel}>
                        {selected ? "Selected source" : "Use as source"}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </section>

          <aside className={styles.rightColumn}>
            <form className={styles.card} onSubmit={saveResponse}>
              <div className={styles.cardHeading}>
                <div>
                  <p className={styles.kicker}>Step 03 · Response</p>
                  <h2>What answer can you support with these sources?</h2>
                </div>
              </div>
              <label className={styles.field}>
                <span>Your answer</span>
                <textarea
                  value={answer}
                  onChange={(event) => {
                    setAnswer(event.target.value);
                    setIsDirty(true);
                    setNotice("");
                  }}
                  placeholder="Write what the evidence supports. It's okay to be unsure."
                  rows={7}
                />
              </label>
              <label className={styles.field}>
                <span>Response status</span>
                <select
                  value={responseStatus}
                  onChange={(event) => {
                    setResponseStatus(event.target.value as ResponseStatus);
                    setIsDirty(true);
                  }}
                >
                  {statuses.map((item) => (
                    <option key={item} value={item}>
                      {formatStatus(item)}
                    </option>
                  ))}
                </select>
              </label>
              <div className={styles.selectionSummary}>
                <div>
                  <span>Selected sources</span>
                  <strong>{selectedChunkIds.size}</strong>
                </div>
                {selectedResults.length ? (
                  <ul>
                    {selectedResults.map((item) => (
                      <li key={item.candidate.chunk_id}>
                        {documents.find(
                          (doc) => doc.id === item.candidate.document_id,
                        )?.name ?? "Evidence document"}{" "}
                        · chunk {item.candidate.chunk_index}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>
                    Select the passages that truly support your answer.
                    Retrieval is not proof of compliance.
                  </p>
                )}
              </div>
              {isDirty ? (
                <p className={styles.dirtyNotice}>You have unsaved changes.</p>
              ) : null}
              <button
                className={styles.primaryWide}
                type="submit"
                disabled={isSaving || !connected || !isComplete(selection)}
              >
                {isSaving ? "Saving…" : "Save for review"}
              </button>
              <div className={styles.actionsVertical}>
                <button
                  className={styles.secondary}
                  type="button"
                  disabled={isSaving || !connected || !isComplete(selection)}
                  onClick={() => void executeReview("ACCEPT")}
                >
                  Approve response
                </button>
                <button
                  className={styles.secondary}
                  type="button"
                  disabled={isSaving || !connected || !isComplete(selection)}
                  onClick={() => void executeReview("EDIT_AND_APPROVE")}
                >
                  Edit and approve
                </button>
                <button
                  className={styles.secondary}
                  type="button"
                  disabled={isSaving || !connected || !isComplete(selection)}
                  onClick={() => void executeReview("REJECT")}
                >
                  Reject draft
                </button>
              </div>
              <p className={styles.help}>
                Approval remains an explicit, server-authorized action. “Not
                sure” is a valid reason to save for review.
              </p>
            </form>

            <section className={styles.card} aria-labelledby="history-heading">
              <div className={styles.cardHeading}>
                <div>
                  <p className={styles.kicker}>Persisted result</p>
                  <h2 id="history-heading">Revision history</h2>
                </div>
                {persistedResponse ? (
                  <span className={styles.connected}>Saved</span>
                ) : null}
              </div>
              {!persistedResponse ? (
                <div className={styles.empty}>
                  <span>No saved response loaded.</span>
                </div>
              ) : (
                <div>
                  <div className={styles.revisionTop}>
                    <strong>
                      Revision{" "}
                      {persistedResponse.current_revision.revision_number}
                    </strong>
                    <span className={styles.pill}>
                      {formatStatus(persistedResponse.current_revision.status)}
                    </span>
                  </div>
                  <p className={styles.persistedAnswer}>
                    {persistedResponse.current_revision.answer ||
                      "No answer text recorded."}
                  </p>
                  <p className={styles.help}>
                    Saved{" "}
                    {formatDate(persistedResponse.current_revision.created_at)}{" "}
                    · {persistedResponse.revisions.length} revision
                    {persistedResponse.revisions.length === 1 ? "" : "s"}
                  </p>
                  <h3 className={styles.citationHeading}>
                    Citation provenance (
                    {persistedResponse.current_revision.citations.length})
                  </h3>
                  {persistedResponse.current_revision.citations.length ? (
                    <ul className={styles.citationList}>
                      {persistedResponse.current_revision.citations.map(
                        (citation) => (
                          <li key={citation.id}>
                            {documents.find(
                              (doc) => doc.id === citation.evidence_document_id,
                            )?.name ?? "Evidence document"}{" "}
                            · version {citation.evidence_version_number} · chunk{" "}
                            {citation.evidence_chunk_index}
                          </li>
                        ),
                      )}
                    </ul>
                  ) : (
                    <p className={styles.help}>
                      This revision has no citations.
                    </p>
                  )}
                </div>
              )}
            </section>
          </aside>
        </div>
      </main>
      <footer className={styles.footer}>
        <span>EvidenceForge · guided review</span>
        <span>Evidence stays authoritative at the API boundary.</span>
      </footer>
    </div>
  );
}
