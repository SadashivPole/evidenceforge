"use client";

import {
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  meaningfulTerms,
  searchQuestions,
  type QuestionSearchEntry,
  type QuestionSearchOutcome,
} from "./questionSearch";
import {
  suggestNextQuestion,
  type QuestionResponseSummary,
} from "./suggestion";
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
  rrf_score?: number | null;
  lexical_rank?: number | null;
  semantic_rank?: number | null;
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
type VersionResponseSummary = {
  questionnaire_version_question_id: string;
  current_revision: {
    status: ResponseStatus;
    answer: string | null;
  };
};
type QuestionSearchState = "idle" | "loading" | "results" | "empty" | "error";

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

const QUESTION_QUERY_MIN_LENGTH = 3;
const QUESTION_QUERY_MAX_LENGTH = 500;
const QUESTION_MATCH_LIMIT = 5;
const emptyResponseSummaries: QuestionResponseSummary[] = [];

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

function versionsPath(workspaceId: string, questionnaireId: string) {
  return `/workspaces/${pathPart(workspaceId)}/questionnaires/${pathPart(questionnaireId)}/versions`;
}

function questionsPath(
  workspaceId: string,
  questionnaireId: string,
  versionId: string,
) {
  return `/workspaces/${pathPart(workspaceId)}/questionnaires/${pathPart(questionnaireId)}/versions/${pathPart(versionId)}/questions`;
}

function versionResponsesPath(selection: Selection) {
  return `/workspaces/${pathPart(selection.workspaceId)}/questionnaires/${pathPart(selection.questionnaireId)}/versions/${pathPart(selection.versionId)}/responses`;
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

// Deterministic, effect-free hydration/readiness signal. useSyncExternalStore
// uses the server snapshot (false) during SSR and the hydration render, then
// switches to the client snapshot (true) after hydration commits and React has
// attached this client component's event handlers. Exposing it as the
// `data-hydrated` attribute on the root shell gives browser tests (and any
// consumer) a state-based readiness cue to wait on instead of a fixed sleep.
// It changes no user-facing behavior and leaves the unsaved-work guards intact.
const noopSubscribe = () => () => {};
const clientHydrated = () => true;
const serverHydrated = () => false;

export default function GuidedQuestionReview() {
  const hydrated = useSyncExternalStore(
    noopSubscribe,
    clientHydrated,
    serverHydrated,
  );
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
  const [isLoadingWorkspaceData, setIsLoadingWorkspaceData] = useState(false);
  const [questionQuery, setQuestionQuery] = useState("");
  const [questionSearchState, setQuestionSearchState] =
    useState<QuestionSearchState>("idle");
  const [questionSearchMessage, setQuestionSearchMessage] = useState("");
  const [questionSearchOutcome, setQuestionSearchOutcome] =
    useState<QuestionSearchOutcome | null>(null);
  const [searchIndex, setSearchIndex] = useState<QuestionSearchEntry[]>([]);
  const [searchIndexWorkspaceId, setSearchIndexWorkspaceId] = useState("");
  const [isIndexingQuestions, setIsIndexingQuestions] = useState(false);
  const [responseSummaries, setResponseSummaries] = useState<
    QuestionResponseSummary[]
  >([]);
  const [responseSummariesKey, setResponseSummariesKey] = useState("");
  const [responseStatusesAvailable, setResponseStatusesAvailable] =
    useState(false);
  const [responseStatusWarning, setResponseStatusWarning] = useState("");
  const workspaceEpochRef = useRef(0);
  const searchVersionsRef = useRef<Record<string, VersionOption[]>>({});
  const searchQuestionsByVersionRef = useRef<Record<string, QuestionOption[]>>(
    {},
  );

  const selectedQuestion =
    questions.find((question) => question.id === selection.questionId) ?? null;
  const selectedQuestionnaire =
    questionnaires.find((item) => item.id === selection.questionnaireId) ??
    null;
  const selectedVersion =
    versions.find((item) => item.id === selection.versionId) ?? null;
  const selectedWorkspace =
    workspaces.find((item) => item.id === selection.workspaceId) ?? null;
  const contextWorkspaceName = selectedWorkspace?.name ?? "Selected workspace";
  const contextQuestionnaireName =
    selectedQuestionnaire?.name ?? "Selected questionnaire";
  const contextVersionNumber = selectedVersion?.version_number ?? null;
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

  const currentVersionEntries = useMemo<QuestionSearchEntry[]>(() => {
    if (!isComplete(selection)) return [];
    return questions.map((question) => ({
      ...question,
      workspaceId: selection.workspaceId,
      workspaceName: contextWorkspaceName,
      questionnaireId: selection.questionnaireId,
      questionnaireName: contextQuestionnaireName,
      versionId: selection.versionId,
      versionNumber: contextVersionNumber ?? 0,
      versionSourceFilename: selectedVersion?.source_filename ?? "",
    }));
  }, [
    questions,
    selection,
    contextWorkspaceName,
    contextQuestionnaireName,
    contextVersionNumber,
    selectedVersion,
  ]);
  const activeEntry =
    currentVersionEntries.find((item) => item.id === selection.questionId) ??
    null;
  const versionKey = [
    selection.workspaceId,
    selection.questionnaireId,
    selection.versionId,
  ].join(":");
  const activeResponseStatusAvailable =
    responseStatusesAvailable && responseSummariesKey === versionKey;
  const activeResponseSummaries = activeResponseStatusAvailable
    ? responseSummaries
    : emptyResponseSummaries;
  const activeResponseStatusWarning =
    responseSummariesKey === versionKey ? responseStatusWarning : "";
  const suggestion = useMemo(() => {
    if (!activeEntry || !grounding) return null;
    return suggestNextQuestion({
      current: activeEntry,
      questions: currentVersionEntries,
      responses: activeResponseSummaries,
      responseStatusAvailable: activeResponseStatusAvailable,
      evidenceTexts: grounding.results.map(
        (result) => result.candidate.content,
      ),
    });
  }, [
    activeEntry,
    grounding,
    currentVersionEntries,
    activeResponseSummaries,
    activeResponseStatusAvailable,
  ]);

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

  function resetQuestionDiscovery() {
    setQuestionQuery("");
    setQuestionSearchState("idle");
    setQuestionSearchMessage("");
    setQuestionSearchOutcome(null);
    setSearchIndex([]);
    setSearchIndexWorkspaceId("");
    searchVersionsRef.current = {};
    searchQuestionsByVersionRef.current = {};
    setIsIndexingQuestions(false);
    clearResponseStatuses();
  }

  function clearResponseStatuses() {
    setResponseSummaries([]);
    setResponseSummariesKey("");
    setResponseStatusesAvailable(false);
    setResponseStatusWarning("");
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
        questionsPath(workspaceId, questionnaireId, versionId),
        token,
      );
      setQuestions(result);
      return result;
    } finally {
      setIsLoadingQuestions(false);
    }
  }

  /**
   * Build a workspace-local discovery index from the existing authorized
   * version/question list endpoints. IDs stay in state and URLs; every visible
   * label comes from the questionnaire/version/question records themselves.
   */
  async function loadSearchIndex(
    workspaceId: string,
    items: QuestionnaireOption[],
    workspaceName: string,
  ): Promise<QuestionSearchEntry[]> {
    const epoch = workspaceEpochRef.current;
    const versionLists = await Promise.all(
      items.map(async (item) => ({
        item,
        versions: await requestApi<VersionOption[]>(
          versionsPath(workspaceId, item.id),
          token,
        ),
      })),
    );
    const requests: Array<{
      item: QuestionnaireOption;
      version: VersionOption;
      request: Promise<QuestionOption[]>;
    }> = [];
    for (const { item, versions } of versionLists) {
      for (const version of versions) {
        requests.push({
          item,
          version,
          request: requestApi<QuestionOption[]>(
            questionsPath(workspaceId, item.id, version.id),
            token,
          ),
        });
      }
    }
    const resolved = await Promise.all(
      requests.map(async ({ item, version, request }) => ({
        item,
        version,
        questionRows: await request,
      })),
    );
    if (epoch !== workspaceEpochRef.current) return [];

    const nextVersions: Record<string, VersionOption[]> = {};
    const nextQuestions: Record<string, QuestionOption[]> = {};
    const entries: QuestionSearchEntry[] = [];
    for (const { item, versions } of versionLists) {
      nextVersions[item.id] = versions;
    }
    for (const { item, version, questionRows } of resolved) {
      nextQuestions[version.id] = questionRows;
      for (const question of questionRows) {
        entries.push({
          ...question,
          workspaceId,
          workspaceName,
          questionnaireId: item.id,
          questionnaireName: item.name,
          versionId: version.id,
          versionNumber: version.version_number,
          versionSourceFilename: version.source_filename,
        });
      }
    }
    searchVersionsRef.current = nextVersions;
    searchQuestionsByVersionRef.current = nextQuestions;
    setSearchIndex(entries);
    setSearchIndexWorkspaceId(workspaceId);
    return entries;
  }

  /** Load real saved-response status for the current authorized version. */
  async function loadResponseSummaries(forSelection: Selection) {
    if (
      !forSelection.workspaceId.trim() ||
      !forSelection.questionnaireId.trim() ||
      !forSelection.versionId.trim()
    ) {
      return;
    }
    const key = [
      forSelection.workspaceId,
      forSelection.questionnaireId,
      forSelection.versionId,
    ].join(":");
    const epoch = workspaceEpochRef.current;
    try {
      const result = await requestApi<VersionResponseSummary[]>(
        versionResponsesPath(forSelection),
        token,
      );
      if (epoch !== workspaceEpochRef.current) return;
      setResponseSummaries(
        result.map((item) => ({
          questionId: item.questionnaire_version_question_id,
          status: item.current_revision.status,
          answer: item.current_revision.answer,
        })),
      );
      setResponseSummariesKey(key);
      setResponseStatusesAvailable(true);
      setResponseStatusWarning("");
    } catch {
      if (epoch !== workspaceEpochRef.current) return;
      setResponseSummaries([]);
      setResponseSummariesKey(key);
      setResponseStatusesAvailable(false);
      setResponseStatusWarning(
        "Saved-response status could not be loaded; recommendations use question order and shared terms only.",
      );
    }
  }

  async function askQuestion(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const query = questionQuery.trim();

    function setSearchError(message: string) {
      setQuestionSearchState("error");
      setQuestionSearchMessage(message);
      setQuestionSearchOutcome(null);
    }

    if (!connected || !selection.workspaceId.trim()) {
      setSearchError(
        "Connect to EvidenceForge and choose an authorized workspace first.",
      );
      return;
    }
    if (query.length < QUESTION_QUERY_MIN_LENGTH) {
      setSearchError(
        `Enter at least ${QUESTION_QUERY_MIN_LENGTH} characters describing the question.`,
      );
      return;
    }
    if (query.length > QUESTION_QUERY_MAX_LENGTH) {
      setSearchError(
        `Keep your question under ${QUESTION_QUERY_MAX_LENGTH} characters.`,
      );
      return;
    }
    if (!meaningfulTerms(query).length) {
      setSearchError(
        "Use at least one specific word, such as “encryption”, “backup”, or “access”.",
      );
      return;
    }
    if (isLoadingWorkspaceData || isIndexingQuestions) {
      setSearchError("Workspace data is still loading. Try again in a moment.");
      return;
    }

    const epoch = workspaceEpochRef.current;
    setQuestionSearchState("loading");
    setQuestionSearchMessage("Searching authorized questions…");
    setQuestionSearchOutcome(null);
    setIsIndexingQuestions(true);
    try {
      if (!questionnaires.length) {
        setQuestionSearchState("empty");
        setQuestionSearchMessage(
          "No questionnaires in this workspace yet. Upload an XLSX questionnaire below to get started.",
        );
        return;
      }
      const entries =
        searchIndexWorkspaceId === selection.workspaceId
          ? searchIndex
          : await loadSearchIndex(
              selection.workspaceId,
              questionnaires,
              contextWorkspaceName,
            );
      if (epoch !== workspaceEpochRef.current) return;
      if (!entries.length) {
        setQuestionSearchState("empty");
        setQuestionSearchMessage(
          "No imported questions were found in this workspace's questionnaire versions.",
        );
        return;
      }

      const outcome = searchQuestions(query, entries, QUESTION_MATCH_LIMIT);
      setQuestionSearchOutcome(outcome);
      setQuestionSearchState("results");
      if (outcome.status === "NO_MATCHES") {
        setQuestionSearchMessage(
          "No authorized questionnaire question matched this wording. Try words from the questionnaire, or choose a question below.",
        );
        return;
      }
      if (outcome.status === "AMBIGUOUS") {
        setQuestionSearchMessage(
          `${outcome.matches.length} authorized questions could match. Choose the exact question to ground.`,
        );
        return;
      }

      const best = outcome.matches[0];
      if (!best) {
        setQuestionSearchMessage(
          "No authorized questionnaire question matched this wording.",
        );
        return;
      }
      setQuestionSearchMessage(
        "One authorized question matched. Retrieving its evidence…",
      );
      const explored = await exploreQuestion(best.entry);
      if (epoch !== workspaceEpochRef.current) return;
      setQuestionSearchMessage(
        explored
          ? "Question selected. See the retrieved evidence and suggested next question below."
          : "Match found. Choose “Explore this question” when you are ready.",
      );
    } catch (caught) {
      if (epoch !== workspaceEpochRef.current) return;
      setSearchError(
        caught instanceof Error
          ? caught.message
          : "Unable to load authorized questions.",
      );
    } finally {
      if (epoch === workspaceEpochRef.current) setIsIndexingQuestions(false);
    }
  }

  /** Select a real search-index entry and run the existing grounding flow. */
  async function exploreQuestion(entry: QuestionSearchEntry): Promise<boolean> {
    if (!confirmDiscard()) return false;
    const next: Selection = {
      workspaceId: entry.workspaceId,
      questionnaireId: entry.questionnaireId,
      versionId: entry.versionId,
      questionId: entry.id,
    };
    clearQuestionState();
    setSelection(next);
    const availableVersions = [
      ...(searchVersionsRef.current[entry.questionnaireId] ?? []),
    ];
    if (!availableVersions.some((version) => version.id === entry.versionId)) {
      availableVersions.unshift({
        id: entry.versionId,
        questionnaire_id: entry.questionnaireId,
        version_number: entry.versionNumber,
        source_filename: entry.versionSourceFilename,
        question_count:
          searchQuestionsByVersionRef.current[entry.versionId]?.length ?? 1,
        created_at: "",
      });
    }
    const availableQuestions = [
      ...(searchQuestionsByVersionRef.current[entry.versionId] ?? [entry]),
    ];
    if (!availableQuestions.some((question) => question.id === entry.id)) {
      availableQuestions.unshift(entry);
    }
    setVersions(availableVersions);
    setQuestions(availableQuestions);
    setQuestionnaireFilter("");
    setQuestionFilter("");
    await Promise.all([loadResponse(next, true), loadResponseSummaries(next)]);
    await runGrounding(next, true);
    return true;
  }

  /** Explore a suggested question in the already selected questionnaire version. */
  async function exploreCurrentVersionQuestion(
    questionId: string,
  ): Promise<boolean> {
    if (!confirmDiscard()) return false;
    clearQuestionState();
    const next = { ...selection, questionId };
    setSelection(next);
    setQuestionFilter("");
    await Promise.all([loadResponse(next, true), loadResponseSummaries(next)]);
    await runGrounding(next, true);
    document
      .getElementById("question-heading")
      ?.scrollIntoView({ block: "start" });
    return true;
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
    workspaceEpochRef.current += 1;
    clearQuestionState();
    resetQuestionDiscovery();
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
    if (!workspaceId) {
      setIsLoadingWorkspaceData(false);
      return;
    }
    setIsLoadingWorkspaceData(true);
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
    } finally {
      setIsLoadingWorkspaceData(false);
    }
  }

  async function chooseQuestionnaire(questionnaireId: string) {
    if (!confirmDiscard()) return;
    clearQuestionState();
    clearResponseStatuses();
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
        versionsPath(selection.workspaceId, questionnaireId),
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
    clearResponseStatuses();
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
    await Promise.all([loadResponse(next), loadResponseSummaries(next)]);
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
      workspaceEpochRef.current += 1;
      resetQuestionDiscovery();
      const available = await loadQuestionnaires(selection.workspaceId);
      if (!available.some((item) => item.id === imported.questionnaire_id)) {
        throw new Error(
          "Import completed, but the questionnaire did not appear in the workspace list.",
        );
      }
      const nextVersions = await requestApi<VersionOption[]>(
        versionsPath(selection.workspaceId, imported.questionnaire_id),
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

  async function runGrounding(
    forSelection: Selection = selection,
    skipGuard = false,
  ) {
    setError("");
    setNotice("");
    if (!isComplete(forSelection)) {
      setError(
        "Choose a workspace, questionnaire, version, and question first.",
      );
      return;
    }
    if (!skipGuard && isDirty && !confirmDiscard()) return;
    if (!skipGuard && isDirty) {
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
        resourcePath(forSelection, "grounding"),
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

  async function loadResponse(
    forSelection: Selection = selection,
    skipGuard = false,
  ) {
    if (!skipGuard && isDirty && !confirmDiscard()) return;
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
    <div className={styles.shell} data-hydrated={hydrated ? "true" : "false"}>
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
            <p className={styles.eyebrow}>Ask about your evidence</p>
            <h1>
              Choose a question.
              <br />
              Check the evidence.
              <br />
              Save your answer.
            </h1>
            <p>
              Ask in your own words. Choose the closest question, review its
              supporting evidence, then continue to a related question.
              EvidenceForge retrieves real passages; an authorized reviewer
              decides what supports the response.
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
                  workspaceEpochRef.current += 1;
                  resetQuestionDiscovery();
                  setIsLoadingWorkspaceData(false);
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

        <section className={styles.askCard} aria-labelledby="ask-heading">
          <div className={styles.cardHeading}>
            <div>
              <p className={styles.kicker}>Question match</p>
              <h2 id="ask-heading">Ask about your evidence</h2>
              <p className={styles.help}>
                Type the question naturally. EvidenceForge compares it with
                imported questions in the selected authorized workspace; it does
                not generate an answer or a new question.
              </p>
            </div>
            <span className={styles.pill}>
              Deterministic word matching · no AI answer
            </span>
          </div>
          <form
            className={styles.askForm}
            onSubmit={(event) => void askQuestion(event)}
            aria-busy={questionSearchState === "loading" || isIndexingQuestions}
          >
            <label
              className={styles.askLabel}
              htmlFor="natural-language-question"
            >
              Ask a question in your own words
            </label>
            <div className={styles.askRow}>
              <input
                id="natural-language-question"
                className={styles.askInput}
                type="search"
                value={questionQuery}
                onChange={(event) => setQuestionQuery(event.target.value)}
                placeholder="Ask a question about your controls, policies, or evidence…"
                maxLength={QUESTION_QUERY_MAX_LENGTH}
                aria-describedby={
                  questionSearchState === "error"
                    ? "question-query-help question-search-error"
                    : "question-query-help"
                }
                aria-invalid={questionSearchState === "error"}
                autoComplete="off"
              />
              <button
                className={styles.primary}
                type="submit"
                disabled={
                  !connected ||
                  !selection.workspaceId.trim() ||
                  isLoadingWorkspaceData ||
                  questionSearchState === "loading" ||
                  isIndexingQuestions ||
                  isGrounding ||
                  isSaving
                }
              >
                {questionSearchState === "loading" || isIndexingQuestions
                  ? "Searching authorized questions…"
                  : "Find matching question"}
              </button>
            </div>
            <p id="question-query-help" className={styles.help}>
              Context: {contextWorkspaceName} · {contextQuestionnaireName} ·{" "}
              {contextVersionNumber === null
                ? "select a version below if needed"
                : `Version ${contextVersionNumber}`}
              . Press Enter or use the search button. IDs stay internal.
            </p>
          </form>

          <div
            className={styles.searchStatus}
            role="status"
            aria-label="Question search status"
            aria-live="polite"
            aria-atomic="true"
          >
            {questionSearchMessage}
          </div>

          {questionSearchState === "error" ? (
            <div
              className={styles.error}
              id="question-search-error"
              role="alert"
              aria-label="Question search error"
            >
              <strong>Question search couldn&apos;t be completed</strong>
              <span>{questionSearchMessage}</span>
              <button
                className={styles.secondary}
                type="button"
                onClick={() => void askQuestion()}
                disabled={isIndexingQuestions}
              >
                Try again
              </button>
            </div>
          ) : null}

          {questionSearchState === "empty" ? (
            <div className={styles.empty} role="status">
              <strong>Nothing to match yet</strong>
              <span>{questionSearchMessage}</span>
              <span>
                You can still use the readable questionnaire, version, and
                question selectors below.
              </span>
            </div>
          ) : null}

          {questionSearchState === "results" && questionSearchOutcome ? (
            questionSearchOutcome.status === "NO_MATCHES" ? (
              <div className={styles.empty} role="status">
                <strong>No matching questionnaire question</strong>
                <span>{questionSearchMessage}</span>
                <span>
                  No question was grounded and no evidence or citation was
                  invented.
                </span>
              </div>
            ) : (
              <div className={styles.matchPanel}>
                <div className={styles.matchPanelHeading}>
                  <h3>
                    {questionSearchOutcome.status === "AMBIGUOUS"
                      ? "Several authorized questions could match"
                      : "Closest authorized question"}
                  </h3>
                  <span className={styles.pill}>
                    {questionSearchOutcome.status === "AMBIGUOUS"
                      ? "Choose one to ground"
                      : "Best match found"}
                  </span>
                </div>
                <ul className={styles.matchList}>
                  {questionSearchOutcome.matches.map((match, index) => {
                    const entry = match.entry;
                    const active =
                      selection.questionId === entry.id &&
                      selection.versionId === entry.versionId;
                    return (
                      <li
                        className={`${styles.matchItem} ${active ? styles.matchItemActive : ""}`}
                        key={`${entry.versionId}:${entry.id}`}
                      >
                        <div className={styles.matchItemHeader}>
                          <span className={styles.matchNumber}>
                            Q{entry.ordinal}
                          </span>
                          {index === 0 ? (
                            <span className={styles.tag}>Best match</span>
                          ) : null}
                          {match.exactPhraseMatch ? (
                            <span className={styles.tag}>Exact wording</span>
                          ) : null}
                          {active ? (
                            <span className={styles.connected}>
                              Grounding now
                            </span>
                          ) : null}
                        </div>
                        <h4 className={styles.matchQuestion}>
                          {entry.question_text}
                        </h4>
                        <p className={styles.matchContext}>
                          {entry.questionnaireName} · Version{" "}
                          {entry.versionNumber} ·{" "}
                          {entry.section_path.join(" / ") || entry.sheet_name}
                        </p>
                        {match.matchedTerms.length ? (
                          <p className={styles.matchTerms}>
                            Matched words:{" "}
                            {match.matchedTerms.slice(0, 6).join(", ")}
                          </p>
                        ) : null}
                        <button
                          className={styles.secondary}
                          type="button"
                          onClick={() => void exploreQuestion(entry)}
                          disabled={isGrounding || isSaving}
                        >
                          Explore this question
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <a className={styles.jumpLink} href="#evidence-heading">
                  Jump to retrieved evidence
                </a>
              </div>
            )
          ) : null}
        </section>

        <section
          className={styles.card}
          aria-labelledby="question-select-heading"
        >
          <div className={styles.cardHeading}>
            <div>
              <p className={styles.kicker}>Step 01</p>
              <h2 id="question-select-heading">
                Or choose a question from the imported questionnaire
              </h2>
              <p className={styles.help}>
                The ask box above can select a question for you. This manual
                path remains available and uses readable names and question
                text; IDs stay in application state.
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
              <span>{contextWorkspaceName}</span>
              <span>{contextQuestionnaireName}</span>
              <span>
                Version{" "}
                {activeEntry?.versionNumber ?? contextVersionNumber ?? "—"}
              </span>
              <span>
                {selectedQuestion.section_path.join(" / ") ||
                  selectedQuestion.sheet_name}
              </span>
              <span>Q{selectedQuestion.ordinal}</span>
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
                <p className={styles.kicker}>Retrieved evidence · Step 02</p>
                <h2 id="evidence-heading">
                  Which passages support your answer?
                </h2>
                <p className={styles.help}>
                  These are actual passages returned by the authorized grounding
                  endpoint. Check that each one really supports your answer
                  before citing it.
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
              <div className={styles.empty} role="status">
                <strong>No evidence found for this question</strong>
                <span>
                  No supporting passages were returned, so no citation or answer
                  was fabricated. Check the question and evidence library, or
                  save the answer as needing review.
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
                          {candidate.document_status &&
                          candidate.document_status !== "active" ? (
                            <span>
                              Document {formatStatus(candidate.document_status)}
                            </span>
                          ) : null}
                        </div>
                        <details className={styles.technical}>
                          <summary>Technical details and provenance</summary>
                          <p>
                            Internal identifiers below are for debugging only.
                            Normal review can rely on the source title, excerpt,
                            version, section, page, and the citation checkbox.
                          </p>
                          <p>Chunk ID: {candidate.chunk_id}</p>
                          <p>SHA-256: {candidate.content_hash}</p>
                          <p>
                            Normalized bytes: {candidate.normalized_start_byte}–
                            {candidate.normalized_end_byte}
                          </p>
                          <p>Retrieval score: {result.score}</p>
                          {result.matched_terms.length ? (
                            <p>
                              Matched terms: {result.matched_terms.join(", ")}
                            </p>
                          ) : null}
                          {result.occurrence_count > 0 ? (
                            <p>Occurrences: {result.occurrence_count}</p>
                          ) : null}
                          {result.rrf_score !== null &&
                          result.rrf_score !== undefined ? (
                            <p>Hybrid rank score: {result.rrf_score}</p>
                          ) : null}
                          {result.lexical_rank !== null &&
                          result.lexical_rank !== undefined ? (
                            <p>Lexical rank: {result.lexical_rank}</p>
                          ) : null}
                          {result.semantic_rank !== null &&
                          result.semantic_rank !== undefined ? (
                            <p>Semantic rank: {result.semantic_rank}</p>
                          ) : null}
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

        <section
          className={styles.nextCard}
          aria-labelledby="next-question-heading"
        >
          <div className={styles.cardHeading}>
            <div>
              <p className={styles.kicker}>Suggested next question</p>
              <h2 id="next-question-heading">
                Continue with a real questionnaire question
              </h2>
              <p className={styles.help}>
                Suggestions use imported question order, real saved-response
                status when available, and words shared with the current
                question or actually retrieved evidence. No question is
                generated by a model.
              </p>
            </div>
            <span className={styles.pill}>No model-generated question</span>
          </div>
          {!grounding ? (
            <div className={styles.empty} role="status">
              <strong>Search a question&apos;s evidence first</strong>
              <span>
                Ask a question above or choose one below, then retrieve its
                evidence to see a suggested next question.
              </span>
            </div>
          ) : !suggestion ? (
            <div className={styles.empty} role="status">
              <strong>No suggested next question available</strong>
              <span>
                No later question exists in this authorized questionnaire
                version. You can still choose any question above.
              </span>
            </div>
          ) : (
            <div className={styles.nextBody}>
              <div className={styles.nextBasisRow}>
                <span className={styles.tag}>{suggestion.label}</span>
                <span className={styles.matchContext}>
                  Q{suggestion.question.ordinal} ·{" "}
                  {suggestion.question.section_path.join(" / ") ||
                    suggestion.question.sheet_name}
                </span>
              </div>
              <h3 className={styles.nextQuestion}>
                {suggestion.question.question_text}
              </h3>
              <p className={styles.help}>{suggestion.detail}</p>
              {activeResponseStatusWarning ? (
                <p className={styles.warningText} role="status">
                  {activeResponseStatusWarning}
                </p>
              ) : null}
              {!grounding.results.length ? (
                <p className={styles.help}>
                  No evidence was found for the current question, so this
                  suggestion uses only real questionnaire questions and saved
                  response status. No citation or answer was fabricated.
                </p>
              ) : null}
              {suggestion.sharedTerms.length ? (
                <p className={styles.sharedTerms}>
                  Shared terms: {suggestion.sharedTerms.slice(0, 6).join(", ")}
                </p>
              ) : null}
              <button
                className={styles.primary}
                type="button"
                onClick={() =>
                  void exploreCurrentVersionQuestion(suggestion.question.id)
                }
                disabled={isGrounding || isLoadingResponse || isSaving}
              >
                Explore this question
              </button>
            </div>
          )}
        </section>
      </main>
      <footer className={styles.footer}>
        <span>EvidenceForge · guided review</span>
        <span>Evidence stays authoritative at the API boundary.</span>
      </footer>
    </div>
  );
}
