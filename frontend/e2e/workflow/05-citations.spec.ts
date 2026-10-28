import { expect, test } from "@playwright/test";

import {
  connectSuiteOwner,
  createWorkspace,
  EVIDENCE_ONE_TXT,
  EVIDENCE_TWO_TXT,
  importWorkbookApi,
  questionIdByText,
  QUESTION_ONE_TEXT,
  readSuiteState,
  runGroundingApi,
  saveResponseApi,
  selectQuestion,
  selectionFor,
  uploadEvidenceApi,
  WORKSPACE_NAME_PREFIX,
  REJECTION_DETAIL_CITATIONS,
} from "../helpers/workflow";

/**
 * 8.5 — citation selection through the UI persists; unknown and
 * cross-workspace (foreign) chunk IDs are rejected with the exact contract
 * (422 {"detail":"One or more citation targets are invalid"} per
 * EvidenceChunkCitationNotFoundError → _response_http_error in
 * questionnaire_responses.py — both classes share it intentionally).
 */
test.describe("8.5 citation selection and rejection", () => {
  test("chosen citations persist; invalid and foreign citations are rejected", async ({
    page,
  }) => {
    const token = readSuiteState().ownerToken;
    const workspace = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.5 Citations`,
    );
    const imported = await importWorkbookApi(workspace.id, token);
    await uploadEvidenceApi(
      workspace.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-src-a.txt",
    );
    await uploadEvidenceApi(
      workspace.id,
      token,
      EVIDENCE_TWO_TXT,
      "e2e-src-b.txt",
    );

    const qid = questionIdByText(imported, QUESTION_ONE_TEXT);
    const sel = selectionFor(workspace.id, imported, qid);
    const candidates = await runGroundingApi(token, sel);
    expect(candidates.length).toBeGreaterThan(0);

    await connectSuiteOwner(page);
    await selectQuestion(
      page,
      sel,
      `${WORKSPACE_NAME_PREFIX} 8.5 Citations`,
      QUESTION_ONE_TEXT,
    );

    await page
      .getByRole("button", { name: "Find supporting evidence" })
      .click();
    const useSource = page
      .getByRole("checkbox", { name: /Use passage \d+ as a source/ })
      .first();
    await useSource.click();
    await expect(
      page.getByText("Selected source", { exact: true }),
    ).toBeVisible();

    await page
      .getByRole("textbox", { name: "Your answer" })
      .fill("Synthetic answer with the evidence selected.");
    await page.getByRole("button", { name: "Save for review" }).click();
    await expect(
      page.getByText(/Revision 1 saved with 1 citation/),
    ).toBeVisible({
      timeout: 20_000,
    });

    // Unknown chunk id → 422 with the exact detail string.
    const unknown = await saveResponseApi(token, sel, {
      answer: "x",
      status: "PROPOSED",
      citation_chunk_ids: ["00000000-0000-4000-8000-0000000000aa"],
    });
    expect(unknown.status).toBe(422);
    expect(unknown.body && (unknown.body as { detail?: string }).detail).toBe(
      REJECTION_DETAIL_CITATIONS,
    );

    // Foreign chunk id (belongs to another workspace) → same safe 422.
    const foreignWs = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.5 Foreign`,
    );
    const foreignImported = await importWorkbookApi(foreignWs.id, token);
    await uploadEvidenceApi(
      foreignWs.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-foreign-src.txt",
    );
    const foreignQid = questionIdByText(foreignImported, QUESTION_ONE_TEXT);
    const foreignSel = selectionFor(foreignWs.id, foreignImported, foreignQid);
    const foreignChunks = await runGroundingApi(token, foreignSel);
    expect(foreignChunks.length).toBeGreaterThan(0);

    const foreign = await saveResponseApi(token, sel, {
      answer: "x",
      status: "PROPOSED",
      citation_chunk_ids: [foreignChunks[0].chunk_id],
    });
    expect(foreign.status).toBe(422);
    expect((foreign.body as { detail?: string }).detail).toBe(
      REJECTION_DETAIL_CITATIONS,
    );
  });
});
