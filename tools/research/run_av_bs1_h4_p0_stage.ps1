param([ValidateSet("manifest")][string]$Stage="manifest")
Set-StrictMode -Version Latest; $ErrorActionPreference="Stop"
$here=Split-Path -Parent $PSCommandPath; $fixture=Join-Path $here "av_bs1_boundary_schur_h4_p0.py"
& (Get-Command python -ErrorAction Stop).Source $fixture --stage manifest
exit $LASTEXITCODE
