param(
    [ValidateSet("manifest")]
    [string]$Stage = "manifest"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# H2-P0 is topology/assembly freeze only.  It deliberately has no child solve,
# authorization token, resource guard, or result-finalizer path.
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$fixturePath = Join-Path $scriptDirectory "av_bs1_boundary_schur_h2.py"
$pythonPath = (Get-Command python -ErrorAction Stop).Source

& $pythonPath $fixturePath --stage manifest
exit $LASTEXITCODE
