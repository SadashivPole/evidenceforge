[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $TargetProfilePath,

    [Parameter(Mandatory = $true)]
    [string[]] $RecoveryArguments
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\') + '\'
$attestationPath = $null
$attestationDirectory = $null
$stageScriptPath = $null
$recoveryStageVolumeCandidate = $null
$recoveryStageVolume = $null
$recoveryStageVolumeCleanupArmed = $false
$composeLocationPushed = $false
$scriptExitCode = 2

function Stop-Recovery([string] $Message) {
    throw "EvidenceForge operator recovery preflight refused: $Message"
}

function Assert-RestrictedAcl([string] $Path, [string] $Label) {
    try {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $allowedWriterSids = @(
            $currentUserSid,
            'S-1-5-18',       # LocalSystem
            'S-1-5-32-544'    # Built-in Administrators
        )
        $allowedOwnerSids = @(
            $allowedWriterSids + @('S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
        )
        $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        if ($allowedOwnerSids -notcontains $ownerSid) {
            Stop-Recovery "$Label owner is not a trusted local principal."
        }
        $writeRights = [int](
            [System.Security.AccessControl.FileSystemRights]::WriteData -bor
            [System.Security.AccessControl.FileSystemRights]::AppendData -bor
            [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
            [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
            [System.Security.AccessControl.FileSystemRights]::Delete -bor
            [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
            [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
            [System.Security.AccessControl.FileSystemRights]::TakeOwnership
        )

        # On a filesystem root, WriteData and AppendData map to CreateFiles and
        # CreateDirectories. Those child-creation rights alone cannot replace an
        # existing protected path component. Still refuse untrusted delete-child,
        # delete, ACL-change, and ownership-change rights on the root itself.
        $aclPathItem = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        $fullAclPath = [System.IO.Path]::GetFullPath($aclPathItem.FullName)
        $volumeRoot = [System.IO.Path]::GetPathRoot($fullAclPath)
        $isFilesystemRoot = $aclPathItem.PSIsContainer -and
            [string]::Equals(
                $fullAclPath.TrimEnd([char[]]@('\', '/')),
                $volumeRoot.TrimEnd([char[]]@('\', '/')),
                [System.StringComparison]::OrdinalIgnoreCase
            )
        $effectiveWriteRights = $writeRights
        if ($isFilesystemRoot) {
            $effectiveWriteRights = [int](
                [System.Security.AccessControl.FileSystemRights]::Delete -bor
                [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
                [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                [System.Security.AccessControl.FileSystemRights]::TakeOwnership
            )
        }
        foreach ($rule in $acl.Access) {
            if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
                continue
            }
            if (($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) {
                continue
            }
            if (([int]$rule.FileSystemRights -band $effectiveWriteRights) -eq 0) {
                continue
            }
            try {
                $principalSid = $rule.IdentityReference.Translate(
                    [System.Security.Principal.SecurityIdentifier]
                ).Value
            }
            catch {
                Stop-Recovery "$Label has an ACL entry whose principal cannot be verified."
            }
            if ($allowedWriterSids -notcontains $principalSid) {
                Stop-Recovery "$Label grants write or permission-changing access to an untrusted principal."
            }
        }
    }
    catch {
        if ($_.Exception.Message -like 'EvidenceForge operator recovery preflight refused:*') {
            throw
        }
        Stop-Recovery "$Label permissions could not be verified safely."
    }
}

function Assert-RestrictedPath([string] $Path, [string] $Label, [switch] $MustBeOutsideRepository) {
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Stop-Recovery "$Label must not be a symbolic link or reparse point."
        }
        if ($MustBeOutsideRepository -and
            $item.FullName.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Stop-Recovery "$Label must be stored outside the repository."
        }
        Assert-RestrictedAcl -Path $item.FullName -Label $Label

        # A writable parent could allow replacing a protected file. Check every
        # ancestor directory as well, refusing untrusted write/delete-child grants.
        $directoryPath = if ($item.PSIsContainer) {
            $item.FullName
        } else {
            [System.IO.Path]::GetDirectoryName($item.FullName)
        }
        while (-not [string]::IsNullOrWhiteSpace($directoryPath)) {
            $directoryItem = Get-Item -LiteralPath $directoryPath -Force -ErrorAction Stop
            if (($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                Stop-Recovery "$Label has a symbolic-link or reparse-point ancestor."
            }
            Assert-RestrictedAcl -Path $directoryItem.FullName -Label "$Label parent directory"
            $parentPath = [System.IO.Path]::GetDirectoryName($directoryItem.FullName)
            if ([string]::IsNullOrWhiteSpace($parentPath) -or $parentPath -eq $directoryPath) {
                break
            }
            $directoryPath = $parentPath
        }
    }
    catch {
        if ($_.Exception.Message -like 'EvidenceForge operator recovery preflight refused:*') {
            throw
        }
        Stop-Recovery "$Label path or permissions could not be verified safely."
    }
}

function Get-Sha256Hex([byte[]] $Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

try {
    $profileItem = Get-Item -LiteralPath $TargetProfilePath -Force -ErrorAction Stop
    $profilePathResolved = $profileItem.FullName
    Assert-RestrictedPath -Path $profilePathResolved -Label 'the trusted target profile' -MustBeOutsideRepository

    try {
        $targetProfile = Get-Content -LiteralPath $profilePathResolved -Raw | ConvertFrom-Json
    }
    catch {
        Stop-Recovery 'the target profile is not valid JSON.'
    }
    if ($targetProfile.version -ne 2) {
        Stop-Recovery 'the target profile version is unsupported.'
    }

    $requiredProperties = @(
        'docker_context', 'compose_file', 'compose_project', 'database_service',
        'api_service', 'deployment_id', 'database_name', 'schema_name',
        'network_name', 'tls_hostname', 'database_image', 'database_image_id',
        'database_port', 'api_image_id',
        'rendered_compose_sha256', 'ca_certificate_sha256',
        'ca_certificate_host_path', 'ca_certificate_container_path'
    )
    foreach ($property in $requiredProperties) {
        $value = $targetProfile.$property
        if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
            Stop-Recovery "the target profile is missing '$property'."
        }
    }

    if ($targetProfile.ca_certificate_container_path -ne '/run/evidenceforge/recovery-ca.pem') {
        Stop-Recovery 'ca_certificate_container_path must be /run/evidenceforge/recovery-ca.pem.'
    }
    if ([string]$targetProfile.rendered_compose_sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        Stop-Recovery 'rendered_compose_sha256 must be a SHA-256 hash from the approved Compose configuration.'
    }
    if ([string]$targetProfile.ca_certificate_sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        Stop-Recovery 'ca_certificate_sha256 must be a valid SHA-256 hash.'
    }
    if ([string]$targetProfile.api_image_id -notmatch '^sha256:[0-9a-fA-F]{64}$') {
        Stop-Recovery 'api_image_id must pin an immutable Docker image ID (sha256:...).'
    }
    if ([string]$targetProfile.database_image_id -notmatch '^sha256:[0-9a-fA-F]{64}$') {
        Stop-Recovery 'database_image_id must pin an immutable Docker image ID (sha256:...).'
    }
    $databasePort = 0
    if (-not [int]::TryParse(([string]$targetProfile.database_port), [ref]$databasePort) -or
        $databasePort -lt 1 -or $databasePort -gt 65535) {
        Stop-Recovery 'database_port must be an integer between 1 and 65535.'
    }
    if (-not [System.IO.Path]::IsPathRooted([string]$targetProfile.compose_file)) {
        Stop-Recovery 'compose_file must be an absolute path.'
    }
    if (-not [System.IO.Path]::IsPathRooted([string]$targetProfile.ca_certificate_host_path)) {
        Stop-Recovery 'ca_certificate_host_path must be an absolute path.'
    }
    if (-not (Test-Path -LiteralPath $targetProfile.compose_file -PathType Leaf)) {
        Stop-Recovery 'the Compose file from the target profile does not exist.'
    }
    if (-not (Test-Path -LiteralPath $targetProfile.ca_certificate_host_path -PathType Leaf)) {
        Stop-Recovery 'the trusted PostgreSQL CA certificate file does not exist.'
    }
    Assert-RestrictedPath -Path $targetProfile.compose_file -Label 'the approved Compose file' -MustBeOutsideRepository
    Assert-RestrictedPath -Path $targetProfile.ca_certificate_host_path -Label 'the PostgreSQL CA certificate' -MustBeOutsideRepository
    $actualCaHash = (Get-FileHash -LiteralPath $targetProfile.ca_certificate_host_path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualCaHash -ne ([string]$targetProfile.ca_certificate_sha256).ToLowerInvariant()) {
        Stop-Recovery 'the PostgreSQL CA certificate hash does not match the trusted target profile.'
    }
    if ([string]::IsNullOrWhiteSpace($env:EVIDENCEFORGE_RECOVERY_DATABASE_URL)) {
        Stop-Recovery 'EVIDENCEFORGE_RECOVERY_DATABASE_URL must be supplied by the approved secret/configuration mechanism.'
    }
    if ($RecoveryArguments -notcontains '--identity-verified' -or
        $RecoveryArguments -notcontains '--confirm-recovery') {
        Stop-Recovery 'explicit --identity-verified and --confirm-recovery arguments are required.'
    }

    # Forward only a validated set of operator arguments. The launcher alone
    # supplies --target-attestation-file; refuse exact, duplicate, equals-form,
    # and abbreviated attempts to override that generated path, and refuse any
    # token outside the documented, allow-listed recovery options.
    $protectedAttestationOption = '--target-attestation-file'
    $allowedRecoveryValueOptions = @(
        '--external-subject',
        '--workspace-name',
        '--expected-database-name',
        '--expected-schema-name',
        '--operator-identity',
        '--approval-reference',
        '--token-ttl-days'
    )
    $allowedRecoveryFlagOptions = @(
        '--identity-verified',
        '--confirm-recovery'
    )
    $validatedRecoveryArguments = @()
    for ($argumentIndex = 0; $argumentIndex -lt $RecoveryArguments.Count; $argumentIndex++) {
        $argument = [string]$RecoveryArguments[$argumentIndex]
        if ([string]::IsNullOrWhiteSpace($argument)) {
            Stop-Recovery 'RecoveryArguments must not contain empty arguments.'
        }
        $argumentName = ($argument -split '=', 2)[0]
        if ($protectedAttestationOption -eq $argumentName -or
            ($argumentName.Length -gt 2 -and
             $argumentName.StartsWith('--') -and
             $protectedAttestationOption.StartsWith($argumentName, [System.StringComparison]::Ordinal))) {
            Stop-Recovery 'RecoveryArguments must not duplicate, abbreviate, or override the launcher-generated --target-attestation-file path.'
        }
        if ($allowedRecoveryFlagOptions -contains $argument) {
            $validatedRecoveryArguments += $argument
            continue
        }
        if ($allowedRecoveryValueOptions -contains $argument) {
            if ($argumentIndex + 1 -ge $RecoveryArguments.Count) {
                Stop-Recovery "recovery option '$argument' requires a value."
            }
            $optionValue = [string]$RecoveryArguments[$argumentIndex + 1]
            if ([string]::IsNullOrWhiteSpace($optionValue) -or
                $optionValue.StartsWith('-', [System.StringComparison]::Ordinal)) {
                Stop-Recovery "recovery option '$argument' requires a value that is not another option."
            }
            $validatedRecoveryArguments += $argument
            $validatedRecoveryArguments += $optionValue
            $argumentIndex++
            continue
        }
        Stop-Recovery "recovery argument '$argument' is not in the validated recovery argument set; use space-separated '--option value' forms of the documented options."
    }

    $composeFile = (Resolve-Path -LiteralPath $targetProfile.compose_file).Path
    Push-Location -LiteralPath ([System.IO.Path]::GetDirectoryName($composeFile))
    $composeLocationPushed = $true
    $activeContextOutput = & docker context show 2>$null
    $activeContextExitCode = $LASTEXITCODE
    $activeContext = ([string]($activeContextOutput | Select-Object -First 1)).Trim()
    if ($activeContextExitCode -ne 0 -or $activeContext -ne [string]$targetProfile.docker_context) {
        Stop-Recovery 'the active Docker context does not match the trusted target profile.'
    }

    $composeBase = @(
        '--context', [string]$targetProfile.docker_context,
        'compose', '--file', $composeFile,
        '--project-directory', ([System.IO.Path]::GetDirectoryName($composeFile)),
        '--project-name', [string]$targetProfile.compose_project
    )

    # Hash the fully rendered configuration, not only compose.yaml. This includes
    # interpolation and merged defaults without printing the rendered values.
    $renderedLines = & docker @composeBase config --format json 2>$null
    if ($LASTEXITCODE -ne 0 -or @($renderedLines).Count -eq 0) {
        Stop-Recovery 'Docker Compose could not render the approved deployment configuration.'
    }
    $renderedText = [string]::Join("`n", [string[]]@($renderedLines))
    $renderedHash = Get-Sha256Hex ([System.Text.UTF8Encoding]::new($false).GetBytes($renderedText))
    if ($renderedHash -ne ([string]$targetProfile.rendered_compose_sha256).ToLowerInvariant()) {
        Stop-Recovery 'the rendered Compose configuration hash does not match the trusted target profile.'
    }
    try {
        $renderedConfig = $renderedText | ConvertFrom-Json
    }
    catch {
        Stop-Recovery 'the rendered Compose configuration is not valid JSON.'
    }
    $services = $renderedConfig.services
    $databaseServiceProperty = $services.PSObject.Properties[[string]$targetProfile.database_service]
    $apiServiceProperty = $services.PSObject.Properties[[string]$targetProfile.api_service]
    if ($null -eq $databaseServiceProperty -or $null -eq $apiServiceProperty) {
        Stop-Recovery 'the rendered Compose configuration lacks the expected database or API service.'
    }
    if ([string]$databaseServiceProperty.Value.image -ne [string]$targetProfile.database_image) {
        Stop-Recovery 'the rendered PostgreSQL service image does not match the trusted profile.'
    }

    $containerQueryArgs = $composeBase + @('ps', '--status', 'running', '--quiet', [string]$targetProfile.database_service)
    $containerOutput = & docker @containerQueryArgs 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker Compose could not inspect the configured database service.'
    }
    $containerIds = @($containerOutput | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '\S' })
    if ($containerIds.Count -ne 1) {
        Stop-Recovery 'exactly one running PostgreSQL service container is required.'
    }

    $containerId = [string]$containerIds[0]
    $inspectOutput = & docker --context ([string]$targetProfile.docker_context) inspect --type container $containerId 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker could not inspect the selected PostgreSQL container.'
    }
    $container = @($inspectOutput | ConvertFrom-Json)[0]
    if ($container.State.Status -ne 'running') {
        Stop-Recovery 'the selected PostgreSQL container is not running.'
    }

    $labels = $container.Config.Labels
    if ([string]$labels.'com.docker.compose.project' -ne [string]$targetProfile.compose_project -or
        [string]$labels.'com.docker.compose.service' -ne [string]$targetProfile.database_service -or
        [string]$labels.'io.evidenceforge.deployment-id' -ne [string]$targetProfile.deployment_id) {
        Stop-Recovery 'Compose project/service labels or the deployment ID do not match the trusted target profile.'
    }
    if ([string]$container.Config.Image -ne [string]$targetProfile.database_image) {
        Stop-Recovery 'the inspected PostgreSQL image does not match the trusted target profile.'
    }
    $databaseContainerImageId = [string]$container.Image
    $resolvedDatabaseImageId = & docker --context ([string]$targetProfile.docker_context) image inspect ([string]$targetProfile.database_image) --format '{{.Id}}' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker could not resolve the PostgreSQL image reference to an immutable image ID.'
    }
    $resolvedDatabaseImageId = ([string]($resolvedDatabaseImageId | Select-Object -First 1)).Trim()
    if ($databaseContainerImageId -ne [string]$targetProfile.database_image_id -or
        $resolvedDatabaseImageId -ne [string]$targetProfile.database_image_id) {
        Stop-Recovery 'the running PostgreSQL container or its current image reference does not match the pinned database image ID.'
    }

    $apiQueryArgs = $composeBase + @('ps', '--status', 'running', '--quiet', [string]$targetProfile.api_service)
    $apiContainerOutput = & docker @apiQueryArgs 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker Compose could not inspect the configured API service.'
    }
    $apiContainerIds = @($apiContainerOutput | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '\S' })
    if ($apiContainerIds.Count -ne 1) {
        Stop-Recovery 'exactly one running API service container is required to pin the recovery image.'
    }
    $apiInspectOutput = & docker --context ([string]$targetProfile.docker_context) inspect --type container ([string]$apiContainerIds[0]) 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker could not inspect the selected API container.'
    }
    $apiContainer = @($apiInspectOutput | ConvertFrom-Json)[0]
    if ($apiContainer.State.Status -ne 'running') {
        Stop-Recovery 'the selected API container is not running.'
    }
    $apiLabels = $apiContainer.Config.Labels
    if ([string]$apiLabels.'com.docker.compose.project' -ne [string]$targetProfile.compose_project -or
        [string]$apiLabels.'com.docker.compose.service' -ne [string]$targetProfile.api_service -or
        [string]$apiLabels.'io.evidenceforge.deployment-id' -ne [string]$targetProfile.deployment_id) {
        Stop-Recovery 'the API container project/service/deployment labels do not match the trusted target profile.'
    }
    $apiContainerImageId = [string]$apiContainer.Image
    $taggedApiImageId = & docker --context ([string]$targetProfile.docker_context) image inspect ([string]$apiContainer.Config.Image) --format '{{.Id}}' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker could not resolve the API image reference to an immutable image ID.'
    }
    $taggedApiImageId = ([string]($taggedApiImageId | Select-Object -First 1)).Trim()
    if ($apiContainerImageId -ne [string]$targetProfile.api_image_id -or
        $taggedApiImageId -ne [string]$targetProfile.api_image_id) {
        Stop-Recovery 'the running API container or its current image tag does not match the pinned API image ID.'
    }
    $configuredApiImage = [string]$apiServiceProperty.Value.image
    if (-not [string]::IsNullOrWhiteSpace($configuredApiImage) -and
        $configuredApiImage -ne [string]$apiContainer.Config.Image) {
        Stop-Recovery 'the rendered API image reference differs from the running API container image reference.'
    }

    $networkProperty = $container.NetworkSettings.Networks.PSObject.Properties[[string]$targetProfile.network_name]
    if ($null -eq $networkProperty) {
        Stop-Recovery 'the PostgreSQL container is not attached to the expected Compose network.'
    }
    $network = $networkProperty.Value
    if (@($network.Aliases) -notcontains [string]$targetProfile.tls_hostname) {
        Stop-Recovery 'the deployment-specific TLS hostname is not an alias on the expected Compose network.'
    }
    try {
        $serverAddress = ([System.Net.IPAddress]::Parse([string]$network.IPAddress)).ToString()
    }
    catch {
        Stop-Recovery 'the selected PostgreSQL container has no valid IP address on the expected network.'
    }
    if ($containerId -notmatch '^[0-9a-fA-F]{64}$') {
        Stop-Recovery 'Docker did not return a full container ID.'
    }

    $attestation = [ordered]@{
        version = 2
        created_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        docker_context = [string]$targetProfile.docker_context
        compose_project = [string]$targetProfile.compose_project
        compose_service = [string]$targetProfile.database_service
        container_id = $containerId.ToLowerInvariant()
        deployment_id = [string]$targetProfile.deployment_id
        server_address = $serverAddress
        server_port = $databasePort
        tls_hostname = ([string]$targetProfile.tls_hostname).ToLowerInvariant().TrimEnd('.')
        database_name = [string]$targetProfile.database_name
        schema_name = [string]$targetProfile.schema_name
    }
    if ([string]::IsNullOrWhiteSpace($env:TEMP) -or -not [System.IO.Path]::IsPathRooted($env:TEMP)) {
        Stop-Recovery 'a valid absolute local TEMP directory is required for the short-lived attestation.'
    }
    $attestationDirectory = Join-Path $env:TEMP ('evidenceforge-recovery-' + [guid]::NewGuid().ToString('N'))
    $directorySecurity = New-Object System.Security.AccessControl.DirectorySecurity
    $directorySecurity.SetAccessRuleProtection($true, $false)
    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @(
        $currentSid,
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    )) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$directorySecurity.AddAccessRule($rule)
    }
    $directorySecurity.SetOwner($currentSid)
    [System.IO.Directory]::CreateDirectory($attestationDirectory, $directorySecurity) | Out-Null
    Assert-RestrictedPath -Path $attestationDirectory -Label 'the temporary attestation directory'
    $attestationPath = Join-Path $attestationDirectory 'target-attestation.json'
    $attestationJson = ($attestation | ConvertTo-Json -Depth 4 -Compress)
    [System.IO.File]::WriteAllText(
        $attestationPath,
        $attestationJson,
        [System.Text.UTF8Encoding]::new($false)
    )

    # Run the operator CLI directly from the immutable API image verified above.
    # This avoids mutable service-tag resolution and prevents inherited app DSN use.
    $apiImageId = ([string]$targetProfile.api_image_id).ToLowerInvariant()
    if ($apiImageId -notmatch '^sha256:[0-9a-f]{64}$') {
        Stop-Recovery 'the trusted API image ID is not a valid immutable image identifier.'
    }

    # Stage the pinned CA and generated attestation into a new, short-lived named volume.
    # Windows bind-mount permissions can present protected host files as unreadable to UID 65532.
    $stageCaHash = (Get-FileHash -LiteralPath $targetProfile.ca_certificate_host_path -Algorithm SHA256).Hash.ToLowerInvariant()
    $stageAttestationHash = (Get-FileHash -LiteralPath $attestationPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($stageCaHash -ne ([string]$targetProfile.ca_certificate_sha256).ToLowerInvariant()) {
        Stop-Recovery 'the PostgreSQL CA certificate changed before staging.'
    }

    $stageScriptPath = Join-Path $attestationDirectory 'stage-recovery-files.py'
    $stageScriptContents = @"
import hashlib
import json
import os
import ssl
import stat
import sys
from pathlib import Path

specs = [
    ("/input-ca.pem", "/stage/recovery-ca.pem", "EXPECTED_CA_SHA256", 0o444),
    ("/input-attestation.json", "/stage/recovery-target.json", "EXPECTED_ATTESTATION_SHA256", 0o400),
]

def verify():
    for _, destination, expected, expected_mode in specs:
        path = Path(destination)
        metadata = path.stat()
        readable = os.access(path, os.R_OK)
        hash_ok = hashlib.sha256(path.read_bytes()).hexdigest() == expected
        mode_ok = stat.S_IMODE(metadata.st_mode) == expected_mode
        owner_ok = metadata.st_uid == 65532 and metadata.st_gid == 65532
        if not (readable and hash_ok and mode_ok and owner_ok):
            raise SystemExit("FAIL: staged file permissions, owner, or hash check")
    ssl.create_default_context(cafile="/stage/recovery-ca.pem")
    json.loads(Path("/stage/recovery-target.json").read_text(encoding="utf-8"))
    print("PASS: staged CA and attestation are readable and verified as UID 65532")

mode = sys.argv[1] if len(sys.argv) > 1 else ""
if mode == "stage":
    for source, destination, expected, file_mode in specs:
        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise SystemExit("FAIL: staged source is not a regular file")
        data = source_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit("FAIL: source SHA-256 does not match trusted value")
        target = Path(destination)
        if target.exists() or target.is_symlink():
            raise SystemExit("FAIL: staging volume is not empty")
        with target.open("xb") as stream:
            stream.write(data)
        os.chown(target, 65532, 65532)
        os.chmod(target, file_mode)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise SystemExit("FAIL: copied file SHA-256 mismatch")
        print("STAGED PASS:", target.name)
elif mode == "verify":
    verify()
else:
    raise SystemExit("FAIL: expected stage or verify mode")
"@
    $stageScriptContents = $stageScriptContents.Replace('EXPECTED_CA_SHA256', $stageCaHash)
    $stageScriptContents = $stageScriptContents.Replace('EXPECTED_ATTESTATION_SHA256', $stageAttestationHash)
    [System.IO.File]::WriteAllText(
        $stageScriptPath,
        $stageScriptContents,
        [System.Text.UTF8Encoding]::new($false)
    )
    Assert-RestrictedPath -Path $stageScriptPath -Label 'the temporary staging helper' -MustBeOutsideRepository

    $caSourceForDocker = ([string]$targetProfile.ca_certificate_host_path) -replace '\\', '/'
    $attestationSourceForDocker = ([string]$attestationPath) -replace '\\', '/'
    $stageScriptSourceForDocker = ([string]$stageScriptPath) -replace '\\', '/'

    $recoveryStageVolumeCandidate = 'evidenceforge-recovery-stage-' + [guid]::NewGuid().ToString('N')
    $existingStageVolumeNames = & docker --context ([string]$targetProfile.docker_context) volume ls --format '{{.Name}}' 2>$null
    $existingStageVolumeExitCode = $LASTEXITCODE
    if ($existingStageVolumeExitCode -ne 0) {
        Stop-Recovery 'Docker could not check whether the generated staging volume name already exists.'
    }

    $existingStageVolumeNames = @(
        $existingStageVolumeNames |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ }
    )
    if ($existingStageVolumeNames -contains $recoveryStageVolumeCandidate) {
        Stop-Recovery 'the generated staging volume name already exists; refusing to reuse it.'
    }

    $volumeCreateArgs = @(
        '--context', [string]$targetProfile.docker_context,
        'volume', 'create',
        '--label', 'io.evidenceforge.recovery-staging=true',
        $recoveryStageVolumeCandidate
    )
    $volumeCreateOutput = & docker @volumeCreateArgs 2>$null
    if ($LASTEXITCODE -ne 0 -or ([string]($volumeCreateOutput | Select-Object -Last 1)).Trim() -ne $recoveryStageVolumeCandidate) {
        Stop-Recovery 'Docker could not create the unique staging volume.'
    }
    $recoveryStageVolumeCleanupArmed = $true

    $volumeInspectOutput = & docker --context ([string]$targetProfile.docker_context) volume inspect $recoveryStageVolumeCandidate 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'Docker could not verify the new staging volume.'
    }
    $stageVolumeInfo = @($volumeInspectOutput | ConvertFrom-Json)[0]
    if ([string]$stageVolumeInfo.Name -ne $recoveryStageVolumeCandidate -or
        [string]$stageVolumeInfo.Driver -ne 'local' -or
        [string]$stageVolumeInfo.Labels.'io.evidenceforge.recovery-staging' -ne 'true') {
        Stop-Recovery 'the staging volume identity or label does not match.'
    }
    $recoveryStageVolume = $recoveryStageVolumeCandidate

    $stageArgs = @(
        '--context', [string]$targetProfile.docker_context,
        'run', '--rm', '--pull=never',
        '--network', 'none',
        '--user', '0:0',
        '--cap-drop', 'ALL',
        '--cap-add', 'DAC_OVERRIDE',
        '--cap-add', 'CHOWN',
        '--cap-add', 'FOWNER',
        '--security-opt', 'no-new-privileges:true',
        '--read-only',
        '--mount', "type=bind,source=$caSourceForDocker,target=/input-ca.pem,readonly",
        '--mount', "type=bind,source=$attestationSourceForDocker,target=/input-attestation.json,readonly",
        '--mount', "type=bind,source=$stageScriptSourceForDocker,target=/stage-files.py,readonly",
        '--mount', "type=volume,source=$recoveryStageVolume,target=/stage",
        '--entrypoint', 'python',
        $apiImageId,
        '/stage-files.py', 'stage'
    )
    & docker @stageArgs
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'the CA and attestation could not be staged with the expected hashes and permissions.'
    }

    # Independently verify the staged files using the same UID as the recovery CLI.
    $verifyArgs = @(
        '--context', [string]$targetProfile.docker_context,
        'run', '--rm', '--pull=never',
        '--network', 'none',
        '--user', '65532:65532',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--read-only',
        '--mount', "type=volume,source=$recoveryStageVolume,target=/stage,readonly",
        '--mount', "type=bind,source=$stageScriptSourceForDocker,target=/stage-files.py,readonly",
        '--entrypoint', 'python',
        $apiImageId,
        '/stage-files.py', 'verify'
    )
    & docker @verifyArgs
    if ($LASTEXITCODE -ne 0) {
        Stop-Recovery 'the staged CA or attestation cannot be read and verified by UID 65532.'
    }

    $runArgs = @(
        '--context', [string]$targetProfile.docker_context,
        'run', '--rm', '--pull=never',
        '--network', [string]$targetProfile.network_name,
        '--workdir', '/app',
        '--user', '65532:65532',
        '--entrypoint', 'python',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--read-only',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
        '--mount', "type=volume,source=$recoveryStageVolume,target=/run/evidenceforge,readonly",
        '--label', "io.evidenceforge.deployment-id=$($targetProfile.deployment_id)",
        '--label', 'io.evidenceforge.recovery-operation=operator-recovery',
        '--env', 'EVIDENCEFORGE_RECOVERY_DATABASE_URL',
        '--env', 'DATABASE_URL=',
        $apiImageId,
        '-m', 'app.cli.operator_recovery',
        '--target-attestation-file', '/run/evidenceforge/recovery-target.json'
    ) + $validatedRecoveryArguments

    & docker @runArgs
    $scriptExitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $scriptExitCode = 2
}
finally {
    # Preserve the recovery outcome before cleanup can overwrite it. Exit-code
    # contract: 0 = success; 1 = operational failure (outcome uncertain; verify
    # the target state read-only before retrying); 2 = launcher or recovery CLI
    # refusal; 3 = recovery reported success but staging cleanup failed or
    # could not be verified. A cleanup problem after a nonzero outcome must
    # never rewrite that outcome. Exit 2 is also emitted by the generic catch
    # above for unexpected launcher or Docker errors, so the code 2 alone does
    # not establish whether a write occurred; examine the diagnostic output
    # and verify the database state read-only whenever the outcome is
    # uncertain.
    $recoveryExitCode = $scriptExitCode
    # A raw exit code 3 from the recovery CLI is not the reserved launcher
    # outcome (CLI success plus failed or unverified staging cleanup). If the
    # CLI itself exited 3, surfacing code 3 would be indistinguishable from
    # that reserved outcome, so this result is reported as exit 1
    # (operational failure; outcome uncertain) with an explicit diagnostic.
    if ($recoveryExitCode -eq 3) {
        [Console]::Error.WriteLine('WARNING: recovery CLI returned exit 3; launcher code 3 is reserved for recovery-success-with-failed-cleanup, so this result is reported as exit 1 (operational failure; outcome uncertain).')
        $recoveryExitCode = 1
        $scriptExitCode = 1
    }
    $cleanupFailure = $null
    if ($recoveryStageVolumeCleanupArmed -and $recoveryStageVolumeCandidate -and $targetProfile -and $targetProfile.docker_context) {
        $cleanupInspectOutput = & docker --context ([string]$targetProfile.docker_context) volume inspect $recoveryStageVolumeCandidate 2>$null
        if ($LASTEXITCODE -eq 0) {
            try {
                $cleanupVolumeInfo = @($cleanupInspectOutput | ConvertFrom-Json)[0]
                $ownedVolume = ([string]$cleanupVolumeInfo.Name -eq $recoveryStageVolumeCandidate -and
                    [string]$cleanupVolumeInfo.Driver -eq 'local' -and
                    [string]$cleanupVolumeInfo.Labels.'io.evidenceforge.recovery-staging' -eq 'true')
                if ($ownedVolume) {
                    $volumeRemoveArgs = @('--context', [string]$targetProfile.docker_context, 'volume', 'rm', $recoveryStageVolumeCandidate)
                    & docker @volumeRemoveArgs 2>$null | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        [Console]::Error.WriteLine("WARNING: staging volume removal failed for '$recoveryStageVolumeCandidate'; verify it with 'docker --context $($targetProfile.docker_context) volume inspect $recoveryStageVolumeCandidate' (expected driver 'local' and label io.evidenceforge.recovery-staging=true), then remove that exact same volume with 'docker --context $($targetProfile.docker_context) volume rm $recoveryStageVolumeCandidate' after review.")
                        $cleanupFailure = 'staging volume removal failed'
                    }
                }
                else {
                    [Console]::Error.WriteLine("WARNING: staging volume identity changed for '$recoveryStageVolumeCandidate'; do NOT delete this volume until you have positively identified it with 'docker --context $($targetProfile.docker_context) volume inspect $recoveryStageVolumeCandidate' (expected driver 'local' and label io.evidenceforge.recovery-staging=true); if identification fails, leave it in place and escalate.")
                    $cleanupFailure = 'staging volume identity changed'
                }
            }
            catch {
                [Console]::Error.WriteLine("WARNING: staging volume cleanup could not be verified for '$recoveryStageVolumeCandidate'; run 'docker --context $($targetProfile.docker_context) volume inspect $recoveryStageVolumeCandidate' and confirm driver 'local' plus label io.evidenceforge.recovery-staging=true before removing anything.")
                $cleanupFailure = 'staging volume cleanup could not be verified'
            }
        }
        else {
            [Console]::Error.WriteLine("WARNING: staging volume cleanup status could not be verified for '$recoveryStageVolumeCandidate'; run 'docker --context $($targetProfile.docker_context) volume inspect $recoveryStageVolumeCandidate' and confirm driver 'local' plus label io.evidenceforge.recovery-staging=true before removing anything.")
            $cleanupFailure = 'staging volume cleanup status could not be verified'
        }
    }
    if ($null -ne $cleanupFailure) {
        if ($recoveryExitCode -eq 0) {
            [Console]::Error.WriteLine('WARNING: recovery reported success, but staging cleanup failed or could not be verified (exit 3 = committed-but-cleanup-failed).')
            $scriptExitCode = 3
        }
        else {
            [Console]::Error.WriteLine("WARNING: ${cleanupFailure}; recovery outcome preserved as exit ${recoveryExitCode}.")
            $scriptExitCode = $recoveryExitCode
        }
        [Console]::Error.WriteLine("Staging volume requiring review: $recoveryStageVolumeCandidate")
    }
    if ($stageScriptPath -and (Test-Path -LiteralPath $stageScriptPath)) {
        Remove-Item -LiteralPath $stageScriptPath -Force -ErrorAction SilentlyContinue
    }
    if ($composeLocationPushed) {
        Pop-Location
    }
    if ($attestationPath -and (Test-Path -LiteralPath $attestationPath)) {
        Remove-Item -LiteralPath $attestationPath -Force -ErrorAction SilentlyContinue
    }
    if ($attestationDirectory -and (Test-Path -LiteralPath $attestationDirectory)) {
        Remove-Item -LiteralPath $attestationDirectory -Force -Recurse -ErrorAction SilentlyContinue
    }
}

exit $scriptExitCode
