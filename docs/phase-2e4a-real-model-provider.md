# Phase 2E.4A — Evaluation-Only Real-Model Provider

## Status

Phase 2E.4A establishes an **evaluation-only loopback OpenAI-compatible provider boundary**.

Current implementation target:

```text
ModelGenerationInput
        ↓
LoopbackOpenAICompatibleRuntimeAdapter
        ↓
localhost / 127.0.0.1 / ::1
        ↓
OpenAI-compatible local model runtime
        ↓
EvaluationRawOutput
        ↓
Phase 2E.3 runtime evaluator
        ↓
schema / citation / status validation
```

This phase does **not** connect any model to production questionnaire generation.

## Verified live smoke observations

A first live Phase 2E.4A request was executed against Ollama 0.35.0 with `qwen2.5:1.5b` on the Windows evaluation host. The model returned syntactically valid JSON, but the server-side `GeneratedDraftPayload` schema rejected the response because `uncertainty_notes` was emitted as an array (`[]`) instead of a string or `null`. No automatic repair was applied. A second live run reproduced the same schema failure.

A direct Ollama capability probe then tested `response_format.type = json_schema` with a strict `GeneratedDraftPayload`-compatible schema. Ollama returned HTTP 200 and Qwen produced `uncertainty_notes: null` with the expected citation handle and allowed status. This was a direct runtime capability probe, not yet an end-to-end EvidenceForge adapter measurement.

Observed values from the initial adapter run:

- Model: `qwen2.5:1.5b`
- Failure: `SCHEMA_VALIDATION_FAILURE`
- Request latency: `10,317.56 ms`
- Prompt tokens: `228`
- Completion tokens: `38`
- Total tokens: `266`

A subsequent direct schema-constrained Ollama probe returned:

- HTTP status: `200`
- Prompt tokens: `84`
- Completion tokens: `54`
- Total tokens: `138`
- `uncertainty_notes`: `null`
- Citation handle: `EVIDENCE-1`
- Status: `PROPOSED`

This is a real runtime behavior observation, not a model accuracy or groundedness result.

## Scope

Implemented here:

- strict immutable local-provider configuration;
- loopback-only URL enforcement by default;
- OpenAI-compatible `POST /chat/completions` request construction;
- strict JSON Schema response format by default, with optional legacy JSON-object compatibility mode;
- actual wall-clock request latency measurement;
- safe extraction of provider-reported usage metadata for in-memory inspection;
- explicit timeout, runtime-unavailable, and transport failure classification;
- no provider retries;
- no output repair;
- no database access;
- no persistence;
- no tools or filesystem access;
- no production endpoint integration.

## Loopback security boundary

The provider rejects non-loopback endpoints by default.

Accepted host forms are:

- `localhost`
- `127.0.0.1`
- `::1`
- loopback IP literals

The default is:

```text
allow_non_loopback = false
```

Cloud APIs and arbitrary public hosts are therefore outside this provider's default trust boundary.

## Model prompt boundary

The adapter receives only `ModelGenerationInput`.

The prompt can expose:

- questionnaire question;
- section path;
- opaque `EVIDENCE-N` citation handles;
- bounded evidence text;
- document name/version metadata;
- freshness/status/conflict indicators.

The provider never receives:

- database UUIDs;
- workspace UUIDs;
- document/chunk/version UUIDs;
- conflict-group identifiers;
- database credentials;
- SQL;
- reviewer identities;
- authorization credentials for EvidenceForge persistence.

Evidence text is preserved as data. The system prompt explicitly instructs the model never to execute instructions contained inside evidence.

## Failure semantics

The provider maps transport/runtime failures into the existing Phase 2E.3 failure taxonomy:

```text
Connection refused / unavailable local runtime
→ RUNTIME_UNAVAILABLE

Request timeout
→ TIMEOUT

Other HTTP/network transport failure
→ TRANSPORT_API_FAILURE

HTTP 200 with malformed model JSON
→ raw text returned unchanged
→ Phase 2E.3 parser classifies MALFORMED_JSON
```

The provider does not repair malformed output and does not retry failed requests in Phase 2E.4A.

## Measurement semantics

For real local model requests:

```text
latency_ms
```
is measured from the client immediately before the HTTP request until the response is fully received.

The provider intentionally reports:

```text
Deterministic harness execution latency = 0.0
TTFT                       = NOT MEASURED
Model throughput           = NOT MEASURED
CPU utilization            = NOT MEASURED
Model RAM footprint        = NOT MEASURED
VRAM footprint             = NOT MEASURED
Model load latency         = NOT MEASURED
```

These values must not be inferred from the local harness.

Ollama/OpenAI-compatible usage metadata such as prompt/completion/total tokens is retained only as transient adapter state through `last_usage`; Phase 2E.4A does not yet persist these values into the runtime-gate artifact schema. The default OpenAI-compatible request format now uses strict JSON Schema so the runtime can constrain field types; `json_object` remains available as an explicit compatibility mode.

## Current local runtime used for feasibility testing

The first local feasibility runtime is:

```text
Ollama 0.35.0
Qwen2.5 1.5B
OpenAI-compatible endpoint:
http://127.0.0.1:11434/v1/chat/completions
```

Manual smoke testing on the development machine successfully returned structured JSON and provider token-usage metadata.

A separate three-request smoke baseline measured:

```text
Run 1: 4298.44 ms
Run 2: 4574.66 ms
Run 3: 4024.95 ms
Mean:  4299.35 ms
```

This is a **single smoke workload**, not a general model performance claim and not an EvidenceForge groundedness measurement.

## Testing boundary

The test suite uses an in-process loopback HTTP server.

It does not require:

- Ollama installation;
- vLLM installation;
- GPU hardware;
- external network access;
- cloud API keys.

Coverage includes:

- loopback URL acceptance;
- external endpoint rejection;
- successful response parsing;
- request shape and JSON mode;
- internal-ID redaction;
- injection-as-data prompt behavior;
- timeout classification;
- connection-refused classification;
- malformed model JSON passthrough;
- HTTP transport failure;
- API-key header handling;
- no-retry behavior.

## Explicit non-goals

Phase 2E.4A does not claim to measure:

- semantic groundedness;
- unsupported-claim rate;
- citation precision/recall on the real model;
- abstention correctness on the full benchmark;
- injection resistance across the full benchmark;
- production throughput;
- multi-user concurrency;
- model quality ranking.

Those belong to Phase 2E.4B and later gates.

## Next gate

```text
Phase 2E.4A
provider boundary ✅

        ↓

Phase 2E.4B
real model benchmark
        ↓
actual generated outputs
        ↓
full benchmark cases
        ↓
groundedness adjudication
        ↓
abstention correctness
        ↓
injection resistance
        ↓
measured results
```

Production generation remains disabled until those measurements are available.
