# Change manifest

Package: `evidenceforge-readme-redesign-final.zip`

> **Historical audit and subsequent publication:** This manifest records the original package, which was audited against public `main` at `027e104`. After that audit, the README redesign was applied locally, committed as [`5eacc0fc5cda4ea8436eafabd47b68f154708c9a`](https://github.com/SadashivPole/evidenceforge/commit/5eacc0fc5cda4ea8436eafabd47b68f154708c9a), and pushed to public `main`. Its [GitHub Actions CI run](https://github.com/SadashivPole/evidenceforge/actions/runs/38136621746) passed the **Backend**, **Backend PostgreSQL concurrency**, and **Frontend** jobs. This later publication does not change the original audit scope or limitations.

| Path in package | Action for target checkout | Notes |
|---|---|---|
| `README.md` | **Updated** (review first; preserve current file before replacing) | Audited and refined README based on public source HEAD `027e104`; distinguishes the lexical baseline from hybrid RRF metrics, explains unavailable first-user/token bootstrap, documents the confirmed Compose frontend proxy mismatch, and labels supplied synthetic demo outcomes. |
| `docs/assets/evidenceforge-architecture.svg` | **Added** | Original architecture diagram. |
| `docs/assets/evidenceforge-evidence-citation.svg` | **Added** | Original evidence/version/chunk/citation diagram. |
| `docs/assets/evidenceforge-local-quickstart.svg` | **Added** | Original local startup path diagram. |
| `docs/assets/evidenceforge-response-lifecycle.svg` | **Added** | Original idempotency and revision flow diagram. |
| `docs/assets/evidenceforge-workspace-authorization.svg` | **Added** | Original server-derived access flow diagram. |
| `docs/README-REDESIGN-REPORT.md` | **Added** | Source scope, feature inventory, design choices, and validation limitations. |
| `CHANGE-MANIFEST.md` | **Added** | This package manifest. |

## Intentionally preserved / excluded

- The previous README is not included in this package. During local application, the prior README was backed up outside the repository; keep that backup until the redesigned documentation has been reviewed.
- All application source, configuration, migrations, tests, workflows, and infrastructure files are excluded and were not modified.
- `.git`, `.env`, credentials, local databases, logs, dependency/build caches, experimental files, and all video/recording files are excluded.
- **Original package state:** No commits, pushes, releases, or public changes had been made when the package was prepared. **Subsequent publication:** the README redesign was committed as [`5eacc0fc5cda4ea8436eafabd47b68f154708c9a`](https://github.com/SadashivPole/evidenceforge/commit/5eacc0fc5cda4ea8436eafabd47b68f154708c9a), pushed to public `main`, and its [CI run](https://github.com/SadashivPole/evidenceforge/actions/runs/38136621746) passed the **Backend**, **Backend PostgreSQL concurrency**, and **Frontend** jobs.
