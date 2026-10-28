import { expect, test } from "@playwright/test";

import {
  connectSuiteOwner,
  createWorkspace,
  importWorkbookApi,
  listQuestionsApi,
  questionIdByText,
  QUESTION_ONE_TEXT,
  readSuiteState,
  selectionFor,
  stepSelect,
  QUESTIONNAIRE_XLSX,
  WORKSPACE_NAME_PREFIX,
  type WorkflowSelection,
} from "../helpers/workflow";

/**
 * 8.2 — synthetic XLSX questionnaire import through the UI (fixture file
 * only; import contract itself is verified against the API).
 */
test.describe("8.2 questionnaire import", () => {
  test("imports the synthetic workbook and selects it", async ({ page }) => {
    const token = readSuiteState().ownerToken;
    const workspace = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.2 Import`,
    );

    await connectSuiteOwner(page);
    await stepSelect(page, 0).selectOption({ value: workspace.id });

    // API-side pre-import sanity: none selected yet.
    // UI import: choose the fixture file, then click Upload questionnaire.
    const fileInput = page.locator("input[type=file]");
    await fileInput.setInputFiles(QUESTIONNAIRE_XLSX);
    await page.getByRole("button", { name: "Upload questionnaire" }).click();
    // The import notice is durable (persists until replaced): assert the real copy.
    await expect(
      page.getByText(
        /^Questionnaire imported\.|^This identical questionnaire was already imported\./,
      ),
    ).toBeVisible({ timeout: 30_000 });

    // Durable assertion: the questionnaire picker contains our workbook and
    // the UI auto-selected it with its questions loaded.
    const questionnaireSelect = stepSelect(page, 1);
    const optionNames = await questionnaireSelect
      .locator("option")
      .allTextContents();
    expect(optionNames.join(" ")).toContain("e2e-synthetic-questionnaire");
    await expect(stepSelect(page, 3)).toBeEnabled();

    // API contract verification against the state the UI just created.
    const imported = await importWorkbookApi(workspace.id, token);
    // Second import of the same workbook is a deterministic no-op or new
    // version; the suite contract: questions list is populated identically.
    expect(imported.questions.map((q) => q.text)).toContain(QUESTION_ONE_TEXT);

    // Drive the cascade by stable IDs and prove question visibility.
    const qid = questionIdByText(imported, QUESTION_ONE_TEXT);
    const sel: WorkflowSelection = selectionFor(workspace.id, imported, qid);
    const historyProbe = await listQuestionsApi(token, sel);
    expect(historyProbe.status).toBe(200);

    await stepSelect(page, 1).selectOption({ value: imported.questionnaireId });
    await stepSelect(page, 2).selectOption({ value: imported.versionId });
    await expect(stepSelect(page, 3).locator("option").nth(1)).toBeAttached();
    await stepSelect(page, 3).selectOption({ value: qid });
    await expect(
      page.getByRole("heading", { name: QUESTION_ONE_TEXT }),
    ).toBeVisible();
  });
});
