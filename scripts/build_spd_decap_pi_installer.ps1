param(
    [switch]$SkipTests,
    [switch]$AllowDirtyWorktree
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = (Get-Command python).Source

Push-Location $repoRoot
try {
    $sourceStatus = @(git status --porcelain=v1 --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the release worktree"
    }
    $sourceCommit = (git rev-parse HEAD).Trim()
    $sourceTree = (git rev-parse "HEAD^{tree}").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $sourceCommit -or -not $sourceTree) {
        throw "Unable to capture the release source identity"
    }
    $worktreeClean = $sourceStatus.Count -eq 0
    if (-not $worktreeClean -and -not $AllowDirtyWorktree) {
        throw (
            "Installer release builds require a clean worktree. " +
            "Use -AllowDirtyWorktree only for non-release development builds."
        )
    }
}
finally {
    Pop-Location
}

$applicationBuildScript = Join-Path $PSScriptRoot "build_spd_decap_pi.ps1"
if ($SkipTests) {
    & $applicationBuildScript -SkipTests
}
else {
    & $applicationBuildScript
}
if ($LASTEXITCODE -ne 0) {
    throw "SPD Decap PI Evaluator application build failed"
}

$versionFile = Join-Path $repoRoot "src\spd_decap_pi\version.py"
$appVersion = & $pythonExe -c "import pathlib,sys; line=next(item for item in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines() if item.startswith('__version__')); print(line.split('=', 1)[1].strip().strip(chr(34)))" $versionFile
if ($LASTEXITCODE -ne 0 -or -not $appVersion) {
    throw "Unable to read the application version from spd_decap_pi.version"
}
$appVersion = $appVersion.Trim()

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 or 7 (ISCC.exe) was not found. Install it, then rerun this script."
}

Push-Location $repoRoot
try {
    $installerBuildStartedAtUtc = [DateTime]::UtcNow
    $installerBuildStartedAt = Get-Date
    & $iscc "/DRepoRoot=$repoRoot" "/DAppVersion=$appVersion" "packaging\spd_decap_pi.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
    $installer = Join-Path $repoRoot "installer-output\SPDDecapPIEvaluatorSetup-$appVersion.exe"
    if (-not (Test-Path -LiteralPath $installer)) {
        throw "Inno Setup completed without producing $installer"
    }
    $installerItem = Get-Item -LiteralPath $installer
    if ($installerItem.LastWriteTime -lt $installerBuildStartedAt) {
        throw "Inno Setup did not refresh the installer: $installer"
    }
    $checksum = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumPath = "$installer.sha256"
    Set-Content `
        -LiteralPath $checksumPath `
        -Value "$checksum *$(Split-Path -Leaf $installer)" `
        -Encoding ascii
    $builtExe = Join-Path `
        $repoRoot `
        "dist\SPDDecapPIEvaluator\SPDDecapPIEvaluator.exe"
    if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
        throw "Built executable is missing before provenance capture: $builtExe"
    }
    $builtExeItem = Get-Item -LiteralPath $builtExe
    $postBuildStatus = @(git status --porcelain=v1 --untracked-files=all)
    $postBuildCommit = (git rev-parse HEAD).Trim()
    $postBuildTree = (git rev-parse "HEAD^{tree}").Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        $postBuildCommit -ne $sourceCommit -or
        $postBuildTree -ne $sourceTree -or
        ($postBuildStatus -join "`n") -ne ($sourceStatus -join "`n")
    ) {
        throw "Release source changed while the installer was building"
    }
    $manifest = [ordered]@{
        schema = "spd-decap-installer-build-v1"
        app_version = $appVersion
        built_at_utc = [DateTime]::UtcNow.ToString("o")
        build_started_at_utc = $installerBuildStartedAtUtc.ToString("o")
        source = [ordered]@{
            commit = $sourceCommit
            tree = $sourceTree
            worktree_clean = $worktreeClean
            dirty_override_used = [bool]$AllowDirtyWorktree
        }
        executable = [ordered]@{
            path = $builtExeItem.FullName
            size_bytes = $builtExeItem.Length
            sha256 = (
                Get-FileHash -LiteralPath $builtExe -Algorithm SHA256
            ).Hash.ToLowerInvariant()
        }
        installer = [ordered]@{
            path = $installerItem.FullName
            size_bytes = $installerItem.Length
            sha256 = $checksum
            checksum_path = $checksumPath
        }
    }
    $buildManifestPath = "$installer.build.json"
    $manifest | ConvertTo-Json -Depth 6 | Set-Content `
        -LiteralPath $buildManifestPath `
        -Encoding UTF8
    Write-Host "SPD Decap PI Evaluator installer output: $installer"
    Write-Host "SPD Decap PI Evaluator installer checksum: $checksumPath"
    Write-Host "SPD Decap PI Evaluator build manifest: $buildManifestPath"
}
finally {
    Pop-Location
}
