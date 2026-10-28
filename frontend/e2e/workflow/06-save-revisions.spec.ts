import { expect, test } from "@playwright/test";

import {
  connectSuiteOwner,
  createWorkspace,
  EVIDENCE_ONE_TXT,
  getHistoryApi,
  getResponseApi,
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
} from "../helpers/workflow";

/**
 * 8.6 — response save semantics: first save appends exactly one revision;
 * byte-identical re-save appends none (design contract, same rule the review
 * path reuses); edited save appends one new revision and history preserves
 * the immutable chain.
 */
test.describe("8.6 response save, idempotency, revisions", () => {
  test("first save / identical re-save / edited save", async ({ page }) => {
    const token = readSuiteState().ownerToken;
    const workspace = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.6 Revisions`,
    );
    const imported = await importWorkbookApi(workspace.id, token);
    await uploadEvidenceApi(
      workspace.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-rev-src.txt",
    );
    const qid = questionIdByText(imported, QUESTION_ONE_TEXT);
    const sel = selectionFor(workspace.id, imported, qid);
    const chunk = (await runGroundingApi(token, sel))[0].chunk_id;

    // UI save (rev 1).
    await connectSuiteOwner(page);
    await selectQuestion(
      page,
      sel,
      `${WORKSPACE_NAME_PREFIX} 8.6 Revisions`,
      QUESTION_ONE_TEXT,
    );
    await page
      .getByRole("button", { name: "Find supporting evidence" })
      .click();
    const useSource = page
      .getByRole("checkbox", { name: /Use passage \d+ as a source/ })
      .first();
    await expect(useSource).toBeVisible({ timeout: 30_000 });
    await useSource.click();
    await page
      .getByRole("textbox", { name: "Your answer" })
      .fill("Revision one answer.");
    await page.getByRole("button", { name: "Save for review" }).click();
    await expect(
      page.getByText(/Revision 1 saved with 1 citation/),
    ).toBeVisible({
      timeout: 20_000,
    });

    let history = await getHistoryApi(token, sel);
    expect(history.body.map((r) => r.revision_number)).toEqual([1]);

    // Identical re-save via API is a designed no-op.
    const again = await saveResponseApi(token, sel, {
      answer: "Revision one answer.",
      status: "NEEDS_REVIEW",
      citation_chunk_ids: [chunk],
    });
    expect(again.status).toBe(200);
    history = await getHistoryApi(token, sel);
    expect(history.body.map((r) => r.revision_number)).toEqual([1]);

    // Edited save appends revision 2 with immutable rev 1 intact.
    const edited = await saveResponseApi(token, sel, {
      answer: "Revision two answer.",
      status: "NEEDS_REVIEW",
      citation_chunk_ids: [chunk],
    });
    expect(edited.status).toBe(200);
    history = await getHistoryApi(token, sel);
    expect(history.body.map((r) => r.revision_number)).toEqual([1, 2]);
    expect(history.body[0].answer).toBe("Revision one answer.");
    expect(history.body[1].answer).toBe("Revision two answer.");

    const response = await getResponseApi(token, sel);
    const current = response.body.current_revision;
    expect(current, "current revision present after edits").not.toBeNull();
    if (current) {
      expect(current.answer).toBe("Revision two answer.");
      expect(current.citations.map((c) => c.evidence_chunk_id)).toContain(
        chunk,
      );
    }
  });
});
