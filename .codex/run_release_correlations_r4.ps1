$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe'
$benchmark = Join-Path $repo 'scripts\benchmark_raw_spd_powersi_correlation.py'
$validator = Join-Path $repo 'scripts\validate_correlation_v5.py'
$knownValidator = Join-Path $repo 'scripts\validate_known_case_nonregression.py'
$policy = Join-Path $repo 'validation-policies\known_case_nonregression_v1.json'
$fixtureRoot = Join-Path $repo 'validation-fixtures\known-case-nonregression-v1'
$validationRoot = Join-Path $repo 'validation-output'
$statusLog = Join-Path $validationRoot 'v0.22.0-correlation-runner-r4.status.log'
$lockDir = Join-Path $validationRoot '.v0.22.0-correlation-runner-r4.lock'
$lockAcquired = $false

function Quote-Argument([string]$Value) {
    return '"' + ($Value -replace '(?<!^)"', '\"') + '"'
}

function Write-Status([string]$Message) {
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'
    Add-Content -LiteralPath $statusLog -Encoding UTF8 -Value "[$timestamp] $Message"
}

function Assert-Identity([string]$Path, [long]$Size, [string]$Sha256) {
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.Length -ne $Size) { throw "Size mismatch for ${Path}: $($item.Length) != $Size" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) { throw "SHA-256 mismatch for ${Path}: $actual != $Sha256" }
}

function Invoke-Python([string[]]$Arguments, [string]$Stdout, [string]$Stderr) {
    $quoted = @($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $p = Start-Process -FilePath $python -ArgumentList $quoted -WorkingDirectory $repo `
        -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr `
        -Wait -PassThru
    return $p.ExitCode
}

function Assert-ReleaseReport([string]$ReportPath) {
    $report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
    if ($report.app_version -ne '0.22.0') { throw 'report app_version mismatch' }
    if ($report.solver_version -ne 'modal-mvp-0.8.3') { throw 'report solver_version mismatch' }
    if ($report.solver_profile_key -ne 'layerwise_admittance_v1') { throw 'report solver profile mismatch' }
    $policy = $report.convergence_policy
    if ($policy.version -ne 'adaptive-frequency-modal-v4' -or [int]$policy.max_refinement_iterations -ne 3) {
        throw 'report convergence policy mismatch'
    }
    $gate = $report.release_gate
    if ($gate.status -ne 'passed' -or [int]$gate.selected_rail_count -ne 16 -or
        $gate.full_rail_manifest_complete -ne $true -or [int]$gate.failure_count -ne 0 -or
        @($gate.failures).Count -ne 0) { throw 'report release gate failed' }
    if (@($report.identity.rail_outcome_provenance.mismatches).Count -ne 0) {
        throw 'report identity mismatches are non-empty'
    }
}

function Invoke-KnownCasePostflight([string]$CaseId, [string]$ReportPath) {
    $artifactBase = Join-Path $validationRoot `
        "v0.22.0-final-correlation-$CaseId-mode10-12-r4.known-case-nonregression"
    $sidecar = "$artifactBase.json"
    $stdout = "$artifactBase.stdout.log"; $stderr = "$artifactBase.stderr.log"
    $verifyStdout = "$artifactBase.verify.stdout.log"
    $verifyStderr = "$artifactBase.verify.stderr.log"
    $common = @($knownValidator, '--policy', $policy, '--report', $ReportPath,
        '--sidecar', $sidecar, '--case', $CaseId, '--v5-validator', $validator,
        '--baseline-root', $repo, '--benchmark-root', $repo)
    if ((Invoke-Python $common $stdout $stderr) -ne 0) {
        throw "$CaseId known-case non-regression creation failed"
    }
    $verify = @($knownValidator, '--verify-sidecar') + $common[1..($common.Count - 1)]
    if ((Invoke-Python $verify $verifyStdout $verifyStderr) -ne 0) {
        throw "$CaseId known-case non-regression verification failed"
    }
    $attestation = Get-Content -LiteralPath $sidecar -Raw | ConvertFrom-Json
    if ($attestation.status -ne 'PASS') { throw "$CaseId sidecar status is not PASS" }
    $expectedReportSha = [string]$attestation.raw_report_sha256
    if ($expectedReportSha -notmatch '^[0-9a-f]{64}$') {
        throw "$CaseId sidecar report SHA-256 is not canonical"
    }
    $currentReportSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReportPath).Hash.ToLowerInvariant()
    if ($currentReportSha -ne $expectedReportSha) {
        throw "$CaseId report changed after sidecar verification"
    }
    Write-Status "$CaseId known-case non-regression sidecar passed and verified."
    return $expectedReportSha
}

function Copy-VerifiedReport(
    [string]$CaseId, [string]$ReportPath, [string]$ExpectedReportSha
) {
    $destination = Join-Path $fixtureRoot "$CaseId\r4\correlation_report.json"
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) `
        -Force -ErrorAction Stop | Out-Null
    $sourceLength = (Get-Item -LiteralPath $ReportPath).Length
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReportPath).Hash.ToLowerInvariant()
    if ($sourceHash -ne $ExpectedReportSha) {
        throw "$CaseId source report changed before fixture copy"
    }
    if (Test-Path -LiteralPath $destination) {
        $destinationHash = (Get-FileHash -Algorithm SHA256 `
            -LiteralPath $destination).Hash.ToLowerInvariant()
        if ($destinationHash -ne $ExpectedReportSha -or
            $sourceLength -ne (Get-Item -LiteralPath $destination).Length) {
            throw "$CaseId tracked-target fixture exists with differing bytes"
        }
    } else {
        [IO.File]::Copy($ReportPath, $destination, $false)
    }
    $finalSourceHash = (Get-FileHash -Algorithm SHA256 `
        -LiteralPath $ReportPath).Hash.ToLowerInvariant()
    $finalDestinationHash = (Get-FileHash -Algorithm SHA256 `
        -LiteralPath $destination).Hash.ToLowerInvariant()
    if ($finalSourceHash -ne $ExpectedReportSha -or
        $finalDestinationHash -ne $ExpectedReportSha -or
        $sourceLength -ne (Get-Item -LiteralPath $destination).Length) {
        throw "$CaseId report/fixture changed across the copy boundary"
    }
    Write-Status "$CaseId report copied byte-identically to the r4 fixture target."
}

function Invoke-Case {
    param(
        [string]$CaseId, [string]$Spd, [long]$SpdSize, [string]$SpdSha,
        [string]$Touchstone, [long]$TouchstoneSize, [string]$TouchstoneSha,
        [string]$Candidate, [long]$CandidateSize, [string]$CandidateSha,
        [string]$ImportReport, [long]$ImportSize, [string]$ImportSha
    )
    $outRel = "validation-output\v0.22.0-final-correlation-$CaseId-mode10-12-r4"
    $out = Join-Path $repo $outRel
    $report = Join-Path $out 'correlation_report.json'
    $stdout = "$out.stdout.log"; $stderr = "$out.stderr.log"
    $vstdout = "$out.validator.stdout.log"; $vstderr = "$out.validator.stderr.log"
    foreach ($path in @($out, $stdout, $stderr, $vstdout, $vstderr)) {
        if (Test-Path -LiteralPath $path) { throw "${CaseId}: reserved r4 path already exists: $path" }
    }
    Assert-Identity $Spd $SpdSize $SpdSha
    Assert-Identity $Touchstone $TouchstoneSize $TouchstoneSha
    Assert-Identity (Join-Path $repo $Candidate) $CandidateSize $CandidateSha
    Assert-Identity (Join-Path $repo $ImportReport) $ImportSize $ImportSha
    New-Item -ItemType Directory -Path $out -ErrorAction Stop | Out-Null
    Write-Status "$CaseId correlation started."
    $args = @('-u', $benchmark, '--spd', $Spd, '--touchstone', $Touchstone,
        '--out-dir', $outRel, '--reuse-candidate', $Candidate,
        '--reuse-candidate-import-report', $ImportReport,
        '--solver-profile', 'layerwise_admittance_v1', '--modal-max-index', '10',
        '--modal-max-index', '12', '--modal-ceiling-index', '12', '--require-all-converged')
    if ((Invoke-Python $args $stdout $stderr) -ne 0) { throw "$CaseId benchmark failed" }
    if (-not (Test-Path -LiteralPath $report)) { throw "$CaseId missing correlation_report.json" }
    $vargs = @($validator, (Join-Path $outRel 'correlation_report.json'))
    if ((Invoke-Python $vargs $vstdout $vstderr) -ne 0) { throw "$CaseId v5 validator failed" }
    Assert-ReleaseReport $report
    Write-Status "$CaseId correlation passed release and identity gates."
    return $report
}

Set-Location -LiteralPath $repo
$env:PYTHONPATH = Join-Path $repo 'src'
try {
    foreach ($reserved in @($statusLog, $lockDir)) {
        if (Test-Path -LiteralPath $reserved) { throw "reserved r4 path already exists: $reserved" }
    }
    foreach ($caseId in @('260804', '260729')) {
        $caseBase = Join-Path $validationRoot `
            "v0.22.0-final-correlation-$caseId-mode10-12-r4"
        $knownBase = "$caseBase.known-case-nonregression"
        $fixture = Join-Path $fixtureRoot "$caseId\r4\correlation_report.json"
        foreach ($reserved in @($caseBase, "$caseBase.stdout.log", "$caseBase.stderr.log",
                "$caseBase.validator.stdout.log", "$caseBase.validator.stderr.log",
                "$knownBase.json", "$knownBase.stdout.log", "$knownBase.stderr.log",
                "$knownBase.verify.stdout.log", "$knownBase.verify.stderr.log", $fixture)) {
            if (Test-Path -LiteralPath $reserved) {
                throw "reserved r4 path already exists: $reserved"
            }
        }
    }
    $activeBenchmarks = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python' -and
        $_.CommandLine -like '*benchmark_raw_spd_powersi_correlation.py*'
    })
    if ($activeBenchmarks.Count -ne 0) {
        throw "another correlation benchmark is already active: $($activeBenchmarks.ProcessId -join ', ')"
    }
    New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null
    $lockAcquired = $true
    New-Item -ItemType File -Path $statusLog -ErrorAction Stop | Out-Null
    Write-Status 'R4 sequential correlation runner started.'
    $report260804 = Invoke-Case '260804' 'D:\S4LB002-2Para_260804_1_injected.spd' 1120159188 '45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35' 'D:\S4LB002-2Para_260804_1_injected_080526_104445_27112_S.s92p' 303902333 'cd103f42412c2a63518105d7e10fae8a0538c84982e1eddbfb74829a6972951b' 'validation-output\import-save-260804-r4\S4LB002-2Para_260804_1_injected_candidate.spdpi' 170345432 '2fc0448b79ce28d074f251e3190b29a621aa66dd7afcf5dec452a8215521e1aa' 'validation-output\import-save-260804-r4\import_save_validation_report.json' 3710 '0c8886d89c007bf68ef398e0337a407fed36fcfcb70336fe754c0c2bbc413b90'
    $reportSha260804 = Invoke-KnownCasePostflight '260804' $report260804
    $report260729 = Invoke-Case '260729' 'D:\S4LB002-2Para_260729_1_injected.spd' 1116717287 '40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2' 'D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p' 303974090 'c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11' 'validation-output\import-save-260729-r16\S4LB002-2Para_260729_1_injected_candidate.spdpi' 245102022 '956f9aa35f66083cc1c455639988f3268c5393d6167eabed4d562246fab7f79c' 'validation-output\import-save-260729-r16\import_save_validation_report.json' 3709 'a2d60769bf08ab27b40e78fb6a27109842ca4a1a28fb6298ce2e8350c16602fe'
    $reportSha260729 = Invoke-KnownCasePostflight '260729' $report260729
    Copy-VerifiedReport '260804' $report260804 $reportSha260804
    Copy-VerifiedReport '260729' $report260729 $reportSha260729
    Write-Status 'Both r4 correlations, v5 gates, sidecars, and fixture copies passed.'
    exit 0
} catch {
    if (Test-Path -LiteralPath $statusLog) { Write-Status "R4 runner failed closed: $($_.Exception.Message)" }
    exit 90
} finally {
    if ($lockAcquired -and (Test-Path -LiteralPath $lockDir)) { Remove-Item -LiteralPath $lockDir -Recurse -Force }
}
