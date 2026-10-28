/**
 * Global setup for the workflow suite.
 *
 * 1. Health-gates the backend (EF_E2E_API_URL) and frontend (PLAYWRIGHT_BASE_URL).
 * 2. Runs the DOCUMENTED operator bootstrap CLI against the disposable
 *    EF_E2E_DATABASE_URL catalog — the only source of the suite-owner token.
 *    The CLI itself refuses a non-fresh database, which guards intent.
 * 3. Writes suite state (owner token only) as mode-0600 JSON for workers.
 */
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

const API = process.env.EF_E2E_API_URL ?? "http://127.0.0.1:8000";
const WEB = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000";
const STATE =
  process.env.EF_E2E_WORKFLOW_STATE ?? "/tmp/ef-e2e-workflow-suite-state.json";

async function gate(name: string, url: string): Promise<void> {
  const res = await fetch(url).catch(() => null);
  if (!res || !res.ok) {
    throw new Error(
      `Workflow suite precondition failed: ${name} not ready at ${url}`,
    );
  }
}

export default async function globalSetup(): Promise<void> {
  const dbUrl = process.env.EF_E2E_DATABASE_URL;
  if (!dbUrl)
    throw new Error("EF_E2E_DATABASE_URL is required (disposable DB)");

  await gate("backend readiness", `${API}/health/ready`);
  await gate("frontend", `${WEB}/guided`);

  const backendDir = path.resolve(__dirname, "..", "..", "..", "backend");
  const out = execFileSync(
    process.env.EF_E2E_PYTHON ?? "python3",
    [
      "-m",
      "app.cli.bootstrap",
      "--external-subject",
      "e2e-workflow-owner",
      "--email",
      "e2e.owner@example.invalid",
      "--display-name",
      "E2E Workflow Owner",
      "--workspace-name",
      "E2E WF bootstrap workspace",
      "--token-ttl-days",
      "1",
    ],
    {
      cwd: backendDir,
      env: { ...process.env, DATABASE_URL: dbUrl },
      encoding: "utf-8",
    },
  );
  const token = out
    .split("\n")
    .map((l) => l.trim())
    .find(
      (l, i, arr) => i > 0 && arr[i - 1].includes("Copy this bearer token"),
    );
  if (!token) throw new Error("bootstrap CLI did not emit a token");

  fs.writeFileSync(
    STATE,
    JSON.stringify(
      { ownerToken: token, principalSubject: "e2e-workflow-owner" },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log("[workflow global setup] bootstrap complete; state:", STATE);
}
