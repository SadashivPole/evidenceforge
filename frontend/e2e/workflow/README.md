# Extended workflow e2e suite (`e2e/workflow/`)

Serial (1 worker) Playwright suite implementing `docs/browser-e2e-test-plan.md`
§8 rows 8.1–8.8 against a disposable PostgreSQL catalog. It never replaces the
smoke suite (`npm run test:e2e`, which stays fast and independent).

## Environment contract

| Variable                | Meaning                                                                                      | Default                                 |
| ----------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------- |
| `EF_E2E_DATABASE_URL`   | SQLAlchemy URL of the disposable test DB (recreated per run; must be empty before bootstrap) | _(required)_                            |
| `EF_E2E_API_URL`        | backend base URL                                                                             | `http://127.0.0.1:8000`                 |
| `PLAYWRIGHT_BASE_URL`   | frontend base URL                                                                            | `http://127.0.0.1:3000`                 |
| `EF_E2E_PYTHON`         | python binary with `backend[dev]` installed (bootstrap CLI execution)                        | `python3`                               |
| `EF_E2E_WORKFLOW_STATE` | suite-state JSON path (mode 0600)                                                            | `/tmp/ef-e2e-workflow-suite-state.json` |

Run: `npm run test:workflow`.

## Identity provisioning (honest disclosure)

- Suite owner: created ONLY by the documented operator CLI
  `python3 -m app.cli.bootstrap` (run fresh per disposable DB).
- VIEWER principal: `seedViewerPrincipal()` inserts user + `viewer` membership
  - sha256-hashed token rows directly into the DISPOSABLE database, mirroring
    `app.auth.hash_token`. This is a **test fixture**, disclosed since the alpha
    exposes no membership-management HTTP endpoint (product gap, tracked in
    `docs/alpha-verification-2026-10-24.md`). No token values are ever echoed to
    logs; all API calls still pass through the real auth boundary.

## Determinism rules used by the specs

- Selects driven by stable `value` IDs via `stepSelect(page, n)` — the three
  step-01 selects lack accessible names because their `<label>`s wrap two
  controls (a11y finding documented in the verification addendum).
- Transient UI confirmations are never asserted where the UI replaces them
  (e.g., "Connected. N workspace available." is replaced by "Loaded N
  questionnaire."); durable state (picker enabled, option lists, persisted
  revisions) is asserted instead.
- Citation-target failures assert the exact 422 safe-shared contract from
  `questionnaire_responses.py`; identical saves/reviews assert the designed
  no-op semantics of `_is_identical_update`, with audit proof for decisions.
