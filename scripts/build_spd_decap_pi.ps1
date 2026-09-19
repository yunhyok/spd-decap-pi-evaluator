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
        "--collect-submodules", "spd_pi_engine",
        "--collect-data", "matplotlib",
        "--hidden-import", "scipy.linalg.cython_blas",
        "--hidden-import", "scipy.linalg.cython_lapack",
        "--hidden-import", "matplotlib.path",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ImageDraw",
        # W11-d (found by testing the frozen --engine-worker path): spd_pi_engine's
        # cache/receipt versioning hashes its own numeric modules' and the product
        # SPD/SPICE parsers' SOURCE BYTES via `Path(<module>.__file__).read_bytes()`
        # (spd_pi_engine/cache.py `parser_version`, spd_pi_engine/receipt.py
        # `source_hashes`/NUMERIC_MODULES) instead of hashing the compiled bytecode.
        # PyInstaller never puts loose .py files next to the frozen _internal
        # package tree, so that read raises FileNotFoundError unless the exact same
        # source files are also placed at the path each module's own __file__
        # resolves to. These --add-data entries are pure packaging (no code here is
        # modified) and use the literal source files, so the resulting hashes are
        # bit-identical to a source-checkout run -- existing engine caches/receipts
        # stay valid.
        "--add-data", "src\spd_decap_pi\_core\io\spd.py;spd_decap_pi\_core\io",
        "--add-data", "src\spd_decap_pi\_core\models\spice.py;spd_decap_pi\_core\models",
        "--add-data", "src\spd_pi_engine\geometry.py;spd_pi_engine",
        "--add-data", "src\spd_pi_engine\homogenise.py;spd_pi_engine",
        "--add-data", "src\spd_pi_engine\solver.py;spd_pi_engine",
        "--add-data", "src\spd_pi_engine\reference.py;spd_pi_engine",
        "--add-data", "src\spd_pi_engine\model.py;spd_pi_engine",
        # matplotlib.backends is NOT excluded (tested in W11-d): matplotlib/__init__.py
        # unconditionally imports rcsetup, which does
        # `from matplotlib.backends import BackendFilter, backend_registry` at its own
        # top level -- excluding the package breaks `import matplotlib` outright.
        # registry.py only pulls in a concrete backend (Qt/Tk/Agg/...) through a
        # runtime importlib.import_module() call that this app's code path never
        # reaches, so nothing GUI-toolkit-heavy actually gets pulled in.
        "--exclude-module", "tkinter",
        "--exclude-module", "cupy",
        "--exclude-module", "nvmath",
        "--exclude-module", "cuda",
        "--exclude-module", "cupyx",
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

    # W11-d: a second gate beyond --smoke-test -- run one small rail through the
    # frozen exe's `--engine-worker` re-entry point (gui_launcher.py) and prove the
    # CPU-only build (no cupy/nvmath/cuda-bindings, excluded above) still solves:
    # `solver="auto"` must fall through spd_pi_engine's own try/except in
    # `Model._gpu_solver` (model.py) to scipy `splu` instead of crashing. Only runs
    # on this workstation, where SPD_PI_DATA_DIR/SPD_PI_WORK_DIR point at the 260729
    # SPD and the exp/engine_w4 CPU reference receipt this compares against.
    if ($env:SPD_PI_DATA_DIR) {
        $smokeScript = @'
import json
import os
import subprocess
import sys
from pathlib import Path

exe = Path(sys.argv[1])
data_dir = Path(os.environ["SPD_PI_DATA_DIR"])
work_dir = os.environ.get("SPD_PI_WORK_DIR")
spd_path = data_dir / "S4LB002-2Para_260729_1_injected.spd"
ref_path = Path(work_dir) / "engine_w4" / "receipt_260729_Port14_SITE0_cpu.json" if work_dir else None

if not spd_path.is_file():
    print(f"[engine-worker-smoke] SKIP: {spd_path} not found")
    raise SystemExit(0)
if not ref_path or not ref_path.is_file():
    print(f"[engine-worker-smoke] SKIP: reference receipt {ref_path} not found")
    raise SystemExit(0)

ref = json.loads(ref_path.read_text(encoding="utf-8"))
cache_dir = os.environ.get("SPD_PI_ENGINE_CACHE") or str(Path(work_dir) / "engine_cache")
freqs = [ref["freq"][i] for i in (0, 5, 10, 15, 20, 23, 26) if i < len(ref["freq"])]

request = {
    "spd_path": str(spd_path),
    "spd_sha256": ref["spd_sha256"],
    "port": "Port14_SITE0",
    "reference_mode": "powersi-compatible",
    "freqs": freqs,
    "cache_dir": cache_dir,
    "solver": "auto",
    "configs": {},
    "extra_models": {},
    "threads": None,
}

tmp = Path(os.environ.get("TEMP", "."))
req_path = tmp / "spd_pi_engine_worker_smoke_request.json"
result_path = tmp / "spd_pi_engine_worker_smoke_request.json.result.json"
for p in (req_path, result_path):
    p.unlink(missing_ok=True)
req_path.write_text(json.dumps(request, indent=1), encoding="utf-8")

proc = subprocess.run([str(exe), "--engine-worker", str(req_path)], capture_output=True, text=True)
if proc.returncode != 0 or not result_path.is_file():
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    raise SystemExit(f"[engine-worker-smoke] FAILED: exit code {proc.returncode}")

payload = json.loads(result_path.read_text(encoding="utf-8"))
receipt = payload["receipts"]["as_built"]
backend = receipt["backend"]
# receipt.py records the *requested* Backend.solver verbatim (model.py never
# rewrites it on fallback) -- the actual proof that this CPU-only build fell
# back off cuDSS is `device is None` (Model._gpu_solver only fills it in when a
# CudssLU actually planned) together with the Z match against the splu baseline.
if backend.get("device") is not None:
    raise SystemExit(f"[engine-worker-smoke] FAILED: backend.device={backend.get('device')!r}, expected None (splu fallback)")

ref_zs = {f: complex(re, im) for f, re, im in zip(ref["freq"], ref["Z_re"], ref["Z_im"])}
worst, compared = 0.0, 0
for f, zre, zim in zip(receipt["freq"], receipt["Z_re"], receipt["Z_im"]):
    rz = ref_zs.get(f) or ref_zs[min(ref_zs, key=lambda rf: abs(rf - f))]
    worst = max(worst, abs(complex(zre, zim) - rz) / (abs(rz) or 1.0))
    compared += 1
if compared == 0:
    raise SystemExit("[engine-worker-smoke] FAILED: no common frequencies to compare")
if worst > 1e-9:
    raise SystemExit(f"[engine-worker-smoke] FAILED: max relative |dZ|/|Z| = {worst:.3e} over {compared} common frequencies (limit 1e-9)")

for p in (req_path, result_path):
    p.unlink(missing_ok=True)
print(f"[engine-worker-smoke] PASS: {compared} common frequencies, max relative |dZ|/|Z| = {worst:.3e}, "
      f"backend.solver={backend.get('solver')!r} device={backend.get('device')!r} "
      f"(device=None => resolved to scipy splu, no cuDSS/cupy in the frozen build)")
'@
        $smokeScriptPath = Join-Path $env:TEMP "spd_pi_engine_worker_smoke.py"
        Set-Content -LiteralPath $smokeScriptPath -Value $smokeScript -Encoding utf8
        Invoke-Native $pythonExe @($smokeScriptPath, $builtExe)
    }
    else {
        Write-Host "SPD_PI_DATA_DIR not set: skipping the frozen --engine-worker smoke case."
    }
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
