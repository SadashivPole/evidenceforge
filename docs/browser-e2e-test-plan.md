# Browser E2E Test Plan

Date: 24 October 2026 · baseline reviewed: `3245974` (public `main`)

This document defines today's smoke-test scaffold and the boundary between it
and the full guided-review workflow browser suite planned as follow-up work.

## 1. Scope

| Area | Current implementation / validation | Follow-up (planned, **NOT** tested today) |
| --- | --- | --- |
| Guided workbench rendering | `/guided` landmark assertions: hero heading, "Connect to EvidenceForge" card, session-token field, `Connect` button, default "Not connected" state | — |
| Routing | `/` redirects to `/guided` | — |
| Same-origin API proxy (Next.js rewrite) | browser-origin `fetch("/backend-api/health/live")` → `200` + `{"status":"ok"}` | rewrite behavior for authenticated routes |
| Session connection | — | bogus-token rejection + valid-token connect (bootstrap operator flow) |
| Questionnaire import (XLSX) | — | UI upload of a synthetic workbook |
| Evidence upload / version inspection | — | synthetic text upload, chunk/version checks |
| Grounding + candidate display | — | deterministic grounding for a synthetic question |
| Citation selection / validation | — | checkbox selection; invalid-citation rejection |
| Response persistence / idempotency | — | save-for-review + identical-save no-op |
| Revision history | — | changed answer yields one new revision |
| Human-review operations | — | explicit ACCEPT / EDIT_AND_APPROVE / REJECT |
| Unauthorized / cross-workspace access | — | 401/404 and workspace-scoping boundary probes |
| CI integration | GitHub Actions job configured; first hosted run pending | Full guided-review workflow browser coverage remains follow-up work |

Nothing in the right-hand column is claimed as browser-tested today. Backend
coverage of these behaviors continues to live in `backend/tests/`.

## 2. What exists in the patch

| Path | Purpose |
| --- | --- |
| `.github/workflows/ci.yml` | Adds an isolated Playwright smoke job using a disposable PostgreSQL/pgvector service. |
| `frontend/package.json` | pins `@playwright/test@1.64.1`, adds `npm run test:e2e` |
| `frontend/package-lock.json` | lockfile updated by `npm install` (npm conventions) |
| `frontend/playwright.config.ts` | base URL from `PLAYWRIGHT_BASE_URL` (default `http://127.0.0.1:3000`); chromium only; no `webServer` hook on purpose |
| `frontend/e2e/smoke.spec.ts` | the three smoke tests from §1 |
| `docs/browser-e2e-test-plan.md` | this document |
| `docs/assets/evidenceforge-architecture.svg` | stale Compose/Next env-var footnote corrected (verified against `compose.yaml` + `frontend/next.config.mjs`) |

## 3. Prerequisites (explicit — nothing auto-starts services)

The smoke suite **never** starts, stops, recreates, or deletes any service,
Docker container, or volume. Before running it:

1. Isolated test stack up per the isolated browser E2E setup section in `README.md`
   guidance (backend on `8000`, frontend on `3000` with
   `EVIDENCEFORGE_API_URL` pointing at that backend).
2. Verify prerequisites independent of Playwright:
   - `curl http://127.0.0.1:8000/health/live` → `200 {"status":"ok"}`
   - `curl http://127.0.0.1:3000/backend-api/health/live` → `200 {"status":"ok"}`
3. No session token is required for today's smoke tests. Do **not** bootstrap
   against development/staging/production databases for future protected-flow
   tests; use only a dedicated, disposable, synthetic-data database per the
   operator bootstrap safeguards.
4. `npm ci` has been run in `frontend/`.
5. Playwright browser installed once: `npx playwright install chromium`.

## 4. Run commands

From `frontend/`:

```sh
npm run test:e2e
```

The suite reports via the `line` reporter. On failure Playwright stores a
trace + screenshot under `frontend/test-results/` (local-only; git-ignored —
do not include it in patches).

Environment: `PLAYWRIGHT_BASE_URL` overrides the base URL, e.g.
`PLAYWRIGHT_BASE_URL=http://127.0.0.1:3000 npm run test:e2e`.

## 5. Windows PowerShell local validation

```powershell
# 1) Prerequisite: isolated test stack running (see §3)
# 2) From the repository root:
Set-Location .\frontend
npm ci
npx playwright install chromium
npm run lint
npm run typecheck
npm run format:check
npm run build
$env:PLAYWRIGHT_BASE_URL = 'http://127.0.0.1:3000'
npm run test:e2e
```

If browser download/install fails in a restricted environment, capture the
exact error and note that browser tests could not be executed — do not report
them as passing.

## 6. Test-design rules (carried into follow-up work)

- **Deterministic and isolated:** no arbitrary sleeps; auto-waiting and
  bounded timeouts only; one worker; no shared mutable state between specs.
- **No real data:** synthetic-only markers; no production accounts, tokens,
  customer evidence, or external services.
- **No hidden side effects:** Playwright must not manage Docker/Compose or
  databases; test prerequisites stay explicit (see §3).
- **Accessible locators first:** headings, roles, labels, placeholders — not
  CSS-class selectors — wherever an accessible locator exists (headings and
  placeholders used today follow the real page source).
- **No hand-issued tokens:** future protected-flow tests must obtain
  short-lived session tokens through the documented operator bootstrap flow,
  keep them out of logs/artifacts, and use a dedicated disposable workspace.
- **Failure diagnostics:** `trace: retain-on-failure`,
  `screenshot: only-on-failure`; review artifacts before sharing.

## 7. Dependency-risk note

The `braces@3.0.3` advisory acceptance in `docs/dependency-risk-acceptance.md`
is **unchanged by today's patch** and remains a separately tracked follow-up
with reassessment due no later than **2 November 2026**. Today's addition of
`@playwright/test` does not alter that disposition; a fresh `npm audit`
(+ `npm explain ...`) belongs to that follow-up.
