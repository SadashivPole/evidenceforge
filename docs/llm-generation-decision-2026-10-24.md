# LLM generation provider decision & evaluation report — 2026-10-24/25

Verification baseline: `d0cded2301ec0c3cab3c774d93539f682e225d81` (post PR #26).
Historical context read first: `docs/phase-2d-search-fusion.md`,
`docs/phase-2e-generation-eval.md`, plus sources
`backend/app/questionnaires/generation/` (provider, schemas, projection,
validator, config, errors, types), `backend/app/questionnaires/review/service.py`,
and `backend/tests/evaluation/test_generation_design_gate.py`.

## 1. Gate audit — which 2D/2E generation exit criteria are already met in-source

| Gate (paraphrased)                                                                             | Status in-source                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Provider-neutral adapter boundary isolating SDK/credentials/network from domain                | **Met** — `GenerationProvider` abstract class; `MockEvaluationProvider` (evaluation-only, no network/keys).                                                                                                                      |
| Strict structured output schema for untrusted model payloads                                   | **Met** — `GeneratedDraftPayload` (extra=forbid, bounded lengths, handle format `EVIDENCE-N`, duplicates rejected).                                                                                                              |
| Model may only propose PROPOSED / INSUFFICIENT_EVIDENCE; workflow/approval states server-owned | **Met** — `ALLOWED_MODEL_PROPOSED_STATUSES` enforced in schema.                                                                                                                                                                  |
| Sanitized projection removes raw UUIDs/scopes before any model call                            | **Met** — `project_generation_context_to_model_input` excludes chunk/doc/version/workspace UUIDs; keeps freshness/conflict markers.                                                                                              |
| Server-side validation of untrusted output against the exact authorized context                | **Met** — `validate_generated_draft`: handle resolution vs. context, stale/conflict guard, status integrity.                                                                                                                     |
| Mandatory human review; no auto-persist of model text                                          | **Met** — review service is the only approval state machine; drafts are transient.                                                                                                                                               |
| Production-path provider implementation (real HTTP adapter)                                    | **Was unmet → addressed by this change set** (`local_ollama_provider.py`).                                                                                                                                                       |
| Configured explicit model id, timeouts, bounded retries                                        | **Was unmet → addressed** (env settings, validated bounds).                                                                                                                                                                      |
| Opt-in local-only runtime with constrained URL (no cloud egress, no arbitrary SSRF)            | **Was unmet → addressed** (loopback-only default; explicit private-IP opt-in; DNS names rejected: rebind risk).                                                                                                                  |
| Mock-vs-real separation                                                                        | **Extended** — adapter tests use mocked transport; real-model results not claimed.                                                                                                                                               |
| Real-model evaluation gate (Precision@1 ≥ 0.7, Coverage > 0.9, Abstention ≥ 0.8)               | **NOT MEASURED** — no local LLM runtime in this environment; no heavyweight model was downloaded. BLOCKED.                                                                                                                       |
| End-to-end product integration (workbench/API invoking the provider)                           | **INCOMPLETE / deliberately not shipped** — no API route or UI wiring; the adapter exists for production-path use, gated OFF, and the generated-draft UX stays out of alpha until the real-model gate above is measured. See §4. |

## 2. Provider decision

- **Ollama-compatible local-only adapter** (`POST {base}/api/generate`, non-streaming, `format: "json"`), built on the existing `httpx` dependency only (no vendor SDK, no API keys, no cloud egress).
- **DISABLED by default** (`GENERATION_PROVIDER_ENABLED=false`). Factory `get_local_generation_provider()` returns None unless enabled AND a model id is set.
- Env surface (all validated/bounded): `GENERATION_PROVIDER_BASE_URL` (loopback-only: 127.0.0.1 / ::1 / localhost), `GENERATION_PROVIDER_MODEL_ID` (required when enabled), `GENERATION_PROVIDER_TIMEOUT_SECONDS` (1–120s), `GENERATION_PROVIDER_MAX_RETRIES` (0–5), `GENERATION_PROVIDER_ALLOW_PRIVATE_HOSTS` (RFC1918 literal IPs only).

## 3. Safety properties (enforced in code, covered by tests)

1. URL rejection matrix: non-http schemes, credentials in URL, public hosts, cloud metadata (169.254.169.254), arbitrary DNS names → ValueError.
2. No silent repair: one JSON parse of the envelope, one of the inner payload; any deviation raises `GenerationPayloadMalformedError`. No regex extraction, no fallback models, no retry-on-malformed.
3. Bounded transport: explicit connect/read timeouts, retry budget ≤5 with capped backoff; `trust_env=False`, `follow_redirects=False`.
4. Authority isolation: adapter only returns an untrusted `GeneratedDraftPayload`; persistence/approval/state-change imports are statically barred (AST scan test) — the module cannot touch sessions, persistence, review service, or workflow state.
5. Injection posture: evidence/question text interpolated as untrusted data; model output is data (never executed); strict schema rejects tool-like extra fields.
6. No raw identifiers on the model surface: prompt template consumes ONLY the sanitized projection; test asserts no UUID strings appear in a rendered prompt.
7. Logging: model id, latency, attempt count only — never prompts/outputs.

## 4. Test evidence (mocked transport; deterministic)

`backend/tests/test_generation_local_provider.py` — 34 cases PASS (re-verified 2026-10-27):
disabled-by-default, success round-trip, unavailable, timeout, 5xx,
malformed×4, bounded retries (exact call count), URL rejections×6 + loopback×2 +
private-opt-in×2, unknown/duplicate handles, forbidden status,
stale-evidence guard, insufficient-evidence×2, injection-as-data,
no-persistence import scan, no-UUID prompt scan.

Combined with existing boundary and 2E design-gate suites: **80 passed** (re-verified 2026-10-27).

Real-model execution: **BLOCKED** — no Ollama runtime present in this
environment, and no heavyweight model download was initiated without
disk/RAM evidence and owner approval.

## 5. Path to product integration (owner decision point)

The remaining step to _product-level_ generation is a reviewable draft
endpoint (e.g., POST `…/questions/{id}/generate-draft`) that authenticates,
builds the server context, sanitizes via projection, calls the provider,
validates via `validate_generated_draft()` against the exact authorized
context, and returns a transient draft for human review — never persisting or
approving automatically. This is intentionally NOT shipped until the
real-model evaluation gate in §1 is measured and recorded.


## 2026-10-27 status clarification

The adapter remains opt-in and disabled by default. Its 34 tests use mocked
transport only; they do not prove a real Ollama runtime, real-model quality, or
a production generation workflow. No API route or UI currently calls
`get_local_generation_provider()` for user-facing generation. Real-model
evaluation and a reviewable transient-draft endpoint are separate backlog items.
