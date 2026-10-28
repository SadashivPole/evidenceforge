# Question-first, evidence-guided UX — 27 October 2026

Scope: a focused change to the existing `/guided` workbench. It adds natural-language question discovery and a deterministic next-question suggestion without replacing the existing questionnaire/version/question selectors, save/revision/citation/review behavior, or workspace authorization model.

## What changed for users

Before: a reviewer connected with a session token, chose a workspace, questionnaire, version, and question from selectors, then manually clicked “Find supporting evidence”. The advanced panel exposed UUIDs and the normal path required knowing which questionnaire/version contained a question.

After: the reviewer can type a question in ordinary words, press Enter, and see matched **real imported questionnaire questions** with readable questionnaire, version, section, and question text. A single confident match is selected and grounded automatically. An ambiguous query shows multiple real options and grounds nothing until the user chooses one. The existing evidence cards show the actual excerpts and provenance returned by the authorized grounding endpoint. A “Suggested next question” card then offers another real question, with an honest basis label and an “Explore this question” action.

The original selectors remain available as a manual fallback. Stable IDs remain in React state and API URLs; they are not used as primary visible labels. The collapsed advanced-developer panel remains diagnostic and is not part of the ordinary review path.

## Matching method — honest statement

This change uses **deterministic lexical word matching**, not a chatbot, not a vector-search capability invented for this task, and not model-backed generation. `frontend/app/guided/questionSearch.ts`:

1. Normalizes text (Unicode normalization, lowercase, alphanumeric tokens).
2. Removes a fixed list of common question/stop words.
3. Applies a small transparent word-form table plus simple suffix normalization (for example, `encrypted`/`encryption` → `encrypt`, `backups` → `backup`).
4. Scores real question text (and, with lower weight, section/sheet names) using inverse document frequency over the authorized question list.
5. Requires an exact phrase or at least two shared meaningful terms for multi-term queries.
6. Treats a close runner-up as ambiguous instead of silently choosing it.

The UI label says “Deterministic word matching · no AI answer”. The new code contains no network calls inside the matching module and no dependency change.

## Existing API endpoints used

All calls go through the existing same-origin `/backend-api` proxy with the in-memory bearer token. No new backend endpoint, cloud dependency, or database change was added.

- `GET /workspaces` — session connection and workspace names.
- `GET /workspaces/{workspace_id}/questionnaires` and `GET /workspaces/{workspace_id}/documents` — workspace-scoped context and readable evidence-document names.
- `GET /workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions` — all authorized immutable versions.
- `GET /workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{version_id}/questions` — all real questions in each authorized version.
- `GET /workspaces/{workspace_id}/questionnaires/{questionnaire_id}/versions/{version_id}/responses` — real latest revision status/answer used by the next-question rule.
- `GET .../questions/{question_id}/grounding` — existing deterministic evidence retrieval.
- `GET/PUT .../questions/{question_id}/response` and `POST .../questions/{question_id}/review` — unchanged save/revision/citation/review behavior.

The discovery index is built lazily on the first search by calling the existing authorized versions and questions endpoints for the currently selected workspace. It is stored in component state keyed by workspace and discarded on token/workspace change or after questionnaire import. A stale response from a previous workspace is ignored with an epoch counter. No question or evidence record from another workspace is added to the index.

## Next-question recommendation

`frontend/app/guided/suggestion.ts` recommends only a real question from the currently selected authorized questionnaire version. It does not generate question text.

Priority:

1. **Unfinished response** — when `GET .../responses` succeeds, choose the first later question whose saved status is not `APPROVED` or whose saved answer is blank. Labels: “No saved response in this version” or “Unfinished response in this version”.
2. **Shared terms/evidence** — otherwise, choose a later real question sharing at least two normalized terms with the current question or with excerpts actually returned by grounding. Label: “Suggested based on shared terms/evidence”.
3. **Order** — otherwise, choose the next question by imported ordinal. Label: “Next in questionnaire” when response status is known; “Next in questionnaire (status unavailable)” when the response-list call failed. The UI also states that saved-response status could not be loaded.

If there is no later question, the UI shows “No suggested next question available”. If grounding returned no evidence, the suggestion card says no evidence was found and that the suggestion uses only real questionnaire questions and saved response status. No citation or answer is fabricated.

## Empty, error, and accessibility states

The new question-search section provides:

- no authorized workspace/connection, too-short query, all-stop-word query, and overlong query errors;
- a loading state with `aria-busy`, disabled submit button, and polite status text;
- no-questionnaire and no-imported-question empty states;
- no-match state that explicitly says no question was grounded and no evidence/citation was invented;
- ambiguous state with multiple keyboard-operable option buttons;
- API/network error state with `role="alert"`, the backend’s safe detail, and a “Try again” button;
- no-evidence state in the existing evidence card;
- no-next-question state.

Semantic headings/labels, a labelled search input, a normal form submit for Enter-key support, visible focus styles, text badges (not color alone), `role="status"`/`role="alert"` regions, and a single-column phone layout are used. Evidence cards continue to expose source name, excerpt, version, section/page when present, a citation checkbox, and collapsed technical provenance. The collapsed technical block now explains that internal identifiers are for debugging only.

## Tests added

- `frontend/e2e/question-first.spec.ts` — pure helper tests plus browser tests using synthetic, browser-local API responses. Covers ordinary paraphrase, ambiguity, no match, evidence found with metadata, no evidence, unfinished-response preference, shared-terms/evidence basis, no next question, no questionnaire, API failure, validation/loading/keyboard/accessibility basics, bearer-token/workspace-scoped calls, and workspace-switch clearing.
- `frontend/e2e/workflow/08-authz.spec.ts` — extended existing real-backend cross-workspace test so the question-first UI cannot surface another workspace’s imported question to a VIEWER principal whose workspace list contains only its own workspace.

No real token, customer evidence, production database, or external service is used by the new tests.

## Known limitations

- Discovery currently loads all versions/questions of the selected workspace lazily. This is acceptable for the alpha-sized questionnaires in the repository but is an N+1-style read pattern; a production-scale deployment should add a deliberately authorized server-side search endpoint with pagination and tests.
- Lexical matching is not semantic understanding. Paraphrases that share no normalized words will produce no match; that is intentional to avoid silently mapping unrelated questions.
- The suggestion is deterministic and conservative. It does not use the optional local Ollama adapter or any model.
- The existing advanced-developer UUID inputs and collapsed evidence chunk IDs remain as diagnostic affordances; ordinary labels use readable names.
- Viewer enrollment/production user registration and real production LLM answer generation remain separate product gaps. **Real production LLM generation is still unimplemented** in this change and, per source inspection, remains unwired to any production user-facing generation route.
- At initial implementation, these checks were outstanding; see the verification update below for completed local checks and remaining release gates.

## Verification update (2026-10-27)

- Backend test suite: 806 passed, 31 skipped.
- Frontend lint, typecheck, format check, and standalone production build: passed.
- Question-first Playwright suite: 70 passed across five repetitions; workflow E2E suite: 9 passed.
- The frontend Docker image built successfully. The isolated Compose database, API, and web services reported healthy; all five API, page, and proxy HTTP smoke checks returned 200.
- The existing backend image was reused only after application/configuration SHA-256 hashes matched the workspace, excluding Python cache files. The modified backend Dockerfile was not freshly built because disk space was limited.
- Candidate GitHub Actions jobs still need a hosted run. The Ollama adapter remains unwired to production API/UI generation, and real-model runtime and quality metrics remain unmeasured.

## Verification update (2026-10-28)

- Hosted GitHub Actions run [#54](https://github.com/SadashivPole/evidenceforge/actions/runs/40617147850) passed all eight jobs for the candidate merged through [PR #27](https://github.com/SadashivPole/evidenceforge/pull/27).
- The hosted Docker image/Compose job freshly built both backend and frontend images and passed its startup, readiness, guided-page, API proxy, negative-control, and teardown checks.
- The local backend suite reported 806 passed and 31 skipped. Frontend lint, typecheck, format checks, and the recorded Playwright checks passed.
- The local Compose check reused the backend image after source/configuration hash parity checks due to local disk constraints. The hosted run provides the fresh backend Docker build verification.
- The optional Ollama adapter remains unwired to a production API/UI generation route. Real-model runtime, inference performance, and answer-quality metrics remain unmeasured.
