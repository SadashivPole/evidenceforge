import { expect, test } from "@playwright/test";

import {
  connectSuiteOwner,
  readSuiteState,
  stepSelect,
} from "../helpers/workflow";

/**
 * 8.1 — session connection: a bogus token must be rejected with a visible
 * error and no connection; a real bootstrap token must connect and enable the
 * workspace picker. Durable-state assertions are used where the success
 * notice ("Connected. N workspaces available.") is intentionally transient
 * (replaced by the subsequent questionnaire-load notice).
 */
test.describe("8.1 session connection", () => {
  test("bogus token is rejected", async ({ page }) => {
    await page.goto("/guided");
    await page.getByLabel("Session token").fill("e2e-bogus-token-not-real");
    await page.getByRole("button", { name: "Connect" }).click();
    const alert = page
      .getByRole("alert")
      .filter({ hasText: "Invalid or revoked token" });
    await expect(alert).toBeVisible();
    await expect(stepSelect(page, 0)).toBeDisabled();
    await expect(
      page.getByText("Connected", { exact: true }),
    ).not.toBeVisible();
  });

  test("bootstrap token connects and enables the workspace picker", async ({
    page,
  }) => {
    await connectSuiteOwner(page);
    const state = readSuiteState();
    expect(state.ownerToken.length).toBeGreaterThan(20);
    // Durable assertions: connected pill + enabled picker with >= 1 option.
    await expect(
      page.getByText("Connected", { exact: true }).first(),
    ).toBeVisible();
    await expect(stepSelect(page, 0)).toBeEnabled({ timeout: 20_000 });
    await expect(stepSelect(page, 0).locator("option").nth(1)).toBeAttached();
  });
});
