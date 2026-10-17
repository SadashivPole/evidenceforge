# Static contract tests for the guarded launcher.
# Compatible with Windows PowerShell 5.1 and Pester 3.4; these tests never execute the launcher.

$launcherPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'operator-recovery.ps1'
$launcherSource = [System.IO.File]::ReadAllText($launcherPath)

Describe 'EvidenceForge operator-recovery launcher static contract' {
    It 'parses without PowerShell syntax errors' {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $launcherPath,
            [ref]$tokens,
            [ref]$parseErrors
        ) | Out-Null
        $parseErrors.Count | Should Be 0
    }

    It 'requires an external target profile and explicit recovery acknowledgements' {
        $launcherSource.Contains('[string] $TargetProfilePath') | Should Be $true
        $launcherSource.Contains('--identity-verified') | Should Be $true
        $launcherSource.Contains('--confirm-recovery') | Should Be $true
        $launcherSource.Contains('Assert-RestrictedPath') | Should Be $true
        $launcherSource.Contains('-MustBeOutsideRepository') | Should Be $true
    }

    It 'requires and verifies pinned rendered Compose configuration' {
        $launcherSource.Contains('rendered_compose_sha256') | Should Be $true
        $launcherSource.Contains('docker @composeBase config --format json') | Should Be $true
        $launcherSource.Contains('the rendered Compose configuration hash does not match the trusted target profile') | Should Be $true
    }

    It 'pins the CA certificate and API image to trusted hashes' {
        $launcherSource.Contains('ca_certificate_sha256') | Should Be $true
        $launcherSource.Contains('Get-FileHash -LiteralPath $targetProfile.ca_certificate_host_path -Algorithm SHA256') | Should Be $true
        $launcherSource.Contains('api_image_id') | Should Be $true
        $launcherSource.Contains('the running API container or its current image tag does not match the pinned API image ID') | Should Be $true
    }

    It 'checks Docker context, Compose labels, network alias, and container address' {
        $launcherSource.Contains('the active Docker context does not match the trusted target profile') | Should Be $true
        $launcherSource.Contains('com.docker.compose.project') | Should Be $true
        $launcherSource.Contains('io.evidenceforge.deployment-id') | Should Be $true
        $launcherSource.Contains('the deployment-specific TLS hostname is not an alias on the expected Compose network') | Should Be $true
        $launcherSource.Contains('NetworkSettings.Networks') | Should Be $true
    }

    It 'captures the Docker context exit code before processing output' {
        $contextCommandIndex = $launcherSource.IndexOf('$activeContextOutput = & docker context show 2>$null', [System.StringComparison]::Ordinal)
        $contextExitIndex = $launcherSource.IndexOf('$activeContextExitCode = $LASTEXITCODE', [System.StringComparison]::Ordinal)
        $contextOutputIndex = $launcherSource.IndexOf('$activeContext = ([string]($activeContextOutput | Select-Object -First 1)).Trim()', [System.StringComparison]::Ordinal)

        ($contextCommandIndex -ge 0) | Should Be $true
        ($contextExitIndex -gt $contextCommandIndex) | Should Be $true
        ($contextOutputIndex -gt $contextExitIndex) | Should Be $true
        $launcherSource.Contains('if ($activeContextExitCode -ne 0 -or $activeContext -ne [string]$targetProfile.docker_context) {') | Should Be $true
    }

    It 'checks staging-volume name collisions without inspecting a missing volume' {
        $nameCheckStart = $launcherSource.IndexOf('$recoveryStageVolumeCandidate =', [System.StringComparison]::Ordinal)
        $volumeCreateStart = $launcherSource.IndexOf('$volumeCreateArgs = @(', $nameCheckStart, [System.StringComparison]::Ordinal)

        ($nameCheckStart -ge 0) | Should Be $true
        ($volumeCreateStart -gt $nameCheckStart) | Should Be $true

        $nameCheckBlock = $launcherSource.Substring($nameCheckStart, $volumeCreateStart - $nameCheckStart)
        $nameCheckBlock.Contains("volume ls --format '{{.Name}}'") | Should Be $true
        $nameCheckBlock.Contains('$existingStageVolumeExitCode = $LASTEXITCODE') | Should Be $true
        $nameCheckBlock.Contains('if ($existingStageVolumeExitCode -ne 0) {') | Should Be $true
        $nameCheckBlock.Contains('if ($existingStageVolumeNames -contains $recoveryStageVolumeCandidate) {') | Should Be $true
        $nameCheckBlock.Contains('volume inspect') | Should Be $false
    }
    It 'checks the rendered Compose hash before invoking the recovery container' {
        $hashRefusalIndex = $launcherSource.IndexOf(
            'the rendered Compose configuration hash does not match the trusted target profile',
            [System.StringComparison]::Ordinal
        )
        $launchIndex = $launcherSource.IndexOf('& docker @runArgs', [System.StringComparison]::Ordinal)
        ($hashRefusalIndex -ge 0) | Should Be $true
        ($launchIndex -ge 0) | Should Be $true
        ($hashRefusalIndex -lt $launchIndex) | Should Be $true
    }

    It 'passes only the recovery DSN variable name to Compose and never adds its value to arguments' {
        $launcherSource.Contains("'--env', 'EVIDENCEFORGE_RECOVERY_DATABASE_URL'") | Should Be $true
        $launcherSource.Contains('[string]$env:EVIDENCEFORGE_RECOVERY_DATABASE_URL') | Should Be $false
    }

    It 'runs the inspected immutable API image directly and clears the ordinary database DSN' {
        $blockStart = $launcherSource.IndexOf('$runArgs = @(', [System.StringComparison]::Ordinal)
        $blockEnd = $launcherSource.IndexOf('& docker @runArgs', $blockStart, [System.StringComparison]::Ordinal)
        ($blockStart -ge 0 -and $blockEnd -gt $blockStart) | Should Be $true
        $runBlock = $launcherSource.Substring($blockStart, $blockEnd - $blockStart)
        $runBlock.Contains('--pull=never') | Should Be $true
        $runBlock.Contains('targetProfile.network_name') | Should Be $true
        $runBlock.Contains('EVIDENCEFORGE_RECOVERY_DATABASE_URL') | Should Be $true
        $runBlock.Contains('DATABASE_URL=') | Should Be $true
        $runBlock.Contains('apiImageId') | Should Be $true
        $runBlock.Contains('--entrypoint') | Should Be $true
        $runBlock.Contains('--read-only') | Should Be $true
        $runBlock.Contains('--cap-drop') | Should Be $true
    }
    It 'creates a dedicated temporary attestation directory and attempts cleanup on all exits' {
        $launcherSource.Contains('evidenceforge-recovery-') | Should Be $true
        $launcherSource.Contains('CreateDirectory($attestationDirectory, $directorySecurity)') | Should Be $true
        $launcherSource.Contains('Remove-Item -LiteralPath $attestationPath -Force') | Should Be $true
        $launcherSource.Contains('Remove-Item -LiteralPath $attestationDirectory -Force -Recurse') | Should Be $true
    }
    It 'stages the CA and attestation through a read-only volume' {
        $launcherSource.Contains('stage-recovery-files.py') | Should Be $true
        $launcherSource.Contains('io.evidenceforge.recovery-staging=true') | Should Be $true
        $launcherSource.Contains('target=/run/evidenceforge,readonly') | Should Be $true
        $launcherSource.Contains('ssl.create_default_context') | Should Be $true
        $launcherSource.Contains('the staged CA or attestation cannot be read and verified by UID 65532') | Should Be $true
        $launcherSource.Contains('docker @volumeRemoveArgs') | Should Be $true
        $launcherSource.Contains('EXPECTED_CA_SHA256') | Should Be $true
        $launcherSource.Contains('EXPECTED_ATTESTATION_SHA256') | Should Be $true
    }
    It 'cleans the staging volume independently from location-stack cleanup' {
        $finallyStart = $launcherSource.LastIndexOf('finally {', [System.StringComparison]::Ordinal)
        ($finallyStart -ge 0) | Should Be $true
        $finallySource = $launcherSource.Substring($finallyStart)
        $volumeCleanupIndex = $finallySource.IndexOf('if ($recoveryStageVolumeCleanupArmed', [System.StringComparison]::Ordinal)
        $locationGuardIndex = $finallySource.IndexOf('if ($composeLocationPushed) {', [System.StringComparison]::Ordinal)
        ($volumeCleanupIndex -ge 0) | Should Be $true
        ($locationGuardIndex -gt $volumeCleanupIndex) | Should Be $true
        $finallySource.Contains('docker @volumeRemoveArgs') | Should Be $true
    }
    It 'uses compatible .NET APIs to resolve parent paths' {
        $launcherSource.Contains('Split-Path -LiteralPath') | Should Be $false
        $launcherSource.Contains('[System.IO.Path]::GetDirectoryName(') | Should Be $true
    }
    It 'treats volume-root child-creation rights separately from replacement rights' {
        $launcherSource.Contains('$isFilesystemRoot = $aclPathItem.PSIsContainer -and') | Should Be $true
        $launcherSource.Contains('$effectiveWriteRights = $writeRights') | Should Be $true
        $launcherSource.Contains('FileSystemRights]::DeleteSubdirectoriesAndFiles -bor') | Should Be $true
        $launcherSource.Contains('band $effectiveWriteRights') | Should Be $true
        $launcherSource.Contains('FileSystemRights]::AppendData') | Should Be $true
    }
    It 'forwards only a validated set of recovery arguments and protects the generated attestation path' {
        $launcherSource.Contains('$protectedAttestationOption') | Should Be $true
        $launcherSource.Contains('$allowedRecoveryValueOptions') | Should Be $true
        $launcherSource.Contains('$allowedRecoveryFlagOptions') | Should Be $true
        $launcherSource.Contains('RecoveryArguments must not duplicate, abbreviate, or override the launcher-generated --target-attestation-file path') | Should Be $true
        $launcherSource.Contains('is not in the validated recovery argument set') | Should Be $true
        $launcherSource.Contains(') + $validatedRecoveryArguments') | Should Be $true
        $launcherSource.Contains(') + $RecoveryArguments') | Should Be $false
        $validationIndex = $launcherSource.IndexOf('$validatedRecoveryArguments = @()', [System.StringComparison]::Ordinal)
        $launchIndex = $launcherSource.IndexOf('& docker @runArgs', [System.StringComparison]::Ordinal)
        ($validationIndex -ge 0) | Should Be $true
        ($launchIndex -gt $validationIndex) | Should Be $true
    }
    It 'pins the PostgreSQL image to a trusted immutable image ID' {
        $launcherSource.Contains('database_image_id') | Should Be $true
        $launcherSource.Contains('database_image_id must pin an immutable Docker image ID (sha256:...).') | Should Be $true
        $launcherSource.Contains('image inspect ([string]$targetProfile.database_image)') | Should Be $true
        $launcherSource.Contains('the running PostgreSQL container or its current image reference does not match the pinned database image ID') | Should Be $true
        $pinIndex = $launcherSource.IndexOf('$databaseContainerImageId', [System.StringComparison]::Ordinal)
        $launchIndex = $launcherSource.IndexOf('& docker @runArgs', [System.StringComparison]::Ordinal)
        ($pinIndex -ge 0) | Should Be $true
        ($launchIndex -gt $pinIndex) | Should Be $true
    }
    It 'binds the expected PostgreSQL port into the profile validation and fresh attestation' {
        $launcherSource.Contains('database_port') | Should Be $true
        $launcherSource.Contains('database_port must be an integer between 1 and 65535.') | Should Be $true
        $launcherSource.Contains('server_port = $databasePort') | Should Be $true
    }
    It 'pins target profile schema version 2 and writes attestation schema version 2' {
        $launcherSource.Contains('if ($targetProfile.version -ne 2) {') | Should Be $true
        $launcherSource.Contains('if ($targetProfile.version -ne 1) {') | Should Be $false
        $attestationStart = $launcherSource.IndexOf('$attestation = [ordered]@{', [System.StringComparison]::Ordinal)
        ($attestationStart -ge 0) | Should Be $true
        $attestationHeader = $launcherSource.Substring($attestationStart, 60)
        $attestationHeader.Contains('version = 2') | Should Be $true
        $launcherSource.Contains('server_port = $databasePort') | Should Be $true
    }
    It 'preserves the recovery outcome before cleanup and maps cleanup failure by that outcome' {
        $dockerRunCapture = $launcherSource.IndexOf('$scriptExitCode = $LASTEXITCODE', [System.StringComparison]::Ordinal)
        ($dockerRunCapture -ge 0) | Should Be $true
        $preserveIndex = $launcherSource.IndexOf('$recoveryExitCode = $scriptExitCode', [System.StringComparison]::Ordinal)
        ($preserveIndex -gt $dockerRunCapture) | Should Be $true
        $cleanupInitIndex = $launcherSource.IndexOf('$cleanupFailure = $null', [System.StringComparison]::Ordinal)
        ($cleanupInitIndex -gt $preserveIndex) | Should Be $true
        $launcherSource.Contains('if ($null -ne $cleanupFailure) {') | Should Be $true
        $launcherSource.Contains('if ($recoveryExitCode -eq 0) {') | Should Be $true
        $launcherSource.Contains('(exit 3 = committed-but-cleanup-failed)') | Should Be $true
        $launcherSource.Contains('${cleanupFailure}; recovery outcome preserved as exit ${recoveryExitCode}.') | Should Be $true
        ([regex]::Matches($launcherSource, [regex]::Escape('$scriptExitCode = 2'))).Count | Should Be 2
        ([regex]::Matches($launcherSource, [regex]::Escape('$scriptExitCode = 3'))).Count | Should Be 1
    }
    It 'records each cleanup failure branch without overwriting the recovery outcome code' {
        $cleanupFailure = '$' + 'cleanupFailure'
        foreach ($marker in @('staging volume removal failed', 'staging volume identity changed', 'staging volume cleanup could not be verified', 'staging volume cleanup status could not be verified')) {
            $launcherSource.Contains(($cleanupFailure + " = '" + $marker + "'")) | Should Be $true
        }
        $regionStart = $launcherSource.IndexOf('$recoveryExitCode = $scriptExitCode', [System.StringComparison]::Ordinal)
        $regionEnd = $launcherSource.IndexOf('if ($stageScriptPath -and', [System.StringComparison]::Ordinal)
        ($regionEnd -gt $regionStart) | Should Be $true
        $cleanupRegion = $launcherSource.Substring($regionStart, $regionEnd - $regionStart)
        $cleanupRegion.Contains('$scriptExitCode = 2') | Should Be $false
        ([regex]::Matches($cleanupRegion, [regex]::Escape('$cleanupFailure = '))).Count | Should Be 5
        # Every cleanup-failure warning names the exact staged volume, and the
        # identity-mismatch warning explicitly forbids deletion until verified.
        $launcherSource.Contains('"Staging volume requiring review: $recoveryStageVolumeCandidate"') | Should Be $true
        $launcherSource.Contains('"WARNING: staging volume identity changed for ''$recoveryStageVolumeCandidate''') | Should Be $true
        $launcherSource.Contains('do NOT delete this volume until you have positively identified it') | Should Be $true
        # Remediation commands inside warnings always carry the configured
        # Docker context and address the exact same volume name.
        (([regex]::Matches($launcherSource, [regex]::Escape('docker --context $($targetProfile.docker_context) volume inspect '))).Count -ge 4) | Should Be $true
        $launcherSource.Contains('docker --context $($targetProfile.docker_context) volume rm ') | Should Be $true
    }
    It 'keeps the explicit final exit as the only process-exit statement' {
        $trimmed = $launcherSource.TrimEnd()
        $trimmed.EndsWith('exit $scriptExitCode') | Should Be $true
        # Single-quoted pattern carries the literal dollar via regex escape; it
        # matches any line-starting 'exit' except the canonical final one.
        $exitPattern = '(?m)^\s*exit\b(?!\s+\$scriptExitCode\b)'
        ([regex]::Matches($launcherSource, $exitPattern)).Count | Should Be 0
        # Negative control: a deliberately invalid fixture appended with a
        # second exit statement must be detected by the same pattern.
        $invalidFixture = $launcherSource + "`r`nexit 2"
        (([regex]::Matches($invalidFixture, $exitPattern)).Count -ge 1) | Should Be $true
    }
}
