import { defineConfig } from "@playwright/test";

/**
 * Playwright smoke-test configuration for the EvidenceForge frontend.
 *
 * SCOPE: smoke-level checks plus the isolated, mocked-API question-first UX
 * contract tests (see docs/browser-e2e-test-plan.md). The extended
 * guided-review workflow suite lives in e2e/workflow/ under
 * playwright.workflow.config.ts and is ignored here.
 *
 * This config intentionally does NOT start any servers (no `webServer` hook):
 * nothing here may silently create/stop/delete Compose services or volumes.
 * The prerequisites are listed in docs/browser-e2e-test-plan.md — briefly:
 * the frontend must already be reachable at PLAYWRIGHT_BASE_URL and the
 * backend behind the /backend-api rewrite must be up, per the documented
 * isolated test-stack instructions in the repository README / quickstart
 * guide.
 */
export default defineConfig({
  testIgnore: ["e2e/workflow/**"],
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["line"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
