import { expect, test, type Page } from "@playwright/test";

import {
  findQuestionMatches,
  searchQuestions,
  type QuestionSearchEntry,
} from "../app/guided/questionSearch";
import {
  suggestNextQuestion,
  type QuestionResponseSummary,
} from "../app/guided/suggestion";

/**
 * Question-first UX contract tests.
 *
 * These tests use synthetic, browser-local API responses. They deliberately do
 * not require a real database, token, or evidence document. Everything the UI
 * sees still arrives through the same `/backend-api/workspaces/...` paths and
 * bearer-token header as the production guided page, so authorization scoping
 * can be asserted without touching any user data.
 */

const TOKEN = "synthetic-browser-token";
const WS_A = "11111111-1111-4111-8111-111111111111";
const WS_B = "22222222-2222-4222-8222-222222222222";
const QUESTIONNAIRE_ID = "33333333-3333-4333-8333-333333333333";
const VERSION_ID = "44444444-4444-4444-8444-444444444444";
const DOCUMENT_ID = "66666666-6666-4666-8666-666666666666";
const CHUNK_ID = "77777777-7777-4777-8777-777777777777";
const Q1_ID = "55555555-5555-4555-8555-555555555551";
const Q2_ID = "55555555-5555-4555-8555-555555555552";
const Q3_ID = "55555555-5555-4555-8555-555555555553";
const Q4_ID = "55555555-5555-4555-8555-555555555554";

const WORKSPACE_A = {
  id: WS_A,
  name: "Synthetic Review Workspace",
  created_by_user_id: "88888888-8888-4888-8888-888888888888",
  created_at: "2026-10-27T00:00:00Z",
  updated_at: "2026-10-27T00:00:00Z",
};
const WORKSPACE_B = {
  id: WS_B,
  name: "Synthetic Empty Workspace",
  created_by_user_id: "88888888-8888-4888-8888-888888888888",
  created_at: "2026-10-27T00:00:00Z",
  updated_at: "2026-10-27T00:00:00Z",
};
const QUESTIONNAIRE = {
  id: QUESTIONNAIRE_ID,
  name: "Synthetic Security Questionnaire",
  created_at: "2026-10-27T00:00:00Z",
  latest_version_id: VERSION_ID,
  latest_version_number: 2,
  source_filename: "synthetic-security-questionnaire.xlsx",
  imported_at: "2026-10-27T00:00:00Z",
  question_count: 4,
};
const VERSION = {
  id: VERSION_ID,
  questionnaire_id: QUESTIONNAIRE_ID,
  version_number: 2,
  source_filename: "synthetic-security-questionnaire.xlsx",
  question_count: 4,
  created_at: "2026-10-27T00:00:00Z",
};
const DOCUMENTS = [
  {
    id: DOCUMENT_ID,
    workspace_id: WS_A,
    name: "Synthetic Security Policy.pdf",
    source_type: "pdf",
    status: "active",
    created_by_user_id: "88888888-8888-4888-8888-888888888888",
    created_at: "2026-10-27T00:00:00Z",
    updated_at: "2026-10-27T00:00:00Z",
  },
];

const Q1_TEXT = "Are backups encrypted with approved keys?";
const Q2_TEXT = "How often is logical access reviewed?";
const Q3_TEXT = "Where are access review records stored?";
const Q4_TEXT = "How often are encryption keys rotated by the duty officer?";

function makeQuestion(
  id: string,
  ordinal: number,
  questionText: string,
  sectionPath: string[],
) {
  return {
    id,
    ordinal,
    source_row: ordinal + 1,
    sheet_name: "Security controls",
    section_path: sectionPath,
    question_text: questionText,
    source_question_id: `SC-${ordinal}`,
    stable_question_id: `stable-${ordinal}`,
  };
}

const QUESTIONS = [
  makeQuestion(Q1_ID, 1, Q1_TEXT, ["Data protection"]),
  makeQuestion(Q2_ID, 2, Q2_TEXT, ["Access control"]),
  makeQuestion(Q3_ID, 3, Q3_TEXT, ["Access control"]),
  makeQuestion(Q4_ID, 4, Q4_TEXT, ["Cryptography"]),
];

const EVIDENCE_EXCERPT =
  "Backups are encrypted with approved keys. Encryption keys are rotated every 90 days by the duty officer.";

function groundingBody(status: "MATCHED" | "NO_MATCHES", results: unknown[]) {
  return {
    workspace_id: WS_A,
    questionnaire_id: QUESTIONNAIRE_ID,
    questionnaire_version_id: VERSION_ID,
    questionnaire_version_question_id: Q1_ID,
    normalized_query: Q1_TEXT,
    search_version: "synthetic-lexical-v1",
    result_limit: 5,
    status,
    results,
    citations: [],
  };
}

function evidenceResult() {
  return {
    candidate: {
      chunk_id: CHUNK_ID,
      document_id: DOCUMENT_ID,
      version_id: "99999999-9999-4999-8999-999999999999",
      version_number: 3,
      chunk_index: 0,
      content: EVIDENCE_EXCERPT,
      content_hash: "synthetic-sha256-abcdef",
      normalized_start_byte: 0,
      normalized_end_byte: 98,
      section_label: "Encryption controls",
      page_number: 7,
      document_status: "active",
      document_version_number: 3,
      latest_document_version_number: 3,
      is_latest_document_version: true,
      conflict_group_id: null,
    },
    score: 8.5,
    matched_terms: ["backup", "encrypt", "key"],
    exact_phrase_match: false,
    occurrence_count: 2,
    rrf_score: 0.032,
    lexical_rank: 1,
    semantic_rank: null,
  };
}

function responseSummary(
  questionId: string,
  status: string,
  answer: string | null,
) {
  return {
    id: `response-${questionId}`,
    workspace_id: WS_A,
    created_by_user_id: "88888888-8888-4888-8888-888888888888",
    questionnaire_id: QUESTIONNAIRE_ID,
    questionnaire_version_id: VERSION_ID,
    questionnaire_version_question_id: questionId,
    current_revision: {
      id: `revision-${questionId}`,
      response_id: `response-${questionId}`,
      revision_number: 1,
      answer,
      status,
      author_user_id: null,
      created_at: "2026-10-27T00:00:00Z",
      citations: [],
    },
  };
}

type ApiCall = { path: string; method: string; authorization: string };

type MockApiOptions = {
  workspaces?: unknown[];
  questionnairesByWorkspace?: Record<string, unknown[]>;
  documentsByWorkspace?: Record<string, unknown[]>;
  questions?: unknown[];
  responses?: unknown[];
  groundingStatus?: "MATCHED" | "NO_MATCHES";
  groundingResults?: unknown[];
  failVersions?: boolean;
  failQuestions?: boolean;
  searchDelayMs?: number;
};

async function installMockApi(page: Page, options: MockApiOptions = {}) {
  const calls: ApiCall[] = [];
  const workspaces = options.workspaces ?? [WORKSPACE_A];
  const questionnairesByWorkspace = options.questionnairesByWorkspace ?? {
    [WS_A]: [QUESTIONNAIRE],
  };
  const documentsByWorkspace = options.documentsByWorkspace ?? {
    [WS_A]: DOCUMENTS,
  };
  const questions = options.questions ?? QUESTIONS;
  const responses = options.responses ?? [];
  const groundingStatus = options.groundingStatus ?? "MATCHED";
  const groundingResults = options.groundingResults ?? [evidenceResult()];

  await page.route("**/backend-api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/backend-api/, "");
    calls.push({
      path,
      method: request.method(),
      authorization: request.headers()["authorization"] ?? "",
    });

    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    if (path === "/workspaces") {
      await json(workspaces);
      return;
    }
    const documentsMatch = path.match(/^\/workspaces\/([^/]+)\/documents$/);
    if (documentsMatch) {
      await json(documentsByWorkspace[documentsMatch[1]] ?? []);
      return;
    }
    const questionnaireListMatch = path.match(
      /^\/workspaces\/([^/]+)\/questionnaires$/,
    );
    if (questionnaireListMatch) {
      await json(questionnairesByWorkspace[questionnaireListMatch[1]] ?? []);
      return;
    }
    const versionsMatch = path.match(
      /^\/workspaces\/([^/]+)\/questionnaires\/([^/]+)\/versions$/,
    );
    if (versionsMatch) {
      if (options.failVersions) {
        await json({ detail: "Synthetic versions API failure" }, 500);
        return;
      }
      if (options.searchDelayMs) {
        await new Promise((resolve) =>
          setTimeout(resolve, options.searchDelayMs),
        );
      }
      await json([VERSION]);
      return;
    }
    const questionsMatch = path.match(
      /^\/workspaces\/([^/]+)\/questionnaires\/([^/]+)\/versions\/([^/]+)\/questions$/,
    );
    if (questionsMatch) {
      if (options.failQuestions) {
        await json({ detail: "Synthetic questions API failure" }, 500);
        return;
      }
      if (options.searchDelayMs) {
        await new Promise((resolve) =>
          setTimeout(resolve, options.searchDelayMs),
        );
      }
      await json(questions);
      return;
    }
    const responsesMatch = path.match(
      /^\/workspaces\/([^/]+)\/questionnaires\/([^/]+)\/versions\/([^/]+)\/responses$/,
    );
    if (responsesMatch) {
      await json(responses);
      return;
    }
    const groundingMatch = path.match(
      /^\/workspaces\/([^/]+)\/questionnaires\/([^/]+)\/versions\/([^/]+)\/questions\/([^/]+)\/grounding$/,
    );
    if (groundingMatch) {
      await json(groundingBody(groundingStatus, groundingResults));
      return;
    }
    const responseMatch = path.match(
      /^\/workspaces\/([^/]+)\/questionnaires\/([^/]+)\/versions\/([^/]+)\/questions\/([^/]+)\/response$/,
    );
    if (responseMatch) {
      await json({ detail: "Questionnaire response not found" }, 404);
      return;
    }
    await json({ detail: `Unmocked synthetic endpoint: ${path}` }, 404);
  });

  return { calls };
}

async function connect(page: Page, workspaceId?: string) {
  await page.goto("/guided", { waitUntil: "domcontentloaded" });
  // React attaches this client component's handlers only after hydration.
  // Filling the controlled "Session token" input before then loses the value
  // (it is reset to "" on the hydration commit), so Connect sees an empty token
  // and shows the validation error. Wait on the page's explicit, deterministic
  // readiness state before any interaction -- a state-based wait, not a fixed
  // sleep. The connection assertion below is intentionally unchanged.
  await page
    .locator('[data-hydrated="true"]')
    .waitFor({ state: "attached", timeout: 15_000 });
  await page.getByLabel("Session token").fill(TOKEN);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByText("Connected", { exact: true }).first(),
  ).toBeVisible();

  if (workspaceId) {
    const workspaceSelect = page
      .locator("section[aria-labelledby='question-select-heading'] select")
      .first();
    await expect(workspaceSelect).toBeEnabled();
    await workspaceSelect.selectOption(workspaceId);
  }
  await expect(
    page.getByRole("button", { name: "Find matching question" }),
  ).toBeEnabled();
}

async function ask(page: Page, query: string) {
  const input = page.getByLabel("Ask a question in your own words");
  await input.fill(query);
  await input.press("Enter");
}

function groundingCalls(calls: ApiCall[]) {
  return calls.filter((call) => call.path.includes("/grounding"));
}

function makeEntry(
  id: string,
  ordinal: number,
  questionText: string,
): QuestionSearchEntry {
  return {
    ...makeQuestion(id, ordinal, questionText, ["Synthetic section"]),
    workspaceId: WS_A,
    workspaceName: WORKSPACE_A.name,
    questionnaireId: QUESTIONNAIRE_ID,
    questionnaireName: QUESTIONNAIRE.name,
    versionId: VERSION_ID,
    versionNumber: 2,
    versionSourceFilename: VERSION.source_filename,
  };
}

test.describe("deterministic question discovery helpers", () => {
  const entries = [
    makeEntry(Q1_ID, 1, Q1_TEXT),
    makeEntry(Q2_ID, 2, Q2_TEXT),
    makeEntry(Q3_ID, 3, Q3_TEXT),
    makeEntry(Q4_ID, 4, Q4_TEXT),
  ];

  test("finds an ordinary paraphrase without a model", () => {
    const matches = findQuestionMatches("Do you encrypt backups?", entries);
    expect(matches.length).toBeGreaterThan(0);
    expect(matches[0].entry.id).toBe(Q1_ID);
    expect(matches[0].matchedTerms).toContain("encrypt");
    expect(matches[0].matchedTerms).toContain("backup");
  });

  test("unrelated wording has no match and ambiguous wording stays ambiguous", () => {
    expect(
      searchQuestions("What is the capital of France?", entries).status,
    ).toBe("NO_MATCHES");
    const ambiguous = searchQuestions("How is access reviewed?", entries);
    expect(ambiguous.status).toBe("AMBIGUOUS");
    expect(ambiguous.matches.length).toBeGreaterThanOrEqual(2);
  });

  test("next-question basis uses response status, shared terms, then order", () => {
    const current = entries[0];
    const unfinished: QuestionResponseSummary[] = [
      {
        questionId: Q2_ID,
        status: "APPROVED",
        answer: "Reviewed quarterly.",
      },
      { questionId: Q3_ID, status: "NEEDS_REVIEW", answer: null },
    ];
    const byStatus = suggestNextQuestion({
      current,
      questions: entries,
      responses: unfinished,
      responseStatusAvailable: true,
      evidenceTexts: [EVIDENCE_EXCERPT],
    });
    expect(byStatus?.question.id).toBe(Q3_ID);
    expect(byStatus?.basis).toBe("UNFINISHED");

    const allApproved: QuestionResponseSummary[] = [
      { questionId: Q2_ID, status: "APPROVED", answer: "Quarterly." },
      { questionId: Q3_ID, status: "APPROVED", answer: "In the register." },
      { questionId: Q4_ID, status: "APPROVED", answer: "Every 90 days." },
    ];
    const byEvidence = suggestNextQuestion({
      current,
      questions: entries,
      responses: allApproved,
      responseStatusAvailable: true,
      evidenceTexts: [EVIDENCE_EXCERPT],
    });
    expect(byEvidence?.question.id).toBe(Q4_ID);
    expect(byEvidence?.basis).toBe("SHARED");
    expect(byEvidence?.label).toBe("Suggested based on shared terms/evidence");

    const neutralEntries = [
      current,
      makeEntry(Q2_ID, 2, "Where is the incident register stored?"),
      makeEntry(Q3_ID, 3, "Who approves visitor badges?"),
      makeEntry(Q4_ID, 4, "When is the fire drill scheduled?"),
    ];
    const noStatus = suggestNextQuestion({
      current,
      questions: neutralEntries,
      responses: [],
      responseStatusAvailable: false,
      evidenceTexts: [],
    });
    expect(noStatus?.question.id).toBe(Q2_ID);
    expect(noStatus?.basis).toBe("NEXT");
    expect(noStatus?.label).toContain("status unavailable");
  });
});

test.describe("question-first guided UX", () => {
  test("ordinary query grounds one real question and shows evidence provenance", async ({
    page,
  }) => {
    const harness = await installMockApi(page);
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    await expect(
      page.getByRole("heading", { level: 2, name: Q1_TEXT }),
    ).toBeVisible();
    const evidenceSection = page.locator(
      "section[aria-labelledby='evidence-heading']",
    );
    await expect(evidenceSection.getByText(EVIDENCE_EXCERPT)).toBeVisible();
    await expect(
      evidenceSection.getByText("Synthetic Security Policy.pdf"),
    ).toBeVisible();
    await expect(evidenceSection.getByText("Version 3")).toBeVisible();
    await expect(
      evidenceSection.getByText("Encryption controls"),
    ).toBeVisible();
    await expect(evidenceSection.getByText("Page 7")).toBeVisible();
    await expect(
      evidenceSection.getByRole("checkbox", {
        name: "Use passage 1 as a source",
      }),
    ).toBeVisible();

    expect(groundingCalls(harness.calls)).toHaveLength(1);
    expect(harness.calls[0]?.authorization).toBe(`Bearer ${TOKEN}`);
    expect(
      harness.calls.every(
        (call) =>
          call.authorization === `Bearer ${TOKEN}` &&
          (call.path === "/workspaces" ||
            call.path.startsWith(`/workspaces/${WS_A}/`)),
      ),
    ).toBe(true);
    expect(harness.calls.some((call) => call.path.includes(WS_B))).toBe(false);

    const visibleText = await page.locator("main").innerText();
    expect(visibleText).not.toContain(WS_A);
    expect(visibleText).not.toContain(Q1_ID);
    expect(visibleText).not.toContain(QUESTIONNAIRE_ID);
  });

  test("ambiguous query offers multiple options and grounds only after selection", async ({
    page,
  }) => {
    const harness = await installMockApi(page);
    await connect(page);
    await ask(page, "How is access reviewed?");

    await expect(
      page.getByRole("heading", {
        name: "Several authorized questions could match",
      }),
    ).toBeVisible();
    const options = page.getByRole("button", {
      name: "Explore this question",
    });
    await expect(options).toHaveCount(2);
    expect(groundingCalls(harness.calls)).toHaveLength(0);

    const chosen = page.locator("li").filter({ hasText: Q2_TEXT });
    await chosen.getByRole("button", { name: "Explore this question" }).click();
    await expect(
      page.getByRole("heading", { level: 2, name: Q2_TEXT }),
    ).toBeVisible();
    expect(groundingCalls(harness.calls)).toHaveLength(1);
    expect(harness.calls[harness.calls.length - 1]?.path).toContain(
      `/questions/${Q2_ID}/grounding`,
    );
  });

  test("unrelated query shows no match and invents no evidence", async ({
    page,
  }) => {
    const harness = await installMockApi(page);
    await connect(page);
    await ask(page, "What is the capital of France?");

    await expect(
      page.getByText("No matching questionnaire question"),
    ).toBeVisible();
    await expect(
      page.getByText(
        "No question was grounded and no evidence or citation was invented.",
      ),
    ).toBeVisible();
    expect(groundingCalls(harness.calls)).toHaveLength(0);
    await expect(
      page.getByRole("heading", {
        level: 2,
        name: "Choose a question above to see its wording here.",
      }),
    ).toBeVisible();
  });

  test("no-evidence case is explicit and has no citation checkbox", async ({
    page,
  }) => {
    const harness = await installMockApi(page, {
      groundingStatus: "NO_MATCHES",
      groundingResults: [],
    });
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    const evidenceSection = page.locator(
      "section[aria-labelledby='evidence-heading']",
    );
    await expect(
      evidenceSection.getByText("No evidence found for this question"),
    ).toBeVisible();
    await expect(
      evidenceSection.getByText(/no citation or answer was fabricated/i),
    ).toBeVisible();
    await expect(evidenceSection.getByRole("checkbox")).toHaveCount(0);
    await expect(
      page.getByPlaceholder(
        "Write what the evidence supports. It's okay to be unsure.",
      ),
    ).toHaveValue("");
    expect(groundingCalls(harness.calls)).toHaveLength(1);
  });

  test("next question prefers a real unfinished saved response", async ({
    page,
  }) => {
    const harness = await installMockApi(page, {
      responses: [
        responseSummary(Q2_ID, "APPROVED", "Reviewed quarterly."),
        responseSummary(Q3_ID, "NEEDS_REVIEW", null),
        responseSummary(Q4_ID, "APPROVED", "Every 90 days."),
      ],
    });
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    const nextSection = page.locator(
      "section[aria-labelledby='next-question-heading']",
    );
    await expect(
      nextSection.getByText("Unfinished response in this version"),
    ).toBeVisible();
    await expect(nextSection.getByText(Q3_TEXT)).toBeVisible();
    await expect(nextSection.getByText(/Needs Review/)).toBeVisible();

    await nextSection
      .getByRole("button", { name: "Explore this question" })
      .click();
    await expect(
      page.getByRole("heading", { level: 2, name: Q3_TEXT }),
    ).toBeVisible();
    expect(
      harness.calls.some((call) =>
        call.path.includes(`/questions/${Q3_ID}/grounding`),
      ),
    ).toBe(true);
  });

  test("shared-terms/evidence basis is labeled honestly when later questions are approved", async ({
    page,
  }) => {
    await installMockApi(page, {
      responses: [
        responseSummary(Q2_ID, "APPROVED", "Quarterly."),
        responseSummary(Q3_ID, "APPROVED", "In the register."),
        responseSummary(Q4_ID, "APPROVED", "Every 90 days."),
      ],
    });
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    const nextSection = page.locator(
      "section[aria-labelledby='next-question-heading']",
    );
    await expect(
      nextSection.getByText("Suggested based on shared terms/evidence"),
    ).toBeVisible();
    await expect(nextSection.getByText(Q4_TEXT)).toBeVisible();
    await expect(nextSection.getByText(/Shared terms:/)).toBeVisible();
  });

  test("no next question has an honest empty state", async ({ page }) => {
    await installMockApi(page, { questions: [QUESTIONS[0]] });
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    await expect(
      page.getByText("No suggested next question available"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Explore this question" }),
    ).toHaveCount(1); // only the single match card, not a suggestion
  });

  test("no questionnaire has a helpful empty state", async ({ page }) => {
    const harness = await installMockApi(page, {
      questionnairesByWorkspace: { [WS_A]: [] },
    });
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    await expect(page.getByText("Nothing to match yet")).toBeVisible();
    await expect(
      page.getByRole("status", { name: "Question search status" }),
    ).toContainText("No questionnaires in this workspace yet");
    expect(groundingCalls(harness.calls)).toHaveLength(0);
  });

  test("API failure is announced with a retry and no silent mapping", async ({
    page,
  }) => {
    const harness = await installMockApi(page, { failVersions: true });
    await connect(page);
    await ask(page, "Do you encrypt backups?");

    await expect(
      page.getByRole("alert", { name: "Question search error" }),
    ).toContainText("Synthetic versions API failure");
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Explore this question" }),
    ).toHaveCount(0);
    expect(groundingCalls(harness.calls)).toHaveLength(0);
  });

  test("keyboard, validation, loading, and accessible status are usable", async ({
    page,
  }) => {
    await installMockApi(page, { searchDelayMs: 500 });
    await connect(page);

    await expect(
      page.getByRole("heading", { level: 2, name: "Ask about your evidence" }),
    ).toBeVisible();
    const input = page.getByLabel("Ask a question in your own words");
    await expect(input).toBeVisible();

    await input.fill("hi");
    await input.press("Enter");
    await expect(
      page.getByRole("alert", { name: "Question search error" }),
    ).toContainText("Enter at least 3 characters");

    await input.fill("the and");
    await input.press("Enter");
    await expect(
      page.getByRole("alert", { name: "Question search error" }),
    ).toContainText("Use at least one specific word");

    await input.fill("Do you encrypt backups?");
    await input.focus();
    await page.keyboard.press("Tab");
    const submit = page.getByRole("button", { name: "Find matching question" });
    await expect(submit).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(
      page.getByRole("button", {
        name: "Searching authorized questions…",
      }),
    ).toBeDisabled();
    await expect(
      page.getByRole("status", { name: "Question search status" }),
    ).toContainText("Searching authorized questions");
    await expect(page.locator("form[aria-busy='true']")).toBeVisible();
    await expect(
      page.getByRole("heading", { level: 2, name: Q1_TEXT }),
    ).toBeVisible();
  });

  test("workspace switch clears the prior workspace index and keeps calls scoped", async ({
    page,
  }) => {
    const harness = await installMockApi(page, {
      workspaces: [WORKSPACE_A, WORKSPACE_B],
      questionnairesByWorkspace: {
        [WS_A]: [QUESTIONNAIRE],
        [WS_B]: [],
      },
      documentsByWorkspace: { [WS_A]: DOCUMENTS, [WS_B]: [] },
    });
    await connect(page, WS_A);
    await ask(page, "Do you encrypt backups?");
    await expect(
      page.getByRole("heading", { level: 2, name: Q1_TEXT }),
    ).toBeVisible();
    await expect(page.getByText(EVIDENCE_EXCERPT)).toBeVisible();
    expect(groundingCalls(harness.calls)).toHaveLength(1);

    const workspaceSelect = page
      .locator("section[aria-labelledby='question-select-heading'] select")
      .first();
    await workspaceSelect.selectOption(WS_B);
    await expect(
      page.getByRole("button", { name: "Find matching question" }),
    ).toBeEnabled();
    await expect(page.getByText(Q1_TEXT, { exact: true })).toHaveCount(0);

    const callsBeforeSecondSearch = harness.calls.length;
    await ask(page, "Do you encrypt backups?");
    await expect(
      page.getByRole("status", { name: "Question search status" }),
    ).toContainText("No questionnaires in this workspace yet");
    await expect(page.getByText(Q1_TEXT, { exact: true })).toHaveCount(0);
    expect(groundingCalls(harness.calls)).toHaveLength(1);
    const newCalls = harness.calls.slice(callsBeforeSecondSearch);
    expect(
      newCalls.every(
        (call) =>
          !call.path.includes(WS_A) && call.authorization === `Bearer ${TOKEN}`,
      ),
    ).toBe(true);
  });
});
