import { expect, test } from "@playwright/test";

/**
 * Smoke-level browser checks for the EvidenceForge web workbench.
 *
 * These tests are deliberately small and dependable (see
 * docs/browser-e2e-test-plan.md):
 *  - They never require a session token or any authenticated resource.
 *  - They never touch Docker, Compose, databases, or volumes.
 *  - Locators come from the real markup of frontend/app/guided/page.tsx at
 *    this commit (headings, accessible names and placeholders below were
 *    confirmed against the page source before being used).
 *
 * NOT covered here (planned follow-ups, NOT tested by this file): XLSX
 * questionnaire import, evidence upload/version inspection, grounding and
 * candidate display, citation selection/validation, response persistence and
 * idempotency, revision history, human-review operations, and
 * unauthorized/invalid-citation scenarios.
 */

test.describe("guided workbench smoke tests", () => {
  test("guided workbench renders its documented landmarks", async ({
    page,
  }) => {
    await page.goto("/guided", { waitUntil: "domcontentloaded" });

    // Page hero heading from app/guided/page.tsx.
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /Choose a question\./,
      }),
    ).toBeVisible();

    // Connection card heading + session-token field + Connect button.
    await expect(
      page.getByRole("heading", {
        name: "Connect to EvidenceForge",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByPlaceholder("Paste your temporary session token"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Connect", exact: true }),
    ).toBeEnabled();

    // "Not connected" state must be the default (no token in this suite).
    await expect(page.getByText("Not connected")).toBeVisible();
  });

  test("root redirects to the guided workbench", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(/\/guided\/?$/);
  });

  test("same-origin proxy reaches backend liveness through the Next rewrite", async ({
    page,
  }) => {
    await page.goto("/guided", { waitUntil: "domcontentloaded" });

    // Browser-origin fetch: the request leaves via the page's own origin, so
    // the configured Next.js rewrite (/backend-api/:path* → backend origin)
    // is what carries it to the API — exactly the path the app uses.
    const result = await page.evaluate(async () => {
      const response = await fetch("/backend-api/health/live", {
        headers: { Accept: "application/json" },
      });
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      return { status: response.status, body };
    });

    // /health/live contract from backend/app/main.py: {"status": "ok"}.
    expect(result.status).toBe(200);
    expect(result.body).toEqual({ status: "ok" });
  });
});
