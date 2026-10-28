import { expect, test } from "@playwright/test";

import {
  connectSuiteOwner,
  createWorkspace,
  EVIDENCE_ONE_TXT,
  importWorkbookApi,
  apiGet,
  selectQuestion,
  uploadEvidenceApi,
  readSuiteState,
  runGroundingApi,
  questionIdByText,
  QUESTION_ONE_TEXT,
  selectionFor,
  WORKSPACE_NAME_PREFIX,
} from "../helpers/workflow";

/**
 * 8.3 — evidence upload + version/chunk inspection. The UI has no evidence
 * import surface yet (plan §8.3 anticipates this), so ingestion is seeded via
 * the documented API; the UI leg still verifies the grounded evidence is
 * surfaced with version/chunk metadata.
 */
test.describe("8.3 evidence upload and inspection", () => {
  test("API-Seeded synthetic evidence is immutable and inspectable", async ({
    page,
  }) => {
    const token = readSuiteState().ownerToken;
    const workspace = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.3 Evidence`,
    );
    const imported = await importWorkbookApi(workspace.id, token);
    const uploaded = await uploadEvidenceApi(
      workspace.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-duty-officer.txt",
    );
    expect(uploaded.documentId).toBeTruthy();
    expect(uploaded.versionId).toBeTruthy();

    // Immutable version: chunk listing is readable and stable.
    const chunks = await apiGet<
      { id: string; chunk_index: number; content: string }[]
    >(
      `/workspaces/${workspace.id}/documents/${uploaded.documentId}/versions/${uploaded.versionId}/chunks`,
      token,
    );
    expect(chunks.status).toBe(200);
    expect(chunks.body.length).toBeGreaterThan(0);
    expect(chunks.body[0].content).toContain("duty officer");

    // UI leg: grounded candidate for the related question surfaces
    // excerpt + metadata (vN · chunk K renders).
    await connectSuiteOwner(page);
    const qid = questionIdByText(imported, QUESTION_ONE_TEXT);
    const sel = selectionFor(workspace.id, imported, qid);
    const candidates = await runGroundingApi(token, sel);
    expect(candidates.length).toBeGreaterThan(0);
    const top = candidates[0];
    await selectQuestion(
      page,
      sel,
      `${WORKSPACE_NAME_PREFIX} 8.3 Evidence`,
      QUESTION_ONE_TEXT,
    );
    await page
      .getByRole("button", { name: "Find supporting evidence" })
      .click();
    // The deterministic candidate excerpt is chunked text: assert exactly
    // the API-returned content, not a guess across a chunk boundary.
    const evidenceSection = page.locator(
      "section[aria-labelledby='evidence-heading']",
    );
    await expect(evidenceSection.getByText(top.content)).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      evidenceSection.getByText(new RegExp(`Version ${top.version_number}$`)),
    ).toBeVisible();
    await expect(
      evidenceSection.getByText("Technical details and provenance"),
    ).toBeVisible();
  });
});
