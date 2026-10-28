/**
 * Shared helpers for the extended guided-review workflow suite
 * (e2e/workflow/*.spec.ts, playwright.workflow.config.ts).
 *
 * Isolation contract: everything below operates against the disposable
 * EF_E2E_DATABASE_URL PostgreSQL catalog and a browser profile that exists
 * only for the test run. Production or developer data is never touched.
 *
 * Identity contract: the suite-owner token is created ONLY by the documented
 * operator bootstrap CLI (see global.setup.ts). A second VIEWER principal is
 * provisioned by `seedViewerPrincipal`, a TEST FIXTURE that inserts user +
 * membership + hashed-token rows directly into the disposable DB mirroring
 * app.auth.hash_token (sha256) :: column-for-column. It exists because the
 * product has no membership-management HTTP endpoint yet (alpha limitation,
 * documented in docs/alpha-verification-2026-10-24.md). Tokens are never
 * fabricated around the API: every test still calls the real routes and the
 * security boundary decides each response.
 */

import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { expect, type Page } from "@playwright/test";

export const API_URL = process.env.EF_E2E_API_URL ?? "http://127.0.0.1:8000";
const STATE_PATH =
  process.env.EF_E2E_WORKFLOW_STATE ?? "/tmp/ef-e2e-workflow-suite-state.json";

export interface SuiteState {
  ownerToken: string;
  workspaceId: string;
  principalSubject: string;
}

export interface WorkflowSelection {
  workspaceId: string;
  questionnaireId: string;
  questionnaireVersionId: string;
  questionnaireQuestionId: string;
}

export const WORKSPACE_NAME_PREFIX = "E2E WF";
export const QUESTION_ONE_TEXT =
  "How often does the duty officer review the alpha gate?";
export const QUESTION_TWO_TEXT =
  "Where is the synthetic incident register stored?";
export const REJECTION_DETAIL_CITATIONS =
  "One or more citation targets are invalid";

export function readSuiteState(): SuiteState {
  return JSON.parse(fs.readFileSync(STATE_PATH, "utf-8"));
}

function headers(token: string | null, extra: Record<string, string> = {}) {
  const h: Record<string, string> = { ...extra };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export interface ApiResult<T> {
  status: number;
  body: T;
}

async function api<T>(
  method: string,
  pathname: string,
  token: string | null,
  body?: unknown,
): Promise<ApiResult<T>> {
  const res = await fetch(`${API_URL}${pathname}`, {
    method,
    headers: headers(token, body ? { "Content-Type": "application/json" } : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let parsed: T;
  try {
    parsed = JSON.parse(text) as T;
  } catch {
    parsed = text as unknown as T;
  }
  return { status: res.status, body: parsed };
}

/** assert 2xx with response payload on failure (real contracts, real evidence). */
export function expectStatus<T>(
  result: ApiResult<T>,
  expected: number | number[],
): void {
  const ok = Array.isArray(expected)
    ? expected.includes(result.status)
    : result.status === expected;
  if (!ok) {
    const detail =
      typeof result.body === "object" &&
      result.body !== null &&
      "detail" in result.body
        ? JSON.stringify((result.body as { detail?: unknown }).detail)
        : typeof result.body === "string"
          ? result.body.slice(0, 300)
          : "";
    throw new Error(
      `Unexpected status ${result.status} (expected ${JSON.stringify(expected)}): ${detail}`,
    );
  }
}

export const apiGet = <T>(pathname: string, token: string | null) =>
  api<T>("GET", pathname, token);
export const apiPost = <T>(
  pathname: string,
  token: string | null,
  body?: unknown,
) => api<T>("POST", pathname, token, body ?? {});
export const apiPut = <T>(
  pathname: string,
  token: string | null,
  body: unknown,
) => api<T>("PUT", pathname, token, body);

export async function createWorkspace(
  ownerToken: string,
  name: string,
): Promise<{ id: string; name: string }> {
  const res = await apiPost<{ id: string; name: string }>(
    "/workspaces",
    ownerToken,
    { name },
  );
  expectStatus(res, 201);
  return res.body;
}

export interface ImportedQuestionnaire {
  name: string;
  questionnaireId: string;
  versionId: string;
  questions: {
    id: string; // questionnaire_version_question_id
    ordinal: number;
    text: string;
  }[];
}

const FIXTURE_DIR = path.join(__dirname, "..", "fixtures");
export const QUESTIONNAIRE_XLSX = path.join(
  FIXTURE_DIR,
  "e2e-synthetic-questionnaire.xlsx",
);
export const EVIDENCE_ONE_TXT = path.join(
  FIXTURE_DIR,
  "e2e-evidence-duty-officer.txt",
);
export const EVIDENCE_TWO_TXT = path.join(
  FIXTURE_DIR,
  "e2e-evidence-incident-register.txt",
);

/** Import the synthetic workbook via multipart PUT (as the UI does). */
export async function importWorkbookApi(
  workspaceId: string,
  ownerToken: string,
): Promise<ImportedQuestionnaire> {
  const form = new FormData();
  form.append(
    "file",
    new File(
      [fs.readFileSync(QUESTIONNAIRE_XLSX)],
      "e2e-synthetic-questionnaire.xlsx",
      {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    ),
  );
  const res = await fetch(
    `${API_URL}/workspaces/${workspaceId}/questionnaires/import`,
    {
      method: "POST",
      headers: headers(ownerToken),
      body: form as unknown as BodyInit,
    },
  );
  const rawText = await res.text();
  const meta = JSON.parse(rawText) as {
    questionnaire_id: string;
    version_id: string;
    questions: {
      id: string;
      ordinal: number;
      normalized_question_text: string;
      question_text?: string;
    }[];
    name: string;
  };
  expectStatus({ status: res.status, body: meta }, 200);
  // Import responses carry question_id HASHES, not the API UUIDs selectors
  // and grounded endpoints need; join with the browser list by ordinal
  // (verified against QuestionnaireVersionQuestionBrowserItem).
  const browser = await apiGet<
    { id: string; ordinal: number; question_text: string }[]
  >(
    `/workspaces/${workspaceId}/questionnaires/${meta.questionnaire_id}/versions/${meta.version_id}/questions`,
    ownerToken,
  );
  expectStatus(browser, 200);
  const byOrdinal = new Map(browser.body.map((b) => [b.ordinal, b]));
  const questions = meta.questions
    .slice()
    .sort((a, b) => a.ordinal - b.ordinal)
    .map((q) => {
      const row = byOrdinal.get(q.ordinal);
      expect(row, `question ordinal ${q.ordinal} persistent`).toBeTruthy();
      return {
        id: row!.id,
        ordinal: q.ordinal,
        text: q.normalized_question_text ?? q.question_text ?? "",
      };
    });
  return {
    name: meta.name,
    questionnaireId: meta.questionnaire_id,
    versionId: meta.version_id,
    questions,
  };
}

/** API-seeded synthetic evidence upload (no UI control exists today — plan §8.3). */
export async function uploadEvidenceApi(
  workspaceId: string,
  ownerToken: string,
  filePath: string,
  name: string,
): Promise<{ documentId: string; versionId: string; name: string }> {
  const created = await apiPost<{ id: string }>(
    `/workspaces/${workspaceId}/documents`,
    ownerToken,
    { name },
  );
  expect(created.status).toBe(201);
  const documentId = created.body.id;

  const form = new FormData();
  form.append(
    "file",
    new File([fs.readFileSync(filePath)], name, { type: "text/plain" }),
  );
  const res = await fetch(
    `${API_URL}/workspaces/${workspaceId}/documents/${documentId}/versions`,
    {
      method: "POST",
      headers: headers(ownerToken),
      body: form as unknown as BodyInit,
    },
  );
  expectStatus({ status: res.status, body: null as null }, [200, 201]);
  const body = (await res.json()) as {
    version_id: string;
    version_number: number;
    chunk_count: number;
  };
  expect(body.chunk_count).toBeGreaterThan(0);
  return { documentId, versionId: body.version_id, name };
}

export interface GroundingCandidate {
  chunk_id: string;
  document_id: string;
  version_id: string;
  version_number: number;
  chunk_index: number;
  content: string;
  content_hash: string;
  section_label: string | null;
  page_number: number | null;
  document_status?: string | null;
}

export interface GroundedQuestionContext {
  selection: WorkflowSelection;
  questionId: string;
  expectedChunkContents: string[];
}

export async function runGroundingApi(
  token: string,
  sel: WorkflowSelection,
): Promise<GroundingCandidate[]> {
  // Grounding is a deterministic read: GET (not POST), verified against
  // app/api/routes/questionnaire_responses.py (_GROUNDING_PATH under @router.get).
  const res = await apiGet<{ results: { candidate: GroundingCandidate }[] }>(
    `/workspaces/${sel.workspaceId}/questionnaires/${sel.questionnaireId}/versions/${sel.questionnaireVersionId}/questions/${sel.questionnaireQuestionId}/grounding`,
    token,
  );
  expect(res.status).toBe(200);
  return res.body.results.map((r) => r.candidate);
}

export async function saveResponseApi(
  token: string,
  sel: WorkflowSelection,
  body: {
    answer: string | null;
    status: string;
    citation_chunk_ids?: string[];
  },
) {
  return apiPut<{ revision: { revision_number: number; status: string } }>(
    `/workspaces/${sel.workspaceId}/questionnaires/${sel.questionnaireId}/versions/${sel.questionnaireVersionId}/questions/${sel.questionnaireQuestionId}/response`,
    token,
    body,
  );
}

export async function getResponseApi(token: string, sel: WorkflowSelection) {
  return apiGet<{
    id: string;
    current_revision: {
      revision_number: number;
      status: string;
      answer: string | null;
      citations: { evidence_chunk_id: string }[];
    } | null;
  }>(
    `/workspaces/${sel.workspaceId}/questionnaires/${sel.questionnaireId}/versions/${sel.questionnaireVersionId}/questions/${sel.questionnaireQuestionId}/response`,
    token,
  );
}

export async function getHistoryApi(token: string, sel: WorkflowSelection) {
  return apiGet<
    { revision_number: number; status: string; answer: string | null }[]
  >(
    `/workspaces/${sel.workspaceId}/questionnaires/${sel.questionnaireId}/versions/${sel.questionnaireVersionId}/questions/${sel.questionnaireQuestionId}/response/history`,
    token,
  );
}

export async function reviewApi(
  token: string | null,
  sel: WorkflowSelection,
  action: string,
  extras: Record<string, unknown> = {},
) {
  return apiPost<{ detail?: string }>(
    `/workspaces/${sel.workspaceId}/questionnaires/${sel.questionnaireId}/versions/${sel.questionnaireVersionId}/questions/${sel.questionnaireQuestionId}/review`,
    token,
    { action, ...extras },
  );
}

export async function listQuestionsApi(
  token: string,
  sel: Pick<
    WorkflowSelection,
    "workspaceId" | "questionnaireId" | "questionnaireVersionId"
  >,
) {
  return apiGet<
    { id: string; ordinal: number; normalized_question_text: string }[]
  >(
    `/workspaces/${sel.workspaceId}/questionnaires/${sel.questionnaireId}/versions/${sel.questionnaireVersionId}/questions`,
    token,
  );
}

export function selectionFor(
  workspaceId: string,
  imported: ImportedQuestionnaire,
  questionId: string,
): WorkflowSelection {
  return {
    workspaceId,
    questionnaireId: imported.questionnaireId,
    questionnaireVersionId: imported.versionId,
    questionnaireQuestionId: questionId,
  };
}

export function questionIdByText(
  imported: ImportedQuestionnaire,
  questionText: string,
): string {
  const target = imported.questions.find((q) => q.text === questionText);
  expect(target, `question '${questionText}' imported`).toBeTruthy();
  return target!.id;
}

/**
 * TEST FIXTURE (see header comment): provision a VIEWER membership directly in
 * the disposable DB, mirroring app.auth.hash_token (sha256) and the model's
 * nullable expires_at / revoked_at semantics. Returns the raw bearer token
 * the tests must use through the REAL API auth boundary.
 *
 * The product exposes no membership-management HTTP endpoint in this alpha
 * (owner path is the bootstrap CLI); the fixture reproduces the owner-side
 * write set verbatim and is explicitly disclosed as fixture provisioning.
 */
/**
 * TEST-FIXTURE ONLY (`workflow suite 8.7/8.8`).
 *
 * The application intentionally exposes no HTTP membership-enrollment route,
 * so a second VIEWER principal cannot be obtained through an authorized API
 * call in this build. This fixture therefore inserts synthetic rows directly
 * into the disposable test database using the production hashing convention
 * (`hash_token` = SHA-256 hex of the raw bearer token) so the seeded token
 * behaves identically to one that the supported bootstrap CLI mints.
 *
 * Guards (fail-closed):
 *  - refuses to run when EF_E2E_DATABASE_URL does not target the dedicated
 *    disposable database named EXACTLY `ef_e2e_wf` on localhost;
 *  - synthetic identities only (`e2e-viewer-*@example.invalid`);
 *  - exists only inside the e2e helper module — no production code imports it.
 *
 * This fixture does NOT prove or replace a production membership endpoint;
 * it only exercises the application's real authorization checks for a
 * VIEWER-role principal in a disposable database.
 */
export function seedViewerPrincipal(
  workspaceId: string,
  username = `e2e-viewer-${Date.now().toString(36)}`,
): {
  token: string;
  userId: string;
} {
  const token = randomBytes(32).toString("base64url");
  const tokenHash = createHash("sha256").update(token).digest("hex");
  const dbUrl = process.env.EF_E2E_DATABASE_URL;
  expect(dbUrl, "EF_E2E_DATABASE_URL set").toBeTruthy();
  // HARD GUARD: this fixture may only ever run against the isolated,
  // disposable database `ef_e2e_wf` bound to localhost. Anything else is
  // refused outright — the suite can never seed identities in a real DB.
  const disposable =
    /^postgresql(\+[a-z]+)?:\/\/[^@]*@(localhost|127\.0\.0\.1)(:\d+)?\/ef_e2e_wf$/.test(
      dbUrl ?? "",
    );
  if (!disposable) {
    throw new Error(
      `seedViewerPrincipal refused: EF_E2E_DATABASE_URL (${dbUrl}) is not the disposable localhost/ef_e2e_wf database`,
    );
  }

  const py = `
import os, uuid
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["EF_E2E_DATABASE_URL"])
with engine.begin() as conn:
    user_id = conn.execute(text(
        "INSERT INTO users (id, external_subject, email, display_name) "
        "VALUES (:id, :s, :e, :d) RETURNING id"),
        {"id": str(uuid.uuid4()), "s": "${username}-subject",
         "e": "${username}@example.invalid", "d": "E2E Viewer Fixture"},
    ).scalar()
    conn.execute(text(
        "INSERT INTO workspace_memberships (id, workspace_id, user_id, role) "
        "VALUES (:id, :w, :u, 'viewer')"),
        {"id": str(uuid.uuid4()), "w": "${workspaceId}", "u": str(user_id)})
    conn.execute(text(
        "INSERT INTO api_tokens (id, user_id, token_hash, name) "
        "VALUES (:id, :u, :h, 'e2e-viewer-fixture')"),
        {"id": str(uuid.uuid4()), "u": str(user_id), "h": "${tokenHash}"})
print(str(user_id))
`;
  const output = execFileSync(
    process.env.EF_E2E_PYTHON ?? "python3",
    ["-c", py],
    {
      env: { ...process.env },
      encoding: "utf-8",
    },
  ).trim();
  return { token, userId: output.split("\n").at(-1) ?? "" };
}

/** UI: connect with the given token through the real login surface. */
export async function connectViaUi(page: Page, token: string): Promise<void> {
  await page.goto("/guided");
  const input = page.getByLabel("Session token");
  await input.fill(token);
  await page.getByRole("button", { name: "Connect" }).click();
}

export async function connectSuiteOwner(page: Page): Promise<void> {
  await connectViaUi(page, readSuiteState().ownerToken);
}

/** Auto-accept confirm dialogs (used by protect-against-loss flows). */
export function acceptDialogs(page: Page): void {
  page.on("dialog", (dialog) => void dialog.accept());
}

const STEP_SECTION = "section[aria-labelledby='question-select-heading']";

/**
 * Step-01 select pickers are structurally addressed: their <label>s wrap both
 * a filter input and a <select>, so implicit association gives them no
 * accessible name (see docs/alpha-verification-2026-10-24.md, product finding).
 * nth: 0 workspace, 1 questionnaire, 2 version, 3 question.
 */
export function stepSelect(page: Page, index: 0 | 1 | 2 | 3) {
  return page.locator(STEP_SECTION).locator("select").nth(index);
}

/**
 * Drive the 4-cascade selectors by stable value (labels carry dynamic
 * suffixes). Asserts the final durable notice instead of transient text.
 */
export async function selectQuestion(
  page: Page,
  sel: WorkflowSelection,
  workspaceName: string,
  questionText: string,
): Promise<void> {
  const workspaceSelect = stepSelect(page, 0);
  await expect(workspaceSelect).toBeEnabled({ timeout: 20_000 });
  await workspaceSelect.selectOption({ value: sel.workspaceId });

  const questionnaireSelect = stepSelect(page, 1);
  await expect(questionnaireSelect).toBeEnabled({ timeout: 20_000 });
  await questionnaireSelect.selectOption({ value: sel.questionnaireId });

  // Wait for the version cascade fetch to land before driving it.
  const versionSelect = stepSelect(page, 2);
  await expect(versionSelect).toBeEnabled({ timeout: 20_000 });
  await expect(versionSelect.locator("option").nth(1)).toBeAttached({
    timeout: 20_000,
  });
  await versionSelect.selectOption({ value: sel.questionnaireVersionId });

  // Wait for the question cascade fetch to land (option count > placeholder).
  const questionSelect = stepSelect(page, 3);
  await expect(questionSelect).toBeEnabled({ timeout: 20_000 });
  await expect(questionSelect.locator("option").nth(1)).toBeAttached({
    timeout: 20_000,
  });
  await questionSelect.selectOption({
    value: sel.questionnaireQuestionId,
  });
  // The selected question's wording renders as the zone heading (h2).
  await expect(page.getByRole("heading", { name: questionText })).toBeVisible();
}

/** Expect a workspace-name-aware option locator (for deterministic assertions). */
export function workspaceOption(page: Page, name: string) {
  return stepSelect(page, 0).locator("option", { hasText: name }).first();
}
