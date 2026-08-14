# SPD Decap PI Evaluator v0.22.0 — AV-BS1 H4-P0R manifest-only runner.
[CmdletBinding()]
param(
    [ValidateSet("manifest")]
    [string]$Stage = "manifest"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath
$python = (Get-Command python -ErrorAction Stop).Source
& $python (Join-Path $here "av_bs1_boundary_schur_h4_p0r.py") --stage $Stage
exit $LASTEXITCODE
