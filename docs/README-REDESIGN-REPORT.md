# EvidenceForge README redesign — final audit report

## Source and package inspected

The initial archive at `/home/user/evidenceforge-readme-redesign.zip` was inspected before editing. It contained `README.md`, `CHANGE-MANIFEST.md`, `docs/README-REDESIGN-REPORT.md`, and five SVGs under `docs/assets/`. The public repository was rechecked at branch `main`, HEAD **`027e104`** (`fix(ci): align lint and pgvector test setup`). The source clone remained clean during the audit. No access to the developer's Windows working tree or local modifications was available.

Inspected source includes the pre-existing README; `.env.example`; `compose.yaml`; `compose.phase2c-pgvector-spike.yaml`; `.github/workflows/ci.yml`; backend Dockerfile, `pyproject.toml`, config, DB, auth, models, API schemas, and all route modules; evidence ingestion, embeddings, persistence, semantic and hybrid retrieval, citations/context; questionnaire XLSX, persistence, grounding, response, review, and generation boundary modules; Alembic migrations; backend test fixtures and auth/questionnaire/retrieval/review/benchmark tests; frontend Dockerfile, package, Next config, and main screen; and architecture, threat-model, evaluation, hybrid/semantic benchmark, and generation-boundary documentation.

## Resolved audit issues

### A — First-user and token bootstrap

No supported first-user/bootstrap command, seed script, token-issue API, or Compose provisioning mechanism exists in the inspected source. `backend/app/auth.py` says tokens are provisioned outside the public API. `backend/tests/conftest.py` creates an ephemeral user, opaque token (stores only its hash), and workspace OWNER for isolated in-memory SQLite tests; it is not a bootstrap for the Compose DB. The README now says a fresh Compose deployment can serve public health endpoints but protected workflows cannot be completed until an approved out-of-band provisioning procedure exists. It explicitly avoids manual database edits or invented commands, documents the test-only fixture path, and explains that an authenticated `POST /workspaces` creates the first workspace/OWNER once such an identity is provisioned. Token handling guidance uses environment placeholders and does not print secrets.

### B — Frontend API proxy mismatch

Confirmed in source: `compose.yaml` sets `NEXT_PUBLIC_API_BASE_URL`; `frontend/next.config.mjs` reads `EVIDENCEFORGE_API_URL` and defaults to `http://127.0.0.1:8000`. The Compose web container does not receive the latter. Thus the rewrite's loopback default points to the web container itself, not the Compose `api` service. The README documents the observed implication as a source/configuration issue, safe checks through host `http://localhost:8000/health/live` and `/docs`, and browser network diagnostics. It states fixing this requires a separate application configuration change. No Compose or frontend configuration was changed.

### C — Retrieval benchmark provenance and metric scope

The source contains a fixed 60-case corpus (`backend/tests/evaluation/retrieval_cases.py`), metric/evaluation code, test procedures (`test_retrieval_baseline.py`, `test_semantic_benchmark.py`, `test_rrf_evaluation.py`), and historical reports (`docs/phase-2c-semantic-benchmark.md`, `docs/phase-2c-rrf-evaluation.md`). There is no separate raw captured run-output file in the repository, and this audit did not run the benchmark. The README now labels figures as historical/documented and reproducible by test procedure rather than as a fresh run. The RRF test fixture recomputes metrics when the real semantic model is available and can skip when it is not; tests assert exact lexical and semantic baseline figures, hybrid case count, and leakage/injection measures, but do not assert the exact hybrid nDCG value.

A substantive correction: Recall@5 `0.8333333333333334` and nDCG@5 `0.9219567263864729` are specifically the **lexical-only baseline** in the RRF report, not hybrid RRF nDCG. The same historical report gives semantic-only nDCG@5 `0.953101098880283` and hybrid RRF nDCG@5 `0.9616266072655447`; all three list leakage count 0, inertness rate 1.0, and 10 injected candidates surfaced. The README narrows these to benchmark-path measurements and warns they do not show answer accuracy, generated-answer injection resistance, or production security. It notes the historical evaluation report predates the current production hybrid retriever and distinguishes its results from current runtime behavior.

### D — Duplicate architecture diagram

The architecture diagram remains in the hero for immediate context and its duplicate embed was removed from the Architecture section. Later visuals serve distinct purposes: local quick-start path, workspace authorization, evidence-to-citation provenance, and response idempotency/revision lifecycle. Every included image remains referenced in README with alt text.

## Technical claim verification

- **Architecture/retrieval:** At `027e104`, question grounding calls `ground_question`, which uses `HybridRetriever` combining lexical and semantic candidate retrieval with RRF. pgvector persistence and embedding services exist. Retrieval returns candidates and is not an LLM answer-generation workflow.
- **Generation/review boundaries:** Provider/schema/evaluation scaffolding exists, but no connected production end-to-end answer-generation workflow is exposed. Explicit human review route/service and focused UI actions exist. They are described as human-triggered operations, not autonomous review or a complete administration/review product.
- **Auth/workspaces:** Opaque bearer token hash lookup, expiry/revocation checks, active user validation, server-side membership scope, 404 masking for inaccessible workspaces, and role checks are present. OWNER/ADMIN/MEMBER can write responses; only OWNER/ADMIN can set `APPROVED`; VIEWER is read-only. No public auth registration/token provisioning route was found.
- **Docker/health/migrations:** Compose services are `db`, `api`, and `web`; ports 8000 and 3000 are published; Postgres port is not published. API image startup executes `alembic upgrade head` before Uvicorn. `/health/live` and `/health/ready` exist; readiness checks database connectivity. `.env.example` requires `POSTGRES_PASSWORD` and `DATABASE_URL` for Compose.
- **Demo outcomes:** The status/results table in README is explicitly attributed to the user-supplied synthetic local demo. None were rerun during this audit.
- **Versions and checks:** Python metadata, Docker base images, Node package/CI versions and scripts are transcribed from inspected files. CI configuration is described as configured workflow, not a passed run.

## Visual review

Five SVGs are retained and used once each: architecture, workspace authorization, evidence citation chain, response lifecycle, and quick start. They use local self-contained XML/SVG with the navy/blue/cyan/violet palette, text labels (not color alone), no external resource dependencies, informative filenames, and README alt text. Architecture is not duplicated. XML parsing was performed. A pixel-level browser/desktop/mobile render inspection was not performed; final appearance should be checked in GitHub/VS Code after staging.

## Checks performed / not performed

**Performed:** inspected initial ZIP inventory; compared implementation and configuration claims with source; confirmed source clone `git status` clean; checked all README local image references, internal hash links, paired code fences, and heading structure with local scripts; parsed all SVGs as XML; searched source for token/bootstrap provisioning paths and benchmark definitions/results; rebuilt and listed the final ZIP; checked it contains only README/report/manifest/used SVG assets and no video extensions.

**Not performed:** application/backend/frontend tests; benchmark execution; Docker startup; migrations; API requests; authentication against a running Compose database; Windows PowerShell execution; visual rendering at target browser sizes; review of the user's local checkout. No test pass or fresh runtime result is asserted.

## Remaining limitation and application note

A new local developer cannot use protected API routes until maintainers provide an approved first-user/token provisioning mechanism. This package documents that prerequisite rather than inventing one. The Compose frontend proxy mismatch also remains a separate code/configuration issue. This report was prepared before the package was applied to the Windows checkout. The README, five SVG assets, report, and manifest were subsequently copied into the local working tree and hash-verified. Application code, configuration, migrations, tests, workflows, and infrastructure were not changed. Documentation files remain unstaged and uncommitted; nothing was pushed or published.