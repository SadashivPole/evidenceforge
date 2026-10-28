# Docker / Compose verification report — 2026-10-24/25

Baseline: `d0cded2301ec0c3cab3c774d93539f682e225d81` (post PR #26; hosted CI
run #53 / 40091805488 green, 6/6 jobs).

## Changes implemented (source)

| File                                              | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/Dockerfile`                             | Production multi-stage: `deps` → `builder` (creates an empty `public` directory because the repo intentionally ships none, then `OUTPUT_MODE=standalone npm run build`, asserts `.next/standalone/server.js`) → `runner` (standalone server only, `.next/static` + empty `public`, unprivileged `nextjs` user, `USER nextjs`, wget healthcheck on `/guided`, `node server.js`). Replaces the prior single-stage `npm run dev` image. |
| `frontend/next.config.mjs`                        | `output` conditional on `OUTPUT_MODE`; classic `npm run build`/`npm run start` behavior preserved (CI smoke unaffected). **Verified:** classic rewrites bake `EVIDENCEFORGE_API_URL` at build time (observed directly: a start-only env override is ignored). Standalone builds route through the runtime proxy below instead.                                                                                                                                               |
| `frontend/app/backend-api/[...path]/route.ts`     | Runtime proxy (pre-existing from prep): standalone mode resolves `EVIDENCEFORGE_API_URL` per process start; non-configured → predictable 502. Accepts only http(s) base URLs.                                                                                                                                                                                                                                                                                                |
| `backend/Dockerfile`                              | Multi-stage; builder pre-installs CPU-only torch (official CPU index; avoids ~2GB CUDA artifacts), then `pip install .`; runtime copies `/usr/local`, creates non-root `evidenceforge` (uid 10001); healthcheck on `/health/live` (process-only liveness by design); startup semantics unchanged (`alembic upgrade head` then uvicorn 0.0.0.0:8000).                                                                                                                         |
| `compose.yaml`                                    | api healthcheck probes **/health/ready** (real readiness contract `{"status":"ok","database":"ok"}` — 200 only when the DB connection succeeds, else 503); start_period 180s covers alembic + embedding model load. web healthcheck probes `/backend-api/health/ready` **through the runtime proxy** (proves api reachable from the web container). web depends on `api: service_healthy`. Published ports parameterized (`EF_API_PORT`, `EF_WEB_PORT`, defaults 8000/3000). |
| `backend/.dockerignore`, `frontend/.dockerignore` | Test/cache/artifact hygiene; build inputs unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `.github/workflows/ci.yml`                        | New jobs: **Playwright extended workflow e2e** (isolated PG service, `npm run test:workflow`) and **Docker image and compose smoke** (`docker compose config` render check, image builds, `up -d --wait`, exact readiness payload, `/guided` and proxy checks, project-scoped negative control with readiness polling). Teardown uses project-scoped `stop` + `rm -f` and removes the throwaway env file; it uses **no `-v`** and no explicit volume deletion. Unique project name `ef-ci-<run_id>-<run_attempt>`. |

## Verification status

| Check                                                  | Result                                                                                                                                                                                                                                                  |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dockerfiles/compose/ci written + YAML parse validation | **PASS (local static)**                                                                                                                                                                                                                                 |
| Image builds locally                                   | **BLOCKED** — no Docker daemon in this environment (`command -v docker` → absent).                                                                                                                                                                      |
| Compose boot/health/proxy locally                      | **BLOCKED** — same root cause.                                                                                                                                                                                                                          |
| CI job definition                                      | **DONE (source)**; first hosted run outcome must be observed in Actions after merge — not claimed green here.                                                                                                                                           |
| Equivalent runtime evidence without Docker             | **PASS (site-tested)** — standalone output built (`OUTPUT_MODE=standalone`), served `/guided` 200 and proxied `/backend-api/health/ready` 200 with runtime-resolved upstream; invalid runtime target yielded deterministic 502 through the proxy route. |

## Windows owner commands (to finish local verification)

```powershell
# From repo root; isolated project + throwaway env file under $env:TEMP
# (OUTSIDE the repository). No 'down -v' anywhere; teardown stops/removes
# only this project's containers/network.
# The password below is SYNTHETIC, unique per run, local to this throwaway
# stack, and must never be a real/production credential.
$env:COMPOSE_PROJECT_NAME = "evidenceforge-review-$((Get-Date).ToString('yyyyMMdd-HHmm'))"
New-Item -ItemType Directory -Force "$env:TEMP\$env:COMPOSE_PROJECT_NAME" | Out-Null
$envFile = "$env:TEMP\$env:COMPOSE_PROJECT_NAME\.env"

# 1) Generate one synthetic password ONCE into a variable...
$pgPassword = (New-Guid).Guid.Replace('-', '')
# 2) ...build DATABASE_URL from that same variable (single source of truth)...
$databaseUrl = "postgresql+psycopg://evidenceforge_review:${pgPassword}@db:5432/evidenceforge_review"
# 3) ...and only then write the complete env file (no reads of $envFile here!)
@"
POSTGRES_DB=evidenceforge_review
POSTGRES_USER=evidenceforge_review
POSTGRES_PASSWORD=$pgPassword
DATABASE_URL=$databaseUrl
ENVIRONMENT=test
LOG_LEVEL=INFO
EVIDENCEFORGE_API_URL=http://api:8000
"@ | Set-Content -Path $envFile -Encoding Utf8

docker compose --project-name $env:COMPOSE_PROJECT_NAME --env-file $envFile config
docker compose --project-name $env:COMPOSE_PROJECT_NAME --env-file $envFile build
docker compose --project-name $env:COMPOSE_PROJECT_NAME --env-file $envFile up -d --wait
irm http://localhost:3000/backend-api/health/ready   # expected: {"status":"ok","database":"ok"}
# Teardown (volumes kept; remove only name-scoped volume if you want it gone):
docker compose --project-name $env:COMPOSE_PROJECT_NAME --env-file $envFile stop
docker compose --project-name $env:COMPOSE_PROJECT_NAME --env-file $envFile rm -f
Remove-Item -Force $envFile   # the throwaway env file lives only in $env:TEMP
```

> Static review note: this snippet was reviewed line-by-line and type-checked
> as plain PowerShell text; it has NOT been executed (no PowerShell runtime
> exists in the verification sandbox). Runtime evidence must come from the
> owner Windows host or a hosted CI run.

## Declared follow-ups

1. API cold-boot downloads the sentence-transformers model once (~hundreds of
   MB) and needs its RAM headroom; start_period accounts for this.
2. API image size remains torch-dominated; further slimming (split embedding
   worker) is roadmap work, not alpha scope.


## 2026-10-27 day-finish fix and re-verification

**Root cause.** `frontend/public` is intentionally absent from Git and from the
build context. The final runtime stage still had
`COPY --from=builder ... /app/public ./public`, so Docker failed at that
instruction even though the Next.js compile had succeeded. The minimal fix adds
`RUN mkdir -p public` in the builder immediately after `COPY . .`. No fake
assets, dependency changes, or runtime-user changes were introduced.

**Newly executed evidence in the Arena review workspace (not a Docker image
build):**

- `npm ci`, `npm run lint`, `npm run typecheck`, `npm run format:check`,
  `npm run build`, `OUTPUT_MODE=standalone npm run build`: all exit 0.
- A host-level reproduction of the runtime tree verified `.next/standalone/server.js`,
  `.next/static`, and an empty `public`; the standalone server returned `/guided`
  200, `/backend-api/health/ready` exact `{"status":"ok","database":"ok"}`,
  `/backend-api/health/live` exact `{"status":"ok"}`, root redirect 307, and a
  deterministic 502 for a bogus upstream while `/guided` stayed 200.
- Playwright browser smoke against that standalone tree: **3 passed**, exit 0.
- Real backend + Next.js classic build + disposable local PostgreSQL/pgvector
  workflow suite: **9 passed**, exit 0.
- `docker compose --project-name ef-review-20261027 ... config --quiet`: exit 0;
  rendered config confirmed `DATABASE_URL` host `db`, web upstream `api:8000`,
  and host ports `18000/13000` for the isolated review project.
- `actionlint` v1.7.12 with ShellCheck: exit 0 after fixing unused loop variables,
  project-scoping the negative-control container, and removing the CI volume removal.
- `docker info`, `docker image ls`, `docker compose ... build web`, and
  `docker build ... ./frontend`: **BLOCKED** — the Docker CLI/client and Compose
  client are installed, but the daemon socket is absent. These are not image
  build passes.

A real image build and isolated Compose container runtime still require the
owner's Docker-capable Windows host or a hosted GitHub Actions run. The host-level
standalone checks reduce risk but are explicitly not equivalent to a container
runtime test.
