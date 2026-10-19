# EvidenceForge operator token and owner-workspace recovery

This command is separate from `evidenceforge-bootstrap`. It is a guarded, one-time recovery path for a very narrow state: PostgreSQL is at the repository migration head; exactly one active user exists and matches the independently verified external identity-provider subject; there are no workspaces or memberships; no currently usable token exists; and no prior bootstrap/recovery marker exists.

**Do not use this recovery path until target identity preflight and PostgreSQL TLS are configured and tested.** The current `compose.yaml` has no PostgreSQL TLS configuration or EvidenceForge deployment-ID label/network alias, so the host launcher intentionally refuses to proceed against that configuration.

## Safety requirements

- Use only after an approved incident/change request and a verified backup/recovery procedure.
- Independently verify the exact external identity-provider subject. `--identity-verified` is an operator acknowledgement, not cryptographic proof of identity.
- Keep the target profile, deployment Compose file, and public CA certificate outside the repository. The launcher rejects reparse points in the files and their ancestor directories and requires restrictive Windows ACLs throughout the path: only the current operator, LocalSystem, and local Administrators may have write/permission-changing access. Do not put credentials, private keys, passwords, tokens, or a connection URL in the profile.
- Set `EVIDENCEFORGE_RECOVERY_DATABASE_URL` only through the approved local secret/configuration mechanism. It must be a dedicated PostgreSQL URL with `sslmode=verify-full`, a deployment-specific hostname, and `sslrootcert=/run/evidenceforge/recovery-ca.pem`. Never print it or pass its value as a command-line argument.
- Run the launcher from a PowerShell session without ambient PostgreSQL driver configuration. The recovery CLI refuses to start when any of `PGOPTIONS`, `PGSERVICE`, `PGSERVICEFILE`, `PGHOST`, `PGPORT`, or `PGHOSTADDR` is set to a non-empty value, so ambient libpq settings cannot influence recovery connection targeting. Clear these variables—or start a clean session—before invoking the launcher, and restore them afterwards only for tools that genuinely rely on them.
- Use a unique DNS TLS hostname and a CA trusted specifically for this PostgreSQL deployment. Mount only the public CA certificate at `/run/evidenceforge/recovery-ca.pem` in the one-off recovery container. Keep the PostgreSQL server private key outside the repository and protect it with appropriate filesystem permissions.
- Configure PostgreSQL with TLS and a server-level custom setting named `evidenceforge.deployment_id`. The value must match the deployment ID in the trusted target profile. A table-only marker is not accepted as a substitute.
- The PostgreSQL service must have the Compose labels `com.docker.compose.project`, `com.docker.compose.service`, and `io.evidenceforge.deployment-id`, and the expected network must advertise the profile's unique TLS hostname as an alias.
- The host launcher checks the active Docker context, Compose project/service, container status, full container ID, PostgreSQL image and its immutable image ID, expected network/alias, deployment labels, and current container IP. It also verifies a trusted SHA-256 hash of the fully rendered Compose configuration (including interpolation), the public CA certificate hash, and the immutable API image ID. The API and database services must carry matching deployment labels. The CLI then checks TLS hostname/CA validation plus the live SQL database, schema/search path, deployment ID, server address, and server port inside the same transaction used for recovery writes.
- The launcher creates a short-lived target-attestation file (schema version 2, including the expected database port), mounts it read-only into a one-off API container, and deletes the temporary file on exit. The patched recovery CLI rejects version-1 attestations. The attestation is an operational control-plane handoff, not protection against a compromised Docker host or a malicious local administrator.
- Do not use this command against shared, staging, or production databases without required operational approval.
- Do not use bootstrap again to recover a token. The command does not delete or revoke historical token rows. It refuses if any token is currently usable, or if any workspace, membership, bootstrap marker, or prior recovery marker exists.
- The new workspace, OWNER membership, token hash, and audit event commit in one transaction. The event records a null application-user actor because the existing user did not initiate the operator operation.
- The plaintext bearer token is printed once after commit. Store it immediately in the approved secret manager. If commit/output status is ambiguous, do not rerun the command; verify state through the approved incident procedure.

## Target profile

Create an access-restricted JSON file outside the repository. These are illustrative placeholders, not a working deployment profile:

```json
{
  "version": 2,
  "docker_context": "default",
  "compose_file": "E:\\Deployments\\evidenceforge\\compose.yaml",
  "compose_project": "evidenceforge",
  "database_service": "db",
  "api_service": "api",
  "deployment_id": "ef-prod-unique-2026-01",
  "database_name": "evidenceforge",
  "schema_name": "public",
  "network_name": "evidenceforge_default",
  "tls_hostname": "pg-ef-prod-unique-2026-01.evidenceforge.internal",
  "database_image": "pgvector/pgvector:0.8.6-pg16",
  "database_image_id": "sha256:<64-hex-immutable-image-id>",
  "database_port": 5432,
  "api_image_id": "sha256:<64-hex-immutable-image-id>",
  "rendered_compose_sha256": "<64-hex-SHA-256-of-rendered-compose-config>",
  "ca_certificate_sha256": "<64-hex-SHA-256-of-public-CA-certificate>",
  "ca_certificate_host_path": "C:\\Secure\\EvidenceForge\\recovery-ca.pem",
  "ca_certificate_container_path": "/run/evidenceforge/recovery-ca.pem"
}
```

Before this profile is usable, the real Compose deployment must be provisioned with the matching label on both the database and API services, DNS alias, TLS server certificate, protected key mount, and server-level deployment-ID setting. Populate `rendered_compose_sha256` only from an independently reviewed, fully rendered `docker compose config --format json` result run from the deployment Compose directory and with the same Docker context, `--project-directory`, file, and project name used by the launcher; the launcher hashes this output in memory without printing it because interpolation may contain secrets. Populate `database_image_id` the same way, from the immutable image ID of the approved PostgreSQL image. The `database_port` value here is illustrative: it is the PostgreSQL server's listening port inside the database container (typically 5432), not any host-published port; independently verify the real value from the inspected database container and the live PostgreSQL server before recording it. Populate `api_image_id` from the inspected immutable image ID (not a mutable tag) of the rebuilt API image that contains the current, patched recovery CLI, and refresh it whenever the approved CLI image is rebuilt. Compute the CA hash from the approved public CA file. Store the profile, deployment Compose file, and CA file with the restrictive ACLs described above. These hashes are integrity pins, not a defense against a compromised Docker daemon or a malicious local administrator. Do not copy illustrative values blindly.

Both the target profile and the launcher-generated attestation are schema version 2; the patched recovery CLI rejects version-1 attestations, and the revised launcher refuses anything other than version-2 profiles. Before using the revised launcher, deliberately review and update every external trusted profile to schema version 2 with the new required fields—old or incompatible profile and attestation versions must fail closed, never be tolerated. Do not copy the illustrative values above into a real deployment profile; derive each value from your own reviewed deployment.

Use this method to calculate the rendered-configuration hash only after the deployment configuration has been reviewed. Substitute the approved non-secret paths/names; do not print `$renderedLines` or `$renderedText`, because rendered Compose JSON can contain interpolated secrets.

```powershell
$composeFile = 'C:\Deployments\EvidenceForge\compose.yaml'
$composeProject = 'evidenceforge'
$dockerContext = 'default'
Push-Location -LiteralPath (Split-Path -LiteralPath $composeFile -Parent)
try {
    $renderedLines = & docker --context $dockerContext compose `
        --file $composeFile `
        --project-directory (Split-Path -LiteralPath $composeFile -Parent) `
        --project-name $composeProject config --format json 2>$null
    if ($LASTEXITCODE -ne 0 -or @($renderedLines).Count -eq 0) {
        throw 'Could not render the approved Compose configuration.'
    }
    $renderedText = [string]::Join("`n", [string[]]@($renderedLines))
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($renderedText)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}
finally {
    Pop-Location
}
```

The printed value is the `rendered_compose_sha256` field. Store it only in the access-restricted profile, independently of the configuration being pinned. The hash calculation does not make an unreviewed Compose file trustworthy.

## Exit-code contract

The launcher ends with an explicit `exit`, so its result is the child `powershell.exe` process exit code. Do not call or dot-source the launcher inside your interactive console: its explicit `exit` closes that PowerShell process. For automation, use the encoded child-process invocation shown after the approved invocation template; this preserves `-RecoveryArguments` as an actual array and lets the parent read the child exit code.

| Exit code | Meaning | Required operator action |
|---|---|---|
| 0 | The recovery CLI reported completion and staging cleanup was verified. | Store the printed bearer token immediately in the approved secret manager. |
| 1 | Operational failure reported by the recovery CLI. The outcome is uncertain; this code intentionally makes no claim about whether a write transaction began, committed, or rolled back. If the recovery CLI itself exited with code 3, the launcher reports this as exit 1 (and prints why), because exit code 3 is reserved exclusively for committed-but-cleanup-failed. | Do NOT rerun. Verify the target database state through the approved read-only procedure first, then decide. |
| 2 | One of two things: (a) a documented refusal — the launcher or a documented CLI refusal check refused before any write; launcher refusals print `EvidenceForge operator recovery preflight refused:` followed by the cause; or (b) the launcher's generic catch trapped an unexpected launcher or Docker error and printed its exception message before exiting 2. Exit 2 alone does not establish whether a write occurred. | Do NOT retry automatically solely because the code is 2. First examine the diagnostic output: a documented refusal states an explicit cause that you can fix, review, and then rerun. If the output is an unexpected error, or anything about the outcome is uncertain, verify the target database through the approved read-only procedure before deciding on any rerun. |
| 3 | The recovery CLI reported success, but staging cleanup failed or could not be verified. The token should already be secured. The launcher emits code 3 only in this case: a raw recovery-CLI exit code 3 is always reported as exit 1 (see the exit 1 row), so launcher exit 3 unambiguously means committed-but-cleanup-failed. | Do NOT rerun recovery. The launcher prints the line `Staging volume requiring review: <name>`, preceded by a specific warning that contains the fully context-qualified inspection command (it always uses the target profile's configured Docker context). Run that printed inspection command — general form `docker --context <target-context> volume inspect <name>` — and confirm driver `local` plus label `io.evidenceforge.recovery-staging=true` for exactly that name. Only after identification succeeds, remove that same volume name in the same context: `docker --context <target-context> volume rm <name>`; inspection and removal must address the identical volume. If the printed warning was `staging volume identity changed` (the launcher tells you not to delete), or inspection cannot positively identify the volume, leave it in place and escalate through the incident channel. Confirm database state afterwards through the approved read-only procedure. |

- Exit 2 always dominates: if a pre-write refusal occurred and a later cleanup step also failed, the preserved result is still exit 2 with an added cleanup warning, never a rewritten code.
- Any other nonzero recovery result passes through unchanged so operational failures are never rewritten as refusals — with one documented, diagnostic-backed exception: a raw recovery-CLI exit code 3 is reported as exit 1, so launcher code 3 is reserved exclusively for committed-but-cleanup-failed.
- Whenever a cleanup problem is detected next to a nonzero recovery result, the launcher prints `recovery outcome preserved as exit <n>` plus `Staging volume requiring review: <name>`; the `<n>` code from the CLI governs retry behavior per the table above, and the volume review procedure from the exit 3 row applies to that name.

## Isolated validation before deployment

Run tests against disposable PostgreSQL containers only. The operator-recovery integration suite uses `EVIDENCEFORGE_OPERATOR_RECOVERY_TEST_DATABASE_URL`, which must point to an approved local/loopback test database. The unit tests validate missing/stale or version-incompatible attestations, invalid TLS modes, hostname mismatch, server-address mismatch, server-port mismatch, prohibited PostgreSQL environment overrides, and deployment-ID mismatch. Never point test variables at an application database.

For the PowerShell launcher contract, also run the Pester suites below on Windows PowerShell 5.1 from the repository root (Pester 3.4 or later):

```powershell
Invoke-Pester .\scripts\tests\operator-recovery-static.Tests.ps1
Invoke-Pester .\scripts\tests\operator-recovery-exit-contract.Tests.ps1
```

The first suite is static string-contract and safe to run anywhere. The second is behavioral: it stages disposable fixtures under %TEMP%, places a `docker` PATH shim in front, and drives the real launcher end to end to assert the entire outcome x cleanup exit-code matrix without contacting a Docker daemon; it no-ops on non-Windows hosts. Expect roughly a minute of runtime on a developer box.

Check the new Python files with Ruff and run the recovery unit/PostgreSQL tests, then rerun both bootstrap suites. Do not launch the PowerShell recovery wrapper merely to test command installation. The wrapper is intentionally expected to refuse against the current Compose file until the TLS and deployment-identity configuration exists.

## Approved invocation template — do not run during development

The recovery URL must already be supplied to the PowerShell process through the approved secret/configuration mechanism. Never paste that URL into a command or chat. Example argument shape only:

```powershell
$recoveryArgs = @(
  '--expected-database-name', '<database-name>',
  '--expected-schema-name', 'public',
  '--external-subject', '<exact-verified-identity-provider-subject>',
  '--workspace-name', '<approved-workspace-name>',
  '--operator-identity', '<approved-operator-handle>',
  '--approval-reference', '<incident-or-change-ticket>',
  '--identity-verified',
  '--confirm-recovery'
)

.\scripts\operator-recovery.ps1 `
  -TargetProfilePath '<protected-profile-outside-repository.json>' `
  -RecoveryArguments $recoveryArgs
```

The launcher forwards only the documented space-separated options shown above and refuses unknown, equals-form, or abbreviated options; `--target-attestation-file` is generated and supplied by the launcher itself, the recovery CLI accepts only its launcher-staged `/run/evidenceforge/recovery-target.json` value, and it must never appear in `$recoveryArgs`.

When automation must read the launcher result, use this child-process pattern. Do not flatten `$recoveryArgs` into `powershell.exe -File` arguments: values such as `--confirm-recovery` can be interpreted as parameter names instead of array elements. Use this only after the approved deployment and formal approval described above.

```powershell
$launcherPath = (Resolve-Path '.\scripts\operator-recovery.ps1').Path
$profilePath = (Resolve-Path $targetProfilePath).Path
$quotePowerShellLiteral = {
    param([string] $Value)
    "'" + $Value.Replace("'", "''") + "'"
}
$launcherLiteral = & $quotePowerShellLiteral $launcherPath
$profileLiteral = & $quotePowerShellLiteral $profilePath
$argumentLiterals = ($recoveryArgs | ForEach-Object {
    & $quotePowerShellLiteral $_
}) -join ', '
$childCommand = "& $launcherLiteral -TargetProfilePath $profileLiteral -RecoveryArguments @($argumentLiterals)"
$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCommand))
& powershell.exe -NoProfile -EncodedCommand $encodedCommand
$recoveryExitCode = $LASTEXITCODE
```

`$LASTEXITCODE` in the calling PowerShell process is the child process exit code. Capture it before running another native executable.

Do not run this template now. Use it only after a reviewed deployment change, verified certificate provisioning, successful disposable integration tests, and formal approval for the intended recovery.
