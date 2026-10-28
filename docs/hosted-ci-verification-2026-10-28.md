# Hosted CI Verification — 2026-10-28

## Release identity

- Repository: [SadashivPole/evidenceforge](https://github.com/SadashivPole/evidenceforge)
- Pull request: [#27 — add EvidenceForge question-first alpha workflow](https://github.com/SadashivPole/evidenceforge/pull/27)
- Merge commit: [`b32dea1acb22ad9674171f36ff62542978b0c35e`](https://github.com/SadashivPole/evidenceforge/commit/b32dea1acb22ad9674171f36ff62542978b0c35e)
- Hosted workflow run: [#54](https://github.com/SadashivPole/evidenceforge/actions/runs/40617147850)
- Result: **Success — all eight jobs passed**

## Hosted jobs

1. Backend PostgreSQL bootstrap and authentication
2. Backend
3. Frontend
4. Playwright guided workflow (8.1–8.8, disposable database)
5. Docker image and Compose smoke
6. Backend PostgreSQL concurrency
7. Playwright browser smoke
8. Backend PostgreSQL migration 0008 backfill

## Docker and Compose verification

The hosted Docker job freshly built both the backend and frontend images from the candidate's source. Its Compose smoke workflow passed startup and service-readiness checks, API readiness, the guided page, the frontend API proxy, an invalid-target negative control, and teardown.

The local Windows Compose smoke check had used an existing backend image only after application/configuration source hashes matched the working tree; local disk space limited a fresh backend build there. The hosted job subsequently built both current images successfully. These are distinct verification environments and should be described separately.

## Local verification recorded for the review tree

- Backend test suite: **806 passed, 31 skipped**
- Frontend lint, typecheck, and format checks: **passed**
- Question-first Playwright suite: **70 passed across five repetitions**
- Workflow E2E suite: **9 passed**
- Browser smoke: **3 passed**
- Latest frontend Playwright run: **17/17 passed**

These are the recorded local results from the review process; the hosted run supplies the independent CI and fresh Docker-build confirmation.

## Limitations that remain open

- The optional Ollama adapter is not wired into a production-facing API/UI answer-generation route.
- Real-model inference runtime, performance, and answer quality have not been measured.
- Evidence retrieval and questionnaire matching do not imply that an answer was generated, approved, or submitted automatically.
- Production registration/enrollment and complete import/export workflows remain outside the implemented alpha capability.

## Interpretation

This run closes the candidate's pending hosted-CI and fresh hosted Docker-build verification items. It does not constitute a production-readiness certification or evidence of measured real-model generation quality.

For product workflow details, see [question-first UX verification](question-first-ux-2026-10-27.md). Historical snapshots remain available in [Docker verification](docker-verification-2026-10-24.md) and [alpha verification](alpha-verification-2026-10-24.md).
