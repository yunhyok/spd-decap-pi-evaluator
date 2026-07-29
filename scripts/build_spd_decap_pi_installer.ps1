param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = (Get-Command python).Source

$buildArguments = @()
if ($SkipTests) {
    $buildArguments += "-SkipTests"
}
& (Join-Path $PSScriptRoot "build_spd_decap_pi.ps1") @buildArguments
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
    & $iscc "/DRepoRoot=$repoRoot" "/DAppVersion=$appVersion" "packaging\spd_decap_pi.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
    $installer = Join-Path $repoRoot "installer-output\SPDDecapPIEvaluatorSetup-$appVersion.exe"
    if (-not (Test-Path -LiteralPath $installer)) {
        throw "Inno Setup completed without producing $installer"
    }
    Write-Host "SPD Decap PI Evaluator installer output: $installer"
}
finally {
    Pop-Location
}
