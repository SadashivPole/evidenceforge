import { defineConfig, devices } from "@playwright/test";

/**
 * Extended workflow e2e suite configuration.
 *
 * Separate from playwright.config.ts (the frozen smoke suite): serial single
 * worker, longer timeouts, its own global setup that bootstraps an isolated
 * disposable PostgreSQL database via the documented operator bootstrap CLI.
 *
 * Required environment:
 *   EF_E2E_DATABASE_URL  SQLAlchemy URL of the disposable test DB
 *   EF_E2E_API_URL       backend base (default http://127.0.0.1:8000)
 *   PLAYWRIGHT_BASE_URL  frontend base (default http://127.0.0.1:3000)
 */
export default defineConfig({
  globalSetup: require.resolve("./e2e/workflow/global.setup.ts"),
  testDir: "./e2e/workflow",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: {} }],
});
