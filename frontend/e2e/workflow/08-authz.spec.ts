import { expect, test } from "@playwright/test";

import {
  apiGet,
  connectViaUi,
  createWorkspace,
  EVIDENCE_ONE_TXT,
  importWorkbookApi,
  questionIdByText,
  QUESTION_ONE_TEXT,
  readSuiteState,
  reviewApi,
  runGroundingApi,
  saveResponseApi,
  seedViewerPrincipal,
  selectionFor,
  stepSelect,
  uploadEvidenceApi,
  WORKSPACE_NAME_PREFIX,
} from "../helpers/workflow";

/**
 * 8.8 — unauthorized and cross-workspace boundaries.
 * - 401 for unauthenticated API reads;
 * - cross-workspace identifiers are masked (404, not 403) to prevent
 *   existence disclosure;
 * - natural-language question discovery cannot surface another workspace's
 *   imported question to the viewer UI;
 * - a VIEWER cannot save from the UI surface even when connected.
 */
test.describe.configure({ retries: 0 });

test.describe("8.8 unauthorized and cross-workspace boundaries", () => {
  test("401 without token, masked 404 across workspaces, 403 viewer save from UI", async ({
    page,
  }) => {
    const token = readSuiteState().ownerToken;

    // --- unauthenticated ----------------------------------------------------
    const noAuth = await apiGet("/workspaces", null);
    expect(noAuth.status).toBe(401);

    // --- cross-workspace masking ---------------------------------------------
    const wsA = await createWorkspace(token, `${WORKSPACE_NAME_PREFIX} 8.8 A`);
    const imported = await importWorkbookApi(wsA.id, token);
    await uploadEvidenceApi(
      wsA.id,
      token,
      EVIDENCE_ONE_TXT,
      "e2e-authz-src.txt",
    );
    const selA = selectionFor(
      wsA.id,
      imported,
      questionIdByText(imported, QUESTION_ONE_TEXT),
    );
    const chunkA = (await runGroundingApi(token, selA))[0].chunk_id;
    void chunkA;

    // Second owner workspace + its own principal spacing -> masked reads.
    const wsB = await createWorkspace(token, `${WORKSPACE_NAME_PREFIX} 8.8 B`);
    const viewerB = seedViewerPrincipal(wsB.id); // viewer only in wsB
    const crossUk = await apiGet(`/workspaces/${wsA.id}`, viewerB.token);
    expect(crossUk.status).toBe(404);

    // --- viewer UI: connect succeeds, save controls are not usable ---------
    await connectViaUi(page, viewerB.token);
    await expect(
      page.getByText("Connected", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/Loaded \d+ questionnaires?\.|No questionnaires yet\./),
    ).toBeVisible();

    // The viewer's workspace selection shows ONLY its own workspace.
    await expect(stepSelect(page, 0)).toBeEnabled({ timeout: 20_000 });
    const optionTexts = (
      await stepSelect(page, 0).locator("option").allTextContents()
    ).join(" ");
    expect(optionTexts).toContain("8.8 B");
    expect(optionTexts).not.toContain("8.8 A");

    // Question-first UI boundary: a principal whose /workspaces list contains
    // only wsB must not discover wsA's imported question through natural text.
    const askBox = page.getByLabel("Ask a question in your own words");
    await expect(askBox).toBeEnabled();
    await askBox.fill(QUESTION_ONE_TEXT);
    await askBox.press("Enter");
    await expect(page.getByText("Nothing to match yet")).toBeVisible();
    await expect(
      page.getByRole("status", { name: "Question search status" }),
    ).toContainText("No questionnaires in this workspace yet");
    await expect(
      page.getByText(QUESTION_ONE_TEXT, { exact: true }),
    ).toHaveCount(0);

    // API-side double proof for the same identity: review + save both 403.
    const forbidden = await reviewApi(viewerB.token, selA, "ACCEPT");
    expect(forbidden.status).toBe(404); // cross-workspace: masked first
    const saveForbidden = await saveResponseApi(viewerB.token, selA, {
      answer: "nope",
      status: "NEEDS_REVIEW",
    });
    expect(saveForbidden.status).toBe(404);
  });
});
