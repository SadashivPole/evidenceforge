# Browser E2E Test Plan

Date: 24 October 2026 · baseline reviewed: `46cba86b` (public `main`)

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
| CI integration | GitHub Actions `Playwright browser smoke` job live and green: PR #25, [run #50 / 40084933365](https://github.com/SadashivPole/evidenceforge/actions/runs/40084933365), 3/3 smoke tests (≈3.0 s) as part of six-of-six successful jobs | Full guided-review workflow browser coverage remains follow-up work (see §8) |

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

The 2026-10-24 reassessment is recorded in
`docs/dependency-risk-acceptance.md`: the `braces` advisory was withdrawn
upstream and its tracking record is closed; the `zod` finding was remediated to
`zod@4.6.7` in the lockfile. The local validation run on this patch tree reports
zero vulnerabilities from both `npm audit` and `npm audit --omit=dev`. Re-run
both audit modes before release and at the next review, no later than
**2 November 2026** (or earlier at release preparation). Closing the withdrawn
`braces` advisory does not declare the package safe in an absolute sense; see
the dependency-risk record for the caveats and reopen triggers.

## 8. Prioritized browser coverage backlog (follow-up work — NOT implemented today)

Status legend: **Browser** = covered by an automated browser test today;
**Backend** = covered by a backend test file; **—** = neither. Verified
against the test tree at `main` `46cba86b`.

| # | Scenario (priority) | Prerequisites / fixtures | Expected result | Cleanup strategy | Current coverage |
| --- | --- | --- | --- | --- | --- |
| 8.1 (P0) | Session connection: bogus token rejected, valid token connects | Disposable bootstrap workspace + short-lived token via documented operator bootstrap only (never hand-issued); synthetic names | Bogus token → visible error, no connect; valid token → workspace selector enabled | Token expires naturally (short TTL); disposable DB | Browser — · Backend `tests/test_bootstrap_cli.py`, `test_identity_contract.py`, `test_security_boundary.py` |
| 8.2 (P0) | Questionnaire import of synthetic XLSX workbook | Tiny deterministic `.xlsx` fixture (synthetic markers, no real questionnaire) | Import succeeds via UI; questionnaire/version/question selects populate | Disposable workspace + DB | Browser — · Backend `tests/test_questionnaire_api_import.py`, `test_xlsx_import.py` |
| 8.3 (P0) | Evidence upload + version/chunk inspection | Synthetic plain-text document seeded via API (UI has no evidence import surface today) | Upload returns immutable version; chunk listing reflects deterministic text | Disposable workspace + DB | Browser — · Backend `tests/test_evidence_api_upload.py`, `test_evidence_api_documents.py`, `test_evidence_chunking.py` |
| 8.4 (P1) | Grounding candidates + source metadata for synthetic question | Seeded synthetic evidence matching fixture question tokens | Candidate list shows passage excerpts + version/chunk metadata; deterministic, no LLM | None needed (read-only) | Browser — · Backend `tests/test_questionnaire_grounding_api.py`, `test_questionnaire_grounding.py`, `test_questionnaire_grounding_postgres.py` |
| 8.5 (P1) | Citation selection + invalid/foreign-citation rejection | Grounded candidates from 8.4; foreign chunk probe via API | UI saves with chosen citations; foreign/cross-workspace citation rejected server-side | Disposable workspace + DB | Browser — · Backend `tests/test_evidence_citations.py`, `test_questionnaire_responses_acceptance.py` |
| 8.6 (P1) | Response save, identical-save idempotency, revision history | Selected question + citations | First save appends exactly one revision; identical re-save appends none; edit appends one | Disposable workspace + DB | Browser — · Backend `tests/test_questionnaire_responses.py`, `test_questionnaire_responses_postgres.py`, `test_questionnaire_response_answer_boundary.py` |
| 8.7 (P2) | Explicit human-review actions + role permissions | Saved draft from 8.6 | ACCEPT / EDIT_AND_APPROVE / REJECT recorded as server-authorized decisions | Disposable workspace + DB | Browser — · Backend `tests/test_questionnaire_human_review.py`, `test_questionnaire_responses_acceptance.py` |
| 8.8 (P0) | Unauthorized / cross-workspace access without data leakage | Second principal via supported isolated test flow only; never fabricated identity subjects | 401 without token; scoped-404/403 where applicable; no cross-workspace rows | Disposable workspace + DB | Browser — · Backend `tests/test_security_boundary.py`, `test_identity_contract.py`, `test_recovery_identity.py` |

Implementation notes for whoever picks these up:

- Keep today's smoke suite untouched; add a separate spec (serial, one
  worker, dedicated disposable database/workspace) so smoke stays fast and
  dependency-free of tokens.
- Tokens come only from the documented operator bootstrap against a
  disposable database — never from manual DB rows or fabricated subjects.
- Report `SKIP`/`BLOCKED` explicitly for any scenario the environment
  cannot run; a skip is not a pass.