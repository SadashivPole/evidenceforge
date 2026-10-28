import { expect, test } from "@playwright/test";

import {
  connectSuiteOwner,
  createWorkspace,
  EVIDENCE_ONE_TXT,
  importWorkbookApi,
  questionIdByText,
  QUESTION_ONE_TEXT,
  readSuiteState,
  runGroundingApi,
  selectQuestion,
  selectionFor,
  uploadEvidenceApi,
  WORKSPACE_NAME_PREFIX,
} from "../helpers/workflow";

/**
 * 8.4 — grounding candidates with per-source metadata (version, chunk,
 * excerpt) visible in the UI and identical to the API payload.
 */
test.describe("8.4 grounding candidates and source metadata", () => {
  test("grounded passages show excerpt and version/chunk metadata", async ({
    page,
  }) => {
    const token = readSuiteState().ownerToken;
    const workspace = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.4 Grounding`,
    );
    const imported = await importWorkbookApi(workspace.id, token);
    await uploadEvidenceApi(
      workspace.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-grounding-source.txt",
    );

    const qid = questionIdByText(imported, QUESTION_ONE_TEXT);
    const sel = selectionFor(workspace.id, imported, qid);
    const candidates = await runGroundingApi(token, sel);
    expect(candidates.length).toBeGreaterThan(0);
    const top = candidates[0];
    expect(top.chunk_id).toBeTruthy();
    expect(top.version_number).toBeGreaterThanOrEqual(1);
    expect(top.content).toContain("duty officer");

    await connectSuiteOwner(page);
    await selectQuestion(
      page,
      sel,
      `${WORKSPACE_NAME_PREFIX} 8.4 Grounding`,
      QUESTION_ONE_TEXT,
    );
    await page
      .getByRole("button", { name: "Find supporting evidence" })
      .click();
    const evidenceSection = page.locator(
      "section[aria-labelledby='evidence-heading']",
    );
    await expect(evidenceSection.getByText(top.content)).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      evidenceSection.getByText(new RegExp(`^Version ${top.version_number}$`), {
        exact: false,
      }),
    ).toBeVisible();
    // Provenance (chunk id + sha256) lives in the collapsed details group;
    // open it, then assert the real values that grounding returned.
    const details = evidenceSection
      .getByText("Technical details and provenance")
      .first();
    await expect(details).toBeVisible();
    await details.click();
    await expect(
      evidenceSection.getByText(`Chunk ID: ${top.chunk_id}`),
    ).toBeVisible();
    await expect(
      evidenceSection.getByText(`SHA-256: ${top.content_hash}`),
    ).toBeVisible();
  });
});
