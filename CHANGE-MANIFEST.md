# Change manifest

Package: `evidenceforge-readme-redesign-final.zip`

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
- No commits, pushes, releases, or public changes were made.