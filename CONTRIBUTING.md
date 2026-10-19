# Contributing to EvidenceForge

Thanks for helping improve EvidenceForge. It is a development workbench for questionnaire evidence, provenance, retrieval, and review—not a compliance certification or an autonomous answer-submission system. Please keep changes focused and preserve that product boundary in documentation and tests.

## Before you start

- Inspect `git status --short` and preserve changes already in your working tree. Use a dedicated branch for your contribution.
- Review the README and relevant runbooks before changing setup, authentication, database, migration, or recovery behavior.
- Do not submit passwords, bearer tokens, `.env` files, database dumps, recovered data, recordings, logs containing sensitive values, or generated build/cache artifacts.
- Do not run integration tests against a personal, production, recovered, or otherwise valuable database. PostgreSQL integration tests must use a dedicated disposable test database and the documented test-only environment variables.
- For a suspected security vulnerability, follow [SECURITY.md](SECURITY.md); do not file exploit details in a public issue.

## Development setup

The CI workflow currently uses Python 3.12 and Node.js 24 LTS. Install backend development dependencies from the repository root:

```powershell
python -m pip install -e "backend[dev]"
```

Install frontend dependencies from `frontend/`:

```powershell
npm ci
```

Use the root README and applicable operator runbooks for Docker Compose and database setup. Do not assume a Compose-internal database hostname is reachable from a host-run process.

## Checks

Run the relevant checks for the files you changed. The usual backend checks from the repository root are:

```powershell
ruff check backend/app backend/tests backend/migrations
ruff format --check backend/app backend/tests backend/migrations
python -m compileall -q backend
python -m pytest -q
```

Some PostgreSQL integration tests require dedicated database environment variables and a migrated, disposable test database. Read the test configuration before running them; do not point tests at an application or recovered validation database. A passing unit-test run that skips database integration tests is not evidence that those integration tests passed.

Frontend checks from `frontend/` are:

```powershell
npm ci
npm run lint
npm run typecheck
npm run format:check
npm run build
```

The repository CI workflow is the authoritative shared check, but do not state that CI passed unless you have verified the actual run for the commit under review.

## Pull requests

- Explain the problem, approach, behavior change, and any operational or security implications.
- Add or update tests for behavior changes and documentation for user-visible setup or recovery changes.
- Keep changes small; avoid unrelated dependency upgrades, generated files, blanket line-ending changes, and drive-by formatting.
- Run `git diff --check`, inspect the full diff, and confirm that only intended files are changed.
- Document known limitations and skipped tests honestly. Never claim test coverage that was not run.

## Licensing

By intentionally submitting a contribution for inclusion in EvidenceForge, you agree that the contribution is submitted under the Apache License, Version 2.0, unless a separate written agreement says otherwise. This does not replace or override any separate agreement you may already have with the maintainers.
