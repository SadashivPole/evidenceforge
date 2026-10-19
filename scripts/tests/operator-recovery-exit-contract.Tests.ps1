# EvidenceForge operator-recovery launcher EXIT-CONTRACT behavioral tests.
# These tests run the REAL launcher script end to end against a deterministic
# docker PATH shim (no Docker daemon contact) and assert the outcome x cleanup
# exit-code matrix:
#   CLI rc 0 + cleanup verified          -> exit 0
#   CLI rc 0 + cleanup failed/unverified -> exit 3 (committed-but-cleanup-failed)
#   CLI rc 1/2/other + cleanup failure   -> the CLI result is preserved
#   pre-write refusal                    -> exit 2
# Pester 3-compatible style ('Should Be', no BeforeAll/AfterAll). Off Windows
# these tests no-op by design because the launcher relies on Windows ACL
# enforcement APIs.
Set-StrictMode -Version Latest

Describe 'EvidenceForge operator-recovery launcher exit contract (behavioral)' {
    $repoRootLocal = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $behavioralLauncherPath = Join-Path $repoRootLocal 'scripts\operator-recovery.ps1'
    $isWindowsHost = ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT)
    $script:behavioralReady = ($isWindowsHost -and ($null -ne (Get-Command powershell.exe -ErrorAction SilentlyContinue)))

    function Get-Sha256HexText {
        param([string] $Text)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $utf8 = New-Object System.Text.UTF8Encoding($false)
            return [System.BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($Text))).Replace('-', '').ToLowerInvariant()
        }
        finally {
            $sha.Dispose()
        }
    }

    function New-ExitContractFixture {
        $dir = Join-Path $env:TEMP ('ef-exit-test-' + [guid]::NewGuid().ToString('n'))
        $shimDir = Join-Path $dir 'shim'
        New-Item -ItemType Directory -Path $shimDir -Force | Out-Null

        $composeProject = 'efstub'
        $deploymentId = 'efstub-deployment-20261016'
        $networkName = 'efstub-net'
        $tlsHostname = 'db.efstub.invalid'
        $databaseImage = 'postgres:16-stub'
        $databaseImageId = 'sha256:' + ('1' * 64)
        $apiImageTag = 'evidenceforge-api:stub'
        $apiImageId = 'sha256:' + ('2' * 64)
        $dbContainerId = 'a' * 64
        $apiContainerId = 'b' * 64

        $utf8 = New-Object System.Text.UTF8Encoding($false)

        $caPath = Join-Path $dir 'ca.pem'
        $caText = 'exit-contract test CA fixture (test-only; not a real certificate)'
        [IO.File]::WriteAllBytes($caPath, $utf8.GetBytes($caText))
        $caSha = Get-Sha256HexText $caText

        $composePath = Join-Path $dir 'compose.yaml'
        [IO.File]::WriteAllBytes($composePath, $utf8.GetBytes("services:`r`n  db:`r`n    image: $databaseImage`r`n  api:`r`n    image: $apiImageTag"))

        $renderedJson = (@{
            services = @{
                db  = @{ image = $databaseImage }
                api = @{ image = $apiImageTag }
            }
        } | ConvertTo-Json -Compress -Depth 8)
        $renderedSha = Get-Sha256HexText $renderedJson

        $dbNetworks = @{}
        $dbNetworks[$networkName] = @{ Aliases = @('db', $tlsHostname); IPAddress = '10.20.30.40' }
        $dbInspectJson = (@{
            State  = @{ Status = 'running' }
            Config = @{
                Labels = @{
                    'com.docker.compose.project'     = $composeProject
                    'com.docker.compose.service'     = 'db'
                    'io.evidenceforge.deployment-id' = $deploymentId
                }
                Image  = $databaseImage
            }
            Image           = $databaseImageId
            NetworkSettings = @{ Networks = $dbNetworks }
        } | ConvertTo-Json -Compress -Depth 10)
        $apiInspectJson = (@{
            State  = @{ Status = 'running' }
            Config = @{
                Labels = @{
                    'com.docker.compose.project'     = $composeProject
                    'com.docker.compose.service'     = 'api'
                    'io.evidenceforge.deployment-id' = $deploymentId
                }
                Image  = $apiImageTag
            }
            Image           = $apiImageId
            NetworkSettings = @{ Networks = @{} }
        } | ConvertTo-Json -Compress -Depth 10)

        $profile = [ordered]@{
            version                       = 2
            docker_context                = 'efstub-context'
            compose_file                  = $composePath
            compose_project               = $composeProject
            database_service              = 'db'
            api_service                   = 'api'
            deployment_id                 = $deploymentId
            database_name                 = 'evidenceforge_test'
            schema_name                   = 'public'
            network_name                  = $networkName
            tls_hostname                  = $tlsHostname
            database_image                = $databaseImage
            database_image_id             = $databaseImageId
            database_port                 = 5432
            api_image_id                  = $apiImageId
            rendered_compose_sha256       = $renderedSha
            ca_certificate_sha256         = $caSha
            ca_certificate_host_path      = $caPath
            ca_certificate_container_path = '/run/evidenceforge/recovery-ca.pem'
        }
        $profilePath = Join-Path $dir 'target-profile.json'
        [IO.File]::WriteAllText($profilePath, ($profile | ConvertTo-Json -Depth 8), $utf8)

        $dockerCmd = @'
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0docker-stub.ps1" %*
exit /b %ERRORLEVEL%
'@
        [IO.File]::WriteAllText((Join-Path $shimDir 'docker.cmd'), ($dockerCmd -replace "`n", "`r`n"), $utf8)

        $stubSource = @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]] $Rest)
$ErrorActionPreference = 'Stop'
$scenarioPath = [Environment]::GetEnvironmentVariable('STUB_SCENARIO_PATH', 'Process')
$scenario = Get-Content -LiteralPath $scenarioPath -Raw | ConvertFrom-Json
$statePath = Join-Path (Split-Path -Parent $scenarioPath) 'shim-state.json'
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
}
else {
    $state = [pscustomobject]@{ run = 0; inspectVolume = 0 }
}
function Save-ShimState {
    if ($null -ne $script:state) {
        [IO.File]::WriteAllText($script:statePath, ($script:state | ConvertTo-Json -Compress))
    }
}
function New-VolumeJson {
    param([string] $Name, [object] $Owned)
    $labels = @{}
    if ($null -ne $Owned) { $labels['io.evidenceforge.recovery-staging'] = 'true' }
    return (@{ Name = $Name; Driver = 'local'; Labels = $labels } | ConvertTo-Json -Compress)
}
$rawArgs = @($Rest)
$a = @($Rest)
while ($a.Count -ge 2 -and $a[0] -eq '--context') {
    if ($a.Count -eq 2) { $a = @() } else { $a = @($a[2..($a.Count - 1)]) }
}
if ($a.Count -eq 0) { exit 1 }
$callLogPath = [Environment]::GetEnvironmentVariable('STUB_CALL_LOG', 'Process')
if ($callLogPath) {
    Add-Content -LiteralPath $callLogPath -Value ([string]::Join(' ', $rawArgs)) -Encoding ASCII
}
switch ($a[0]) {
    'context' {
        [Console]::WriteLine($scenario.dockerContext)
        exit 0
    }
    'compose' {
        if (@($a) -contains 'config') {
            [Console]::WriteLine($scenario.renderedJson)
            exit 0
        }
        if (@($a) -contains 'ps') {
            $service = [string]$a[$a.Count - 1]
            if ($service -eq $scenario.databaseService) { [Console]::WriteLine($scenario.dbContainerId) } else { [Console]::WriteLine($scenario.apiContainerId) }
            exit 0
        }
        exit 1
    }
    'inspect' {
        $idValue = [string]$a[$a.Count - 1]
        if ($idValue -eq $scenario.dbContainerId) { [Console]::WriteLine($scenario.dbInspectJson); exit 0 }
        if ($idValue -eq $scenario.apiContainerId) { [Console]::WriteLine($scenario.apiInspectJson); exit 0 }
        exit 1
    }
    'image' {
        $refValue = [string]$a[2]
        if ($refValue -eq $scenario.databaseImage) { [Console]::WriteLine($scenario.databaseImageId); exit 0 }
        if ($refValue -eq $scenario.apiImageTag) { [Console]::WriteLine($scenario.apiImageId); exit 0 }
        exit 1
    }
    'volume' {
        $volumeName = [string]$a[$a.Count - 1]
        switch ($a[1]) {
            'ls' { exit 0 }
            'create' { [Console]::WriteLine($volumeName); exit 0 }
            'inspect' {
                $state.inspectVolume = [int]$state.inspectVolume + 1
                Save-ShimState
                if ([int]$state.inspectVolume -eq 1) {
                    [Console]::WriteLine((New-VolumeJson $volumeName 'owned'))
                    exit 0
                }
                if ([bool]$scenario.cleanupInspectCorrupt) { [Console]::WriteLine('{ not-valid-json') ; exit 0 }
                if ([int]$scenario.cleanupInspectRc -ne 0) { exit ([int]$scenario.cleanupInspectRc) }
                if ([bool]$scenario.cleanupIdentityOk) {
                    [Console]::WriteLine((New-VolumeJson $volumeName 'owned'))
                }
                else {
                    [Console]::WriteLine((New-VolumeJson $volumeName $null))
                }
                exit 0
            }
            'rm' { exit ([int]$scenario.rmRc) }
            default { exit 1 }
        }
    }
    'run' {
        $state.run = [int]$state.run + 1
        Save-ShimState
        if ([int]$state.run -le 2) { exit 0 }
        exit ([int]$scenario.runRc)
    }
    default { exit 1 }
}
exit 1
'@
        [IO.File]::WriteAllText((Join-Path $shimDir 'docker-stub.ps1'), ($stubSource -replace "`n", "`r`n"), $utf8)

        return @{
            Dir             = $dir
            ShimDir         = $shimDir
            ProfilePath     = $profilePath
            Context         = 'efstub-context'
            DbContainerId   = $dbContainerId
            ApiContainerId  = $apiContainerId
            RenderedJson    = $renderedJson
            DbInspectJson   = $dbInspectJson
            ApiInspectJson  = $apiInspectJson
            DatabaseImage   = $databaseImage
            DatabaseImageId = $databaseImageId
            ApiImageTag     = $apiImageTag
            ApiImageId      = $apiImageId
        }
    }

    function Remove-ExitContractFixture {
        param([object] $Fixture)
        if ($Fixture -and $Fixture.Dir -and (Test-Path -LiteralPath $Fixture.Dir)) {
            Remove-Item -LiteralPath $Fixture.Dir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    function ConvertTo-EncodedCommand {
        param([string] $Command)
        # powershell.exe -EncodedCommand requires base64 of UTF-16LE. Using UTF-8
        # here produces a uniform exit-1 failure with garbage text on stderr.
        return [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($Command))
    }

    function New-ArgumentLiteral {
        param([string[]] $Values)
        return ($Values | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ', '
    }

    function Assert-LauncherExitCode {
        param([int] $Expected, [hashtable] $Result)
        # Thrown message carries the captured child stderr so a wrong exit code
        # can never hide the actual diagnostic output from the test log.
        if ($Result.ExitCode -ne $Expected) {
            $stderrPreview = '(stderr empty)'
            if ($Result.StdErr) {
                $stderrPreview = ($Result.StdErr.Substring(0, [Math]::Min(400, $Result.StdErr.Length)) -replace '\r?\n', ' | ')
            }
            $stdoutPreview = '(stdout empty)'
            if ($Result.StdOut) {
                $stdoutPreview = ($Result.StdOut.Substring(0, [Math]::Min(400, $Result.StdOut.Length)) -replace '\r?\n', ' | ')
            }
            throw ("launcher exit code was " + $Result.ExitCode + ", expected " + $Expected + ". stderr: " + $stderrPreview + ". stdout: " + $stdoutPreview)
        }
    }
    function Invoke-LauncherScenario {
        param(
            [hashtable] $Fixture,
            [int] $RunRc = 0,
            [int] $RmRc = 0,
            [int] $CleanupInspectRc = 0,
            [bool] $CleanupInspectCorrupt = $false,
            [bool] $CleanupIdentityOk = $true,
            [bool] $IncludeConfirmRecovery = $true
        )
        $statePath = Join-Path $Fixture.Dir 'shim-state.json'
        if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath -Force }

        $scenarioPath = Join-Path $Fixture.Dir 'scenario.json'
        $scenario = [ordered]@{
            dockerContext     = $Fixture.Context
            databaseService   = 'db'
            dbContainerId     = $Fixture.DbContainerId
            apiContainerId    = $Fixture.ApiContainerId
            renderedJson      = $Fixture.RenderedJson
            dbInspectJson     = $Fixture.DbInspectJson
            apiInspectJson    = $Fixture.ApiInspectJson
            databaseImage     = $Fixture.DatabaseImage
            databaseImageId   = $Fixture.DatabaseImageId
            apiImageTag       = $Fixture.ApiImageTag
            apiImageId        = $Fixture.ApiImageId
            runRc             = $RunRc
            rmRc              = $RmRc
            cleanupInspectRc  = $CleanupInspectRc
            cleanupInspectCorrupt = $CleanupInspectCorrupt
            cleanupIdentityOk = $CleanupIdentityOk
        }
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($scenarioPath, ($scenario | ConvertTo-Json -Compress), $utf8)

        $outPath = Join-Path $Fixture.Dir 'launcher.out.txt'
        $errPath = Join-Path $Fixture.Dir 'launcher.err.txt'
        foreach ($artifact in @($outPath, $errPath)) {
            if (Test-Path -LiteralPath $artifact) { Remove-Item -LiteralPath $artifact -Force }
        }

        $originalPath = $env:PATH
        $originalDbUrl = $env:EVIDENCEFORGE_RECOVERY_DATABASE_URL
        $originalScenario = $env:STUB_SCENARIO_PATH
        $originalPathExt = $env:PATHEXT
        $originalCallLog = $env:STUB_CALL_LOG
        $callLogPath = Join-Path $Fixture.Dir 'shim-calls.log'
        if (Test-Path -LiteralPath $callLogPath) { Remove-Item -LiteralPath $callLogPath -Force }
        try {
            $env:PATH = ($Fixture.ShimDir + ';' + $originalPath)
            $env:PATHEXT = '.COM;.EXE;.BAT;.CMD'
            $env:EVIDENCEFORGE_RECOVERY_DATABASE_URL = 'exit-contract-fixture-value-test-only'
            $env:STUB_SCENARIO_PATH = $scenarioPath
            $env:STUB_CALL_LOG = $callLogPath

            $resolvedDocker = (Get-Command docker -ErrorAction SilentlyContinue).Source
            if (-not $resolvedDocker -or -not $resolvedDocker.StartsWith($Fixture.ShimDir, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'docker shim resolution failed; refusing to drive the launcher against a real Docker daemon.'
            }

            $recoveryArgs = @('--identity-verified')
            if ($IncludeConfirmRecovery) { $recoveryArgs += '--confirm-recovery' }
            # Invoke via -EncodedCommand (parsed PowerShell), never raw -File:
            # with -File the native argument flattener feeds the array elements
            # one token at a time and the script binder treats '--confirm-recovery'
            # as if it were a launcher parameter name, so the child exits 1
            # before any launcher logic runs. Quoted literals keep paths and
            # flags intact regardless of spaces.
            $launcherLiteral = ("'" + $behavioralLauncherPath.Replace("'", "''") + "'")
            $profileLiteral = ("'" + ([string]$Fixture.ProfilePath).Replace("'", "''") + "'")
            $argumentsLiteral = New-ArgumentLiteral $recoveryArgs
            $command = "& $launcherLiteral -TargetProfilePath $profileLiteral -RecoveryArguments @($argumentsLiteral); exit `$LASTEXITCODE"
            $encoded = ConvertTo-EncodedCommand $command
            # Use Start-Process instead of native-command stream redirection.
            # In Windows PowerShell 5.1, native stderr can surface as a
            # terminating RemoteException when $ErrorActionPreference is Stop.
            # The encoded command is a single space-free argument.
            $powershellExe = Join-Path $PSHOME 'powershell.exe'
            $childArgs = @(
                '-NoProfile'
                '-ExecutionPolicy'
                'Bypass'
                '-EncodedCommand'
                $encoded
            )
            $launcherProcess = Start-Process `
                -FilePath $powershellExe `
                -ArgumentList ($childArgs -join ' ') `
                -PassThru `
                -Wait `
                -NoNewWindow `
                -RedirectStandardOutput $outPath `
                -RedirectStandardError $errPath
            if ($null -eq $launcherProcess.ExitCode) {
                throw 'Launcher child process did not expose an exit code.'
            }
            $code = [int]$launcherProcess.ExitCode
            # Keep the child process exit code; parent $LASTEXITCODE is unrelated and may be stale.
            $stderrText = ''
            if (Test-Path -LiteralPath $errPath) { $stderrText = [IO.File]::ReadAllText($errPath) }
            $stdoutText = ''
            if (Test-Path -LiteralPath $outPath) { $stdoutText = [IO.File]::ReadAllText($outPath) }
            return @{ ExitCode = $code; StdErr = $stderrText; StdOut = $stdoutText }
        }
        finally {
            $env:PATH = $originalPath
            $env:PATHEXT = $originalPathExt
            $env:EVIDENCEFORGE_RECOVERY_DATABASE_URL = $originalDbUrl
            $env:STUB_SCENARIO_PATH = $originalScenario
            $env:STUB_CALL_LOG = $originalCallLog
        }
    }

    It 'exit 0: successful recovery with verified staging cleanup' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 0
            Assert-LauncherExitCode -Expected 0 -Result $result
            $result.StdErr.Contains('committed-but-cleanup-failed') | Should Be $false
            $callLogPath = Join-Path $fixture.Dir 'shim-calls.log'
            $callLog = @(Get-Content -LiteralPath $callLogPath)
            $rmPrefix = '--context ' + $fixture.Context + ' volume rm '
            $rmCalls = @($callLog | Where-Object {
                $_.StartsWith($rmPrefix, [System.StringComparison]::Ordinal)
            })
            $rmCalls.Count | Should Be 1
            $removedVolume = $rmCalls[0].Substring($rmPrefix.Length)
            $inspectCall = '--context ' + $fixture.Context + ' volume inspect ' + $removedVolume
            ($callLog -contains $inspectCall) | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 3: successful recovery whose staging cleanup status could not be verified' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 0 -CleanupInspectRc 1
            Assert-LauncherExitCode -Expected 3 -Result $result
            $result.StdErr.Contains('staging volume cleanup status could not be verified') | Should Be $true
            $result.StdErr.Contains('Staging volume requiring review:') | Should Be $true
            $result.StdErr.Contains('committed-but-cleanup-failed') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 3: successful recovery whose staging cleanup could not be verified (unparseable inspect output)' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 0 -CleanupInspectCorrupt $true
            Assert-LauncherExitCode -Expected 3 -Result $result
            $result.StdErr.Contains('staging volume cleanup could not be verified for') | Should Be $true
            $result.StdErr.Contains('Staging volume requiring review:') | Should Be $true
            $result.StdErr.Contains('committed-but-cleanup-failed') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 3: successful recovery whose staging volume removal failed' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 0 -RmRc 1
            Assert-LauncherExitCode -Expected 3 -Result $result
            $result.StdErr.Contains('staging volume removal failed') | Should Be $true
            $result.StdErr.Contains(('docker --context ' + $fixture.Context + ' volume inspect ')) | Should Be $true
            $result.StdErr.Contains('Staging volume requiring review:') | Should Be $true
            $result.StdErr.Contains('committed-but-cleanup-failed') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 3: successful recovery whose staging volume identity changed' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 0 -CleanupIdentityOk $false
            Assert-LauncherExitCode -Expected 3 -Result $result
            $result.StdErr.Contains('staging volume identity changed') | Should Be $true
            $result.StdErr.Contains(('docker --context ' + $fixture.Context + ' volume inspect ')) | Should Be $true
            $result.StdErr.Contains('do NOT delete this volume until you have positively identified it') | Should Be $true
            $callLogPath = Join-Path $fixture.Dir 'shim-calls.log'
            $callLog = @(Get-Content -LiteralPath $callLogPath)
            (@($callLog | Where-Object {
                $_ -match '(^|\s)volume rm(\s|$)'
            }).Count -eq 0) | Should Be $true
            $inspectPrefix = '--context ' + $fixture.Context + ' volume inspect '
            (@($callLog | Where-Object {
                $_.StartsWith($inspectPrefix, [System.StringComparison]::Ordinal)
            }).Count -gt 0) | Should Be $true
            $result.StdErr.Contains('Staging volume requiring review:') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 1 preserved: operational failure is never rewritten as exit 2' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 1 -RmRc 1
            Assert-LauncherExitCode -Expected 1 -Result $result
            $result.StdErr.Contains('recovery outcome preserved as exit 1') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 2 preserved: pre-write refusal dominates a later cleanup failure' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 2 -RmRc 1
            Assert-LauncherExitCode -Expected 2 -Result $result
            $result.StdErr.Contains('recovery outcome preserved as exit 2') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 7 preserved: unexpected nonzero outcomes pass through unchanged' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 7 -RmRc 1
            Assert-LauncherExitCode -Expected 7 -Result $result
            $result.StdErr.Contains('recovery outcome preserved as exit 7') | Should Be $true
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 1 reported: raw CLI exit 3 with verified cleanup never surfaces reserved code 3' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 3
            Assert-LauncherExitCode -Expected 1 -Result $result
            $result.StdErr.Contains('recovery CLI returned exit 3') | Should Be $true
            $result.StdErr.Contains('reported as exit 1 (operational failure; outcome uncertain)') | Should Be $true
            $result.StdErr.Contains('committed-but-cleanup-failed') | Should Be $false
            $result.StdErr.Contains('Staging volume requiring review:') | Should Be $false
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 1 reported: raw CLI exit 3 plus cleanup failure keeps volume review and reports 1' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -RunRc 3 -RmRc 1
            Assert-LauncherExitCode -Expected 1 -Result $result
            $result.StdErr.Contains('recovery CLI returned exit 3') | Should Be $true
            $result.StdErr.Contains('staging volume removal failed') | Should Be $true
            $result.StdErr.Contains('recovery outcome preserved as exit 1') | Should Be $true
            $result.StdErr.Contains('Staging volume requiring review:') | Should Be $true
            $result.StdErr.Contains('committed-but-cleanup-failed') | Should Be $false
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }

    It 'exit 2 refusal: missing confirmation flag refuses before any docker contact' {
        if (-not $script:behavioralReady) { return }
        $fixture = New-ExitContractFixture
        try {
            $result = Invoke-LauncherScenario -Fixture $fixture -IncludeConfirmRecovery $false
            Assert-LauncherExitCode -Expected 2 -Result $result
            $result.StdErr.Contains('--confirm-recovery') | Should Be $true
            (Test-Path -LiteralPath (Join-Path $fixture.Dir 'shim-calls.log')) | Should Be $false
        }
        finally {
            Remove-ExitContractFixture $fixture
        }
    }
}
