param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = (Get-Command python).Source
$expectedPackageRoot = (Join-Path $repoRoot "src\spd_decap_pi")
$previousPythonNoUserSite = $env:PYTHONNOUSERSITE

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
    $env:PYTHONNOUSERSITE = "1"
    Invoke-Native $pythonExe @("-m", "pip", "install", "-e", ".[dev]")
    $originCheck = "import pathlib,sys,spd_decap_pi; expected=pathlib.Path(sys.argv[1]).resolve(); actual=pathlib.Path(spd_decap_pi.__file__).resolve().parent; sys.exit(f'active-checkout import guard failed: expected {expected}, imported {actual}') if actual != expected else print(actual)"
    Invoke-Native $pythonExe @("-c", $originCheck, $expectedPackageRoot)
    if (-not $SkipTests) {
        Invoke-Native $pythonExe @("-m", "pytest")
    }
    $applicationBuildStartedAt = Get-Date
    Invoke-Native $pythonExe @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--paths", "src",
        "--name", "SPDDecapPIEvaluator",
        "--version-file", "packaging\spd_decap_pi_version_info.txt",
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
    $builtExeItem = Get-Item -LiteralPath $builtExe
    if ($builtExeItem.LastWriteTime -lt $applicationBuildStartedAt) {
        throw "PyInstaller did not refresh the application executable: $builtExe"
    }
    $smoke = Start-Process -FilePath $builtExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
    if ($smoke.ExitCode -ne 0) {
        throw "Built SPD Decap PI Evaluator smoke test failed with exit code $($smoke.ExitCode)"
    }
    Write-Host "Built and smoke-tested SPD Decap PI Evaluator: $builtExe"
}
finally {
    if ($null -eq $previousPythonNoUserSite) {
        Remove-Item Env:PYTHONNOUSERSITE -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONNOUSERSITE = $previousPythonNoUserSite
    }
    Pop-Location
}
