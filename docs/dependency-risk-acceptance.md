# Temporary Dependency Risk Acceptance

Decision date: 2026-10-19
Accepted by: Project maintainer
Review deadline: 2026-11-02
Status: Temporarily accepted; reassessment required by the deadline

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

## Release limitation

This record does not replace CI validation. The local frontend checks passed
on Node.js 24.19.0 after the CI and documentation update, but a hosted
GitHub Actions run for the updated workflow has not yet been verified.
No release, commit, or push is authorized by this document.