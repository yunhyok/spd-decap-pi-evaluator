param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = (Get-Command python).Source

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Native command failed with exit code $LASTEXITCODE`: $FilePath $($ArgumentList -join ' ')"
    }
}

Push-Location $repoRoot
try {
    Invoke-Native $pythonExe @("-m", "pip", "install", "-e", ".[dev]")
    if (-not $SkipTests) {
        Invoke-Native $pythonExe @("-m", "pytest")
    }
    # scipy vendors the array-API shim packages under two different paths across
    # the range pyproject allows (scipy>=1.15): scipy._lib.* through scipy 1.17
    # and scipy._external.* from scipy 1.18. Their backends are resolved with
    # importlib at run time, so PyInstaller cannot discover them statically.
    # Both layouts are listed because --collect-submodules silently returns an
    # empty list for a package that is not installed, which makes collecting the
    # pair safe on either scipy version instead of silently collecting nothing.
    Invoke-Native $pythonExe @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--paths", "src",
        "--name", "SPDDecapPIEvaluator",
        "--version-file", "packaging\spd_decap_pi_version_info.txt",
        "--collect-submodules", "scipy._lib.array_api_compat",
        "--collect-submodules", "scipy._lib.array_api_extra",
        "--collect-submodules", "scipy._external.array_api_compat",
        "--collect-submodules", "scipy._external.array_api_extra",
        "--hidden-import", "scipy.linalg.cython_blas",
        "--hidden-import", "scipy.linalg.cython_lapack",
        "src\spd_decap_pi\gui_launcher.py"
    )
    $builtExe = Join-Path $repoRoot "dist\SPDDecapPIEvaluator\SPDDecapPIEvaluator.exe"
    if (-not (Test-Path -LiteralPath $builtExe)) {
        throw "PyInstaller completed without producing $builtExe"
    }
    $smoke = Start-Process -FilePath $builtExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
    if ($smoke.ExitCode -ne 0) {
        throw "Built SPD Decap PI Evaluator smoke test failed with exit code $($smoke.ExitCode)"
    }
    Write-Host "Built and smoke-tested SPD Decap PI Evaluator: $builtExe"
}
finally {
    Pop-Location
}
