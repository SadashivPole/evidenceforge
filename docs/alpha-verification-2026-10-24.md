# 2026-10-24/25 milestone verification addendum

> Additive note alongside `docs/alpha-limitations-2026-10-24.md`; phase docs
> untouched.

**Baseline verified:** commit `d0cded2301ec0c3cab3c774d93539f682e225d81`
(post PR #26) is `origin/main`; hosted CI run #53
(`actions/runs/40091805488`) passed 6/6 jobs including _Playwright browser smoke_.
All statements below were re-executed in this environment (results section at
bottom), not copied from earlier sessions.

## Workstream A — browser workflow e2e

| Plan row                                       | Spec                                       | Coverage                                                                                                     |
| ---------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| 8.1 session connection (P0)                    | `frontend/e2e/workflow/01-connect.spec.ts` | bogus token rejected (visible error, picker disabled); real bootstrap token connects (durable state asserts) |
| 8.2 XLSX import (P0)                           | `02-import.spec.ts`                        | UI file upload + API contract (questions list by stable ids)                                                 |
| 8.3 evidence upload/versions/chunks (P0)       | `03-evidence.spec.ts`                      | API seeding (no UI control exists today) + UI candidate metadata                                             |
| 8.4 grounding candidates + metadata (P1)       | `04-grounding.spec.ts`                     | excerpt + version/chunk metadata, API↔UI identical payload                                                  |
| 8.5 citations + invalid/foreign rejection (P1) | `05-citations.spec.ts`                     | UI select persists; unknown + foreign chunks → 422 shared safe contract                                      |
| 8.6 save/idempotency/revisions (P1)            | `06-save-revisions.spec.ts`                | first-save/identical-no-op/edit-append; immutable history                                                    |
| 8.7 review actions + roles (P2)                | `07-review.spec.ts`                        | ACCEPT/EDIT_AND_APPROVE/REJECT via UI; audit-trail proof; viewer 403                                         |
| 8.8 authz boundaries (P0)                      | `08-authz.spec.ts`                         | 401 no token; masked 404 cross-workspace; viewer UI scoping                                                  |

Semantics verified during implementation (documented as product contracts):

1. **422 for citation-target failures** — unknown _and_ foreign chunk IDs both
   become `{"detail":"One or more citation targets are invalid"}`
   (`_response_http_error` in `questionnaire_responses.py`), deliberately
   identical so unknown-UUID probing learns nothing about existence.
2. **Identical review decisions are designed no-ops** — `_is_identical_update`
   in `responses/service.py`; a REJECT of an already-identical NEEDS_REVIEW
   draft persists nothing (decision IS audit-recorded, asserted via
   `questionnaire.response.reviewed`).
3. **Ready endpoint contract:** `GET /health/ready` → 200
   `{"status":"ok","database":"ok"}`; DB unreachable → 503 with
   `{"status":"unavailable","database":"unavailable"}`. (Earlier draft docs
   cited a different shape; corrected here from live source + curl.)
4. **Classic-mode frontend builds bake `EVIDENCEFORGE_API_URL` at build time**;
   standalone mode resolves it per-process via the runtime proxy route.
5. **Accessibility finding:** step-01 Questionnaire/Version/Question `<select>`s
   currently have no accessible name (their `<label>`s wrap a filter input + a
   select; implicit association binds the first labelable descendant only).
   The suite drives them structurally (`stepSelect`); fix is tracked here for
   the a11y roadmap, not silently patched in this milestone.
6. **No membership-management HTTP endpoint exists in alpha** — the suite's
   `seedViewerPrincipal` is a direct-DB test fixture that mirrors
   `app.auth.hash_token` (sha256); it is explicitly disclosed, and no token
   values are ever logged or committed.

## Workstream B — containers

See `docs/docker-verification-2026-10-24.md`. Local image build/boot:
**BLOCKED** (no Docker daemon in this execution environment); CI job provided;
Windows commands documented for the owner.

## Workstream C — opt-in local LLM generation

See `docs/llm-generation-decision-2026-10-24.md`. Adapter + settings + 34
contract tests ship; feature stays OFF; product-level end-to-end draft flow
deliberately **INCOMPLETE** pending the real-model evaluation gate.

## Acceptance-fix addendum (2026-10-25, evening) — FINAL TREE results

Independent review found three real defects; all fixed and proven on the final
candidate tree (`/home/user/ef-final-2026-10-25`, baseline ancestor `d0cded`,
no commits made):

1. **IPv6 loopback URL normalization** — `validate_local_base_url()` emitted
   `http://::1:11434/...` (invalid). Now literal IPv6 hosts are RFC-2732
   bracket-encoded (`http://[::1]:11434/api/generate`); DNS/IPv4 output
   unchanged; non-loopback IPv6 stays rejected. Regression tests added.
2. **Broken PowerShell `.env` example** in `docker-verification` — the
   here-string read `$envFile` before it existed. Now one synthetic password
   is generated into a variable first, `DATABASE_URL` is built from that same
   variable, then the file is written. Not runtime-executed (no PowerShell in
   sandbox): static-reviewed label only.
3. **Unbounded HTTP response body** — the provider buffered full responses via
   `response.content`. Now bodies stream-accumulate through
   `_read_bounded_body()` with a documented `MAX_GENERATION_RESPONSE_BYTES`
   (1 MiB) ceiling; violating responses raise typed
   `GenerationResponseTooLargeError`, are never parsed, never retried.

| Final-tree gate (25-Oct 09:35-09:41 UTC) | Command | Count | RC |
|---|---|---|---|
| Focused provider regressions | `pytest tests/test_generation_local_provider.py` | **34 passed** (28+6 new; + pre-append guard test) | 0 |
| Ruff check / format / compile | `ruff check .` etc. | clean | 0 |
| Full backend pytest | `pytest -q tests` | **806 passed, 31 skipped** (52.1 s on 2026-10-27 re-run) | 0 |
| PG persistence / bootstrap / migration-0008 | exact disposable DBs `evidenceforge_test`, `evidenceforge_bootstrap_it_local` (migrated), `evidenceforge_migration_backfill_it` | 3 + 1 + 9 passed | 0 |
| Generation trio | boundary + local provider + design gate | **80 passed** (32+34+14) | 0 |
| Frontend npm ci / audit / audit --omit=dev | final tree | 355 pkgs / 0 / 0 vulns | 0 |
| Lint / typecheck / format:check | final tree | 0 problems | 0 |
| Build classic / build standalone | `npm run build`; `OUTPUT_MODE=standalone npm run build` | OK, `.next/standalone/server.js` present | 0 |
| Workflow suite (smoke DB distinct) | run A 09:40:07 + run B 09:40:37 with guarded reset between | **9/9 (17.7 s), 9/9 (14.4 s)** | 0 |
| Smoke suite | disposable DB `ef_e2e` | **3/3 (2.1 s)** | 0 |
| Workflow viewer-fixture DB guard | node control harness (positive+5 negatives incl. ::1) | GUARD CONTROLS OK | 0 |
| Docker runtime / Ollama real model | `docker version`, `which ollama` | BLOCKED (no daemon, 1982 MB RAM) | n/a |

Raw dated logs (outside the repo, referenced not shipped):
`/home/user/_validation/A-provider-gates-final.log`, `B-backend-full-final.log`,
`CD-pg-generation-final.log`, `E-frontend-gates-final.log`, `E-smoke-final.log`,
`F-workflow-final-A.log`, `F-workflow-final-B.log`.

## Fresh results in this environment (25-Oct re-verification, pre-fix tree)

Superseded by the acceptance-fix addendum above. Measured earlier + pgvector
(dedicated disposable databases; loopback-only stack):

| Gate | Command | Result |
|---|---|---|
| Backend unit | `python -m pytest -q tests` | **800 passed, 31 skipped** (110.8 s), RC 0 |
| Backend ruff | `ruff check .` / `ruff format --check .` | All checks passed, RC 0 |
| PG persistence | `pytest tests/test_evidence_persistence_postgres.py` | **3 passed** (0.45 s), RC 0 |
| PG bootstrap/concurrency | `pytest tests/test_bootstrap_postgres.py` (post-migrate) | **1 passed** (0.34 s), RC 0 |
| PG migration 0008 | `pytest tests/test_migration_0008_backfill_postgres.py` | **9 passed** (1.92 s), RC 0 |
| Generation trio | `pytest tests/test_questionnaire_generation_boundary.py tests/test_generation_local_provider.py tests/evaluation/test_generation_design_gate.py` | **80 passed** (1.24 s on 2026-10-27 re-run), RC 0 |
| Frontend deps | `npm ci` | 355 packages, RC 0 |
| npm audit / `--omit=dev` | 0 / 0 vulnerabilities, RC 0 |
| Lint / typecheck / format:check | 0 errors 0 warnings / RC 0 / RC 0 |
| Build classic (non-standalone) | `npm run build` | RC 0 |
| Build standalone | `OUTPUT_MODE=standalone npm run build` (+ asserts `.next/standalone/server.js`) | RC 0 |
| Smoke browser suite | `npx playwright test` (3 tests, disposable DB `ef_e2e`) | **3 passed** (2.5 s), RC 0 |
| Workflow suite 8.1-8.8 | `npm run test:workflow` (9 tests, disposable DB `ef_e2e_wf`) | **9 passed** (14.9 s) then fresh-reset repeat **9 passed** (15.0 s), RC 0 both |
| Docker (`docker version`, `docker compose version`) | **BLOCKED** — `docker: command not found` (RC 127); YAML statically validated; runtime must run via the CI job on a Docker runner |
| Ollama real model | **BLOCKED** — binary absent, no daemon on :11434, host RAM 1982 MB; adapter contract tests (34) prove the boundary only |

Appendix: full commands/exit codes live in `ARENA_VALIDATION_REPORT.txt` shipped
in the milestone ZIP.


## 2026-10-27 re-verification addendum

The corrected candidate was reproduced from the public baseline and the supplied
text bundle. Newly executed local results: full backend **806 passed, 31 skipped**
(one process, exit 0), 16/16 isolated backend batches with the same aggregate,
offline-control full backend **787 passed, 50 skipped** (exit 0), ruff/format/
compileall clean, generation trio **80 passed**, standalone runtime contract and
negative control pass, browser smoke **3/3**, workflow E2E **9/9**, and 37
PostgreSQL-backed tests passed against freshly created isolated local databases.
Three version-gated tests failed only because the Arena host provides PostgreSQL
17 / pgvector 0.8.0 while those tests assert PostgreSQL 16; four further
version-gated tests were not run. These are environment mismatches, not product
regressions, and must be re-run on the pinned PostgreSQL 16 / pgvector 0.8.6
service. Docker image/runtime and hosted CI remain BLOCKED / NOT RUN for this
candidate. Ollama real-model generation and production viewer enrollment remain
incomplete.
