# Temporary Dependency Risk Acceptance

Decision date: 2026-10-19
Accepted by: Project maintainer
Review deadline: 2026-11-02
Status: Closed for the braces advisory on 2026-10-24 (advisory withdrawn upstream);
renewed-once temporary acceptance superseded; next periodic dependency review
no later than 2026-11-02 or at any release preparation, whichever is earlier

## Finding

GitHub Advisory: GHSA-vfj7-8cjw-p6xm
CVE: CVE-2026-93687
Package: npm `braces@3.0.3`
Advisory severity: High
Advisory: https://github.com/advisories/GHSA-vfj7-8cjw-p6xm
Maintainer discussion: https://github.com/micromatch/braces/issues/70

At the decision date, the GitHub advisory lists versions through 3.0.3 as
affected and does not list a patched version. The upstream package maintainer
has publicly disputed the advisory's assessment. This record documents the
decision; it does not declare the finding resolved or prove it is
non-exploitable.

## Dependency path and observed audit results

The local dependency path reported by `npm explain braces` is:

`eslint-config-next@16.4.2`
-> `@next/eslint-plugin-next@16.4.2`
-> `fast-glob@3.3.1`
-> `micromatch@4.0.8`
-> `braces@3.0.3`

At the time of review, `npm audit --omit=dev` reported zero vulnerabilities.
The full `npm audit` reported five high-severity findings associated with
this dependency chain. The production-only result does not establish that
the complete dependency tree is clean, nor does it prove that every
deployment artifact excludes the development tooling.

## Decision and rationale

The project maintainer has temporarily accepted this finding because the
observed dependency path is in the frontend development-tooling tree, the
production-only audit was clean, and the advisory has no listed patched
version while the upstream maintainer disputes the finding.

This is a time-bounded risk decision, not a claim that the package is safe
or the finding is false. The report must remain visible in full dependency
audits until reassessment resolves or renews the decision.

## Current safeguards

- Do not run `npm audit fix --force`; npm proposed a breaking downgrade of
  `eslint-config-next`.
- Preserve the lockfile and review dependency changes rather than making
  unverified overrides.
- Do not introduce application behavior that passes attacker-controlled
  brace/glob patterns into this tooling.
- Run `npm audit` and `npm audit --omit=dev` before a release and when
  reassessing this risk.
- Do not describe the full dependency audit as clean while the finding
  remains.
- Verify what is included in release/container artifacts before asserting
  that a development dependency is absent from production.

## Reassessment requirements

Reassess no later than 2026-11-02, and earlier if any of the following
occurs:

1. A patched `braces` release or a material advisory update is published.
2. The dependency chain or severity changes.
3. Review establishes that attacker-controlled brace/glob patterns can
   reach the affected tooling in the project's build or CI workflow.
4. The production/container dependency inventory changes.

At reassessment, record the new audit output, the available remediation
options, and whether the temporary acceptance is closed, renewed, or the
release is held.

## Reassessment update (2026-10-21)

A fresh frontend audit on 2026-10-21 reported:

- `npm audit`: five high-severity findings flowing through the `braces@3.0.3` advisory and the `micromatch` / `fast-glob` / Next.js ESLint tooling dependency chain.
- `npm audit --omit=dev`: zero reported vulnerabilities.

The production-only result does not prove the affected package is absent from the current frontend container. `frontend/Dockerfile` runs `npm ci` and starts `npm run dev`; a production-optimized frontend image and its dependency inventory have not been validated. Do not run `npm audit fix --force` as a blind remediation because npm proposed a breaking downgrade of `eslint-config-next`.

The push-triggered CI run for the pre-release documentation baseline (`f96760d01a9c3eddb65bd2119e69ed0ff5c5d5fc`; [run 39606573588](https://github.com/SadashivPole/evidenceforge/actions/runs/39606573588)) completed successfully with all four jobs passing: Backend, Frontend, PostgreSQL bootstrap/authentication, and PostgreSQL concurrency. Passing CI does not resolve the advisory or independently establish production deployment safety.

The temporary acceptance remains in effect until reassessment, due no later than 2026-11-02. This is not a claim that the affected package is safe, and the full dependency audit must not be described as clean.

## Release limitation

This file records a time-bounded risk decision; it does not resolve the advisory or independently authorize a production release. Any alpha release must disclose this finding. Reassess earlier if a patch or material advisory change appears, the dependency chain changes, or the production/container dependency inventory is updated.

## Reassessment update (2026-10-24) — braces closed; new zod finding remediated

Fresh commands were executed today against `main` `46cba86b9f4cf78dc130e9937452d984b27581f4`
(from `frontend/`, npm@11.21.1 conventions, lockfile honored):

- `npm audit` (full): initially reported **1 high** finding — `zod <4.6.6` (GHSA-mrhx-c7xc-m3w5,
  memory exhaustion validating oversized constrained collections), dev-only chain
  `eslint-config-next@16.4.2` → `eslint-plugin-react-hooks@7.1.1` → `zod@^3.25.0 || ^4.0.0` → `zod@4.6.5`.
  The former `braces` advisory no longer appears.
- `npm audit --omit=dev`: **0** vulnerabilities.
- `npm explain braces`: `braces@3.0.3` is still installed, dev-only,
  via `eslint-config-next@16.4.2` → `@next/eslint-plugin-next@16.4.2` → `fast-glob@3.3.1` →
  `micromatch@4.0.8` → `braces@3.0.3` (unchanged chain).

braces disposition — **closed**: GitHub formally withdrew GHSA-vfj7-8cjw-p6xm on
2026-10-23 (the official advisory API now titles it "Withdrawn Advisory: braces vulnerable
to stack-exhaustion denial of service through deeply nested patterns"; withdrawal timestamp
reported as 2026-10-23T12:53:02Z, reason: not considered practically exploitable; no patched
version was ever listed and `braces@3.0.3` remains the latest published release). Today's
fresh `npm audit` output confirms the advisory database no longer reports the finding.
This closes the tracked risk record; it deliberately does **not** declare `braces@3.0.3`
"safe" in any absolute sense, and the dev-only provenance + production-artifact caveats
below still apply.

zod disposition — **remediated (option B, supported update)**: plain `npm audit fix`
(no `--force`) applied a non-breaking `zod@4.6.5 → 4.6.7` lockfile bump (single-entry diff
verified; no version overrides, no breaking downgrades). Post-fix verification on this tree:
`npm audit` **0** (full), `npm audit --omit=dev` **0**, `npm explain zod` resolves to `zod@4.6.7`,
all above advisory's patched floor (4.6.6). Existing gates re-run after the bump:
`npm run lint`, `npm run typecheck`, `npm run format:check`, `npm run build` — all pass.

Ongoing controls retained (unchanged): do not run `npm audit fix --force`; do not describe
the full dependency tree as clean without a fresh audit; before asserting a development
dependency is absent from a release/container artifact, verify that artifact's actual
dependency inventory; re-run both audit modes before any release and at the next periodic
review (no later than 2026-11-02 **or** at release preparation, whichever is earlier). If
the braces advisory is reinstated or materially updated, or the zod chain regresses below
4.6.6, reopen this record immediately.
