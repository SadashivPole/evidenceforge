import { expect, test } from "@playwright/test";

import {
  acceptDialogs,
  apiGet,
  connectSuiteOwner,
  createWorkspace,
  EVIDENCE_ONE_TXT,
  getHistoryApi,
  importWorkbookApi,
  questionIdByText,
  QUESTION_ONE_TEXT,
  QUESTION_TWO_TEXT,
  readSuiteState,
  reviewApi,
  runGroundingApi,
  saveResponseApi,
  seedViewerPrincipal,
  selectQuestion,
  selectionFor,
  uploadEvidenceApi,
  WORKSPACE_NAME_PREFIX,
} from "../helpers/workflow";

/**
 * 8.7 — explicit human review actions and role permissions.
 *
 * Design semantics verified in this suite (and asserted, not assumed):
 * - review persists through the same immutable-revision save path;
 * - an identical REJECT (NEEDS_REVIEW -> NEEDS_REVIEW with same answer and
 *   citations) is a designed no-op — persistence stays untouched while the
 *   decision is recorded in the audit trail;
 * - state-changing sequences (draft -> EDIT_AND_APPROVE -> REJECT) append
 *   revisions N, N+1, ...;
 * - VIEWER principals cannot call review or save APIs (authorization 403).
 */
test.describe("8.7 human review actions and role permissions", () => {
  test("ACCEPT / REJECT / EDIT_AND_APPROVE and viewer authorization", async ({
    page,
  }) => {
    acceptDialogs(page);
    const token = readSuiteState().ownerToken;
    const workspace = await createWorkspace(
      token,
      `${WORKSPACE_NAME_PREFIX} 8.7 Review`,
    );
    const imported = await importWorkbookApi(workspace.id, token);
    await uploadEvidenceApi(
      workspace.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-review-src.txt",
    );

    const selOne = selectionFor(
      workspace.id,
      imported,
      questionIdByText(imported, QUESTION_ONE_TEXT),
    );
    const selTwo = selectionFor(
      workspace.id,
      imported,
      questionIdByText(imported, QUESTION_TWO_TEXT),
    );
    const chunkOne = (await runGroundingApi(token, selOne))[0].chunk_id;

    // Seed drafts through the same save path (NEEDS_REVIEW, cited).
    expect(
      (
        await saveResponseApi(token, selOne, {
          answer: "Synthetic E2E draft for accept review.",
          status: "NEEDS_REVIEW",
          citation_chunk_ids: [chunkOne],
        })
      ).status,
    ).toBe(200);

    // Viewer fixture: real principal + membership in the disposable DB
    // (documented test fixture — no membership HTTP endpoint exists).
    const viewer = seedViewerPrincipal(workspace.id);
    const viewerReview = await reviewApi(viewer.token, selOne, "ACCEPT");
    expect(viewerReview.status).toBe(403);
    const viewerSave = await saveResponseApi(viewer.token, selOne, {
      answer: "viewer must not save",
      status: "NEEDS_REVIEW",
      citation_chunk_ids: [chunkOne],
    });
    expect(viewerSave.status).toBe(403);

    // --- ACCEPT (UI): rev1 NEEDS_REVIEW -> rev2 APPROVED -------------------
    await connectSuiteOwner(page);
    await selectQuestion(
      page,
      selOne,
      `${WORKSPACE_NAME_PREFIX} 8.7 Review`,
      QUESTION_ONE_TEXT,
    );
    await page
      .getByRole("button", { name: "Find supporting evidence" })
      .click();
    await expect(
      page.getByRole("textbox", { name: "Your answer" }),
    ).toHaveValue("Synthetic E2E draft for accept review.", {
      timeout: 20_000,
    });
    await page.getByRole("button", { name: "Approve response" }).click();
    await expect(page.getByText("Response approved.")).toBeVisible({
      timeout: 20_000,
    });
    let history = await getHistoryApi(token, selOne);
    expect(history.body.map((r) => r.revision_number)).toEqual([1, 2]);
    expect(history.body[1].status).toBe("APPROVED");

    // --- EDIT_AND_APPROVE from Q1... via Q2 to keep legs independent -------
    const chunkTwo = (await runGroundingApi(token, selTwo))[0].chunk_id;
    expect(
      (
        await saveResponseApi(token, selTwo, {
          answer: "Synthetic E2E draft for reject review.",
          status: "NEEDS_REVIEW",
          citation_chunk_ids: [chunkTwo],
        })
      ).status,
    ).toBe(200);

    await selectQuestion(
      page,
      selTwo,
      `${WORKSPACE_NAME_PREFIX} 8.7 Review`,
      QUESTION_TWO_TEXT,
    );
    await expect(
      page.getByRole("textbox", { name: "Your answer" }),
    ).toHaveValue("Synthetic E2E draft for reject review.");
    const editedAnswer =
      "Synthetic E2E edited: the duty officer reviews the alpha gate weekly.";
    await page.getByRole("textbox", { name: "Your answer" }).fill(editedAnswer);
    await page.getByRole("button", { name: "Edit and approve" }).click();
    await expect(page.getByText("Edited response approved.")).toBeVisible({
      timeout: 20_000,
    });
    history = await getHistoryApi(token, selTwo);
    expect(history.body.map((r) => r.revision_number)).toEqual([1, 2]);
    expect(history.body[1].status).toBe("APPROVED");
    expect(history.body[1].answer).toBe(editedAnswer);

    // --- REJECT (APPROVED -> NEEDS_REVIEW: appends a revision) -------------
    await page.getByRole("button", { name: "Reject draft" }).click();
    await expect(
      page.getByText("Draft rejected and marked for review."),
    ).toBeVisible({ timeout: 20_000 });
    history = await getHistoryApi(token, selTwo);
    expect(history.body.map((r) => r.revision_number)).toEqual([1, 2, 3]);
    expect(history.body[2].status).toBe("NEEDS_REVIEW");
    expect(history.body[2].answer).toBe(editedAnswer);

    // --- Identical REJECT is a designed no-op -------------------------------
    await page.getByRole("button", { name: "Reject draft" }).click();
    await expect(
      page.getByText("Draft rejected and marked for review."),
    ).toBeVisible({ timeout: 20_000 });
    history = await getHistoryApi(token, selTwo);
    expect(history.body).toHaveLength(3);

    // --- Audit trail records every decision ----------------------------------
    const audit = await apiGet<
      { action: string; event_metadata?: Record<string, unknown> }[]
    >(`/workspaces/${workspace.id}/audit-events`, token);
    expect(audit.status).toBe(200);
    const reviewEvents = audit.body.filter(
      (event) => event.action === "questionnaire.response.reviewed",
    );
    const reviewActions = reviewEvents.map(
      (event) => event.event_metadata?.review_action,
    );
    expect(reviewActions).toContain("ACCEPT");
    expect(reviewActions).toContain("EDIT_AND_APPROVE");
    expect(reviewActions.filter((a) => a === "REJECT").length).toBe(2);
  });
});
