param(
    [ValidateSet("manifest", "primary-h4-p0r")]
    [string]$Stage = "manifest",
    [string]$InternalMode = "",
    [string]$OuterObserverSessionPath = "",
    [string]$OuterObserverNonce = "",
    [int]$OuterObserverParentProcessId = 0,
    [int64]$OuterObserverParentBirthUtcTicks = 0,
    [string]$OuterObserverRunnerSha256 = "",
    [string]$OuterObserverReviewTokenSha256 = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$program = "SPD Decap PI Evaluator v0.22.0"
$schema = "AV-BS1-h4-p0r-resource-report-v1"
$guardSchema = "AV-BS1-h4-p0r-resource-guard-v1"
$tombstoneSchema = "AV-BS1-h4-p0r-consumed-review-token-v2"
$emergencyTombstoneSchema = "AV-BS1-h4-p0r-emergency-consumed-review-token-v2"
$emergencyReplacementIntentSchema = "AV-BS1-h4-p0r-emergency-replacement-intent-v1"
$emergencyReplacementPostvalidationSchema = "AV-BS1-h4-p0r-emergency-replacement-postvalidation-v1"
$controlPlaneSchema = "AV-BS1-h4-p0r-control-plane-process-report-v2"
$controlPlaneEnvelopeCloseSchema = "AV-BS1-h4-p0r-control-plane-envelope-close-v2"
$preExitControlPlaneEvidenceSchema = "AV-BS1-h4-p0r-control-plane-pre-exit-intent-evidence-v1"
$outerInnerReadySchema = "AV-BS1-h4-p0r-outer-inner-ready-v1"
$outerStartReleaseSchema = "AV-BS1-h4-p0r-outer-start-release-v1"
$outerInnerCompleteSchema = "AV-BS1-h4-p0r-outer-inner-complete-v1"
$outerExitReleaseSchema = "AV-BS1-h4-p0r-outer-exit-release-v1"
$outerTerminalSealSchema = "AV-BS1-h4-p0r-outer-terminal-seal-v2"
$outerObserverEnvelopeCloseSchema = "AV-BS1-h4-p0r-outer-resource-envelope-close-v2"
$outerHandshakePrefixSchema = "AV-BS1-h4-p0r-outer-observer-handshake-prefix-v1"
$outerInternalModeName = "primary-h4-p0r-inner-v1"
$pollMilliseconds = 100
$treeSampleMaximumAttempts = 3
$treeSampleRetryEventLimit = 16
$wallStopSeconds = 900
$controlPlaneWallStopSeconds = 180
$treeWorkingSetStop = [int64](4GB)
$treePrivateStop = [int64](5GB)
$treeCommitStop = [int64](5GB)
$commitHeadroomFloor = [int64](2GB)
$availablePhysicalFloor = [int64](1.5GB)
$minimumCommitHeadroomBeforeSpawn = [int64]5618345703
$minimumAvailablePhysicalBeforeSpawn = [int64]5081474791

$runnerScriptPath = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
$scriptDirectory = Split-Path -Parent $runnerScriptPath
$fixturePath = Join-Path $scriptDirectory "av_bs1_boundary_schur_h4_p0r_p1.py"
$reviewTokenPath = Join-Path $scriptDirectory "av_bs1_h4_p0r_p1_review_token.json"
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $scriptDirectory "..\.."))
$validationRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot "validation-output\av-bs1"))
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonPath = $pythonCommand.Source
$controlPlaneSessionRoot = $null
$controlPlaneBootstrapPath = $null
$controlPlaneCanonicalHashPath = $null
$controlPlaneReportReferences = $null
$controlPlaneLatestIndexPath = $null
$controlPlaneLatestIndexSha256 = $null
$controlPlaneIndexSequence = 0
$executionResourceScopeSha256 = $null
$emergencyReplacementIntentPath = $null
$emergencyReplacementIntentSha256 = $null
$emergencyReplacementPostvalidationPath = $null
$emergencyReplacementPostvalidationSha256 = $null
$emergencyReplacementRecoveryPath = $null
$emergencyReplacementRecoverySha256 = $null
$outerObserverInnerActive = $false
$outerObserverSessionRoot = $null
$outerObserverSessionRelativePath = $null
$outerObserverNonceValue = $null
$outerObserverParentPidValue = $null
$outerObserverParentBirthTicksValue = $null
$outerObserverRunnerSha256Value = $null
$outerObserverOriginalTokenSha256 = $null
$outerObserverOriginalTokenId = $null
$outerObserverContractSha256 = $null
$outerObserverExpectedTerminalSealRelativePath = $null
$outerObserverTerminalSealRequired = $true
$outerObserverReadyPath = $null
$outerObserverReadySha256 = $null
$outerObserverStartReleasePath = $null
$outerObserverStartReleaseSha256 = $null
$outerObserverCompletePath = $null
$outerObserverExitReleasePath = $null
$outerObserverHandshakePrefix = $null
$outerObserverHandshakePrefixSha256 = $null
$outerObserverPreExitEvidenceReference = $null
$outerObserverAttemptCleanupDisposition = "not_completed"
$outerObserverAttemptEvidenceRelativePath = $null
$executionTreeRootProcessId = $null
$executionTreeRootBirthUtcTicks = $null
$innerRunnerProcessId = $null
$innerRunnerBirthUtcTicks = $null

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-Utf8TextSha256([string]$Text) {
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { $hash = $algorithm.ComputeHash($bytes) }
    finally { $algorithm.Dispose() }
    return ([BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant())
}

function Get-RepositoryRelativePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $rootPrefix = $repositoryRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane artifact escaped the research checkout"
    }
    return $full.Substring($rootPrefix.Length).Replace('\', '/')
}

function Get-AttemptArtifactProvenance([System.Collections.IDictionary]$ArtifactPaths) {
    $provenance = [ordered]@{}
    foreach ($role in @("review_token", "claim", "guard", "resource", "result", "canonical_input")) {
        $rawPath = $null
        if ($null -ne $ArtifactPaths -and $ArtifactPaths.Contains($role)) {
            $rawPath = [string]$ArtifactPaths[$role]
        }
        if ([string]::IsNullOrWhiteSpace($rawPath)) {
            $provenance[$role] = [ordered]@{
                path = $null
                exists = $false
                bytes = $null
                sha256 = $null
            }
            continue
        }
        $fullPath = [IO.Path]::GetFullPath($rawPath)
        $exists = Test-Path -LiteralPath $fullPath -PathType Leaf
        $provenance[$role] = [ordered]@{
            path = $fullPath
            exists = $exists
            bytes = if ($exists) { [int64](Get-Item -LiteralPath $fullPath).Length } else { $null }
            sha256 = if ($exists) { Get-Sha256 $fullPath } else { $null }
        }
    }
    return $provenance
}

function Get-CanonicalJsonSha256(
    [string]$Path,
    [System.Collections.IDictionary]$AttemptArtifactPaths = $null
) {
    if (
        $null -eq $controlPlaneSessionRoot -or
        $null -eq $controlPlaneCanonicalHashPath -or
        -not (Test-Path -LiteralPath $controlPlaneCanonicalHashPath -PathType Leaf)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: bounded canonical JSON helper is unavailable"
    }
    $artifactPaths = [ordered]@{ canonical_input = [IO.Path]::GetFullPath($Path) }
    if ($null -ne $AttemptArtifactPaths) {
        foreach ($key in $AttemptArtifactPaths.Keys) {
            $artifactPaths[[string]$key] = $AttemptArtifactPaths[$key]
        }
    }
    $invocation = Invoke-ControlPlanePython `
        -Operation "canonical_json_hash" `
        -TargetScriptPath $controlPlaneCanonicalHashPath `
        -ScriptArguments @([IO.Path]::GetFullPath($Path)) `
        -AllowedExitCodes @(0) `
        -AttemptArtifactPaths $artifactPaths
    $canonicalHashLines = @($invocation.stdout_text -split "`r?`n" | Where-Object { $_ -ne "" })
    if ($canonicalHashLines.Count -ne 1 -or [string]$canonicalHashLines[0] -notmatch '^[0-9a-f]{64}$') {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: canonical JSON SHA-256 failed"
    }
    return [string]$canonicalHashLines[0]
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Write-AtomicUtf8NoBom([string]$Path, [string]$Text) {
    $target = [IO.Path]::GetFullPath($Path)
    $directory = [IO.Path]::GetDirectoryName($target)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw "BLOCKED_AV_BS_RESOURCE: atomic target directory is missing"
    }
    if (Test-Path -LiteralPath $target) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: atomic target already exists"
    }
    $temporary = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        if (Test-Path -LiteralPath $target) { throw "BLOCKED_AV_BS_RESULT_SCHEMA: atomic target raced" }
        [IO.File]::Move($temporary, $target)
        $temporary = $null
    }
    finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Read-BoundedJsonObject([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -le 0 -or $item.Length -gt 16MB) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json) }
    catch { return $null }
}

function Test-ExactJsonFieldSet([object]$Value, [string[]]$ExpectedFields) {
    if ($null -eq $Value) { return $false }
    $actualFields = @($Value.PSObject.Properties.Name | Sort-Object)
    $expectedSorted = @($ExpectedFields | Sort-Object)
    if ($actualFields.Count -ne $expectedSorted.Count) { return $false }
    $difference = @(Compare-Object -ReferenceObject $expectedSorted -DifferenceObject $actualFields)
    return ($difference.Count -eq 0)
}

function Assert-StrictJsonClrFieldTypes(
    [object]$Value,
    [string[]]$StringFields,
    [string[]]$Int32Fields,
    [string[]]$Int64Fields,
    [string[]]$BooleanFields,
    [string]$Label
) {
    foreach ($field in @($StringFields)) {
        if ($Value.$field -isnot [string]) { throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " string type mismatch: " + $field) }
    }
    foreach ($field in @($Int32Fields)) {
        if ($Value.$field -isnot [int]) { throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " Int32 type mismatch: " + $field) }
    }
    foreach ($field in @($Int64Fields)) {
        if ($Value.$field -isnot [long]) { throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " Int64 type mismatch: " + $field) }
    }
    foreach ($field in @($BooleanFields)) {
        if ($Value.$field -isnot [bool]) { throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " Boolean type mismatch: " + $field) }
    }
}

function ConvertFrom-StrictOuterMarkerUtc([object]$Value, [string]$Label) {
    if (
        $Value -isnot [string] -or
        [string]$Value -notmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}\+00:00$'
    ) {
        throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " UTC text is not canonical")
    }
    try {
        $parsed = [DateTimeOffset]::ParseExact(
            [string]$Value,
            "o",
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::None
        )
    }
    catch {
        throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " UTC text is invalid")
    }
    if ($parsed.Offset -ne [TimeSpan]::Zero) {
        throw ("BLOCKED_AV_BS_RESULT_SCHEMA: " + $Label + " UTC offset is not zero")
    }
    return $parsed
}

function Test-ByteArrayEqual([byte[]]$Left, [byte[]]$Right) {
    if ($null -eq $Left -or $null -eq $Right -or $Left.Length -ne $Right.Length) {
        return $false
    }
    for ($index = 0; $index -lt $Left.Length; $index += 1) {
        if ($Left[$index] -ne $Right[$index]) { return $false }
    }
    return $true
}

function Get-MatchingNormalConsumedTombstone([string]$Path) {
    try {
        $value = Read-BoundedJsonObject $Path
        if ($null -eq $value) { return $null }
        $expectedFields = @(
            "schema", "program", "case_id", "authorized_stage",
            "authorization_state", "uses_remaining", "next_stage_authorized",
            "review_disposition", "consumed_review_token_id",
            "consumed_review_token_sha256",
            "consumed_review_token_canonical_sha256",
            "p0r_preregistration_commit", "consumed_git_head",
            "review_binding_sha256", "bindings", "claim_relative_path",
            "claim_sha256", "claim_canonical_sha256", "claim_evidence_valid",
            "claim_evidence", "preflight_payload_sha256",
            "guard_contract_sha256", "guard_canonical_sha256",
            "guard_evidence_valid", "guard_evidence",
            "resource_guard_policy_sha256", "execution_resource_scope_sha256",
            "attempt_status", "effective_attempt_status",
            "consumption_validated_pass", "child_launched",
            "child_process_id", "child_exit_code", "consumed_result_file_sha256",
            "consumed_result_payload_sha256", "consumed_resource_report_sha256",
            "consumed_child_stdout_file_sha256", "consumed_child_payload_sha256",
            "result_evidence", "resource_evidence", "child_stdout_evidence",
            "result_evidence_valid", "resource_evidence_valid",
            "child_stdout_evidence_valid", "factor_prefix_evidence_valid",
            "factor_prefix_evidence", "resource_gate_pass",
            "resource_gate_recheck_failures", "evidence_validation_errors",
            "failure_codes", "mandatory_stage_pass", "factorization_attempted",
            "factorization_performed", "active_factor", "completed_factors",
            "factor_order", "factor_certificates", "physics_solve_performed",
            "outer_observer_contract_sha256",
            "outer_observer_handshake_prefix_sha256",
            "expected_terminal_seal_relative_path",
            "terminal_seal_required_for_authoritative_disposition",
            "terminal_seal_state", "terminal_evidence_complete",
            "authoritative_stage_pass",
            "consumed_utc"
        )
        if (-not (Test-ExactJsonFieldSet $value $expectedFields)) { return $null }
        $failureCodes = @($value.failure_codes)
        $terminalOutcomeConsistent = (
            (
                $value.consumption_validated_pass -eq $true -and
                $value.mandatory_stage_pass -eq $true -and
                $failureCodes.Count -eq 0
            ) -or (
                $value.consumption_validated_pass -eq $false -and
                $value.mandatory_stage_pass -eq $false -and
                $failureCodes.Count -gt 0
            )
        )
        $claimBindingConsistent = (
            (
                $value.claim_evidence_valid -eq $true -and
                $value.claim_canonical_sha256 -eq $claimCanonicalHash
            ) -or (
                $value.claim_evidence_valid -eq $false -and
                $null -eq $value.claim_canonical_sha256
            )
        )
        $guardBindingConsistent = (
            (
                $value.guard_evidence_valid -eq $true -and
                $value.guard_contract_sha256 -eq $guardHash -and
                $value.guard_canonical_sha256 -eq $guardCanonicalHash
            ) -or (
                $value.guard_evidence_valid -eq $false -and
                $value.guard_contract_sha256 -eq $guardHash -and
                $null -eq $value.guard_canonical_sha256
            )
        )
        if (
            $value.schema -ne $tombstoneSchema -or
            $value.program -ne $program -or
            $value.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
            $value.authorized_stage -ne "primary-h4-p0r" -or
            $value.authorization_state -ne "consumed" -or
            $value.uses_remaining -isnot [int] -or
            $value.uses_remaining -ne 0 -or
            $value.next_stage_authorized -isnot [bool] -or
            $value.next_stage_authorized -ne $false -or
            $value.review_disposition -ne "consumed_after_primary_h4_p0r_claim" -or
            $value.consumed_review_token_id -ne $token.review_token_id -or
            $value.consumed_review_token_sha256 -ne $reviewTokenHash -or
            $value.consumed_review_token_canonical_sha256 -ne $reviewTokenCanonicalHash -or
            $value.claim_relative_path -ne $claimRelativePath -or
            $value.claim_sha256 -ne $claimHash -or
            $value.claim_evidence_valid -isnot [bool] -or
            -not $claimBindingConsistent -or
            $value.guard_evidence_valid -isnot [bool] -or
            -not $guardBindingConsistent -or
            $value.resource_guard_policy_sha256 -ne $token.resource_policy_sha256 -or
            $value.execution_resource_scope_sha256 -ne $executionResourceScopeSha256 -or
            $value.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
            $value.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
            $value.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
            $value.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
            $value.terminal_seal_required_for_authoritative_disposition -ne $true -or
            $value.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
            $value.terminal_evidence_complete -isnot [bool] -or
            $value.terminal_evidence_complete -ne $false -or
            $value.authoritative_stage_pass -isnot [bool] -or
            $value.authoritative_stage_pass -ne $false -or
            $value.attempt_status -ne $attemptStatus -or
            $value.consumption_validated_pass -isnot [bool] -or
            $value.mandatory_stage_pass -isnot [bool] -or
            $value.physics_solve_performed -isnot [bool] -or
            $value.physics_solve_performed -ne $false -or
            -not $terminalOutcomeConsistent
        ) { return $null }
        return $value
    }
    catch { return $null }
}

function Get-MatchingEmergencyConsumedTombstone([string]$Path) {
    try {
        $value = Read-BoundedJsonObject $Path
        $expectedFields = @(
            "schema", "program", "case_id", "authorized_stage",
            "authorization_state", "uses_remaining", "next_stage_authorized",
            "review_disposition", "emergency_consumption",
            "consumer_control_gate_pass", "consumer_failure_message",
            "replaced_token_file_sha256", "consumed_review_token_id",
            "consumed_review_token_sha256",
            "consumed_review_token_canonical_sha256",
            "p0r_preregistration_commit", "consumed_git_head",
            "review_binding_sha256", "bindings", "claim_relative_path",
            "claim_sha256", "claim_canonical_sha256", "claim_evidence_valid",
            "claim_evidence", "preflight_payload_sha256",
            "guard_contract_sha256", "guard_canonical_sha256",
            "guard_evidence_valid", "guard_evidence",
            "resource_guard_policy_sha256", "execution_resource_scope_sha256",
            "attempt_status", "effective_attempt_status",
            "consumption_validated_pass", "child_launched",
            "child_process_id", "child_exit_code", "consumed_result_file_sha256",
            "consumed_result_payload_sha256", "consumed_resource_report_sha256",
            "consumed_child_stdout_file_sha256", "consumed_child_payload_sha256",
            "result_evidence", "resource_evidence", "child_stdout_evidence",
            "result_evidence_valid", "resource_evidence_valid",
            "child_stdout_evidence_valid", "factor_prefix_evidence_valid",
            "factor_prefix_evidence", "resource_gate_pass",
            "resource_gate_recheck_failures", "evidence_validation_errors",
            "failure_codes", "mandatory_stage_pass", "factorization_attempted",
            "factorization_performed", "active_factor", "completed_factors",
            "factor_order", "factor_certificates", "physics_solve_performed",
            "outer_observer_contract_sha256",
            "outer_observer_handshake_prefix_sha256",
            "expected_terminal_seal_relative_path",
            "terminal_seal_required_for_authoritative_disposition",
            "terminal_seal_state", "terminal_evidence_complete",
            "authoritative_stage_pass",
            "runner_sha256", "consumed_utc"
        )
        if (
            $null -eq $value -or
            -not (Test-ExactJsonFieldSet $value $expectedFields) -or
            $value.schema -ne $emergencyTombstoneSchema -or
            $value.program -ne $program -or
            $value.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
            $value.authorized_stage -ne "primary-h4-p0r" -or
            $value.authorization_state -ne "consumed" -or
            $value.uses_remaining -isnot [int] -or
            $value.uses_remaining -ne 0 -or
            $value.next_stage_authorized -isnot [bool] -or
            $value.next_stage_authorized -ne $false -or
            $value.review_disposition -ne "emergency_consumed_after_control_plane_failure" -or
            $value.emergency_consumption -isnot [bool] -or
            $value.emergency_consumption -ne $true -or
            $value.consumer_control_gate_pass -isnot [bool] -or
            $null -ne $value.replaced_token_file_sha256 -or
            $value.consumed_review_token_id -ne $token.review_token_id -or
            $value.consumed_review_token_sha256 -ne $reviewTokenHash -or
            $value.consumed_review_token_canonical_sha256 -ne $reviewTokenCanonicalHash -or
            $value.claim_relative_path -ne $claimRelativePath -or
            $value.claim_sha256 -ne $claimHash -or
            $value.claim_evidence_valid -isnot [bool] -or
            $value.claim_evidence_valid -ne $false -or
            $null -ne $value.claim_canonical_sha256 -or
            $null -ne $value.claim_evidence -or
            $value.guard_contract_sha256 -ne $guardHash -or
            $value.guard_evidence_valid -isnot [bool] -or
            $value.guard_evidence_valid -ne $false -or
            $null -ne $value.guard_canonical_sha256 -or
            $null -ne $value.guard_evidence -or
            $value.resource_guard_policy_sha256 -ne $token.resource_policy_sha256 -or
            $value.execution_resource_scope_sha256 -ne $executionResourceScopeSha256 -or
            $value.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
            $value.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
            $value.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
            $value.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
            $value.terminal_seal_required_for_authoritative_disposition -ne $true -or
            $value.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
            $value.terminal_evidence_complete -isnot [bool] -or
            $value.terminal_evidence_complete -ne $false -or
            $value.authoritative_stage_pass -isnot [bool] -or
            $value.authoritative_stage_pass -ne $false -or
            $value.attempt_status -ne $attemptStatus -or
            $value.consumption_validated_pass -isnot [bool] -or
            $value.consumption_validated_pass -ne $false -or
            $value.mandatory_stage_pass -isnot [bool] -or
            $value.mandatory_stage_pass -ne $false -or
            $value.physics_solve_performed -isnot [bool] -or
            $value.physics_solve_performed -ne $false -or
            @($value.failure_codes).Count -lt 1 -or
            $value.runner_sha256 -ne $runnerHash
        ) { return $null }
        return $value
    }
    catch { return $null }
}

function Write-OrReadExactJsonArtifact(
    [string]$Path,
    [System.Collections.Specialized.OrderedDictionary]$Value,
    [string]$TimestampField
) {
    $target = [IO.Path]::GetFullPath($Path)
    $requestedTimestamp = [string]$Value[$TimestampField]
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $existing = Read-BoundedJsonObject $target
        if ($null -eq $existing) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: durable JSON artifact collision is unreadable"
        }
        $Value[$TimestampField] = [string]$existing.$TimestampField
        $expectedJson = $Value | ConvertTo-Json -Depth 16 -Compress
        $actualJson = $existing | ConvertTo-Json -Depth 16 -Compress
        $Value[$TimestampField] = $requestedTimestamp
        if (
            -not (Test-ExactJsonFieldSet $existing @($Value.Keys)) -or
            $actualJson -cne $expectedJson
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: durable JSON artifact collision or mismatch"
        }
        return [pscustomobject]@{ path = $target; sha256 = Get-Sha256 $target; value = $existing }
    }
    $json = $Value | ConvertTo-Json -Depth 16 -Compress
    Write-AtomicUtf8NoBom $target $json
    $readback = Read-BoundedJsonObject $target
    $readbackJson = if ($null -ne $readback) { $readback | ConvertTo-Json -Depth 16 -Compress } else { $null }
    if (
        $null -eq $readback -or
        -not (Test-ExactJsonFieldSet $readback @($Value.Keys)) -or
        $readbackJson -cne $json
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: durable JSON artifact readback mismatch"
    }
    return [pscustomobject]@{ path = $target; sha256 = Get-Sha256 $target; value = $readback }
}

function Get-EmergencyReplacementJournalPaths {
    $journalRoot = [IO.Path]::GetFullPath((Join-Path $validationRoot "emergency-replacement-journals"))
    [IO.Directory]::CreateDirectory($journalRoot) | Out-Null
    $leafBase = [string]$token.review_token_id + "-" + [string]$nonce
    if ($leafBase -notmatch '^[0-9a-f]{32}-[0-9a-f]{32}$') {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency replacement journal identity is invalid"
    }
    $intentPath = [IO.Path]::GetFullPath((Join-Path $journalRoot ($leafBase + ".intent.json")))
    $postvalidationPath = [IO.Path]::GetFullPath((Join-Path $journalRoot ($leafBase + ".postvalidation.json")))
    $recoveryPath = [IO.Path]::GetFullPath((Join-Path $journalRoot ($leafBase + ".authorized-token-backup.bin")))
    foreach ($candidate in @($intentPath, $postvalidationPath, $recoveryPath)) {
        if (
            -not $candidate.StartsWith($journalRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetDirectoryName($candidate).TrimEnd('\') -ne $journalRoot.TrimEnd('\')
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency replacement journal path escaped its root"
        }
    }
    return [pscustomobject]@{
        root = $journalRoot
        intent = $intentPath
        postvalidation = $postvalidationPath
        recovery = $recoveryPath
    }
}

function Get-MatchingEmergencyReplacementPostvalidation([object]$EmergencyTombstone) {
    try {
        $paths = Get-EmergencyReplacementJournalPaths
        $intent = Read-BoundedJsonObject $paths.intent
        $postvalidation = Read-BoundedJsonObject $paths.postvalidation
        $targetRelativePath = Get-RepositoryRelativePath $reviewTokenPath
        $targetRelativeSlash = $targetRelativePath.LastIndexOf('/')
        if ($targetRelativeSlash -lt 0) { return $null }
        $expectedBackupRelativePrefix = (
            $targetRelativePath.Substring(0, $targetRelativeSlash + 1) + "." +
            $targetRelativePath.Substring($targetRelativeSlash + 1) + ".replaced."
        )
        $backupRelativePathShapeValid = $false
        if ($null -ne $intent) {
            $backupRelativePathShapeValid = (
                [string]$intent.file_replace_backup_relative_path -match
                ('^' + [regex]::Escape($expectedBackupRelativePrefix) + '[0-9a-f]{32}\.bak$')
            )
        }
        $intentFields = @(
            "schema", "program", "case_id", "authorized_stage",
            "journal_identity", "review_token_id",
            "original_review_token_sha256",
            "original_review_token_canonical_sha256",
            "claim_relative_path", "claim_sha256", "guard_sha256",
            "attempt_status", "target_relative_path",
            "file_replace_backup_relative_path",
            "expected_original_review_token_sha256",
            "expected_original_review_token_canonical_sha256",
            "pre_replace_observed_sha256",
            "pre_replace_observed_exact_retained_bytes_match",
            "replacement_state", "file_replace_is_atomic_compare_and_swap",
            "prior_bytes_verified_from_file_replace_backup",
            "replacement_authority", "authorization_blocker",
            "terminal_evidence_complete",
            "external_runner_or_machine_kill_terminal_state_guaranteed",
            "created_utc"
        )
        $postvalidationFields = @(
            "schema", "program", "case_id", "authorized_stage",
            "journal_identity", "review_token_id",
            "original_review_token_sha256",
            "original_review_token_canonical_sha256",
            "claim_relative_path", "claim_sha256", "guard_sha256",
            "attempt_status", "intent_relative_path", "intent_sha256",
            "recovery_relative_path", "recovery_sha256",
            "emergency_tombstone_relative_path",
            "emergency_tombstone_sha256", "emergency_tombstone_schema",
            "file_replace_is_atomic_compare_and_swap",
            "prior_bytes_verified_from_file_replace_backup",
            "backup_exact_retained_original_bytes_match",
            "replacement_state", "replacement_authority",
            "authorization_blocker", "lifecycle_completion_claimed",
            "terminal_evidence_complete",
            "external_observer_required_for_terminal_exit",
            "external_runner_or_machine_kill_terminal_state_guaranteed",
            "recorded_utc"
        )
        if (
            $null -eq $intent -or
            $null -eq $postvalidation -or
            -not (Test-ExactJsonFieldSet $intent $intentFields) -or
            -not (Test-ExactJsonFieldSet $postvalidation $postvalidationFields) -or
            $intent.schema -ne $emergencyReplacementIntentSchema -or
            $postvalidation.schema -ne $emergencyReplacementPostvalidationSchema -or
            $intent.program -ne $program -or
            $postvalidation.program -ne $program -or
            $intent.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
            $postvalidation.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
            $intent.authorized_stage -ne "primary-h4-p0r" -or
            $postvalidation.authorized_stage -ne "primary-h4-p0r" -or
            $intent.journal_identity -ne ($token.review_token_id + "-" + $nonce) -or
            $postvalidation.journal_identity -ne $intent.journal_identity -or
            $intent.review_token_id -ne $token.review_token_id -or
            $postvalidation.review_token_id -ne $token.review_token_id -or
            $intent.original_review_token_sha256 -ne $reviewTokenHash -or
            $postvalidation.original_review_token_sha256 -ne $reviewTokenHash -or
            $intent.original_review_token_canonical_sha256 -ne $reviewTokenCanonicalHash -or
            $postvalidation.original_review_token_canonical_sha256 -ne $reviewTokenCanonicalHash -or
            $intent.claim_relative_path -ne $claimRelativePath -or
            $postvalidation.claim_relative_path -ne $claimRelativePath -or
            $intent.claim_sha256 -ne $claimHash -or
            $postvalidation.claim_sha256 -ne $claimHash -or
            $intent.guard_sha256 -ne $guardHash -or
            $postvalidation.guard_sha256 -ne $guardHash -or
            $intent.attempt_status -ne $attemptStatus -or
            $postvalidation.attempt_status -ne $attemptStatus -or
            $intent.target_relative_path -ne $targetRelativePath -or
            -not $backupRelativePathShapeValid -or
            $intent.expected_original_review_token_sha256 -ne $reviewTokenHash -or
            $intent.expected_original_review_token_canonical_sha256 -ne $reviewTokenCanonicalHash -or
            $intent.pre_replace_observed_sha256 -ne $reviewTokenHash -or
            $intent.pre_replace_observed_exact_retained_bytes_match -isnot [bool] -or
            $intent.pre_replace_observed_exact_retained_bytes_match -ne $true -or
            $intent.replacement_state -ne "pending_file_replace_postvalidation" -or
            $intent.file_replace_is_atomic_compare_and_swap -isnot [bool] -or
            $intent.file_replace_is_atomic_compare_and_swap -ne $false -or
            $intent.prior_bytes_verified_from_file_replace_backup -isnot [bool] -or
            $intent.prior_bytes_verified_from_file_replace_backup -ne $false -or
            $intent.replacement_authority -ne "none_pending_postvalidation" -or
            $intent.authorization_blocker -isnot [bool] -or
            $intent.authorization_blocker -ne $true -or
            $intent.terminal_evidence_complete -isnot [bool] -or
            $intent.terminal_evidence_complete -ne $false -or
            $intent.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
            $intent.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false -or
            $postvalidation.replacement_state -ne "replacement_postvalidated_pending_external_observer" -or
            $postvalidation.file_replace_is_atomic_compare_and_swap -isnot [bool] -or
            $postvalidation.file_replace_is_atomic_compare_and_swap -ne $false -or
            $postvalidation.prior_bytes_verified_from_file_replace_backup -isnot [bool] -or
            $postvalidation.prior_bytes_verified_from_file_replace_backup -ne $true -or
            $postvalidation.backup_exact_retained_original_bytes_match -isnot [bool] -or
            $postvalidation.backup_exact_retained_original_bytes_match -ne $true -or
            $postvalidation.intent_relative_path -ne (Get-RepositoryRelativePath $paths.intent) -or
            $postvalidation.intent_sha256 -ne (Get-Sha256 $paths.intent) -or
            $postvalidation.recovery_relative_path -ne (Get-RepositoryRelativePath $paths.recovery) -or
            -not (Test-Path -LiteralPath $paths.recovery -PathType Leaf) -or
            $postvalidation.recovery_sha256 -ne (Get-Sha256 $paths.recovery) -or
            $postvalidation.recovery_sha256 -ne $reviewTokenHash -or
            $postvalidation.emergency_tombstone_relative_path -ne (Get-RepositoryRelativePath $reviewTokenPath) -or
            $postvalidation.emergency_tombstone_sha256 -ne (Get-Sha256 $reviewTokenPath) -or
            $postvalidation.emergency_tombstone_schema -ne $emergencyTombstoneSchema -or
            $EmergencyTombstone.schema -ne $emergencyTombstoneSchema -or
            $postvalidation.replacement_authority -ne "postvalidated_file_replacement_only_not_terminal_lifecycle_evidence" -or
            $postvalidation.authorization_blocker -isnot [bool] -or
            $postvalidation.authorization_blocker -ne $true -or
            $postvalidation.lifecycle_completion_claimed -isnot [bool] -or
            $postvalidation.lifecycle_completion_claimed -ne $false -or
            $postvalidation.terminal_evidence_complete -isnot [bool] -or
            $postvalidation.terminal_evidence_complete -ne $false -or
            $postvalidation.external_observer_required_for_terminal_exit -isnot [bool] -or
            $postvalidation.external_observer_required_for_terminal_exit -ne $true -or
            $postvalidation.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
            $postvalidation.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false
        ) { return $null }
        $recoveryBytes = [IO.File]::ReadAllBytes($paths.recovery)
        if (-not (Test-ByteArrayEqual $recoveryBytes $reviewTokenAuthorizedBytes)) {
            return $null
        }
        $script:emergencyReplacementIntentPath = $paths.intent
        $script:emergencyReplacementIntentSha256 = Get-Sha256 $paths.intent
        $script:emergencyReplacementPostvalidationPath = $paths.postvalidation
        $script:emergencyReplacementPostvalidationSha256 = Get-Sha256 $paths.postvalidation
        $script:emergencyReplacementRecoveryPath = $paths.recovery
        $script:emergencyReplacementRecoverySha256 = Get-Sha256 $paths.recovery
        return $postvalidation
    }
    catch { return $null }
}

function Set-EmergencyConsumedTombstone(
    [string]$ConsumerFailureMessage,
    [bool]$ConsumerControlGatePassed,
    [bool]$FactorRootExitObserved,
    [bool]$FactorCleanupVerified,
    [int]$OwnedSurvivorCount
) {
    if (
        -not $FactorRootExitObserved -or
        -not $FactorCleanupVerified -or
        $OwnedSurvivorCount -ne 0
    ) {
        throw "BLOCKED_AV_BS_RESOURCE: emergency consumption withheld before verified factor-tree cleanup"
    }
    $existingEmergency = Get-MatchingEmergencyConsumedTombstone $reviewTokenPath
    if ($null -ne $existingEmergency) {
        if ($null -eq (Get-MatchingEmergencyReplacementPostvalidation $existingEmergency)) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: existing emergency replacement remains pending postvalidation"
        }
        return $existingEmergency
    }

    # A normal same-attempt tombstone is already fail-closed. Never replace it
    # merely because the surrounding wrapper/report path failed afterward.
    $existingNormal = Get-MatchingNormalConsumedTombstone $reviewTokenPath
    if ($null -ne $existingNormal) { return $existingNormal }

    if (-not (Test-Path -LiteralPath $reviewTokenPath -PathType Leaf)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency consumption found no canonical token file"
    }
    if ($null -eq $reviewTokenAuthorizedBytes) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: exact authorized token bytes were not retained"
    }
    $priorTokenBytes = [IO.File]::ReadAllBytes($reviewTokenPath)
    $priorTokenFileSha256 = Get-Sha256 $reviewTokenPath
    if (
        $priorTokenFileSha256 -ne $reviewTokenHash -or
        -not (Test-ByteArrayEqual $priorTokenBytes $reviewTokenAuthorizedBytes)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency consumption refused bytes other than the exact authorized token"
    }
    $allowedPriorTokenFileSha256 = $reviewTokenHash

    $boundedFailureMessage = [string]$ConsumerFailureMessage
    if ($boundedFailureMessage.Length -gt 4096) {
        $boundedFailureMessage = $boundedFailureMessage.Substring(0, 4096)
    }
    $emergencyFailureCode = if ($ConsumerControlGatePassed) {
        "BLOCKED_AV_BS_RESULT_SCHEMA"
    } else {
        "BLOCKED_AV_BS_RESOURCE"
    }
    $emergency = [ordered]@{
        schema = $emergencyTombstoneSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        authorized_stage = "primary-h4-p0r"
        authorization_state = "consumed"
        uses_remaining = 0
        next_stage_authorized = $false
        review_disposition = "emergency_consumed_after_control_plane_failure"
        emergency_consumption = $true
        consumer_control_gate_pass = $ConsumerControlGatePassed
        consumer_failure_message = $boundedFailureMessage
        # File.Replace is not compare-and-swap. The emergency tombstone never
        # self-asserts which prior bytes were displaced; only the later journal
        # may record that after validating the File.Replace backup.
        replaced_token_file_sha256 = $null
        consumed_review_token_id = $token.review_token_id
        consumed_review_token_sha256 = $reviewTokenHash
        consumed_review_token_canonical_sha256 = $reviewTokenCanonicalHash
        p0r_preregistration_commit = $token.p0r_preregistration_commit
        consumed_git_head = $preflightWrapper.payload.git_head
        review_binding_sha256 = $preflightWrapper.payload.review_binding_sha256
        bindings = $preflightWrapper.payload.manifest_bindings
        claim_relative_path = $claimRelativePath
        claim_sha256 = $claimHash
        claim_canonical_sha256 = $null
        claim_evidence_valid = $false
        claim_evidence = $null
        preflight_payload_sha256 = $preflightWrapper.payload_sha256
        guard_contract_sha256 = $guardHash
        guard_canonical_sha256 = $null
        guard_evidence_valid = $false
        guard_evidence = $null
        resource_guard_policy_sha256 = $token.resource_policy_sha256
        execution_resource_scope_sha256 = $executionResourceScopeSha256
        attempt_status = $attemptStatus
        effective_attempt_status = "control_plane_consumer_failure"
        consumption_validated_pass = $false
        child_launched = $childLaunched
        child_process_id = if ($process) { [int]$process.Id } else { $null }
        child_exit_code = $null
        consumed_result_file_sha256 = if (Test-Path -LiteralPath $finalPath -PathType Leaf) { Get-Sha256 $finalPath } else { $null }
        consumed_result_payload_sha256 = $null
        consumed_resource_report_sha256 = if (Test-Path -LiteralPath $resourcePath -PathType Leaf) { Get-Sha256 $resourcePath } else { $null }
        consumed_child_stdout_file_sha256 = if (Test-Path -LiteralPath $stdoutPath -PathType Leaf) { Get-Sha256 $stdoutPath } else { $null }
        consumed_child_payload_sha256 = $null
        result_evidence = $null
        resource_evidence = $null
        child_stdout_evidence = $null
        result_evidence_valid = $false
        resource_evidence_valid = $false
        child_stdout_evidence_valid = $false
        factor_prefix_evidence_valid = $false
        factor_prefix_evidence = $null
        resource_gate_pass = $false
        resource_gate_recheck_failures = @("emergency_consumer_control_plane_failure")
        evidence_validation_errors = @($boundedFailureMessage)
        failure_codes = @($emergencyFailureCode)
        mandatory_stage_pass = $false
        factorization_attempted = if ($childLaunched) { $null } else { $false }
        factorization_performed = if ($childLaunched) { $null } else { $false }
        active_factor = $null
        completed_factors = @()
        factor_order = @()
        factor_certificates = @()
        physics_solve_performed = $false
        outer_observer_contract_sha256 = $script:outerObserverContractSha256
        outer_observer_handshake_prefix_sha256 = $script:outerObserverHandshakePrefixSha256
        expected_terminal_seal_relative_path = $script:outerObserverExpectedTerminalSealRelativePath
        terminal_seal_required_for_authoritative_disposition = $true
        terminal_seal_state = "pending_outer_observed_inner_exit"
        terminal_evidence_complete = $false
        authoritative_stage_pass = $false
        runner_sha256 = $runnerHash
        consumed_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }

    $target = [IO.Path]::GetFullPath($reviewTokenPath)
    $directory = [IO.Path]::GetDirectoryName($target)
    $temporary = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + ".emergency." + [guid]::NewGuid().ToString("N") + ".tmp")
    $backup = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + ".replaced." + [guid]::NewGuid().ToString("N") + ".bak")
    $recoveryCopy = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + ".recovery." + [guid]::NewGuid().ToString("N") + ".bak")
    $rollbackDiscard = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + ".rollback." + [guid]::NewGuid().ToString("N") + ".tmp")
    $replacementCommitted = $false
    $preserveBackup = $false
    $preserveRecoveryCopy = $false
    $preserveRollbackDiscard = $false
    try {
        # File.Replace is not compare-and-swap. Record durable, nonauthorizing
        # intent before it can displace the canonical token, including the
        # pathname where File.Replace has been asked to retain prior bytes.
        $journalPaths = Get-EmergencyReplacementJournalPaths
        $journalIdentity = $token.review_token_id + "-" + $nonce
        $intent = [ordered]@{
            schema = $emergencyReplacementIntentSchema
            program = $program
            case_id = "AV-BS1-CIRCLE-PRIMARY"
            authorized_stage = "primary-h4-p0r"
            journal_identity = $journalIdentity
            review_token_id = $token.review_token_id
            original_review_token_sha256 = $reviewTokenHash
            original_review_token_canonical_sha256 = $reviewTokenCanonicalHash
            claim_relative_path = $claimRelativePath
            claim_sha256 = $claimHash
            guard_sha256 = $guardHash
            attempt_status = $attemptStatus
            target_relative_path = Get-RepositoryRelativePath $reviewTokenPath
            file_replace_backup_relative_path = Get-RepositoryRelativePath $backup
            expected_original_review_token_sha256 = $reviewTokenHash
            expected_original_review_token_canonical_sha256 = $reviewTokenCanonicalHash
            pre_replace_observed_sha256 = $priorTokenFileSha256
            pre_replace_observed_exact_retained_bytes_match = $true
            replacement_state = "pending_file_replace_postvalidation"
            file_replace_is_atomic_compare_and_swap = $false
            prior_bytes_verified_from_file_replace_backup = $false
            replacement_authority = "none_pending_postvalidation"
            authorization_blocker = $true
            terminal_evidence_complete = $false
            external_runner_or_machine_kill_terminal_state_guaranteed = $false
            created_utc = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $intentReference = Write-OrReadExactJsonArtifact $journalPaths.intent $intent "created_utc"
        $script:emergencyReplacementIntentPath = $intentReference.path
        $script:emergencyReplacementIntentSha256 = $intentReference.sha256

        $raw = $emergency | ConvertTo-Json -Depth 16 -Compress
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($raw)
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: canonical token disappeared before emergency replacement"
        }
        [IO.File]::Replace($temporary, $target, $backup, $true)
        $temporary = $null
        $replacedBackupSha256 = Get-Sha256 $backup
        $replacedBackupBytes = [IO.File]::ReadAllBytes($backup)
        if (
            $replacedBackupSha256 -ne $allowedPriorTokenFileSha256 -or
            -not (Test-ByteArrayEqual $replacedBackupBytes $reviewTokenAuthorizedBytes)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency replacement backup detected raced prior bytes"
        }
        $readback = Get-MatchingEmergencyConsumedTombstone $target
        if ($null -eq $readback) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency consumed tombstone readback mismatch"
        }

        # Preserve the validated pre-replacement bytes at the deterministic
        # journal path before recording postvalidation. If anything fails after
        # this move, the emergency token remains pending and the recovery bytes
        # remain durable; no terminal or authorization claim is made.
        if (Test-Path -LiteralPath $journalPaths.recovery -PathType Leaf) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency replacement recovery artifact collision"
        }
        [IO.File]::Move($backup, $journalPaths.recovery)
        $backup = $null
        $recoverySha256 = Get-Sha256 $journalPaths.recovery
        $recoveryBytes = [IO.File]::ReadAllBytes($journalPaths.recovery)
        if (
            $recoverySha256 -ne $reviewTokenHash -or
            -not (Test-ByteArrayEqual $recoveryBytes $reviewTokenAuthorizedBytes)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency replacement recovery bytes failed postvalidation"
        }

        $emergencyTombstoneSha256 = Get-Sha256 $target
        $postvalidation = [ordered]@{
            schema = $emergencyReplacementPostvalidationSchema
            program = $program
            case_id = "AV-BS1-CIRCLE-PRIMARY"
            authorized_stage = "primary-h4-p0r"
            journal_identity = $journalIdentity
            review_token_id = $token.review_token_id
            original_review_token_sha256 = $reviewTokenHash
            original_review_token_canonical_sha256 = $reviewTokenCanonicalHash
            claim_relative_path = $claimRelativePath
            claim_sha256 = $claimHash
            guard_sha256 = $guardHash
            attempt_status = $attemptStatus
            intent_relative_path = Get-RepositoryRelativePath $journalPaths.intent
            intent_sha256 = $intentReference.sha256
            recovery_relative_path = Get-RepositoryRelativePath $journalPaths.recovery
            recovery_sha256 = $recoverySha256
            emergency_tombstone_relative_path = Get-RepositoryRelativePath $target
            emergency_tombstone_sha256 = $emergencyTombstoneSha256
            emergency_tombstone_schema = $emergencyTombstoneSchema
            file_replace_is_atomic_compare_and_swap = $false
            prior_bytes_verified_from_file_replace_backup = $true
            backup_exact_retained_original_bytes_match = $true
            replacement_state = "replacement_postvalidated_pending_external_observer"
            replacement_authority = "postvalidated_file_replacement_only_not_terminal_lifecycle_evidence"
            authorization_blocker = $true
            lifecycle_completion_claimed = $false
            terminal_evidence_complete = $false
            external_observer_required_for_terminal_exit = $true
            external_runner_or_machine_kill_terminal_state_guaranteed = $false
            recorded_utc = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $postvalidationReference = Write-OrReadExactJsonArtifact $journalPaths.postvalidation $postvalidation "recorded_utc"
        $script:emergencyReplacementPostvalidationPath = $postvalidationReference.path
        $script:emergencyReplacementPostvalidationSha256 = $postvalidationReference.sha256
        $script:emergencyReplacementRecoveryPath = $journalPaths.recovery
        $script:emergencyReplacementRecoverySha256 = $recoverySha256
        if ($null -eq (Get-MatchingEmergencyReplacementPostvalidation $readback)) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency replacement postvalidation journal mismatch"
        }
        $replacementCommitted = $true
        return $readback
    }
    finally {
        if (
            -not $replacementCommitted -and
            (Test-Path -LiteralPath $backup -PathType Leaf) -and
            (Test-Path -LiteralPath $target -PathType Leaf)
        ) {
            try {
                # Keep a second durable copy until the restored target is read
                # back byte-for-byte. File.Replace moves the backup pathname,
                # so the copy prevents an unproved restore from deleting the
                # sole retained pre-replacement state.
                [IO.File]::Copy($backup, $recoveryCopy, $false)
                $recoveryBytes = [IO.File]::ReadAllBytes($recoveryCopy)
                $backupBytes = [IO.File]::ReadAllBytes($backup)
                if (-not (Test-ByteArrayEqual $recoveryBytes $backupBytes)) {
                    throw "emergency rollback recovery copy mismatch"
                }
                [IO.File]::Replace($backup, $target, $rollbackDiscard, $true)
                $restoredBytes = [IO.File]::ReadAllBytes($target)
                if (-not (Test-ByteArrayEqual $restoredBytes $recoveryBytes)) {
                    $preserveRecoveryCopy = $true
                    $preserveRollbackDiscard = $true
                    throw "emergency rollback target readback mismatch"
                }
                $backup = $null
            }
            catch {
                # Preserve the backup rather than deleting the only copy of the
                # pre-replacement bytes when rollback itself cannot be proven.
                $preserveBackup = $true
                if (Test-Path -LiteralPath $recoveryCopy -PathType Leaf) {
                    $preserveRecoveryCopy = $true
                }
                if (Test-Path -LiteralPath $rollbackDiscard -PathType Leaf) {
                    $preserveRollbackDiscard = $true
                }
            }
        }
        elseif (-not $replacementCommitted -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
            # With no target available there is nothing safe to replace. Keep
            # the backup as the only known pre-replacement state.
            $preserveBackup = $true
        }
        if ($temporary -and (Test-Path -LiteralPath $temporary -PathType Leaf)) {
            Remove-Item -LiteralPath $temporary -Force
        }
        if ($backup -and -not $preserveBackup -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
            Remove-Item -LiteralPath $backup -Force
        }
        if (-not $preserveRecoveryCopy -and (Test-Path -LiteralPath $recoveryCopy -PathType Leaf)) {
            Remove-Item -LiteralPath $recoveryCopy -Force
        }
        if (-not $preserveRollbackDiscard -and (Test-Path -LiteralPath $rollbackDiscard -PathType Leaf)) {
            Remove-Item -LiteralPath $rollbackDiscard -Force
        }
    }
}

function Get-OuterEmergencyReplacementJournalPaths(
    [string]$ReviewTokenId,
    [string]$ObserverNonce
) {
    $journalRoot = [IO.Path]::GetFullPath((Join-Path $validationRoot "emergency-replacement-journals"))
    [IO.Directory]::CreateDirectory($journalRoot) | Out-Null
    $leafBase = $ReviewTokenId + "-" + $ObserverNonce
    if ($leafBase -notmatch '^[0-9a-f]{32}-[0-9a-f]{32}$') {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency journal identity is invalid"
    }
    $intentPath = [IO.Path]::GetFullPath((Join-Path $journalRoot ($leafBase + ".intent.json")))
    $postvalidationPath = [IO.Path]::GetFullPath((Join-Path $journalRoot ($leafBase + ".postvalidation.json")))
    $recoveryPath = [IO.Path]::GetFullPath((Join-Path $journalRoot ($leafBase + ".authorized-token-backup.bin")))
    foreach ($candidate in @($intentPath, $postvalidationPath, $recoveryPath)) {
        if (
            -not $candidate.StartsWith($journalRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetDirectoryName($candidate).TrimEnd('\') -ne $journalRoot.TrimEnd('\')
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency journal path escaped its root"
        }
    }
    return [pscustomobject]@{
        intent = $intentPath
        postvalidation = $postvalidationPath
        recovery = $recoveryPath
    }
}

function Set-OuterObserverEmergencyConsumedTombstone(
    [object]$CandidateToken,
    [byte[]]$OriginalTokenBytes,
    [string]$OriginalTokenSha256,
    [string]$RunnerSha256,
    [object]$ClaimCandidate,
    [string]$ClaimRelativePath,
    [string]$ClaimSha256,
    [object]$ObserverHandshakePrefix,
    [string]$ObserverHandshakePrefixSha256,
    [string]$ObserverNonce,
    [bool]$RetainedInnerActualExitObserved,
    [bool]$OuterCleanupVerified,
    [int]$OwnedSurvivorCount,
    [string]$FailureMessage,
    [string]$FailureCode
) {
    # This recovery is intentionally weaker than normal consumption. The outer
    # observer can prove the exact authorized token and owned claim, but cannot
    # prove the inner runner's factor-child phase, guard, result, or resource
    # evidence after a controlled interruption. Those fields remain null/false.
    if (
        $null -eq $CandidateToken -or
        $null -eq $ClaimCandidate -or
        $null -eq $OriginalTokenBytes -or
        -not $RetainedInnerActualExitObserved -or
        -not $OuterCleanupVerified -or
        $OwnedSurvivorCount -ne 0 -or
        $FailureCode -notin @("BLOCKED_AV_BS_RESOURCE", "BLOCKED_AV_BS_RESULT_SCHEMA")
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery inputs are incomplete"
    }
    if (-not (Test-Path -LiteralPath $reviewTokenPath -PathType Leaf)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery token is missing"
    }
    $currentTokenSha256 = Get-Sha256 $reviewTokenPath
    $currentTokenBytes = [IO.File]::ReadAllBytes($reviewTokenPath)
    if (
        $currentTokenSha256 -ne $OriginalTokenSha256 -or
        -not (Test-ByteArrayEqual $currentTokenBytes $OriginalTokenBytes)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery refused drifted token bytes"
    }
    if (
        $ClaimCandidate.claim_relative_path -ne $ClaimRelativePath -or
        $ClaimCandidate.review_token_id -ne $CandidateToken.review_token_id -or
        $ClaimCandidate.review_token_sha256 -ne $OriginalTokenSha256 -or
        $ClaimCandidate.runner_sha256 -ne $RunnerSha256 -or
        $ClaimCandidate.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
        $ClaimCandidate.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
        $ClaimCandidate.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $ClaimCandidate.terminal_seal_required_for_authoritative_disposition -ne $true -or
        -not (Test-StrictJsonValueEqual $ClaimCandidate.outer_observer_handshake_prefix $ObserverHandshakePrefix) -or
        $ClaimCandidate.outer_observer_handshake_prefix_sha256 -ne $ObserverHandshakePrefixSha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery claim is not observer-bound"
    }
    $claimSha256Readback = Get-Sha256 (Resolve-RepositoryRelativePath $ClaimRelativePath)
    if ($claimSha256Readback -ne $ClaimSha256) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery claim changed"
    }

    $boundedFailureMessage = [string]$FailureMessage
    if ($boundedFailureMessage.Length -gt 4096) {
        $boundedFailureMessage = $boundedFailureMessage.Substring(0, 4096)
    }
    $bindings = [ordered]@{
        fixture_sha256 = [string]$CandidateToken.fixture_sha256
        runner_sha256 = [string]$CandidateToken.runner_sha256
        static_test_sha256 = [string]$CandidateToken.static_test_sha256
        preregistration_doc_sha256 = [string]$CandidateToken.preregistration_doc_sha256
        manifest_payload_sha256 = [string]$CandidateToken.manifest_payload_sha256
        matrix_contract_sha256 = [string]$CandidateToken.matrix_contract_sha256
        parent_bindings = $CandidateToken.parent_bindings
    }
    $emergency = [ordered]@{
        schema = $emergencyTombstoneSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        authorized_stage = "primary-h4-p0r"
        authorization_state = "consumed"
        uses_remaining = 0
        next_stage_authorized = $false
        review_disposition = "emergency_consumed_after_outer_observer_post_claim_failure"
        emergency_consumption = $true
        consumer_control_gate_pass = $false
        consumer_failure_message = $boundedFailureMessage
        replaced_token_file_sha256 = $null
        consumed_review_token_id = [string]$CandidateToken.review_token_id
        consumed_review_token_sha256 = $OriginalTokenSha256
        consumed_review_token_canonical_sha256 = [string]$ClaimCandidate.review_token_canonical_sha256
        p0r_preregistration_commit = [string]$CandidateToken.p0r_preregistration_commit
        consumed_git_head = [string]$ClaimCandidate.git_head
        review_binding_sha256 = [string]$ClaimCandidate.review_binding_sha256
        bindings = $bindings
        claim_relative_path = $ClaimRelativePath
        claim_sha256 = $ClaimSha256
        claim_canonical_sha256 = $null
        claim_evidence_valid = $false
        claim_evidence = $null
        preflight_payload_sha256 = [string]$ClaimCandidate.preflight_payload_sha256
        guard_contract_sha256 = $null
        guard_canonical_sha256 = $null
        guard_evidence_valid = $false
        guard_evidence = $null
        resource_guard_policy_sha256 = [string]$CandidateToken.resource_policy_sha256
        execution_resource_scope_sha256 = [string]$CandidateToken.execution_resource_scope_sha256
        attempt_status = "outer_observer_post_claim_failure"
        effective_attempt_status = "outer_observer_post_claim_failure"
        consumption_validated_pass = $false
        child_launched = $null
        child_process_id = $null
        child_exit_code = $null
        consumed_result_file_sha256 = $null
        consumed_result_payload_sha256 = $null
        consumed_resource_report_sha256 = $null
        consumed_child_stdout_file_sha256 = $null
        consumed_child_payload_sha256 = $null
        result_evidence = $null
        resource_evidence = $null
        child_stdout_evidence = $null
        result_evidence_valid = $false
        resource_evidence_valid = $false
        child_stdout_evidence_valid = $false
        factor_prefix_evidence_valid = $false
        factor_prefix_evidence = $null
        resource_gate_pass = $false
        resource_gate_recheck_failures = @("outer_observer_post_claim_failure")
        evidence_validation_errors = @($boundedFailureMessage)
        failure_codes = @($FailureCode)
        mandatory_stage_pass = $false
        factorization_attempted = $null
        factorization_performed = $null
        active_factor = $null
        completed_factors = @()
        factor_order = @()
        factor_certificates = @()
        physics_solve_performed = $false
        outer_observer_contract_sha256 = [string]$CandidateToken.outer_observer_contract_sha256
        outer_observer_handshake_prefix_sha256 = $ObserverHandshakePrefixSha256
        expected_terminal_seal_relative_path = [string]$CandidateToken.expected_terminal_seal_relative_path
        terminal_seal_required_for_authoritative_disposition = $true
        terminal_seal_state = "pending_outer_observed_inner_exit"
        terminal_evidence_complete = $false
        authoritative_stage_pass = $false
        runner_sha256 = $RunnerSha256
        consumed_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }

    $journalPaths = Get-OuterEmergencyReplacementJournalPaths `
        ([string]$CandidateToken.review_token_id) $ObserverNonce
    foreach ($collisionPath in @($journalPaths.intent, $journalPaths.postvalidation, $journalPaths.recovery)) {
        if (Test-Path -LiteralPath $collisionPath) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery journal collision"
        }
    }
    $target = [IO.Path]::GetFullPath($reviewTokenPath)
    $directory = [IO.Path]::GetDirectoryName($target)
    $temporary = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + ".outer-emergency." + [guid]::NewGuid().ToString("N") + ".tmp")
    $backup = Join-Path $directory ("." + [IO.Path]::GetFileName($target) + ".replaced." + [guid]::NewGuid().ToString("N") + ".bak")
    $journalIdentity = [string]$CandidateToken.review_token_id + "-" + $ObserverNonce
    $intent = [ordered]@{
        schema = $emergencyReplacementIntentSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        authorized_stage = "primary-h4-p0r"
        journal_identity = $journalIdentity
        review_token_id = [string]$CandidateToken.review_token_id
        original_review_token_sha256 = $OriginalTokenSha256
        original_review_token_canonical_sha256 = [string]$ClaimCandidate.review_token_canonical_sha256
        claim_relative_path = $ClaimRelativePath
        claim_sha256 = $ClaimSha256
        guard_sha256 = $null
        attempt_status = "outer_observer_post_claim_failure"
        target_relative_path = Get-RepositoryRelativePath $target
        file_replace_backup_relative_path = Get-RepositoryRelativePath $backup
        expected_original_review_token_sha256 = $OriginalTokenSha256
        expected_original_review_token_canonical_sha256 = [string]$ClaimCandidate.review_token_canonical_sha256
        pre_replace_observed_sha256 = $currentTokenSha256
        pre_replace_observed_exact_retained_bytes_match = $true
        replacement_state = "pending_file_replace_postvalidation"
        file_replace_is_atomic_compare_and_swap = $false
        prior_bytes_verified_from_file_replace_backup = $false
        replacement_authority = "none_pending_postvalidation"
        authorization_blocker = $true
        terminal_evidence_complete = $false
        external_runner_or_machine_kill_terminal_state_guaranteed = $false
        created_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $intentReference = Write-OrReadExactJsonArtifact $journalPaths.intent $intent "created_utc"
    try {
        $raw = $emergency | ConvertTo-Json -Depth 16 -Compress
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($raw)
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        if (
            (Get-Sha256 $target) -ne $OriginalTokenSha256 -or
            -not (Test-ByteArrayEqual ([IO.File]::ReadAllBytes($target)) $OriginalTokenBytes)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency token raced before replacement"
        }
        [IO.File]::Replace($temporary, $target, $backup, $true)
        $temporary = $null
        if (
            (Get-Sha256 $backup) -ne $OriginalTokenSha256 -or
            -not (Test-ByteArrayEqual ([IO.File]::ReadAllBytes($backup)) $OriginalTokenBytes)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency backup differs from authorized token"
        }
        $readback = Read-BoundedJsonObject $target
        if (
            $null -eq $readback -or
            -not (Test-ExactJsonFieldSet $readback @($emergency.Keys)) -or
            -not (Test-StrictJsonValueEqual $readback $emergency)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency tombstone readback mismatch"
        }
        [IO.File]::Move($backup, $journalPaths.recovery)
        $backup = $null
        if (
            (Get-Sha256 $journalPaths.recovery) -ne $OriginalTokenSha256 -or
            -not (Test-ByteArrayEqual ([IO.File]::ReadAllBytes($journalPaths.recovery)) $OriginalTokenBytes)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency recovery bytes mismatch"
        }
        $emergencySha256 = Get-Sha256 $target
        $postvalidation = [ordered]@{
            schema = $emergencyReplacementPostvalidationSchema
            program = $program
            case_id = "AV-BS1-CIRCLE-PRIMARY"
            authorized_stage = "primary-h4-p0r"
            journal_identity = $journalIdentity
            review_token_id = [string]$CandidateToken.review_token_id
            original_review_token_sha256 = $OriginalTokenSha256
            original_review_token_canonical_sha256 = [string]$ClaimCandidate.review_token_canonical_sha256
            claim_relative_path = $ClaimRelativePath
            claim_sha256 = $ClaimSha256
            guard_sha256 = $null
            attempt_status = "outer_observer_post_claim_failure"
            intent_relative_path = Get-RepositoryRelativePath $journalPaths.intent
            intent_sha256 = $intentReference.sha256
            recovery_relative_path = Get-RepositoryRelativePath $journalPaths.recovery
            recovery_sha256 = $OriginalTokenSha256
            emergency_tombstone_relative_path = Get-RepositoryRelativePath $target
            emergency_tombstone_sha256 = $emergencySha256
            emergency_tombstone_schema = $emergencyTombstoneSchema
            file_replace_is_atomic_compare_and_swap = $false
            prior_bytes_verified_from_file_replace_backup = $true
            backup_exact_retained_original_bytes_match = $true
            replacement_state = "replacement_postvalidated_pending_external_observer"
            replacement_authority = "postvalidated_file_replacement_only_not_terminal_lifecycle_evidence"
            authorization_blocker = $true
            lifecycle_completion_claimed = $false
            terminal_evidence_complete = $false
            external_observer_required_for_terminal_exit = $true
            external_runner_or_machine_kill_terminal_state_guaranteed = $false
            recorded_utc = [DateTimeOffset]::UtcNow.ToString("o")
        }
        [void](Write-OrReadExactJsonArtifact $journalPaths.postvalidation $postvalidation "recorded_utc")
        $finalReadback = Read-BoundedJsonObject $target
        if (
            (Get-Sha256 $target) -ne $emergencySha256 -or
            $null -eq $finalReadback -or
            -not (Test-ExactJsonFieldSet $finalReadback @($emergency.Keys)) -or
            -not (Test-StrictJsonValueEqual $finalReadback $emergency)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer emergency tombstone changed after postvalidation"
        }
        return $finalReadback
    }
    finally {
        # Never restore authorization after a controlled post-claim replacement.
        # Any retained .bak plus the intent journal is recovery evidence only.
        if ($null -ne $temporary -and (Test-Path -LiteralPath $temporary -PathType Leaf)) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Write-PreExitControlPlaneEvidence(
    [object]$ValidatedTombstone,
    [bool]$ConsumerControlGatePassed,
    [object]$ConsumerReportReference,
    [System.Collections.IDictionary]$ArtifactHashes
) {
    if ($null -eq $ValidatedTombstone) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence requires validated tombstone readback"
    }
    $requiredArtifactHashKeys = @(
        "resource_report_sha256", "result_file_sha256",
        "child_stdout_sha256", "child_stderr_sha256",
        "monitor_ready_marker_sha256", "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
        "factor_prefix_one_sha256", "factor_prefix_two_sha256"
    )
    if (
        $null -eq $ArtifactHashes -or
        @($ArtifactHashes.Keys).Count -ne $requiredArtifactHashKeys.Count -or
        @(Compare-Object ($requiredArtifactHashKeys | Sort-Object) (@($ArtifactHashes.Keys) | Sort-Object)).Count -ne 0
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence artifact hash field set mismatch"
    }
    foreach ($artifactHashKey in $requiredArtifactHashKeys) {
        $artifactHashValue = $ArtifactHashes[$artifactHashKey]
        if ($null -ne $artifactHashValue -and [string]$artifactHashValue -notmatch '^[0-9a-f]{64}$') {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence artifact hash is invalid"
        }
    }
    $monitorReadyMarkerPresent = $null -ne $ArtifactHashes["monitor_ready_marker_sha256"]
    $factorCompleteMarkerPresent = $null -ne $ArtifactHashes["factor_complete_marker_sha256"]
    $monitorReleaseMarkerPresent = $null -ne $ArtifactHashes["monitor_release_marker_sha256"]
    if (
        ($factorCompleteMarkerPresent -and -not $monitorReadyMarkerPresent) -or
        ($monitorReleaseMarkerPresent -and -not $factorCompleteMarkerPresent)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit factor-monitor marker hash prefix is invalid"
    }
    if (
        $null -eq $script:controlPlaneLatestIndexPath -or
        $null -eq $script:controlPlaneLatestIndexSha256 -or
        -not (Test-Path -LiteralPath $script:controlPlaneLatestIndexPath -PathType Leaf) -or
        (Get-Sha256 $script:controlPlaneLatestIndexPath) -ne $script:controlPlaneLatestIndexSha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence lacks a valid final control-plane session index"
    }

    $tombstoneSha256BeforeReadback = Get-Sha256 $reviewTokenPath
    $normalReadback = Get-MatchingNormalConsumedTombstone $reviewTokenPath
    $emergencyReadback = Get-MatchingEmergencyConsumedTombstone $reviewTokenPath
    if ($null -eq $normalReadback -and $null -eq $emergencyReadback) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence tombstone readback no longer matches"
    }
    $currentTombstone = if ($null -ne $normalReadback) { $normalReadback } else { $emergencyReadback }
    $currentTombstoneSha256 = Get-Sha256 $reviewTokenPath
    if ($currentTombstoneSha256 -ne $tombstoneSha256BeforeReadback) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence detected raced tombstone bytes"
    }
    $tombstoneSchemaValue = [string]$currentTombstone.schema
    $validatedTombstoneJson = $ValidatedTombstone | ConvertTo-Json -Depth 16 -Compress
    $currentTombstoneJson = $currentTombstone | ConvertTo-Json -Depth 16 -Compress
    if ($validatedTombstoneJson -cne $currentTombstoneJson) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence validated tombstone differs from current readback"
    }
    $emergencyReplacementPostvalidated = $false
    $emergencyReplacementIntentRelativePath = $null
    $emergencyReplacementIntentHash = $null
    $emergencyReplacementPostvalidationRelativePath = $null
    $emergencyReplacementPostvalidationHash = $null
    $emergencyReplacementRecoveryRelativePath = $null
    $emergencyReplacementRecoveryHash = $null
    if ($tombstoneSchemaValue -eq $emergencyTombstoneSchema) {
        $replacementPostvalidation = Get-MatchingEmergencyReplacementPostvalidation $currentTombstone
        if (
            $null -eq $replacementPostvalidation -or
            $null -eq $script:emergencyReplacementIntentPath -or
            $null -eq $script:emergencyReplacementIntentSha256 -or
            $null -eq $script:emergencyReplacementPostvalidationPath -or
            $null -eq $script:emergencyReplacementPostvalidationSha256 -or
            $null -eq $script:emergencyReplacementRecoveryPath -or
            $null -eq $script:emergencyReplacementRecoverySha256
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: emergency tombstone lacks replacement postvalidation evidence"
        }
        $emergencyReplacementPostvalidated = $true
        $emergencyReplacementIntentRelativePath = Get-RepositoryRelativePath $script:emergencyReplacementIntentPath
        $emergencyReplacementIntentHash = $script:emergencyReplacementIntentSha256
        $emergencyReplacementPostvalidationRelativePath = Get-RepositoryRelativePath $script:emergencyReplacementPostvalidationPath
        $emergencyReplacementPostvalidationHash = $script:emergencyReplacementPostvalidationSha256
        $emergencyReplacementRecoveryRelativePath = Get-RepositoryRelativePath $script:emergencyReplacementRecoveryPath
        $emergencyReplacementRecoveryHash = $script:emergencyReplacementRecoverySha256
    }
    $normalPassAudit = if ($tombstoneSchemaValue -eq $tombstoneSchema) {
        Get-NormalPassEvidenceAudit $currentTombstone $ArtifactHashes
    }
    else {
        [pscustomobject]@{
            pass = $false
            errors = @("normal tombstone is required for provisional success intent")
        }
    }
    if ($runnerExitCode -eq 0 -and -not $normalPassAudit.pass) {
        $script:runnerExitCode = 2
        $script:preserveAttemptEvidence = $true
    }

    $consumerReportPath = $null
    $consumerReportSha256 = $null
    $consumerReportGatePass = $false
    $consumerEnvelopeClosePath = $null
    $consumerEnvelopeCloseSha256 = $null
    if ($null -ne $ConsumerReportReference) {
        $consumerReportPath = [string]$ConsumerReportReference.report_path
        $consumerReportSha256 = [string]$ConsumerReportReference.report_sha256
        $consumerEnvelopeClosePath = [string]$ConsumerReportReference.envelope_close_path
        $consumerEnvelopeCloseSha256 = [string]$ConsumerReportReference.envelope_close_sha256
        $consumerReportGatePass = [bool]$ConsumerReportReference.mandatory_control_plane_gate_pass
        if (
            $ConsumerReportReference.operation -ne "token_consumer" -or
            -not (Test-Path -LiteralPath $consumerReportPath -PathType Leaf) -or
            (Get-Sha256 $consumerReportPath) -ne $consumerReportSha256 -or
            -not (Test-Path -LiteralPath $consumerEnvelopeClosePath -PathType Leaf) -or
            (Get-Sha256 $consumerEnvelopeClosePath) -ne $consumerEnvelopeCloseSha256
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence consumer report binding mismatch"
        }
        $consumerEnvelopeClose = Read-BoundedJsonObject $consumerEnvelopeClosePath
        if (
            $null -eq $consumerEnvelopeClose -or
            $consumerEnvelopeClose.schema -ne $controlPlaneEnvelopeCloseSchema -or
            $consumerEnvelopeClose.operation -ne "token_consumer" -or
            $consumerEnvelopeClose.report_path -ne [IO.Path]::GetFullPath($consumerReportPath) -or
            $consumerEnvelopeClose.report_sha256 -ne $consumerReportSha256 -or
            $consumerEnvelopeClose.mandatory_control_plane_gate_pass -ne $consumerReportGatePass
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence consumer envelope-close binding mismatch"
        }
    }
    if ($ConsumerControlGatePassed -and (-not $consumerReportGatePass)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence control-gate disposition mismatch"
    }
    if ($null -ne $ConsumerReportReference) {
        $finalSessionIndex = Read-BoundedJsonObject $script:controlPlaneLatestIndexPath
        if (
            $null -eq $finalSessionIndex -or
            $finalSessionIndex.schema -ne "AV-BS1-h4-p0r-control-plane-session-index-v1" -or
            $finalSessionIndex.index_role -ne "final_binds_resource_envelope_close" -or
            $finalSessionIndex.envelope_close_path -ne $consumerEnvelopeClosePath -or
            $finalSessionIndex.envelope_close_sha256 -ne $consumerEnvelopeCloseSha256
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence final index does not bind consumer envelope close"
        }
    }

    $provisionalSuccessIntent = (
        $ConsumerControlGatePassed -and
        $consumerReportGatePass -and
        $runnerExitCode -eq 0 -and
        $tombstoneSchemaValue -eq $tombstoneSchema -and
        $normalPassAudit.pass -and
        $currentTombstone.consumption_validated_pass -eq $true -and
        $currentTombstone.mandatory_stage_pass -eq $true -and
        @($currentTombstone.failure_codes).Count -eq 0
    )
    if ($runnerExitCode -eq 0 -and -not $provisionalSuccessIntent) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: success intent cannot be recorded without passed consumer evidence"
    }

    $preExitEvidenceRoot = [IO.Path]::GetFullPath((Join-Path $validationRoot "control-plane-pre-exit-evidence"))
    [IO.Directory]::CreateDirectory($preExitEvidenceRoot) | Out-Null
    $preExitEvidenceLeaf = $token.review_token_id + ".json"
    $preExitEvidencePath = [IO.Path]::GetFullPath((Join-Path $preExitEvidenceRoot $preExitEvidenceLeaf))
    if (
        -not $preExitEvidencePath.StartsWith($preExitEvidenceRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetDirectoryName($preExitEvidencePath).TrimEnd('\') -ne $preExitEvidenceRoot.TrimEnd('\') -or
        [IO.Path]::GetFileName($preExitEvidencePath) -ne $preExitEvidenceLeaf
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence path validation failed"
    }

    $evidence = [ordered]@{
        schema = $preExitControlPlaneEvidenceSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        authorized_stage = "primary-h4-p0r"
        evidence_kind = "pre_exit_intent_only"
        review_token_id = $token.review_token_id
        original_review_token_sha256 = $reviewTokenHash
        original_review_token_canonical_sha256 = $reviewTokenCanonicalHash
        claim_relative_path = $claimRelativePath
        claim_sha256 = $claimHash
        claim_canonical_sha256 = $claimCanonicalHash
        guard_sha256 = $guardHash
        guard_canonical_sha256 = $guardCanonicalHash
        resource_report_sha256 = $ArtifactHashes["resource_report_sha256"]
        result_file_sha256 = $ArtifactHashes["result_file_sha256"]
        child_stdout_sha256 = $ArtifactHashes["child_stdout_sha256"]
        child_stderr_sha256 = $ArtifactHashes["child_stderr_sha256"]
        monitor_ready_marker_sha256 = $ArtifactHashes["monitor_ready_marker_sha256"]
        factor_complete_marker_sha256 = $ArtifactHashes["factor_complete_marker_sha256"]
        monitor_release_marker_sha256 = $ArtifactHashes["monitor_release_marker_sha256"]
        factor_prefix_one_sha256 = $ArtifactHashes["factor_prefix_one_sha256"]
        factor_prefix_two_sha256 = $ArtifactHashes["factor_prefix_two_sha256"]
        tombstone_schema = $tombstoneSchemaValue
        tombstone_relative_path = Get-RepositoryRelativePath $reviewTokenPath
        tombstone_sha256 = $currentTombstoneSha256
        emergency_replacement_postvalidated = $emergencyReplacementPostvalidated
        emergency_replacement_intent_relative_path = $emergencyReplacementIntentRelativePath
        emergency_replacement_intent_sha256 = $emergencyReplacementIntentHash
        emergency_replacement_postvalidation_relative_path = $emergencyReplacementPostvalidationRelativePath
        emergency_replacement_postvalidation_sha256 = $emergencyReplacementPostvalidationHash
        emergency_replacement_recovery_relative_path = $emergencyReplacementRecoveryRelativePath
        emergency_replacement_recovery_sha256 = $emergencyReplacementRecoveryHash
        consumer_control_gate_pass = $ConsumerControlGatePassed
        consumer_report_relative_path = if ($consumerReportPath) { Get-RepositoryRelativePath $consumerReportPath } else { $null }
        consumer_report_sha256 = $consumerReportSha256
        consumer_envelope_close_relative_path = if ($consumerEnvelopeClosePath) { Get-RepositoryRelativePath $consumerEnvelopeClosePath } else { $null }
        consumer_envelope_close_sha256 = $consumerEnvelopeCloseSha256
        final_session_index_relative_path = Get-RepositoryRelativePath $script:controlPlaneLatestIndexPath
        final_session_index_sha256 = $script:controlPlaneLatestIndexSha256
        outer_observer_contract_sha256 = $script:outerObserverContractSha256
        expected_terminal_seal_relative_path = $script:outerObserverExpectedTerminalSealRelativePath
        terminal_seal_required_for_authoritative_disposition = $true
        outer_observer_handshake_prefix = $script:outerObserverHandshakePrefix
        outer_observer_handshake_prefix_sha256 = $script:outerObserverHandshakePrefixSha256
        attempt_status = $attemptStatus
        runner_exit_observed = $false
        intended_runner_exit_code = [int]$runnerExitCode
        intended_runner_disposition = if ($provisionalSuccessIntent) { "success_intent_pending_external_exit_observation" } else { "failure_intent_pending_external_exit_observation" }
        mandatory_stage_pass = $false
        authorization_blocker = $true
        authorization_effect = "none_authorization_remains_blocked"
        normal_tombstone_authority = if ($tombstoneSchemaValue -eq $tombstoneSchema) { "provisional_candidate_only_pending_external_observer" } else { "not_applicable_emergency_failure_tombstone" }
        normal_pass_outer_evidence_reconciliation_pass = [bool]$normalPassAudit.pass
        normal_pass_outer_evidence_reconciliation_errors = @($normalPassAudit.errors)
        tombstone_success_remains_provisional = $true
        pre_exit_evidence_complete = $true
        terminal_evidence_complete = $false
        external_observer_required_for_terminal_exit = $true
        written_before_runner_exit = $true
        temporary_attempt_evidence_cleanup_observed = $false
        temporary_attempt_evidence_cleanup_occurs_after_this_record = $true
        consume_after_every_owned_claim_outcome = $false
        external_runner_or_machine_kill_terminal_state_guaranteed = $false
        execution_resource_scope_sha256 = $executionResourceScopeSha256
        recorded_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }

    if (Test-Path -LiteralPath $preExitEvidencePath -PathType Leaf) {
        $existing = Read-BoundedJsonObject $preExitEvidencePath
        $requestedRecordedUtc = $evidence.recorded_utc
        if ($null -ne $existing) { $evidence.recorded_utc = [string]$existing.recorded_utc }
        $expectedExistingJson = $evidence | ConvertTo-Json -Depth 12 -Compress
        $evidence.recorded_utc = $requestedRecordedUtc
        $actualExistingJson = if ($null -ne $existing) {
            $existing | ConvertTo-Json -Depth 12 -Compress
        } else { $null }
        if (
            $null -eq $existing -or
            -not (Test-ExactJsonFieldSet $existing @($evidence.Keys)) -or
            $actualExistingJson -cne $expectedExistingJson -or
            $existing.schema -ne $preExitControlPlaneEvidenceSchema -or
            $existing.review_token_id -ne $token.review_token_id -or
            $existing.original_review_token_sha256 -ne $reviewTokenHash -or
            $existing.claim_sha256 -ne $claimHash -or
            $existing.guard_sha256 -ne $guardHash -or
            $existing.monitor_ready_marker_sha256 -ne $ArtifactHashes["monitor_ready_marker_sha256"] -or
            $existing.factor_complete_marker_sha256 -ne $ArtifactHashes["factor_complete_marker_sha256"] -or
            $existing.monitor_release_marker_sha256 -ne $ArtifactHashes["monitor_release_marker_sha256"] -or
            $existing.tombstone_sha256 -ne $currentTombstoneSha256 -or
            $existing.final_session_index_sha256 -ne $script:controlPlaneLatestIndexSha256 -or
            $existing.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
            $existing.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
            $existing.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
            $existing.terminal_seal_required_for_authoritative_disposition -ne $true -or
            -not (Test-StrictJsonValueEqual $existing.outer_observer_handshake_prefix $script:outerObserverHandshakePrefix) -or
            $existing.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
            $existing.runner_exit_observed -isnot [bool] -or
            $existing.runner_exit_observed -ne $false -or
            $existing.intended_runner_exit_code -ne [int]$runnerExitCode -or
            $existing.intended_runner_disposition -ne $evidence.intended_runner_disposition -or
            $existing.tombstone_success_remains_provisional -isnot [bool] -or
            $existing.tombstone_success_remains_provisional -ne $true -or
            $existing.terminal_evidence_complete -isnot [bool] -or
            $existing.terminal_evidence_complete -ne $false
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence collision or mismatch"
        }
        return [pscustomobject]@{
            path = $preExitEvidencePath
            sha256 = Get-Sha256 $preExitEvidencePath
            value = $existing
        }
    }

    $evidenceJson = $evidence | ConvertTo-Json -Depth 12 -Compress
    Write-AtomicUtf8NoBom $preExitEvidencePath $evidenceJson
    $readback = Read-BoundedJsonObject $preExitEvidencePath
    $readbackJson = if ($null -ne $readback) {
        $readback | ConvertTo-Json -Depth 12 -Compress
    } else { $null }
    if (
        $null -eq $readback -or
        -not (Test-ExactJsonFieldSet $readback @($evidence.Keys)) -or
        $readbackJson -cne $evidenceJson -or
        $readback.schema -ne $preExitControlPlaneEvidenceSchema -or
        $readback.monitor_ready_marker_sha256 -ne $ArtifactHashes["monitor_ready_marker_sha256"] -or
        $readback.factor_complete_marker_sha256 -ne $ArtifactHashes["factor_complete_marker_sha256"] -or
        $readback.monitor_release_marker_sha256 -ne $ArtifactHashes["monitor_release_marker_sha256"] -or
        $readback.tombstone_sha256 -ne $currentTombstoneSha256 -or
        $readback.final_session_index_sha256 -ne $script:controlPlaneLatestIndexSha256 -or
        $readback.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
        $readback.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
        $readback.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $readback.terminal_seal_required_for_authoritative_disposition -ne $true -or
        -not (Test-StrictJsonValueEqual $readback.outer_observer_handshake_prefix $script:outerObserverHandshakePrefix) -or
        $readback.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
        $readback.runner_exit_observed -isnot [bool] -or
        $readback.runner_exit_observed -ne $false -or
        $readback.intended_runner_disposition -ne $evidence.intended_runner_disposition -or
        $readback.tombstone_success_remains_provisional -isnot [bool] -or
        $readback.tombstone_success_remains_provisional -ne $true -or
        $readback.terminal_evidence_complete -isnot [bool] -or
        $readback.terminal_evidence_complete -ne $false
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence readback mismatch"
    }
    return [pscustomobject]@{
        path = $preExitEvidencePath
        sha256 = Get-Sha256 $preExitEvidencePath
        value = $readback
    }
}

function Get-CurrentTerminalArtifactHashes {
    $currentHashes = [ordered]@{
        resource_report_sha256 = if (Test-Path -LiteralPath $resourcePath -PathType Leaf) { Get-Sha256 $resourcePath } else { $null }
        result_file_sha256 = if (Test-Path -LiteralPath $finalPath -PathType Leaf) { Get-Sha256 $finalPath } else { $null }
        child_stdout_sha256 = if (Test-Path -LiteralPath $stdoutPath -PathType Leaf) { Get-Sha256 $stdoutPath } else { $null }
        child_stderr_sha256 = if (Test-Path -LiteralPath $stderrPath -PathType Leaf) { Get-Sha256 $stderrPath } else { $null }
        monitor_ready_marker_sha256 = if (Test-Path -LiteralPath $monitorReadyPath -PathType Leaf) { Get-Sha256 $monitorReadyPath } else { $null }
        factor_complete_marker_sha256 = if (Test-Path -LiteralPath $factorCompletePath -PathType Leaf) { Get-Sha256 $factorCompletePath } else { $null }
        monitor_release_marker_sha256 = if (Test-Path -LiteralPath $monitorReleasePath -PathType Leaf) { Get-Sha256 $monitorReleasePath } else { $null }
        factor_prefix_one_sha256 = if (Test-Path -LiteralPath $factorPrefixOnePath -PathType Leaf) { Get-Sha256 $factorPrefixOnePath } else { $null }
        factor_prefix_two_sha256 = if (Test-Path -LiteralPath $factorPrefixTwoPath -PathType Leaf) { Get-Sha256 $factorPrefixTwoPath } else { $null }
    }
    if (
        ($null -ne $currentHashes.factor_complete_marker_sha256 -and $null -eq $currentHashes.monitor_ready_marker_sha256) -or
        ($null -ne $currentHashes.monitor_release_marker_sha256 -and $null -eq $currentHashes.factor_complete_marker_sha256)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: current factor-monitor marker files are not a valid prefix"
    }
    return $currentHashes
}

function Test-StrictJsonValueEqual([object]$Left, [object]$Right) {
    try {
        $leftJson = $Left | ConvertTo-Json -Depth 32 -Compress
        $rightJson = $Right | ConvertTo-Json -Depth 32 -Compress
        return ($leftJson -ceq $rightJson)
    }
    catch { return $false }
}

function Get-NormalPassEvidenceAudit(
    [object]$Tombstone,
    [System.Collections.IDictionary]$ExpectedArtifactHashes
) {
    $errors = New-Object System.Collections.ArrayList
    $addError = { param([string]$Message) [void]$errors.Add($Message) }
    $requiredArtifactKeys = @(
        "resource_report_sha256", "result_file_sha256",
        "child_stdout_sha256", "child_stderr_sha256",
        "monitor_ready_marker_sha256", "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
        "factor_prefix_one_sha256", "factor_prefix_two_sha256"
    )
    if (
        $null -eq $Tombstone -or
        $Tombstone.schema -ne $tombstoneSchema
    ) {
        & $addError "normal tombstone schema is required"
    }
    if (
        $null -eq $ExpectedArtifactHashes -or
        @($ExpectedArtifactHashes.Keys).Count -ne $requiredArtifactKeys.Count -or
        @(Compare-Object ($requiredArtifactKeys | Sort-Object) (@($ExpectedArtifactHashes.Keys) | Sort-Object)).Count -ne 0
    ) {
        & $addError "outer artifact hash field set mismatch"
    }

    try {
    if ($errors.Count -eq 0) {
        if (
            $Tombstone.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
            $Tombstone.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
            $Tombstone.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
            $Tombstone.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
            $Tombstone.terminal_seal_required_for_authoritative_disposition -ne $true -or
            $Tombstone.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
            $Tombstone.terminal_evidence_complete -isnot [bool] -or
            $Tombstone.terminal_evidence_complete -ne $false -or
            $Tombstone.authoritative_stage_pass -isnot [bool] -or
            $Tombstone.authoritative_stage_pass -ne $false
        ) {
            & $addError "normal tombstone pending-terminal bindings mismatch"
        }
        foreach ($field in @(
            "claim_evidence_valid", "guard_evidence_valid",
            "result_evidence_valid", "resource_evidence_valid",
            "child_stdout_evidence_valid", "factor_prefix_evidence_valid",
            "resource_gate_pass", "consumption_validated_pass",
            "mandatory_stage_pass", "factorization_attempted",
            "factorization_performed"
        )) {
            if ($Tombstone.$field -isnot [bool] -or $Tombstone.$field -ne $true) {
                & $addError ("normal pass requires true " + $field)
            }
        }
        if ($Tombstone.attempt_status -ne "completed_pass" -or $Tombstone.effective_attempt_status -ne "completed_pass") {
            & $addError "normal pass attempt status mismatch"
        }
        if ($Tombstone.child_launched -isnot [bool] -or $Tombstone.child_launched -ne $true) {
            & $addError "normal pass child launch evidence mismatch"
        }
        if ($Tombstone.child_exit_code -isnot [int] -or $Tombstone.child_exit_code -ne 0) {
            & $addError "normal pass child exit evidence mismatch"
        }
        if ($null -ne $Tombstone.active_factor) { & $addError "normal pass active factor is not null" }
        if (@($Tombstone.failure_codes).Count -ne 0) { & $addError "normal pass failure codes are not empty" }
        if (@($Tombstone.resource_gate_recheck_failures).Count -ne 0) { & $addError "normal pass resource recheck failures are not empty" }
        if (@($Tombstone.evidence_validation_errors).Count -ne 0) { & $addError "normal pass evidence validation errors are not empty" }
        $expectedFactorOrder = @("A_background_II", "A_conductor_II")
        if (-not (Test-StrictJsonValueEqual @($Tombstone.completed_factors) $expectedFactorOrder)) {
            & $addError "normal pass completed factor order mismatch"
        }
        if (-not (Test-StrictJsonValueEqual @($Tombstone.factor_order) $expectedFactorOrder)) {
            & $addError "normal pass factor order mismatch"
        }
        if (@($Tombstone.factor_certificates).Count -ne 2) {
            & $addError "normal pass requires two factor certificates"
        }

        $currentClaim = Read-BoundedJsonObject $claimPath
        $currentGuard = Read-BoundedJsonObject $guardPath
        if (
            $null -eq $currentClaim -or
            (Get-Sha256 $claimPath) -ne $claimHash -or
            $Tombstone.claim_sha256 -ne $claimHash -or
            $Tombstone.claim_canonical_sha256 -ne $claimCanonicalHash -or
            $Tombstone.preflight_payload_sha256 -ne $preflightWrapper.payload_sha256 -or
            $null -eq $Tombstone.claim_evidence -or
            -not (Test-StrictJsonValueEqual $currentClaim $Tombstone.claim_evidence)
        ) {
            & $addError "current claim and tombstone claim evidence are inconsistent"
        }
        if (
            $null -eq $currentGuard -or
            (Get-Sha256 $guardPath) -ne $guardHash -or
            $Tombstone.guard_contract_sha256 -ne $guardHash -or
            $Tombstone.guard_canonical_sha256 -ne $guardCanonicalHash -or
            $null -eq $Tombstone.guard_evidence -or
            -not (Test-StrictJsonValueEqual $currentGuard $Tombstone.guard_evidence)
        ) {
            & $addError "current guard and tombstone guard evidence are inconsistent"
        }

        $currentBefore = Get-CurrentTerminalArtifactHashes
        foreach ($key in $requiredArtifactKeys) {
            $expectedHash = $ExpectedArtifactHashes[$key]
            $currentHash = $currentBefore[$key]
            if (
                $null -eq $expectedHash -or [string]$expectedHash -notmatch '^[0-9a-f]{64}$' -or
                $null -eq $currentHash -or [string]$currentHash -notmatch '^[0-9a-f]{64}$' -or
                $expectedHash -ne $currentHash
            ) {
                & $addError ("outer artifact changed or is missing: " + $key)
            }
        }
        if ($Tombstone.consumed_resource_report_sha256 -ne $currentBefore.resource_report_sha256) {
            & $addError "resource file hash differs from tombstone binding"
        }
        if ($Tombstone.consumed_result_file_sha256 -ne $currentBefore.result_file_sha256) {
            & $addError "result file hash differs from tombstone binding"
        }
        if ($Tombstone.consumed_child_stdout_file_sha256 -ne $currentBefore.child_stdout_sha256) {
            & $addError "child stdout hash differs from tombstone binding"
        }
        if (
            $null -eq $Tombstone.resource_evidence -or
            $Tombstone.resource_evidence.child_stdout_sha256 -ne $currentBefore.child_stdout_sha256 -or
            $Tombstone.resource_evidence.child_stderr_sha256 -ne $currentBefore.child_stderr_sha256 -or
            $Tombstone.resource_evidence.monitor_ready_marker_sha256 -ne $currentBefore.monitor_ready_marker_sha256 -or
            $Tombstone.resource_evidence.factor_complete_marker_sha256 -ne $currentBefore.factor_complete_marker_sha256 -or
            $Tombstone.resource_evidence.monitor_release_marker_sha256 -ne $currentBefore.monitor_release_marker_sha256 -or
            $Tombstone.resource_evidence.factor_monitor_handshake_complete -isnot [bool] -or
            $Tombstone.resource_evidence.factor_monitor_handshake_complete -ne $true -or
            $Tombstone.resource_evidence.child_process_id -ne $Tombstone.child_process_id -or
            $Tombstone.resource_evidence.child_exit_code -ne $Tombstone.child_exit_code -or
            $Tombstone.resource_evidence.mandatory_resource_gate_pass -ne $true
        ) {
            & $addError "resource evidence does not bind current child streams and passed gate"
        }

        $currentResource = Read-BoundedJsonObject $resourcePath
        $currentResultWrapper = Read-BoundedJsonObject $finalPath
        $currentChildWrapper = Read-BoundedJsonObject $stdoutPath
        if ($null -eq $currentResource -or -not (Test-StrictJsonValueEqual $currentResource $Tombstone.resource_evidence)) {
            & $addError "current resource JSON differs from tombstone evidence"
        }
        if (
            $null -eq $currentResultWrapper -or
            $currentResultWrapper.payload_sha256 -ne $Tombstone.consumed_result_payload_sha256 -or
            -not (Test-StrictJsonValueEqual $currentResultWrapper.payload $Tombstone.result_evidence)
        ) {
            & $addError "current result wrapper differs from tombstone evidence"
        }
        if (
            $null -eq $currentChildWrapper -or
            $currentChildWrapper.payload_sha256 -ne $Tombstone.consumed_child_payload_sha256 -or
            -not (Test-StrictJsonValueEqual $currentChildWrapper.payload $Tombstone.child_stdout_evidence)
        ) {
            & $addError "current child wrapper differs from tombstone evidence"
        }
        if (
            $null -eq $Tombstone.result_evidence -or
            $Tombstone.result_evidence.schema -ne "AV-BS1-h4-p0r-result-v2" -or
            $Tombstone.result_evidence.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
            $Tombstone.result_evidence.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
            $Tombstone.result_evidence.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
            $Tombstone.result_evidence.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
            $Tombstone.result_evidence.terminal_seal_required_for_authoritative_disposition -ne $true -or
            $Tombstone.result_evidence.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
            $Tombstone.result_evidence.terminal_evidence_complete -isnot [bool] -or
            $Tombstone.result_evidence.terminal_evidence_complete -ne $false -or
            $Tombstone.result_evidence.authoritative_stage_pass -isnot [bool] -or
            $Tombstone.result_evidence.authoritative_stage_pass -ne $false -or
            $Tombstone.result_evidence.mandatory_stage_pass -ne $true -or
            $Tombstone.result_evidence.factorization_attempted -ne $true -or
            $Tombstone.result_evidence.factorization_performed -ne $true -or
            $Tombstone.result_evidence.resource_report_sha256 -ne $currentBefore.resource_report_sha256 -or
            $Tombstone.result_evidence.child_stdout_sha256 -ne $currentBefore.child_stdout_sha256 -or
            -not (Test-StrictJsonValueEqual $Tombstone.result_evidence.resource $Tombstone.resource_evidence) -or
            -not (Test-StrictJsonValueEqual @($Tombstone.result_evidence.factor_prefix_evidence) @($Tombstone.factor_prefix_evidence)) -or
            -not (Test-StrictJsonValueEqual @($Tombstone.result_evidence.factor_order) $expectedFactorOrder) -or
            -not (Test-StrictJsonValueEqual @($Tombstone.result_evidence.factor_certificates) @($Tombstone.factor_certificates)) -or
            $null -eq $Tombstone.child_stdout_evidence -or
            $Tombstone.child_stdout_evidence.factorization_attempted -ne $true -or
            $Tombstone.child_stdout_evidence.factorization_performed -ne $true -or
            -not (Test-StrictJsonValueEqual $Tombstone.result_evidence.numerical $Tombstone.child_stdout_evidence) -or
            -not (Test-StrictJsonValueEqual @($Tombstone.child_stdout_evidence.factor_order) $expectedFactorOrder) -or
            -not (Test-StrictJsonValueEqual @($Tombstone.child_stdout_evidence.factor_certificates) @($Tombstone.factor_certificates))
        ) {
            & $addError "result, child, and factor certificate evidence are inconsistent"
        }

        $prefixEvidence = @($Tombstone.factor_prefix_evidence)
        if ($prefixEvidence.Count -ne 2) {
            & $addError "normal pass requires two bound prefix checkpoints"
        }
        else {
            $prefixPaths = @($factorPrefixOnePath, $factorPrefixTwoPath)
            $prefixHashes = @($currentBefore.factor_prefix_one_sha256, $currentBefore.factor_prefix_two_sha256)
            for ($prefixIndex = 0; $prefixIndex -lt 2; $prefixIndex += 1) {
                $prefixItem = $prefixEvidence[$prefixIndex]
                $prefixWrapper = Read-BoundedJsonObject $prefixPaths[$prefixIndex]
                if (
                    $prefixItem.count -isnot [int] -or $prefixItem.count -ne ($prefixIndex + 1) -or
                    $prefixItem.file_sha256 -ne $prefixHashes[$prefixIndex] -or
                    $null -eq $prefixWrapper -or
                    $prefixWrapper.payload_sha256 -ne $prefixItem.payload_sha256 -or
                    -not (Test-StrictJsonValueEqual $prefixWrapper.payload $prefixItem.payload)
                ) {
                    & $addError ("factor prefix checkpoint mismatch at count " + ($prefixIndex + 1))
                }
            }
            if (
                -not (Test-StrictJsonValueEqual @($prefixEvidence[1].payload.factor_order) $expectedFactorOrder) -or
                -not (Test-StrictJsonValueEqual @($prefixEvidence[1].payload.factor_certificates) @($Tombstone.factor_certificates)) -or
                -not (Test-StrictJsonValueEqual @($prefixEvidence[0].payload.factor_order) @($expectedFactorOrder[0])) -or
                @($prefixEvidence[0].payload.factor_certificates).Count -ne 1 -or
                @($prefixEvidence[1].payload.factor_certificates).Count -ne 2 -or
                -not (Test-StrictJsonValueEqual @($prefixEvidence[0].payload.factor_certificates) @($prefixEvidence[1].payload.factor_certificates)[0..0])
            ) {
                & $addError "factor prefix chain differs from final factor evidence"
            }
        }

        $currentAfter = Get-CurrentTerminalArtifactHashes
        if (-not (Test-StrictJsonValueEqual $currentBefore $currentAfter)) {
            & $addError "outer artifacts changed during pre-exit reconciliation"
        }
    }
    }
    catch {
        & $addError ("normal pass evidence is malformed or unreadable: " + $_.Exception.Message)
    }
    return [pscustomobject]@{
        pass = ($errors.Count -eq 0)
        errors = @($errors)
    }
}

function Quote-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Get-MonotonicNanoseconds {
    $ticks = [Diagnostics.Stopwatch]::GetTimestamp()
    return [int64][Math]::Floor(
        [double]$ticks * 1000000000.0 / [double][Diagnostics.Stopwatch]::Frequency
    )
}

$observerInputsPresent = (
    -not [string]::IsNullOrEmpty($OuterObserverSessionPath) -or
    -not [string]::IsNullOrEmpty($OuterObserverNonce) -or
    $OuterObserverParentProcessId -ne 0 -or
    $OuterObserverParentBirthUtcTicks -ne 0 -or
    -not [string]::IsNullOrEmpty($OuterObserverRunnerSha256) -or
    -not [string]::IsNullOrEmpty($OuterObserverReviewTokenSha256)
)
if (
    -not [string]::IsNullOrEmpty($InternalMode) -and
    $InternalMode -ne $outerInternalModeName
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden runner mode is invalid"
}
if ($Stage -eq "manifest" -and (-not [string]::IsNullOrEmpty($InternalMode) -or $observerInputsPresent)) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: manifest does not accept hidden observer inputs"
}
if ([string]::IsNullOrEmpty($InternalMode) -and $observerInputsPresent) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: public primary does not accept caller-supplied observer inputs"
}
if ($InternalMode -eq $outerInternalModeName -and $Stage -ne "primary-h4-p0r") {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner mode requires primary-h4-p0r stage"
}

if ($Stage -eq "manifest") {
    & $pythonPath $fixturePath --stage manifest
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $reviewTokenPath -PathType Leaf)) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: reviewed primary-h4-p0r token is missing"
}

if ("AvBsH4P0RNativeV1" -as [type]) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: H4-P0R native monitor type unexpectedly preloaded"
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class AvBsH4P0RNativeV1 {
    const uint TH32CS_SNAPPROCESS = 0x00000002;
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    const uint PROCESS_VM_READ = 0x0010;
    const int ERROR_NO_MORE_FILES = 18;
    const int ERROR_INVALID_PARAMETER = 87;
    static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct PROCESSENTRY32 {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=260)]
        public string szExeFile;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PROCESS_MEMORY_COUNTERS_EX2 {
        public uint cb;
        public uint PageFaultCount;
        public UIntPtr PeakWorkingSetSize;
        public UIntPtr WorkingSetSize;
        public UIntPtr QuotaPeakPagedPoolUsage;
        public UIntPtr QuotaPagedPoolUsage;
        public UIntPtr QuotaPeakNonPagedPoolUsage;
        public UIntPtr QuotaNonPagedPoolUsage;
        public UIntPtr PagefileUsage;
        public UIntPtr PeakPagefileUsage;
        public UIntPtr PrivateUsage;
        public UIntPtr PrivateWorkingSetSize;
        public UIntPtr SharedCommitUsage;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PERFORMANCE_INFORMATION {
        public uint cb;
        public UIntPtr CommitTotal;
        public UIntPtr CommitLimit;
        public UIntPtr CommitPeak;
        public UIntPtr PhysicalTotal;
        public UIntPtr PhysicalAvailable;
        public UIntPtr SystemCache;
        public UIntPtr KernelTotal;
        public UIntPtr KernelPaged;
        public UIntPtr KernelNonpaged;
        public UIntPtr PageSize;
        public uint HandleCount;
        public uint ProcessCount;
        public uint ThreadCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct FILETIME {
        public uint dwLowDateTime;
        public uint dwHighDateTime;
    }

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool Process32FirstW(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool Process32NextW(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint processId);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetProcessTimes(IntPtr process, out FILETIME creation, out FILETIME exit, out FILETIME kernel, out FILETIME user);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    [DllImport("psapi.dll", SetLastError=true)]
    static extern bool GetProcessMemoryInfo(IntPtr process, ref PROCESS_MEMORY_COUNTERS_EX2 counters, uint size);
    [DllImport("psapi.dll", SetLastError=true)]
    static extern bool GetPerformanceInfo(ref PERFORMANCE_INFORMATION information, uint size);

    static long U(UIntPtr value) { return unchecked((long)value.ToUInt64()); }
    static long DateTimeTicks(FILETIME value) {
        long fileTime = unchecked(((long)value.dwHighDateTime << 32) | value.dwLowDateTime);
        return checked(fileTime + 504911232000000000L);
    }
    static bool IsZero(FILETIME value) {
        return value.dwLowDateTime == 0 && value.dwHighDateTime == 0;
    }

    public static Dictionary<int,int> ProcessParents() {
        var result = new Dictionary<int,int>();
        IntPtr snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snapshot == INVALID_HANDLE_VALUE) throw new InvalidOperationException("CreateToolhelp32Snapshot failed");
        try {
            var entry = new PROCESSENTRY32();
            entry.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            if (!Process32FirstW(snapshot, ref entry)) throw new InvalidOperationException("Process32First failed");
            while (true) {
                result[(int)entry.th32ProcessID] = (int)entry.th32ParentProcessID;
                entry.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
                if (Process32NextW(snapshot, ref entry)) continue;
                int error = Marshal.GetLastWin32Error();
                if (error != ERROR_NO_MORE_FILES) {
                    throw new InvalidOperationException("Process32Next failed with Win32 error " + error);
                }
                break;
            }
        } finally { CloseHandle(snapshot); }
        return result;
    }

    // Status: 1=complete sample, 2=identity-bound exited process,
    // 0=OpenProcess reported ERROR_INVALID_PARAMETER, -1=other native failure.
    // Operation: 1=OpenProcess, 2=GetProcessTimes, 3=GetProcessMemoryInfo.
    public static long[] ProcessMetricProbe(int processId) {
        IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, false, (uint)processId);
        if (process == IntPtr.Zero) {
            int error = Marshal.GetLastWin32Error();
            return new long[] { error == ERROR_INVALID_PARAMETER ? 0 : -1, 1, error, 0, 0 };
        }
        try {
            FILETIME creation, exit, kernel, user;
            if (!GetProcessTimes(process, out creation, out exit, out kernel, out user)) {
                return new long[] { -1, 2, Marshal.GetLastWin32Error(), 0, 0 };
            }
            long birth = DateTimeTicks(creation);
            if (!IsZero(exit)) return new long[] { 2, 2, 0, birth, 1 };
            var counters = new PROCESS_MEMORY_COUNTERS_EX2();
            counters.cb = (uint)Marshal.SizeOf(typeof(PROCESS_MEMORY_COUNTERS_EX2));
            if (!GetProcessMemoryInfo(process, ref counters, counters.cb)) {
                int memoryError = Marshal.GetLastWin32Error();
                FILETIME creationAfter, exitAfter, kernelAfter, userAfter;
                if (
                    GetProcessTimes(
                        process, out creationAfter, out exitAfter,
                        out kernelAfter, out userAfter
                    ) &&
                    !IsZero(exitAfter)
                ) {
                    return new long[] { 2, 3, memoryError, birth, 1 };
                }
                return new long[] { -1, 3, memoryError, birth, 0 };
            }
            return new long[] {
                1, 3, 0, birth, 0,
                U(counters.WorkingSetSize), U(counters.PeakWorkingSetSize),
                U(counters.PagefileUsage), U(counters.PeakPagefileUsage),
                U(counters.PrivateUsage), U(counters.PrivateWorkingSetSize),
                U(counters.SharedCommitUsage), counters.PageFaultCount
            };
        } finally { CloseHandle(process); }
    }

    // Status: 1=live, 2=exited but still identity-queryable,
    // 0=OpenProcess reported ERROR_INVALID_PARAMETER, -1=other native failure.
    public static long[] ProcessIdentityProbe(int processId) {
        IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, (uint)processId);
        if (process == IntPtr.Zero) {
            int error = Marshal.GetLastWin32Error();
            return new long[] { error == ERROR_INVALID_PARAMETER ? 0 : -1, 1, error, 0 };
        }
        try {
            FILETIME creation, exit, kernel, user;
            if (!GetProcessTimes(process, out creation, out exit, out kernel, out user)) {
                return new long[] { -1, 2, Marshal.GetLastWin32Error(), 0 };
            }
            return new long[] { IsZero(exit) ? 1 : 2, 2, 0, DateTimeTicks(creation) };
        } finally { CloseHandle(process); }
    }

    public static long[] SystemMetrics() {
        var information = new PERFORMANCE_INFORMATION();
        information.cb = (uint)Marshal.SizeOf(typeof(PERFORMANCE_INFORMATION));
        if (!GetPerformanceInfo(ref information, information.cb)) return new long[0];
        long page = U(information.PageSize);
        return new long[] {
            U(information.CommitTotal) * page,
            U(information.CommitLimit) * page,
            U(information.PhysicalAvailable) * page,
            page
        };
    }
}
'@

function Get-NativeProcessParents {
    return ,([AvBsH4P0RNativeV1]::ProcessParents())
}

function Get-ProcessIdentityState([int]$ProcessId) {
    $raw = @([AvBsH4P0RNativeV1]::ProcessIdentityProbe($ProcessId))
    if ($raw.Count -ne 4) {
        return [ordered]@{
            status = "malformed"
            operation = "process_identity_probe"
            win32_error_code = $null
            birth_utc_ticks = $null
        }
    }
    $operation = switch ([int]$raw[1]) {
        1 { "open_process" }
        2 { "get_process_times" }
        default { "process_identity_probe" }
    }
    $status = switch ([int]$raw[0]) {
        0 { "not_found" }
        1 { "live" }
        2 { "exited" }
        default { "query_failed" }
    }
    return [ordered]@{
        status = $status
        operation = $operation
        win32_error_code = if ([int]$raw[2] -gt 0) { [int]$raw[2] } else { $null }
        birth_utc_ticks = if ([int64]$raw[3] -gt 0) { [int64]$raw[3] } else { $null }
    }
}

function Get-ProcessMetricState([int]$ProcessId) {
    $raw = @([AvBsH4P0RNativeV1]::ProcessMetricProbe($ProcessId))
    if ($raw.Count -lt 5) {
        return [ordered]@{
            status = "malformed"
            operation = "process_metric_probe"
            win32_error_code = $null
            birth_utc_ticks = $null
            values = $null
        }
    }
    $operation = switch ([int]$raw[1]) {
        1 { "open_process" }
        2 { "get_process_times" }
        3 { "get_process_memory_info" }
        default { "process_metric_probe" }
    }
    $status = switch ([int]$raw[0]) {
        0 { "not_found" }
        1 { if ($raw.Count -eq 13) { "ok" } else { "malformed" } }
        2 { "exited" }
        default { "query_failed" }
    }
    return [ordered]@{
        status = $status
        operation = $operation
        win32_error_code = if ([int]$raw[2] -gt 0) { [int]$raw[2] } else { $null }
        birth_utc_ticks = if ([int64]$raw[3] -gt 0) { [int64]$raw[3] } else { $null }
        values = if ($status -eq "ok") { @($raw[5..12] | ForEach-Object { [int64]$_ }) } else { $null }
    }
}

function Test-CompleteProcessSnapshotContainsId([int]$ProcessId) {
    $parents = Get-NativeProcessParents
    return [bool]$parents.ContainsKey($ProcessId)
}

function New-ProductionTreeSampleProviders {
    return [ordered]@{
        Enumerate = { param([int]$RootProcessId) return @(Get-DescendantIds $RootProcessId) }
        Identity = { param([int]$ProcessId) return (Get-ProcessIdentityState $ProcessId) }
        Metrics = { param([int]$ProcessId) return (Get-ProcessMetricState $ProcessId) }
        SnapshotContains = { param([int]$ProcessId) return (Test-CompleteProcessSnapshotContainsId $ProcessId) }
    }
}

function Get-DescendantIds([int]$RootProcessId) {
    $parents = Get-NativeProcessParents
    $selected = New-Object 'System.Collections.Generic.HashSet[int]'
    [void]$selected.Add($RootProcessId)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($item in $parents.GetEnumerator()) {
            if ($selected.Contains([int]$item.Value) -and -not $selected.Contains([int]$item.Key)) {
                [void]$selected.Add([int]$item.Key)
                $changed = $true
            }
        }
    }
    return @($selected | Sort-Object)
}

function Get-SystemSample() {
    $values = [AvBsH4P0RNativeV1]::SystemMetrics()
    if ($values.Length -ne 4) { throw "BLOCKED_AV_BS_RESOURCE: system counter query failed" }
    return [ordered]@{
        commit_total_bytes = [int64]$values[0]
        commit_limit_bytes = [int64]$values[1]
        commit_headroom_bytes = [int64]($values[1] - $values[0])
        available_physical_bytes = [int64]$values[2]
        page_size_bytes = [int64]$values[3]
    }
}

# AV_BS_TREE_SAMPLE_TEST_SLICE_BEGIN
function New-TreeSampleDiagnostics([int]$EventLimit = 16) {
    return [ordered]@{
        confirmed_disappearance_count = 0
        event_limit = [int]$EventLimit
        last_failure = $null
        retry_events = New-Object System.Collections.ArrayList
        retry_events_truncated = $false
    }
}

function New-TreeSampleEvidence(
    [int]$Attempt,
    [object]$Confirmation,
    [string]$Context,
    [object]$ExpectedBirthUtcTicks,
    [string]$MessageCode,
    [object]$ObservedBirthUtcTicks,
    [string]$Operation,
    [object]$ProcessId,
    [object]$ProcessRole,
    [object]$Win32ErrorCode
) {
    return [ordered]@{
        attempt = [int]$Attempt
        confirmation = if ($null -eq $Confirmation) { $null } else { [string]$Confirmation }
        context = [string]$Context
        expected_birth_utc_ticks = if ($null -eq $ExpectedBirthUtcTicks) { $null } else { [int64]$ExpectedBirthUtcTicks }
        message_code = [string]$MessageCode
        observed_birth_utc_ticks = if ($null -eq $ObservedBirthUtcTicks) { $null } else { [int64]$ObservedBirthUtcTicks }
        operation = [string]$Operation
        process_id = if ($null -eq $ProcessId) { $null } else { [int]$ProcessId }
        process_role = if ($null -eq $ProcessRole) { $null } else { [string]$ProcessRole }
        win32_error_code = if ($null -eq $Win32ErrorCode) { $null } else { [int]$Win32ErrorCode }
    }
}

function New-TreeSampleFailureException(
    [System.Collections.IDictionary]$Evidence
) {
    $exception = New-Object System.InvalidOperationException(
        "BLOCKED_AV_BS_RESOURCE: tree sample failure " + [string]$Evidence.message_code
    )
    foreach ($key in @(
        "attempt", "confirmation", "context", "expected_birth_utc_ticks",
        "message_code", "observed_birth_utc_ticks", "operation", "process_id",
        "process_role", "win32_error_code"
    )) {
        $exception.Data[$key] = $Evidence[$key]
    }
    return $exception
}

function Set-TreeSampleFailureAndThrow(
    [System.Collections.IDictionary]$Diagnostics,
    [System.Collections.IDictionary]$Evidence
) {
    if ($null -ne $Diagnostics) { $Diagnostics.last_failure = $Evidence }
    throw (New-TreeSampleFailureException $Evidence)
}

function Add-TreeSampleRetryEvidence(
    [System.Collections.IDictionary]$Diagnostics,
    [System.Collections.IDictionary]$Evidence
) {
    if ($null -eq $Diagnostics) { return }
    $Diagnostics.confirmed_disappearance_count = [int]$Diagnostics.confirmed_disappearance_count + 1
    if ($Diagnostics.retry_events.Count -lt [int]$Diagnostics.event_limit) {
        [void]$Diagnostics.retry_events.Add($Evidence)
    }
    else {
        $Diagnostics.retry_events_truncated = $true
    }
}

function Assert-TreeSampleRootLive(
    [System.Collections.IDictionary]$Probe,
    [int]$RootProcessId,
    [int64]$RootBirthTicks,
    [string]$Context,
    [int]$Attempt,
    [string]$MessagePhase,
    [System.Collections.IDictionary]$Diagnostics
) {
    $operation = if ($null -ne $Probe -and $Probe.operation -is [string]) {
        [string]$Probe.operation
    } else {
        "process_identity_probe"
    }
    $observedBirth = if ($null -ne $Probe) { $Probe.birth_utc_ticks } else { $null }
    $win32Error = if ($null -ne $Probe) { $Probe.win32_error_code } else { $null }
    if (
        $null -ne $Probe -and
        $Probe.status -eq "live" -and
        $Probe.birth_utc_ticks -is [long] -and
        [int64]$Probe.birth_utc_ticks -eq $RootBirthTicks
    ) {
        return
    }
    $messageCode = if ($null -ne $Probe -and $Probe.status -eq "live") {
        "ROOT_PID_REUSE_" + $MessagePhase
    } elseif ($null -ne $Probe -and $Probe.status -in @("not_found", "exited")) {
        "ROOT_DISAPPEARED_" + $MessagePhase
    } else {
        "ROOT_IDENTITY_QUERY_FAILED_" + $MessagePhase
    }
    $evidence = New-TreeSampleEvidence `
        $Attempt $null $Context $RootBirthTicks $messageCode $observedBirth `
        $operation $RootProcessId "root" $win32Error
    Set-TreeSampleFailureAndThrow $Diagnostics $evidence
}

function Get-ConfirmedTreeSampleDisappearance(
    [System.Collections.IDictionary]$Probe,
    [scriptblock]$IdentityProvider,
    [scriptblock]$SnapshotContainsProvider,
    [int]$RootProcessId,
    [int64]$RootBirthTicks,
    [int]$ProcessId,
    [object]$ExpectedBirthUtcTicks,
    [string]$Context,
    [int]$Attempt,
    [string]$FailureOperation,
    [object]$FailureWin32ErrorCode,
    [System.Collections.IDictionary]$Diagnostics
) {
    if ($null -eq $Probe -or $Probe.status -notin @("not_found", "exited")) {
        return $null
    }
    if (
        $Probe.status -eq "not_found" -and
        ($Probe.win32_error_code -isnot [int] -or [int]$Probe.win32_error_code -ne 87)
    ) {
        $evidence = New-TreeSampleEvidence `
            $Attempt $null $Context $ExpectedBirthUtcTicks `
            "NONROOT_ABSENCE_NATIVE_CODE_INVALID" $Probe.birth_utc_ticks `
            ([string]$Probe.operation) $ProcessId "descendant" $Probe.win32_error_code
        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
    }
    if (
        $Probe.status -eq "exited" -and
        ($Probe.birth_utc_ticks -isnot [long] -or [int64]$Probe.birth_utc_ticks -le 0)
    ) {
        $evidence = New-TreeSampleEvidence `
            $Attempt $null $Context $ExpectedBirthUtcTicks `
            "NONROOT_EXIT_IDENTITY_INVALID" $Probe.birth_utc_ticks `
            ([string]$Probe.operation) $ProcessId "descendant" $Probe.win32_error_code
        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
    }
    if (
        $null -ne $ExpectedBirthUtcTicks -and
        $null -ne $Probe.birth_utc_ticks -and
        [int64]$Probe.birth_utc_ticks -ne [int64]$ExpectedBirthUtcTicks
    ) {
        $evidence = New-TreeSampleEvidence `
            $Attempt $null $Context $ExpectedBirthUtcTicks "NONROOT_PID_REUSE" `
            $Probe.birth_utc_ticks ([string]$Probe.operation) $ProcessId `
            "descendant" $Probe.win32_error_code
        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
    }
    $rootBefore = & $IdentityProvider $RootProcessId
    Assert-TreeSampleRootLive `
        $rootBefore $RootProcessId $RootBirthTicks $Context $Attempt `
        "BEFORE_DISAPPEARANCE_CONFIRMATION" $Diagnostics
    try {
        $snapshotContains = & $SnapshotContainsProvider $ProcessId
    }
    catch {
        $evidence = New-TreeSampleEvidence `
            $Attempt $null $Context $ExpectedBirthUtcTicks `
            "COMPLETE_PROCESS_SNAPSHOT_QUERY_FAILED" $Probe.birth_utc_ticks `
            "toolhelp_process_snapshot" $ProcessId "descendant" $null
        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
    }
    if ($snapshotContains -isnot [bool]) {
        $evidence = New-TreeSampleEvidence `
            $Attempt $null $Context $ExpectedBirthUtcTicks `
            "COMPLETE_PROCESS_SNAPSHOT_RESULT_INVALID" $Probe.birth_utc_ticks `
            "toolhelp_process_snapshot" $ProcessId "descendant" $null
        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
    }
    $rootAfter = & $IdentityProvider $RootProcessId
    Assert-TreeSampleRootLive `
        $rootAfter $RootProcessId $RootBirthTicks $Context $Attempt `
        "AFTER_DISAPPEARANCE_CONFIRMATION" $Diagnostics
    if ($snapshotContains) {
        $evidence = New-TreeSampleEvidence `
            $Attempt $null $Context $ExpectedBirthUtcTicks `
            "NONROOT_DISAPPEARANCE_NOT_CONFIRMED" $Probe.birth_utc_ticks `
            "toolhelp_process_snapshot" $ProcessId "descendant" $FailureWin32ErrorCode
        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
    }
    $confirmation = if ($Probe.status -eq "exited") {
        "signaled_handle_and_complete_snapshot_absent"
    } else {
        "limited_query_not_found_and_complete_snapshot_absent"
    }
    return (New-TreeSampleEvidence `
        $Attempt $confirmation $Context $ExpectedBirthUtcTicks `
        "CONFIRMED_NONROOT_DISAPPEARANCE" $Probe.birth_utc_ticks `
        $FailureOperation $ProcessId "descendant" $FailureWin32ErrorCode)
}

function Get-TreeSample(
    [int]$RootProcessId,
    [int64]$RootBirthTicks,
    [System.Collections.Generic.HashSet[int]]$ObservedProcessIds,
    [System.Collections.Generic.Dictionary[int, Int64]]$ObservedBirthTicks,
    [string]$Context = "unclassified_tree_sample",
    [System.Collections.IDictionary]$Diagnostics = $null,
    [System.Collections.IDictionary]$Providers = $null,
    [ValidateRange(1, 3)][int]$MaximumAttempts = 1
) {
    if ([string]::IsNullOrEmpty($Context) -or $Context.Length -gt 96) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: tree sample context is invalid"
    }
    if ($null -eq $Providers) { $Providers = New-ProductionTreeSampleProviders }
    foreach ($providerName in @("Enumerate", "Identity", "Metrics", "SnapshotContains")) {
        if (-not $Providers.Contains($providerName) -or $Providers[$providerName] -isnot [scriptblock]) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: tree sample provider set is incomplete"
        }
    }
    $enumerateProvider = [scriptblock]$Providers["Enumerate"]
    $identityProvider = [scriptblock]$Providers["Identity"]
    $metricsProvider = [scriptblock]$Providers["Metrics"]
    $snapshotContainsProvider = [scriptblock]$Providers["SnapshotContains"]
    if ($null -ne $Diagnostics) { $Diagnostics.last_failure = $null }

    :treeSampleAttempt for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt += 1) {
        $rootBefore = & $identityProvider $RootProcessId
        Assert-TreeSampleRootLive `
            $rootBefore $RootProcessId $RootBirthTicks $Context $attempt `
            "BEFORE_ENUMERATION" $Diagnostics
        try {
            $rawIds = @(& $enumerateProvider $RootProcessId)
        }
        catch {
            $evidence = New-TreeSampleEvidence `
                $attempt $null $Context $RootBirthTicks "TREE_ENUMERATION_FAILED" `
                $null "toolhelp_process_snapshot" $RootProcessId "root" $null
            Set-TreeSampleFailureAndThrow $Diagnostics $evidence
        }
        $ids = @()
        foreach ($candidateId in $rawIds) {
            if ($candidateId -isnot [int] -or [int]$candidateId -le 0) {
                $evidence = New-TreeSampleEvidence `
                    $attempt $null $Context $RootBirthTicks "TREE_ENUMERATION_ID_INVALID" `
                    $null "toolhelp_process_snapshot" $RootProcessId "root" $null
                Set-TreeSampleFailureAndThrow $Diagnostics $evidence
            }
            $ids += [int]$candidateId
        }
        $ids = @($ids | Sort-Object -Unique)
        if ($ids.Count -lt 1 -or $ids -notcontains $RootProcessId) {
            $evidence = New-TreeSampleEvidence `
                $attempt $null $Context $RootBirthTicks "ROOT_MISSING_FROM_TREE_ENUMERATION" `
                $null "toolhelp_process_snapshot" $RootProcessId "root" $null
            Set-TreeSampleFailureAndThrow $Diagnostics $evidence
        }
        $rootAfterEnumeration = & $identityProvider $RootProcessId
        Assert-TreeSampleRootLive `
            $rootAfterEnumeration $RootProcessId $RootBirthTicks $Context $attempt `
            "AFTER_ENUMERATION" $Diagnostics

        [int64]$working = 0
        [int64]$peakWorking = 0
        [int64]$commit = 0
        [int64]$peakCommit = 0
        [int64]$private = 0
        [int64]$privateWorking = 0
        [int64]$sharedCommit = 0
        [int64]$pageFaults = 0
        $retryEvidence = $null

        foreach ($id in $ids) {
            $expectedBirth = $null
            if ([int]$id -eq $RootProcessId) {
                $expectedBirth = [int64]$RootBirthTicks
            }
            else {
                $identity = & $identityProvider ([int]$id)
                if ($null -eq $identity -or $identity.status -notin @("live", "exited", "not_found", "query_failed", "malformed")) {
                    $identity = [ordered]@{
                        status = "malformed"; operation = "process_identity_probe"
                        win32_error_code = $null; birth_utc_ticks = $null
                    }
                }
                if ($identity.status -eq "live") {
                    if ($identity.birth_utc_ticks -isnot [long] -or [int64]$identity.birth_utc_ticks -lt $RootBirthTicks) {
                        $evidence = New-TreeSampleEvidence `
                            $attempt $null $Context $null "NONROOT_IDENTITY_INVALID_OR_STALE" `
                            $identity.birth_utc_ticks ([string]$identity.operation) `
                            ([int]$id) "descendant" $identity.win32_error_code
                        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                    }
                    $expectedBirth = [int64]$identity.birth_utc_ticks
                    if ($ObservedBirthTicks.ContainsKey([int]$id)) {
                        if ([int64]$ObservedBirthTicks[[int]$id] -ne $expectedBirth) {
                            $evidence = New-TreeSampleEvidence `
                                $attempt $null $Context $ObservedBirthTicks[[int]$id] `
                                "NONROOT_PID_REUSE" $expectedBirth ([string]$identity.operation) `
                                ([int]$id) "descendant" $identity.win32_error_code
                            Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                        }
                    }
                    else {
                        $ObservedBirthTicks.Add([int]$id, $expectedBirth)
                    }
                    [void]$ObservedProcessIds.Add([int]$id)
                }
                elseif ($identity.status -in @("exited", "not_found")) {
                    if ($identity.status -eq "exited") {
                        if (
                            $identity.birth_utc_ticks -isnot [long] -or
                            [int64]$identity.birth_utc_ticks -lt $RootBirthTicks
                        ) {
                            $evidence = New-TreeSampleEvidence `
                                $attempt $null $Context $null `
                                "NONROOT_IDENTITY_INVALID_OR_STALE" `
                                $identity.birth_utc_ticks ([string]$identity.operation) `
                                ([int]$id) "descendant" $identity.win32_error_code
                            Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                        }
                        $expectedBirth = [int64]$identity.birth_utc_ticks
                    }
                    elseif ($ObservedBirthTicks.ContainsKey([int]$id)) {
                        $expectedBirth = [int64]$ObservedBirthTicks[[int]$id]
                    }
                    $retryEvidence = Get-ConfirmedTreeSampleDisappearance `
                        $identity $identityProvider $snapshotContainsProvider `
                        $RootProcessId $RootBirthTicks ([int]$id) $expectedBirth `
                        $Context $attempt ([string]$identity.operation) `
                        $identity.win32_error_code $Diagnostics
                    if ($identity.status -eq "exited") {
                        if ($ObservedBirthTicks.ContainsKey([int]$id)) {
                            if ([int64]$ObservedBirthTicks[[int]$id] -ne $expectedBirth) {
                                $evidence = New-TreeSampleEvidence `
                                    $attempt $null $Context $ObservedBirthTicks[[int]$id] `
                                    "NONROOT_PID_REUSE" $expectedBirth `
                                    ([string]$identity.operation) ([int]$id) `
                                    "descendant" $identity.win32_error_code
                                Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                            }
                        }
                        else {
                            $ObservedBirthTicks.Add([int]$id, $expectedBirth)
                        }
                        [void]$ObservedProcessIds.Add([int]$id)
                    }
                    break
                }
                else {
                    $evidence = New-TreeSampleEvidence `
                        $attempt $null $Context $null "NONROOT_IDENTITY_QUERY_FAILED" `
                        $identity.birth_utc_ticks ([string]$identity.operation) `
                        ([int]$id) "descendant" $identity.win32_error_code
                    Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                }
            }

            $metric = & $metricsProvider ([int]$id)
            if ($null -eq $metric -or $metric.status -notin @("ok", "exited", "not_found", "query_failed", "malformed")) {
                $metric = [ordered]@{
                    status = "malformed"; operation = "process_metric_probe"
                    win32_error_code = $null; birth_utc_ticks = $null; values = $null
                }
            }
            if ($metric.status -ne "ok") {
                if ([int]$id -eq $RootProcessId) {
                    $evidence = New-TreeSampleEvidence `
                        $attempt $null $Context $RootBirthTicks "ROOT_METRIC_QUERY_FAILED" `
                        $metric.birth_utc_ticks ([string]$metric.operation) `
                        $RootProcessId "root" $metric.win32_error_code
                    Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                }
                if ($metric.status -eq "malformed") {
                    $evidence = New-TreeSampleEvidence `
                        $attempt $null $Context $expectedBirth "PROCESS_METRIC_RESULT_MALFORMED" `
                        $metric.birth_utc_ticks ([string]$metric.operation) `
                        ([int]$id) "descendant" $metric.win32_error_code
                    Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                }
                if ($metric.status -notin @("not_found", "exited")) {
                    $evidence = New-TreeSampleEvidence `
                        $attempt $null $Context $expectedBirth `
                        "PROCESS_METRIC_QUERY_FAILED_WHILE_LIVE" `
                        $metric.birth_utc_ticks ([string]$metric.operation) `
                        ([int]$id) "descendant" $metric.win32_error_code
                    Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                }
                if ($metric.status -eq "exited") {
                    if (
                        $metric.birth_utc_ticks -isnot [long] -or
                        [int64]$metric.birth_utc_ticks -le 0
                    ) {
                        $evidence = New-TreeSampleEvidence `
                            $attempt $null $Context $expectedBirth `
                            "PROCESS_METRIC_IDENTITY_OR_VALUE_INVALID" `
                            $metric.birth_utc_ticks ([string]$metric.operation) `
                            ([int]$id) "descendant" $metric.win32_error_code
                        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                    }
                    if ([int64]$metric.birth_utc_ticks -ne [int64]$expectedBirth) {
                        $evidence = New-TreeSampleEvidence `
                            $attempt $null $Context $expectedBirth "NONROOT_PID_REUSE" `
                            $metric.birth_utc_ticks ([string]$metric.operation) `
                            ([int]$id) "descendant" $metric.win32_error_code
                        Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                    }
                }
                $confirmationProbe = & $identityProvider ([int]$id)
                $retryEvidence = Get-ConfirmedTreeSampleDisappearance `
                    $confirmationProbe $identityProvider $snapshotContainsProvider `
                    $RootProcessId $RootBirthTicks ([int]$id) $expectedBirth `
                    $Context $attempt ([string]$metric.operation) `
                    $metric.win32_error_code $Diagnostics
                if ($null -eq $retryEvidence) {
                    $evidence = New-TreeSampleEvidence `
                        $attempt $null $Context $expectedBirth `
                        "PROCESS_METRIC_QUERY_FAILED_WHILE_LIVE" `
                        $confirmationProbe.birth_utc_ticks ([string]$metric.operation) `
                        ([int]$id) "descendant" $metric.win32_error_code
                    Set-TreeSampleFailureAndThrow $Diagnostics $evidence
                }
                break
            }
            $metricValues = @($metric.values)
            if (
                $metricValues.Count -ne 8 -or
                $metric.birth_utc_ticks -isnot [long] -or
                [int64]$metric.birth_utc_ticks -ne [int64]$expectedBirth -or
                @($metricValues | Where-Object { $_ -isnot [long] -or [int64]$_ -lt 0 }).Count -gt 0
            ) {
                $evidence = New-TreeSampleEvidence `
                    $attempt $null $Context $expectedBirth "PROCESS_METRIC_IDENTITY_OR_VALUE_INVALID" `
                    $metric.birth_utc_ticks ([string]$metric.operation) ([int]$id) `
                    $(if ([int]$id -eq $RootProcessId) { "root" } else { "descendant" }) `
                    $metric.win32_error_code
                Set-TreeSampleFailureAndThrow $Diagnostics $evidence
            }
            $working += [int64]$metricValues[0]
            $peakWorking += [int64]$metricValues[1]
            $commit += [int64]$metricValues[2]
            $peakCommit += [int64]$metricValues[3]
            $private += [int64]$metricValues[4]
            $privateWorking += [int64]$metricValues[5]
            $sharedCommit += [int64]$metricValues[6]
            $pageFaults += [int64]$metricValues[7]
        }

        if ($null -ne $retryEvidence) {
            Add-TreeSampleRetryEvidence $Diagnostics $retryEvidence
            if ($attempt -ge $MaximumAttempts) {
                $exhausted = New-TreeSampleEvidence `
                    $attempt $retryEvidence.confirmation $Context `
                    $retryEvidence.expected_birth_utc_ticks `
                    "TRANSIENT_DESCENDANT_DISAPPEARANCE_RETRY_EXHAUSTED" `
                    $retryEvidence.observed_birth_utc_ticks $retryEvidence.operation `
                    $retryEvidence.process_id $retryEvidence.process_role `
                    $retryEvidence.win32_error_code
                Set-TreeSampleFailureAndThrow $Diagnostics $exhausted
            }
            continue treeSampleAttempt
        }

        $rootBeforeReturn = & $identityProvider $RootProcessId
        Assert-TreeSampleRootLive `
            $rootBeforeReturn $RootProcessId $RootBirthTicks $Context $attempt `
            "BEFORE_SAMPLE_RETURN" $Diagnostics
        if ($null -ne $Diagnostics) { $Diagnostics.last_failure = $null }
        return [ordered]@{
            process_ids = @($ids)
            process_count = $ids.Count
            working_set_bytes = $working
            summed_process_peak_working_set_bytes = $peakWorking
            committed_pagefile_bytes = $commit
            summed_process_peak_commit_bytes = $peakCommit
            private_commit_bytes = $private
            private_working_set_bytes = $privateWorking
            nonprivate_working_set_proxy_bytes = [math]::Max([int64]0, $working - $privateWorking)
            shared_commit_bytes = $sharedCommit
            page_fault_count = $pageFaults
        }
    }
    throw "BLOCKED_AV_BS_RESOURCE: tree sample attempt loop terminated unexpectedly"
}
# AV_BS_TREE_SAMPLE_TEST_SLICE_END

function Get-NormalizedOuterMonitorFailure(
    [System.Exception]$Exception,
    [string]$FallbackOperation
) {
    $required = @(
        "attempt", "confirmation", "context", "expected_birth_utc_ticks",
        "message_code", "observed_birth_utc_ticks", "operation", "process_id",
        "process_role", "win32_error_code"
    )
    $data = if ($null -ne $Exception) { $Exception.Data } else { $null }
    $structured = $null -ne $data
    foreach ($key in $required) {
        if (-not $structured -or -not $data.Contains($key)) { $structured = $false; break }
    }
    if ($structured) {
        $structured = (
            $data["attempt"] -is [int] -and [int]$data["attempt"] -ge 1 -and
            ($null -eq $data["confirmation"] -or $data["confirmation"] -is [string]) -and
            $data["context"] -is [string] -and
            ($null -eq $data["expected_birth_utc_ticks"] -or $data["expected_birth_utc_ticks"] -is [long]) -and
            $data["message_code"] -is [string] -and
            ($null -eq $data["observed_birth_utc_ticks"] -or $data["observed_birth_utc_ticks"] -is [long]) -and
            $data["operation"] -is [string] -and
            ($null -eq $data["process_id"] -or $data["process_id"] -is [int]) -and
            ($null -eq $data["process_role"] -or $data["process_role"] -is [string]) -and
            ($null -eq $data["win32_error_code"] -or $data["win32_error_code"] -is [int])
        )
    }
    if ($structured) {
        return (New-TreeSampleEvidence `
            ([int]$data["attempt"]) $data["confirmation"] ([string]$data["context"]) `
            $data["expected_birth_utc_ticks"] ([string]$data["message_code"]) `
            $data["observed_birth_utc_ticks"] ([string]$data["operation"]) `
            $data["process_id"] $data["process_role"] $data["win32_error_code"])
    }
    $messageCode = if (
        $null -ne $Exception -and
        [string]$Exception.Message -like "BLOCKED_AV_BS_RESOURCE:*"
    ) {
        "OUTER_RESOURCE_EXCEPTION"
    } else {
        "OUTER_OBSERVER_EXCEPTION"
    }
    return (New-TreeSampleEvidence `
        1 $null ([string]$FallbackOperation) $null $messageCode $null `
        ([string]$FallbackOperation) $null $null $null)
}

function Get-ProcessBirthTicks([int]$ProcessId) {
    $candidate = Get-Process -Id $ProcessId -ErrorAction Stop
    try { return [int64]$candidate.StartTime.ToUniversalTime().Ticks }
    catch { throw "BLOCKED_AV_BS_RESOURCE: PID $ProcessId creation time is unavailable" }
}

function Register-ObservedProcess(
    [int]$ProcessId,
    [System.Collections.Generic.Dictionary[int, Int64]]$BirthTicks,
    [int64]$TreeRootBirthTicks
) {
    $birth = Get-ProcessBirthTicks $ProcessId
    if ($birth -lt $TreeRootBirthTicks) {
        throw "BLOCKED_AV_BS_RESOURCE: stale descendant PID $ProcessId predates the tree root"
    }
    if ($BirthTicks.ContainsKey($ProcessId)) {
        if ($BirthTicks[$ProcessId] -ne $birth) { throw "BLOCKED_AV_BS_RESOURCE: PID reuse detected for $ProcessId" }
    }
    else { $BirthTicks.Add($ProcessId, $birth) }
}

function Get-LiveObservedProcessIds(
    [System.Collections.Generic.HashSet[int]]$ObservedProcessIds,
    [System.Collections.Generic.Dictionary[int, Int64]]$ObservedBirthTicks
) {
    $live = @()
    foreach ($id in @($ObservedProcessIds | Sort-Object)) {
        $candidate = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
        if ($null -eq $candidate) { continue }
        if (-not $ObservedBirthTicks.ContainsKey([int]$id)) {
            throw "BLOCKED_AV_BS_RESOURCE: PID $id has no recorded creation time"
        }
        try { $birth = [int64]$candidate.StartTime.ToUniversalTime().Ticks }
        catch { throw "BLOCKED_AV_BS_RESOURCE: PID $id creation time is unavailable" }
        if ($ObservedBirthTicks[[int]$id] -ne $birth) {
            throw "BLOCKED_AV_BS_RESOURCE: PID reuse detected for $id"
        }
        $live += [int]$id
    }
    return @($live)
}

function Get-ObservedProcessIdentities(
    [System.Collections.Generic.Dictionary[int, Int64]]$ObservedBirthTicks
) {
    $identities = @()
    foreach ($id in @($ObservedBirthTicks.Keys | Sort-Object)) {
        $identities += [ordered]@{
            process_id = [int]$id
            birth_utc_ticks = [int64]$ObservedBirthTicks[[int]$id]
        }
    }
    return @($identities)
}

function Get-VerifiedRootDescendantIds([int]$RootProcessId, [int64]$RootBirthTicks) {
    $rootBefore = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
    if ($null -eq $rootBefore) { return @() }
    try { $birthBefore = [int64]$rootBefore.StartTime.ToUniversalTime().Ticks }
    catch { throw "BLOCKED_AV_BS_RESOURCE: root PID $RootProcessId creation time is unavailable" }
    if ($birthBefore -ne $RootBirthTicks) {
        throw "BLOCKED_AV_BS_RESOURCE: root PID reuse detected for $RootProcessId"
    }
    $ids = @(Get-DescendantIds $RootProcessId)
    $rootAfter = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
    if ($null -eq $rootAfter) {
        # The released PID cannot safely authorize new descendants. Already
        # identity-bound processes remain eligible for cleanup.
        return @()
    }
    try { $birthAfter = [int64]$rootAfter.StartTime.ToUniversalTime().Ticks }
    catch { throw "BLOCKED_AV_BS_RESOURCE: root PID $RootProcessId creation time is unavailable" }
    if ($birthAfter -ne $RootBirthTicks) {
        throw "BLOCKED_AV_BS_RESOURCE: root PID reuse detected for $RootProcessId"
    }
    return @($ids)
}

function Stop-ProcessTree(
    [int]$RootProcessId,
    [System.Collections.Generic.HashSet[int]]$ObservedProcessIds,
    [System.Collections.Generic.Dictionary[int, Int64]]$ObservedBirthTicks
) {
    if (-not $ObservedBirthTicks.ContainsKey($RootProcessId)) {
        throw "BLOCKED_AV_BS_RESOURCE: root PID $RootProcessId was not identity-bound before termination"
    }
    [void]$ObservedProcessIds.Add($RootProcessId)
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        foreach ($id in @(Get-VerifiedRootDescendantIds $RootProcessId $ObservedBirthTicks[$RootProcessId])) {
            if ([int]$id -ne $RootProcessId) {
                Register-ObservedProcess ([int]$id) $ObservedBirthTicks $ObservedBirthTicks[$RootProcessId]
                [void]$ObservedProcessIds.Add([int]$id)
            }
        }
        foreach ($id in @(Get-LiveObservedProcessIds $ObservedProcessIds $ObservedBirthTicks | Where-Object { $_ -ne $RootProcessId })) {
            try { Stop-Process -Id $id -Force -ErrorAction Stop }
            catch {
                if (Get-Process -Id $id -ErrorAction SilentlyContinue) { throw }
            }
        }
        if (Get-LiveObservedProcessIds $ObservedProcessIds $ObservedBirthTicks | Where-Object { $_ -eq $RootProcessId }) {
            try { Stop-Process -Id $RootProcessId -Force -ErrorAction Stop }
            catch {
                if (Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue) { throw }
            }
        }
        Start-Sleep -Milliseconds 50
        $survivors = @(Get-LiveObservedProcessIds $ObservedProcessIds $ObservedBirthTicks)
        if ($survivors.Count -eq 0) { return }
    }
    throw (
        "BLOCKED_AV_BS_RESOURCE: observed child process termination could not be verified: " +
        (($ObservedProcessIds | Sort-Object) -join ",")
    )
}

function Update-ControlPlanePeak(
    [System.Collections.IDictionary]$Peak,
    [System.Collections.IDictionary]$Tree,
    [System.Collections.IDictionary]$System
) {
    $Peak.tree_working_set_bytes = [math]::Max([int64]$Peak.tree_working_set_bytes, [int64]$Tree.working_set_bytes)
    $Peak.tree_summed_process_lifetime_peak_working_set_bytes = [math]::Max(
        [int64]$Peak.tree_summed_process_lifetime_peak_working_set_bytes,
        [int64]$Tree.summed_process_peak_working_set_bytes
    )
    $Peak.tree_private_commit_bytes = [math]::Max([int64]$Peak.tree_private_commit_bytes, [int64]$Tree.private_commit_bytes)
    $Peak.tree_committed_pagefile_bytes = [math]::Max([int64]$Peak.tree_committed_pagefile_bytes, [int64]$Tree.committed_pagefile_bytes)
    $Peak.tree_summed_process_lifetime_peak_commit_bytes = [math]::Max(
        [int64]$Peak.tree_summed_process_lifetime_peak_commit_bytes,
        [int64]$Tree.summed_process_peak_commit_bytes
    )
    $Peak.tree_nonprivate_working_set_proxy_bytes = [math]::Max(
        [int64]$Peak.tree_nonprivate_working_set_proxy_bytes,
        [int64]$Tree.nonprivate_working_set_proxy_bytes
    )
    $Peak.tree_page_fault_count = [math]::Max([int64]$Peak.tree_page_fault_count, [int64]$Tree.page_fault_count)
    $Peak.system_commit_total_bytes = [math]::Max([int64]$Peak.system_commit_total_bytes, [int64]$System.commit_total_bytes)
    $Peak.system_commit_headroom_min_bytes = [math]::Min(
        [int64]$Peak.system_commit_headroom_min_bytes,
        [int64]$System.commit_headroom_bytes
    )
    $Peak.available_physical_min_bytes = [math]::Min(
        [int64]$Peak.available_physical_min_bytes,
        [int64]$System.available_physical_bytes
    )
}

function Invoke-ControlPlanePython {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("preflight", "canonical_json_hash", "finalizer", "token_consumer")]
        [string]$Operation,
        [Parameter(Mandatory = $true)]
        [string]$TargetScriptPath,
        [string[]]$ScriptArguments = @(),
        [int[]]$AllowedExitCodes = @(0),
        [System.Collections.IDictionary]$AttemptArtifactPaths = $null
    )

    if (
        $null -eq $controlPlaneSessionRoot -or
        $null -eq $controlPlaneBootstrapPath -or
        -not (Test-Path -LiteralPath $controlPlaneBootstrapPath -PathType Leaf)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane bootstrap is unavailable"
    }
    $target = [IO.Path]::GetFullPath($TargetScriptPath)
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane target is missing"
    }
    $allowed = @($AllowedExitCodes | Sort-Object -Unique)
    if ($allowed.Count -lt 1) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane allowed exit-code set is empty"
    }
    $scopeBindingSemantics = if ($Operation -eq "preflight") {
        "candidate_token_hash_untrusted_until_preflight_payload_validation"
    }
    else {
        "validated_preflight_execution_scope_hash"
    }

    $startedUtc = [DateTimeOffset]::UtcNow
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $invocationId = [guid]::NewGuid().ToString("N")
    $invocationLeaf = $Operation + "-" + $invocationId
    $invocationDirectory = [IO.Path]::GetFullPath((Join-Path $controlPlaneSessionRoot $invocationLeaf))
    if (
        -not $invocationDirectory.StartsWith($controlPlaneSessionRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetDirectoryName($invocationDirectory).TrimEnd('\') -ne $controlPlaneSessionRoot.TrimEnd('\') -or
        [IO.Path]::GetFileName($invocationDirectory) -ne $invocationLeaf
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane invocation path validation failed"
    }
    [IO.Directory]::CreateDirectory($invocationDirectory) | Out-Null
    $stdout = Join-Path $invocationDirectory "stdout.txt"
    $stderr = Join-Path $invocationDirectory "stderr.txt"
    $ready = Join-Path $invocationDirectory "bootstrap-ready.json"
    $startRelease = Join-Path $invocationDirectory "start-release.json"
    $completion = Join-Path $invocationDirectory "target-complete.json"
    $exitRelease = Join-Path $invocationDirectory "exit-release.json"
    $reportPath = Join-Path $invocationDirectory "control-plane-report.json"
    $streamStopBytes = [int64](16MB)
    $targetArguments = @($ScriptArguments | ForEach-Object { [string]$_ })
    $targetArgv = @([string]$target) + $targetArguments
    $processArgv = @(
        [string]$controlPlaneBootstrapPath,
        [string]$ready,
        [string]$startRelease,
        [string]$completion,
        [string]$exitRelease
    ) + $targetArgv
    $targetArgvJson = ConvertTo-Json -InputObject $targetArgv -Depth 4 -Compress
    $processArgvJson = ConvertTo-Json -InputObject $processArgv -Depth 4 -Compress
    $targetArgvHash = Get-Utf8TextSha256 $targetArgvJson
    $processArgvHash = Get-Utf8TextSha256 $processArgvJson
    $attemptProvenanceBefore = $null
    $attemptProvenanceBeforeHash = $null
    $attemptProvenanceAfter = $null
    $attemptProvenanceAfterHash = $null
    $wallSeconds = $null
    $terminalSystemSampleAfterVerifiedTerminationOrNoSpawn = $false
    $preHelperTreeAfterProvenance = $null
    $preHelperSystemAfterProvenance = $null
    $preCloseIndexPath = $null
    $preCloseIndexSha256 = $null
    $envelopeClosePath = Join-Path $invocationDirectory "control-plane-envelope-close.json"
    $envelopeCloseSha256 = $null
    $closeTree = $null
    $preSpawnSystem = $null
    $finalSystem = $null
    $process = $null
    $retainedHandle = [IntPtr]::Zero
    $processBirthTicks = $null
    $processHandleAcquired = $false
    $processLaunched = $false
    $supervisorBirthTicks = Get-ProcessBirthTicks $PID
    $monitorRootProcessId = [int]$script:executionTreeRootProcessId
    $monitorRootBirthTicks = [int64]$script:executionTreeRootBirthUtcTicks
    if (
        $monitorRootProcessId -le 0 -or $monitorRootBirthTicks -le 0 -or
        $monitorRootProcessId -eq [int]$PID -or
        $supervisorBirthTicks -ne [int64]$script:innerRunnerBirthUtcTicks -or
        (Get-ProcessBirthTicks $monitorRootProcessId) -ne $monitorRootBirthTicks
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane outer execution-tree identity is invalid"
    }
    $monitorObservedIds = New-Object 'System.Collections.Generic.HashSet[int]'
    $monitorObservedBirthTicks = New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
    $monitorObservedBirthTicks.Add($monitorRootProcessId, $monitorRootBirthTicks)
    $monitorObservedBirthTicks.Add([int]$PID, [int64]$supervisorBirthTicks)
    [void]$monitorObservedIds.Add($monitorRootProcessId)
    [void]$monitorObservedIds.Add([int]$PID)
    $observedIds = New-Object 'System.Collections.Generic.HashSet[int]'
    $observedBirthTicks = New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
    $successfulSamples = 0
    $targetVisibleSamples = 0
    $readyObserved = $false
    $startReleased = $false
    $completionObserved = $false
    $exitReleased = $false
    $reportedExitCode = $null
    $actualExitCode = $null
    $stopReason = $null
    $monitorError = $null
    $monitorFailure = $null
    $currentControlOperation = "control_supervisor_exception"
    $currentControlTreeSampleContext = "control_pre_helper_tree_sample"
    $controlTreeSampleDiagnostics = New-TreeSampleDiagnostics $treeSampleRetryEventLimit
    $cleanupAttempted = $false
    $cleanupVerified = $false
    $fallbackProcessObjectCleanupAttempted = $false
    $fallbackProcessObjectRootExitVerified = $false
    $cleanupIdentityComplete = $false
    $survivorsAfterCleanup = @()
    $stdoutText = $null
    $peak = [ordered]@{
        tree_working_set_bytes = [int64]0
        tree_summed_process_lifetime_peak_working_set_bytes = [int64]0
        tree_private_commit_bytes = [int64]0
        tree_committed_pagefile_bytes = [int64]0
        tree_summed_process_lifetime_peak_commit_bytes = [int64]0
        tree_nonprivate_working_set_proxy_bytes = [int64]0
        tree_page_fault_count = [int64]0
        system_commit_total_bytes = [int64]0
        system_commit_headroom_min_bytes = [int64]::MaxValue
        available_physical_min_bytes = [int64]::MaxValue
    }

    try {
        $preSpawnSystem = Get-SystemSample
        $peak.system_commit_total_bytes = [int64]$preSpawnSystem.commit_total_bytes
        $peak.system_commit_headroom_min_bytes = [int64]$preSpawnSystem.commit_headroom_bytes
        $peak.available_physical_min_bytes = [int64]$preSpawnSystem.available_physical_bytes
        if ($preSpawnSystem.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) {
            $stopReason = "CONTROL_PRESPAWN_COMMIT_HEADROOM_STOP"
            throw "BLOCKED_AV_BS_RESOURCE: control-plane pre-spawn commit headroom is below the frozen high floor"
        }
        if ($preSpawnSystem.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) {
            $stopReason = "CONTROL_PRESPAWN_AVAILABLE_PHYSICAL_STOP"
            throw "BLOCKED_AV_BS_RESOURCE: control-plane pre-spawn available physical memory is below the frozen high floor"
        }

        $attemptProvenanceBefore = Get-AttemptArtifactProvenance $AttemptArtifactPaths
        $attemptProvenanceBeforeJson = ConvertTo-Json -InputObject $attemptProvenanceBefore -Depth 6 -Compress
        $attemptProvenanceBeforeHash = Get-Utf8TextSha256 $attemptProvenanceBeforeJson
        $currentControlTreeSampleContext = "control_pre_helper_tree_sample"
        $preHelperTreeAfterProvenance = Get-TreeSample `
            $monitorRootProcessId $monitorRootBirthTicks `
            $monitorObservedIds $monitorObservedBirthTicks `
            $currentControlTreeSampleContext $controlTreeSampleDiagnostics `
            $null $treeSampleMaximumAttempts
        $preHelperSystemAfterProvenance = Get-SystemSample
        Update-ControlPlanePeak $peak $preHelperTreeAfterProvenance $preHelperSystemAfterProvenance
        if ($preHelperTreeAfterProvenance.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_WS_STOP" }
        elseif ($preHelperTreeAfterProvenance.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_WS_STOP" }
        elseif ($preHelperTreeAfterProvenance.private_commit_bytes -gt $treePrivateStop) { $stopReason = "CONTROL_TREE_PRIVATE_STOP" }
        elseif ($preHelperTreeAfterProvenance.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_COMMIT_STOP" }
        elseif ($preHelperTreeAfterProvenance.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_COMMIT_STOP" }
        elseif ($preHelperSystemAfterProvenance.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) { $stopReason = "CONTROL_PRESPAWN_COMMIT_HEADROOM_STOP" }
        elseif ($preHelperSystemAfterProvenance.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) { $stopReason = "CONTROL_PRESPAWN_AVAILABLE_PHYSICAL_STOP" }
        if ($stopReason) {
            throw "BLOCKED_AV_BS_RESOURCE: control-plane pre-helper provenance envelope gate failed"
        }
        if ($stopwatch.Elapsed.TotalSeconds -gt $controlPlaneWallStopSeconds) {
            $stopReason = "CONTROL_WALL_TIME_STOP"
            throw "BLOCKED_AV_BS_RESOURCE: control-plane wall limit expired before helper spawn"
        }

        $bootstrapArguments = @(
            (Quote-Argument $controlPlaneBootstrapPath),
            (Quote-Argument $ready),
            (Quote-Argument $startRelease),
            (Quote-Argument $completion),
            (Quote-Argument $exitRelease),
            (Quote-Argument $target)
        )
        foreach ($item in $targetArguments) {
            $bootstrapArguments += (Quote-Argument ([string]$item))
        }
        $argumentList = $bootstrapArguments -join " "
        $process = Start-Process -FilePath $pythonPath -ArgumentList $argumentList -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $processLaunched = $true
        $processBirthTicks = [int64]$process.StartTime.ToUniversalTime().Ticks
        if (
            $processBirthTicks -lt [int64]$supervisorBirthTicks -or
            [int64]$supervisorBirthTicks -lt $monitorRootBirthTicks
        ) {
            throw "BLOCKED_AV_BS_RESOURCE: control-plane process identity chronology is invalid"
        }
        $observedBirthTicks.Add([int]$process.Id, [int64]$processBirthTicks)
        [void]$observedIds.Add([int]$process.Id)
        $monitorObservedBirthTicks.Add([int]$process.Id, [int64]$processBirthTicks)
        [void]$monitorObservedIds.Add([int]$process.Id)
        $cleanupIdentityComplete = $true
        $retainedHandle = $process.Handle
        if ($retainedHandle -eq [IntPtr]::Zero) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane process handle was not acquired"
        }
        $processHandleAcquired = $true

        while ($true) {
            $process.Refresh()
            if ($process.HasExited) { break }
            $currentControlTreeSampleContext = "control_active_outer_tree_sample"
            $tree = Get-TreeSample `
                $monitorRootProcessId $monitorRootBirthTicks `
                $monitorObservedIds $monitorObservedBirthTicks `
                $currentControlTreeSampleContext $controlTreeSampleDiagnostics `
                $null $treeSampleMaximumAttempts
            $currentControlTreeSampleContext = "control_active_cleanup_root_tree_sample"
            $cleanupTree = Get-TreeSample `
                $process.Id $processBirthTicks $observedIds $observedBirthTicks `
                $currentControlTreeSampleContext $controlTreeSampleDiagnostics `
                $null $treeSampleMaximumAttempts
            $system = Get-SystemSample
            $successfulSamples += 1
            if (
                $tree.process_ids -contains $process.Id -and
                $cleanupTree.process_ids -contains $process.Id
            ) { $targetVisibleSamples += 1 }
            Update-ControlPlanePeak $peak $tree $system

            if ((Test-Path -LiteralPath $ready -PathType Leaf) -and -not $startReleased) {
                $readyObserved = $true
                $startReleasePayload = [ordered]@{
                    schema = "AV-BS1-h4-p0r-control-plane-start-release-v1"
                    invocation_id = $invocationId
                    process_id = [int]$process.Id
                    sample_perf_counter_ns = Get-MonotonicNanoseconds
                    sample_utc = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-AtomicUtf8NoBom $startRelease ($startReleasePayload | ConvertTo-Json -Depth 4 -Compress)
                $startReleased = $true
            }

            if ($tree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_WS_STOP" }
            elseif ($tree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_WS_STOP" }
            elseif ($tree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "CONTROL_TREE_PRIVATE_STOP" }
            elseif ($tree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_COMMIT_STOP" }
            elseif ($tree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_COMMIT_STOP" }
            elseif ($system.commit_headroom_bytes -lt $commitHeadroomFloor) { $stopReason = "CONTROL_SYSTEM_COMMIT_HEADROOM_STOP" }
            elseif ($system.available_physical_bytes -lt $availablePhysicalFloor) { $stopReason = "CONTROL_AVAILABLE_PHYSICAL_STOP" }
            elseif ((Test-Path -LiteralPath $stdout -PathType Leaf) -and (Get-Item -LiteralPath $stdout).Length -gt $streamStopBytes) { $stopReason = "CONTROL_STDOUT_SIZE_STOP" }
            elseif ((Test-Path -LiteralPath $stderr -PathType Leaf) -and (Get-Item -LiteralPath $stderr).Length -gt $streamStopBytes) { $stopReason = "CONTROL_STDERR_SIZE_STOP" }
            elseif ($stopwatch.Elapsed.TotalSeconds -gt $controlPlaneWallStopSeconds) { $stopReason = "CONTROL_WALL_TIME_STOP" }

            if ((Test-Path -LiteralPath $completion -PathType Leaf) -and -not $completionObserved -and -not $stopReason) {
                $completionObserved = $true
                $completionValue = Get-Content -LiteralPath $completion -Raw -Encoding utf8 | ConvertFrom-Json
                if (
                    $completionValue.schema -ne "AV-BS1-h4-p0r-control-plane-target-complete-v1" -or
                    $completionValue.process_id -ne $process.Id -or
                    $completionValue.exit_code -isnot [int]
                ) {
                    $stopReason = "CONTROL_COMPLETION_MARKER_INVALID"
                }
                else {
                    $reportedExitCode = [int]$completionValue.exit_code
                    # The bootstrap is still alive. This second sample is after
                    # target completion and therefore captures its OS lifetime peaks.
                    $currentControlTreeSampleContext = "control_post_completion_outer_tree_sample"
                    $postTree = Get-TreeSample `
                        $monitorRootProcessId $monitorRootBirthTicks `
                        $monitorObservedIds $monitorObservedBirthTicks `
                        $currentControlTreeSampleContext $controlTreeSampleDiagnostics `
                        $null $treeSampleMaximumAttempts
                    $currentControlTreeSampleContext = "control_post_completion_cleanup_root_tree_sample"
                    $postCleanupTree = Get-TreeSample `
                        $process.Id $processBirthTicks $observedIds $observedBirthTicks `
                        $currentControlTreeSampleContext $controlTreeSampleDiagnostics `
                        $null $treeSampleMaximumAttempts
                    $postSystem = Get-SystemSample
                    $successfulSamples += 1
                    if (
                        -not ($postTree.process_ids -contains $process.Id) -or
                        -not ($postCleanupTree.process_ids -contains $process.Id)
                    ) {
                        $stopReason = "CONTROL_POST_COMPLETION_ROOT_MISSING"
                    }
                    else {
                        $targetVisibleSamples += 1
                        Update-ControlPlanePeak $peak $postTree $postSystem
                        if ($postTree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_WS_STOP" }
                        elseif ($postTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_WS_STOP" }
                        elseif ($postTree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "CONTROL_TREE_PRIVATE_STOP" }
                        elseif ($postTree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_COMMIT_STOP" }
                        elseif ($postTree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_COMMIT_STOP" }
                        elseif ($postSystem.commit_headroom_bytes -lt $commitHeadroomFloor) { $stopReason = "CONTROL_SYSTEM_COMMIT_HEADROOM_STOP" }
                        elseif ($postSystem.available_physical_bytes -lt $availablePhysicalFloor) { $stopReason = "CONTROL_AVAILABLE_PHYSICAL_STOP" }
                    }
                    if (-not $stopReason) {
                        $exitReleasePayload = [ordered]@{
                            schema = "AV-BS1-h4-p0r-control-plane-exit-release-v1"
                            invocation_id = $invocationId
                            process_id = [int]$process.Id
                            sample_perf_counter_ns = Get-MonotonicNanoseconds
                            sample_utc = [DateTimeOffset]::UtcNow.ToString("o")
                        }
                        Write-AtomicUtf8NoBom $exitRelease ($exitReleasePayload | ConvertTo-Json -Depth 4 -Compress)
                        $exitReleased = $true
                    }
                }
            }

            if ($stopReason) {
                $cleanupAttempted = $true
                Stop-ProcessTree $process.Id $observedIds $observedBirthTicks
                break
            }
            Start-Sleep -Milliseconds $pollMilliseconds
        }

        $process.Refresh()
        if (-not $process.HasExited) {
            $cleanupAttempted = $true
            Stop-ProcessTree $process.Id $observedIds $observedBirthTicks
            $process.Refresh()
        }
        if ($process.HasExited) {
            $process.WaitForExit()
            $process.Refresh()
            $actualExitCode = [int]$process.ExitCode
        }
        $survivorsAfterCleanup = @(Get-LiveObservedProcessIds $observedIds $observedBirthTicks)
        if ($survivorsAfterCleanup.Count -gt 0) {
            $cleanupAttempted = $true
            Stop-ProcessTree $process.Id $observedIds $observedBirthTicks
            $survivorsAfterCleanup = @(Get-LiveObservedProcessIds $observedIds $observedBirthTicks)
        }
        $cleanupVerified = ($survivorsAfterCleanup.Count -eq 0 -and $process.HasExited)
    }
    catch {
        if ($null -eq $monitorFailure) {
            $monitorFailure = Get-NormalizedOuterMonitorFailure `
                $_.Exception ([string]$currentControlOperation)
        }
        $monitorError = $_.Exception.Message
        if (-not $stopReason) { $stopReason = "CONTROL_PLANE_SUPERVISOR_EXCEPTION" }
    }
    finally {
        if ($processLaunched -and -not $cleanupVerified) {
            $cleanupAttempted = $true
            $currentControlOperation = "control_cleanup_exception"
            try {
                if ($cleanupIdentityComplete) {
                    $process.Refresh()
                    $live = @(Get-LiveObservedProcessIds $observedIds $observedBirthTicks)
                    if ((-not $process.HasExited) -or $live.Count -gt 0) {
                        Stop-ProcessTree $process.Id $observedIds $observedBirthTicks
                    }
                    $process.Refresh()
                    if ($process.HasExited) {
                        $process.WaitForExit()
                        $actualExitCode = [int]$process.ExitCode
                    }
                    $survivorsAfterCleanup = @(Get-LiveObservedProcessIds $observedIds $observedBirthTicks)
                    $cleanupVerified = ($process.HasExited -and $survivorsAfterCleanup.Count -eq 0)
                }
                elseif ($process) {
                    # If native handle or birth-time acquisition failed, the retained
                    # Process object is still the only safe root-termination authority.
                    # Prove that root exit, but fail the cleanup gate because descendants
                    # could not be identity-bound.
                    $fallbackProcessObjectCleanupAttempted = $true
                    $process.Refresh()
                    if (-not $process.HasExited) {
                        $process.Kill()
                    }
                    $process.WaitForExit()
                    $process.Refresh()
                    $fallbackProcessObjectRootExitVerified = [bool]$process.HasExited
                    if (-not $fallbackProcessObjectRootExitVerified) {
                        throw "retained Process object did not reach the exited state"
                    }
                    try { $actualExitCode = [int]$process.ExitCode }
                    catch { $actualExitCode = $null }
                    $cleanupVerified = $false
                    $stopReason = "CONTROL_PROCESS_IDENTITY_ACQUISITION_FAILED"
                }
            }
            catch {
                $cleanupVerified = $false
                $cleanupMessage = $_.Exception.Message
                if ($null -eq $monitorFailure) {
                    $monitorFailure = Get-NormalizedOuterMonitorFailure `
                        $_.Exception ([string]$currentControlOperation)
                }
                $monitorError = if ($monitorError) { $monitorError + "; cleanup failed: " + $cleanupMessage } else { "cleanup failed: " + $cleanupMessage }
                $stopReason = "CONTROL_OBSERVED_PROCESS_TERMINATION_FAILED"
            }
        }
        elseif (-not $processLaunched) {
            $cleanupVerified = $true
        }

        $terminalSnapshotEligible = ((-not $processLaunched) -or $cleanupVerified)
        $stdoutExists = $false
        $stderrExists = $false
        $stdoutBytes = $null
        $stderrBytes = $null
        $stdoutHash = $null
        $stderrHash = $null
        $readyHash = $null
        $startReleaseHash = $null
        $completionHash = $null
        $exitReleaseHash = $null
        if ($terminalSnapshotEligible) {
            $currentControlOperation = "control_provenance_exception"
            try {
                $stdoutExists = Test-Path -LiteralPath $stdout -PathType Leaf
                $stderrExists = Test-Path -LiteralPath $stderr -PathType Leaf
                $stdoutBytes = if ($stdoutExists) { [int64](Get-Item -LiteralPath $stdout).Length } else { $null }
                $stderrBytes = if ($stderrExists) { [int64](Get-Item -LiteralPath $stderr).Length } else { $null }
                $stdoutHash = if ($stdoutExists) { Get-Sha256 $stdout } else { $null }
                $stderrHash = if ($stderrExists) { Get-Sha256 $stderr } else { $null }
                $readyHash = if (Test-Path -LiteralPath $ready -PathType Leaf) { Get-Sha256 $ready } else { $null }
                $startReleaseHash = if (Test-Path -LiteralPath $startRelease -PathType Leaf) { Get-Sha256 $startRelease } else { $null }
                $completionHash = if (Test-Path -LiteralPath $completion -PathType Leaf) { Get-Sha256 $completion } else { $null }
                $exitReleaseHash = if (Test-Path -LiteralPath $exitRelease -PathType Leaf) { Get-Sha256 $exitRelease } else { $null }
                $attemptProvenanceAfter = Get-AttemptArtifactProvenance $AttemptArtifactPaths
                $attemptProvenanceAfterJson = ConvertTo-Json -InputObject $attemptProvenanceAfter -Depth 6 -Compress
                $attemptProvenanceAfterHash = Get-Utf8TextSha256 $attemptProvenanceAfterJson
            }
            catch {
                $provenanceMessage = $_.Exception.Message
                if ($null -eq $monitorFailure) {
                    $monitorFailure = Get-NormalizedOuterMonitorFailure `
                        $_.Exception ([string]$currentControlOperation)
                }
                $monitorError = if ($monitorError) { $monitorError + "; terminal artifact/provenance snapshot failed: " + $provenanceMessage } else { "terminal artifact/provenance snapshot failed: " + $provenanceMessage }
                if (-not $stopReason) { $stopReason = "CONTROL_ATTEMPT_PROVENANCE_FAILED" }
            }
        }
        else {
            if (-not $stopReason) { $stopReason = "CONTROL_OBSERVED_PROCESS_TERMINATION_FAILED" }
            if ($null -eq $monitorFailure) {
                $terminalSnapshotException = New-Object System.InvalidOperationException(
                    "terminal snapshot refused before verified termination"
                )
                $monitorFailure = Get-NormalizedOuterMonitorFailure `
                    $terminalSnapshotException "control_provenance_exception"
            }
            $monitorError = if ($monitorError) { $monitorError + "; terminal snapshot refused before verified termination" } else { "terminal snapshot refused before verified termination" }
        }

        $exitAllowed = ($null -ne $actualExitCode -and $allowed -contains [int]$actualExitCode)
        $reportedExitMatches = ($null -ne $reportedExitCode -and $null -ne $actualExitCode -and [int]$reportedExitCode -eq [int]$actualExitCode)
        $monitorOkBeforeEnvelopeClose = (
            -not $stopReason -and -not $monitorError -and $null -eq $monitorFailure -and
            $processHandleAcquired -and
            $successfulSamples -ge 2 -and $targetVisibleSamples -ge 2 -and
            $readyObserved -and $startReleased -and $completionObserved -and $exitReleased -and
            $cleanupVerified -and $terminalSnapshotEligible
        )
        $canonicalControlTreeSampleRetryEventsBeforeEnvelopeClose = @()
        foreach ($event in @($controlTreeSampleDiagnostics.retry_events)) {
            $canonicalControlTreeSampleRetryEventsBeforeEnvelopeClose += (New-TreeSampleEvidence `
                ([int]$event.attempt) $event.confirmation ([string]$event.context) `
                $event.expected_birth_utc_ticks ([string]$event.message_code) `
                $event.observed_birth_utc_ticks ([string]$event.operation) `
                $event.process_id $event.process_role $event.win32_error_code)
        }
        $reportObservedProcessIdentities = @(Get-ObservedProcessIdentities $monitorObservedBirthTicks)
        $reportObservedBirthByProcessId = @{}
        foreach ($identity in $reportObservedProcessIdentities) {
            $reportObservedBirthByProcessId[[int]$identity.process_id] = [int64]$identity.birth_utc_ticks
        }
        $reportTreeSampleConfirmedDisappearanceCount = [int]$controlTreeSampleDiagnostics.confirmed_disappearance_count
        $reportTreeSampleRetryEventCount = [int]$canonicalControlTreeSampleRetryEventsBeforeEnvelopeClose.Count
        $report = [ordered]@{
            schema = $controlPlaneSchema
            program = $program
            case_id = "AV-BS1-CIRCLE-PRIMARY"
            stage = "control-plane"
            operation = $Operation
            invocation_id = $invocationId
            report_path = [IO.Path]::GetFullPath($reportPath)
            runner_path = $runnerScriptPath
            runner_sha256 = Get-Sha256 $runnerScriptPath
            python_path = [IO.Path]::GetFullPath($pythonPath)
            python_sha256 = Get-Sha256 $pythonPath
            bootstrap_path = [IO.Path]::GetFullPath($controlPlaneBootstrapPath)
            bootstrap_sha256 = Get-Sha256 $controlPlaneBootstrapPath
            target_script_path = $target
            target_script_sha256 = Get-Sha256 $target
            target_argv = @($targetArgv)
            target_argv_json_sha256 = $targetArgvHash
            process_argv = @($processArgv)
            process_argv_json_sha256 = $processArgvHash
            attempt_provenance_before = $attemptProvenanceBefore
            attempt_provenance_before_sha256 = $attemptProvenanceBeforeHash
            attempt_provenance_after = $attemptProvenanceAfter
            attempt_provenance_after_sha256 = $attemptProvenanceAfterHash
            execution_resource_scope_sha256 = $script:executionResourceScopeSha256
            execution_resource_scope_binding_semantics = $scopeBindingSemantics
            factor_fit_interval_included = $false
            simultaneous_current_interval_semantics = "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_during_this_control_invocation"
            summed_os_lifetime_peak_semantics = "conservative_stop_gate_includes_outer_and_inner_work_before_this_control_invocation_not_invocation_only_peak"
            authorization_effect = "none_control_plane_evidence_only"
            previous_session_index_path = $script:controlPlaneLatestIndexPath
            previous_session_index_sha256 = $script:controlPlaneLatestIndexSha256
            monitor_kind = "Win32_outer_observer_plus_inner_runner_plus_identity_bound_sampled_python_tree_Toolhelp32_Psapi_100ms_with_completion_handshake"
            process_membership_semantics = "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_identity_bound_helper_tree_descendants_between_samples_not_claimed"
            cleanup_scope = "inner_owned_identity_bound_helper_tree_or_retained_Process_object_root_fallback_outer_observer_and_inner_runner_excluded_no_unobserved_descendant_or_external_kill_guarantee"
            resource_envelope_status = "pending_envelope_close"
            resource_envelope_included_work = @(
                "attempt_provenance_before",
                "helper_spawn_identity_sampling_handshake_and_cleanup",
                "stdout_stderr_marker_hashes",
                "attempt_provenance_after",
                "process_report_materialization_and_hash",
                "preclose_session_index_materialization_and_hash",
                "runner_lifetime_peak_and_system_sample_after_preclose_index"
            )
            resource_envelope_excluded_work = @(
                "envelope_close_materialization_and_hash",
                "final_session_index_materialization_and_hash",
                "stdout_return_text_read_and_return_packaging",
                "caller_side_processing_after_return",
                "session_bootstrap_and_helper_script_materialization_before_first_invocation",
                "candidate_scope_seed_read_before_preflight_invocation",
                "runner_emergency_token_replacement_after_failed_consumer_invocation",
                "pre_exit_intent_evidence_materialization",
                "temporary_attempt_evidence_cleanup_or_quarantine",
                "unobserved_descendants_created_and_exited_between_100ms_samples",
                "external_runner_or_machine_kill"
            )
            report_and_index_required_for_invocation_success = $true
            gate_is_authoritative_only_in_envelope_close = $true
            envelope_close_path = [IO.Path]::GetFullPath($envelopeClosePath)
            envelope_close_sha256 = $null
            poll_interval_ms = $pollMilliseconds
            wall_stop_seconds = $controlPlaneWallStopSeconds
            stream_stop_bytes_each = $streamStopBytes
            thresholds = [ordered]@{
                tree_ws_stop_bytes = $treeWorkingSetStop
                tree_private_stop_bytes = $treePrivateStop
                tree_commit_stop_bytes = $treeCommitStop
                commit_headroom_floor_bytes = $commitHeadroomFloor
                available_physical_floor_bytes = $availablePhysicalFloor
                high_pre_post_commit_headroom_floor_bytes = $minimumCommitHeadroomBeforeSpawn
                high_pre_post_available_physical_floor_bytes = $minimumAvailablePhysicalBeforeSpawn
            }
            pre_spawn_system = $preSpawnSystem
            pre_helper_after_provenance_tree = $preHelperTreeAfterProvenance
            pre_helper_after_provenance_system = $preHelperSystemAfterProvenance
            final_system = $null
            high_system_floor_recheck_before_and_after = $false
            high_system_floor_recheck_authoritative_in_envelope_close = $true
            started_utc = $startedUtc.ToString("o")
            ended_utc = $null
            wall_seconds = $null
            wall_clock_kind = "System.Diagnostics.Stopwatch"
            process_id = if ($process) { [int]$process.Id } else { $null }
            process_birth_utc_ticks = $processBirthTicks
            process_handle_acquired = $processHandleAcquired
            execution_tree_root_pid = $monitorRootProcessId
            execution_tree_root_birth_utc_ticks = $monitorRootBirthTicks
            execution_tree_includes_runner = $true
            inner_runner_pid = [int]$PID
            inner_runner_birth_utc_ticks = [int64]$supervisorBirthTicks
            observed_process_ids = @($monitorObservedIds | Sort-Object)
            observed_process_identities = @($reportObservedProcessIdentities)
            cleanup_observed_process_ids = @($observedIds | Sort-Object)
            cleanup_observed_process_identities = @(Get-ObservedProcessIdentities $observedBirthTicks)
            successful_tree_sample_count = $successfulSamples
            target_visible_tree_sample_count = $targetVisibleSamples
            tree_sample_max_attempts = [int]$treeSampleMaximumAttempts
            tree_sample_confirmed_disappearance_count = $reportTreeSampleConfirmedDisappearanceCount
            tree_sample_retry_events = @($canonicalControlTreeSampleRetryEventsBeforeEnvelopeClose)
            tree_sample_retry_events_truncated = [bool]$controlTreeSampleDiagnostics.retry_events_truncated
            peak = $peak
            bootstrap_ready_sha256 = $readyHash
            start_release_sha256 = $startReleaseHash
            target_complete_sha256 = $completionHash
            target_exit_evidence_path = [IO.Path]::GetFullPath($completion)
            target_exit_evidence_sha256 = $completionHash
            exit_release_sha256 = $exitReleaseHash
            handshake_complete = ($readyObserved -and $startReleased -and $completionObserved -and $exitReleased)
            reported_exit_code = $reportedExitCode
            actual_exit_code = $actualExitCode
            allowed_exit_codes = @($allowed)
            exit_code_allowed = $exitAllowed
            reported_exit_matches_actual = $reportedExitMatches
            stdout_path = [IO.Path]::GetFullPath($stdout)
            stdout_bytes = $stdoutBytes
            stdout_sha256 = $stdoutHash
            stderr_path = [IO.Path]::GetFullPath($stderr)
            stderr_bytes = $stderrBytes
            stderr_sha256 = $stderrHash
            monitor_ok = $false
            monitor_ok_before_envelope_close = $monitorOkBeforeEnvelopeClose
            monitor_error = $monitorError
            monitor_failure = $monitorFailure
            stop_reason = $stopReason
            cleanup_attempted = $cleanupAttempted
            cleanup_verified = $cleanupVerified
            cleanup_identity_complete = $cleanupIdentityComplete
            fallback_process_object_cleanup_attempted = $fallbackProcessObjectCleanupAttempted
            fallback_process_object_root_exit_verified = $fallbackProcessObjectRootExitVerified
            observed_survivors_after_cleanup = @($survivorsAfterCleanup)
            terminal_system_sample_after_verified_termination_or_no_spawn = $false
            mandatory_control_plane_gate_pass = $false
        }
        Write-AtomicUtf8NoBom $reportPath ($report | ConvertTo-Json -Depth 12 -Compress)
        $reportHash = Get-Sha256 $reportPath
        if ($null -ne $controlPlaneReportReferences) {
            $currentReportReferenceIndex = $controlPlaneReportReferences.Count
            [void]$controlPlaneReportReferences.Add([ordered]@{
                operation = $Operation
                invocation_id = $invocationId
                report_path = [IO.Path]::GetFullPath($reportPath)
                report_sha256 = $reportHash
                target_argv_json_sha256 = $targetArgvHash
                process_argv_json_sha256 = $processArgvHash
                attempt_provenance_before_sha256 = $attemptProvenanceBeforeHash
                attempt_provenance_after_sha256 = $attemptProvenanceAfterHash
                envelope_close_path = $null
                envelope_close_sha256 = $null
                mandatory_control_plane_gate_pass = $false
            })
            $script:controlPlaneIndexSequence += 1
            $preCloseIndexLeaf = "session-index-{0:D4}.json" -f $script:controlPlaneIndexSequence
            $preCloseIndexPath = [IO.Path]::GetFullPath((Join-Path $controlPlaneSessionRoot $preCloseIndexLeaf))
            $preCloseIndexPayload = [ordered]@{
                schema = "AV-BS1-h4-p0r-control-plane-session-index-v1"
                program = $program
                case_id = "AV-BS1-CIRCLE-PRIMARY"
                session_id = [IO.Path]::GetFileName($controlPlaneSessionRoot)
                sequence = $script:controlPlaneIndexSequence
                index_role = "preclose_in_resource_envelope"
                previous_index_path = $script:controlPlaneLatestIndexPath
                previous_index_sha256 = $script:controlPlaneLatestIndexSha256
                runner_sha256 = Get-Sha256 $runnerScriptPath
                bootstrap_sha256 = Get-Sha256 $controlPlaneBootstrapPath
                canonical_hash_helper_sha256 = Get-Sha256 $controlPlaneCanonicalHashPath
                execution_resource_scope_sha256 = $script:executionResourceScopeSha256
                execution_resource_scope_binding_semantics = $scopeBindingSemantics
                report_references = @($controlPlaneReportReferences)
                envelope_close_path = [IO.Path]::GetFullPath($envelopeClosePath)
                envelope_close_sha256 = $null
                mandatory_control_plane_gate_pass = $false
                this_index_materialization_inside_resource_envelope = $true
                consume_after_every_owned_claim_outcome = $false
                external_runner_or_machine_kill_terminal_state_guaranteed = $false
            }
            Write-AtomicUtf8NoBom $preCloseIndexPath ($preCloseIndexPayload | ConvertTo-Json -Depth 10 -Compress)
            $preCloseIndexSha256 = Get-Sha256 $preCloseIndexPath
        }

        try {
            $currentControlOperation = "control_supervisor_exception"
            $currentControlTreeSampleContext = "control_envelope_close_tree_sample"
            if (-not $terminalSnapshotEligible) {
                throw "verified termination or no-spawn state is required before envelope close"
            }
            $closeTree = Get-TreeSample `
                $monitorRootProcessId $monitorRootBirthTicks `
                $monitorObservedIds $monitorObservedBirthTicks `
                $currentControlTreeSampleContext $controlTreeSampleDiagnostics `
                $null $treeSampleMaximumAttempts
            $finalSystem = Get-SystemSample
            Update-ControlPlanePeak $peak $closeTree $finalSystem
            $terminalSystemSampleAfterVerifiedTerminationOrNoSpawn = $true
            if (-not $stopReason) {
                if ($closeTree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_WS_STOP" }
                elseif ($closeTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_WS_STOP" }
                elseif ($closeTree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "CONTROL_TREE_PRIVATE_STOP" }
                elseif ($closeTree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_COMMIT_STOP" }
                elseif ($closeTree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "CONTROL_TREE_LIFETIME_PEAK_COMMIT_STOP" }
                elseif ($finalSystem.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) { $stopReason = "CONTROL_POSTEVIDENCE_COMMIT_HEADROOM_STOP" }
                elseif ($finalSystem.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) { $stopReason = "CONTROL_POSTEVIDENCE_AVAILABLE_PHYSICAL_STOP" }
            }
        }
        catch {
            if ($null -eq $monitorFailure) {
                $monitorFailure = Get-NormalizedOuterMonitorFailure `
                    $_.Exception ([string]$currentControlOperation)
            }
            $finalMessage = $_.Exception.Message
            $monitorError = if ($monitorError) { $monitorError + "; envelope-close sample failed: " + $finalMessage } else { "envelope-close sample failed: " + $finalMessage }
            if (-not $stopReason) { $stopReason = "CONTROL_FINAL_SYSTEM_SAMPLE_FAILED" }
        }
        if ($stopwatch.IsRunning) { $stopwatch.Stop() }
        $wallSeconds = [double]$stopwatch.Elapsed.TotalSeconds
        $endedUtc = [DateTimeOffset]::UtcNow
        $postHighFloorsPass = (
            $terminalSystemSampleAfterVerifiedTerminationOrNoSpawn -and
            $null -ne $finalSystem -and
            $finalSystem.commit_headroom_bytes -ge $minimumCommitHeadroomBeforeSpawn -and
            $finalSystem.available_physical_bytes -ge $minimumAvailablePhysicalBeforeSpawn
        )
        $highPrePostFloorsPass = (
            $null -ne $preSpawnSystem -and
            $preSpawnSystem.commit_headroom_bytes -ge $minimumCommitHeadroomBeforeSpawn -and
            $preSpawnSystem.available_physical_bytes -ge $minimumAvailablePhysicalBeforeSpawn -and
            $null -ne $preHelperSystemAfterProvenance -and
            $preHelperSystemAfterProvenance.commit_headroom_bytes -ge $minimumCommitHeadroomBeforeSpawn -and
            $preHelperSystemAfterProvenance.available_physical_bytes -ge $minimumAvailablePhysicalBeforeSpawn -and
            $postHighFloorsPass
        )
        $canonicalControlTreeSampleRetryEvents = @()
        foreach ($event in @($controlTreeSampleDiagnostics.retry_events)) {
            $canonicalControlTreeSampleRetryEvents += (New-TreeSampleEvidence `
                ([int]$event.attempt) $event.confirmation ([string]$event.context) `
                $event.expected_birth_utc_ticks ([string]$event.message_code) `
                $event.observed_birth_utc_ticks ([string]$event.operation) `
                $event.process_id $event.process_role $event.win32_error_code)
        }
        $closeOnlyRetryIdentityCoveragePass = $true
        $finalTreeSampleConfirmedDisappearanceCount = [int]$controlTreeSampleDiagnostics.confirmed_disappearance_count
        $finalTreeSampleRetryEventCount = [int]$canonicalControlTreeSampleRetryEvents.Count
        $controlTreeSampleRetryEvidenceComplete = (
            -not [bool]$controlTreeSampleDiagnostics.retry_events_truncated -and
            $finalTreeSampleConfirmedDisappearanceCount -eq $finalTreeSampleRetryEventCount
        )
        if (-not $controlTreeSampleRetryEvidenceComplete) {
            $retryEvidenceMessage = "BLOCKED_AV_BS_RESOURCE: control tree-sample retry evidence is incomplete"
            if ($null -eq $monitorFailure) {
                $monitorFailure = New-TreeSampleEvidence `
                    1 $null "control_provenance_exception" $null `
                    "CONTROL_TREE_SAMPLE_RETRY_EVIDENCE_INCOMPLETE" $null `
                    "control_provenance_exception" $null $null $null
            }
            $monitorError = if ($monitorError) { $monitorError + "; " + $retryEvidenceMessage } else { $retryEvidenceMessage }
            if (-not $stopReason) { $stopReason = "CONTROL_TREE_SAMPLE_RETRY_EVIDENCE_INCOMPLETE" }
        }
        $closeOnlyConfirmedDisappearanceCount = $finalTreeSampleConfirmedDisappearanceCount - $reportTreeSampleConfirmedDisappearanceCount
        $closeOnlyStoredRetryEventCount = $finalTreeSampleRetryEventCount - $reportTreeSampleRetryEventCount
        if (
            $closeOnlyConfirmedDisappearanceCount -lt 0 -or
            $closeOnlyStoredRetryEventCount -lt 0 -or
            $closeOnlyConfirmedDisappearanceCount -ne $closeOnlyStoredRetryEventCount
        ) {
            $closeOnlyRetryIdentityCoveragePass = $false
        }
        if ($closeOnlyRetryIdentityCoveragePass) {
            for ($eventIndex = 0; $eventIndex -lt $reportTreeSampleRetryEventCount; $eventIndex += 1) {
                if (-not (Test-StrictJsonValueEqual `
                    $canonicalControlTreeSampleRetryEventsBeforeEnvelopeClose[$eventIndex] `
                    $canonicalControlTreeSampleRetryEvents[$eventIndex])) {
                    $closeOnlyRetryIdentityCoveragePass = $false
                    break
                }
            }
        }
        if ($closeOnlyRetryIdentityCoveragePass) {
            for ($eventIndex = $reportTreeSampleRetryEventCount; $eventIndex -lt $finalTreeSampleRetryEventCount; $eventIndex += 1) {
                $closeEvent = $canonicalControlTreeSampleRetryEvents[$eventIndex]
                $closeEventProcessId = $closeEvent.process_id
                $closeEventExpectedBirth = $closeEvent.expected_birth_utc_ticks
                $closeEventObservedBirth = $closeEvent.observed_birth_utc_ticks
                if (
                    $closeEvent.context -ne "control_envelope_close_tree_sample" -or
                    $closeEvent.message_code -ne "CONFIRMED_NONROOT_DISAPPEARANCE" -or
                    $closeEventProcessId -isnot [int] -or
                    -not $reportObservedBirthByProcessId.ContainsKey([int]$closeEventProcessId) -or
                    ($null -eq $closeEventExpectedBirth -and $null -eq $closeEventObservedBirth) -or
                    ($null -ne $closeEventExpectedBirth -and [int64]$closeEventExpectedBirth -ne [int64]$reportObservedBirthByProcessId[[int]$closeEventProcessId]) -or
                    ($null -ne $closeEventObservedBirth -and [int64]$closeEventObservedBirth -ne [int64]$reportObservedBirthByProcessId[[int]$closeEventProcessId])
                ) {
                    $closeOnlyRetryIdentityCoveragePass = $false
                    break
                }
            }
        }
        if (-not $closeOnlyRetryIdentityCoveragePass) {
            $coverageMessage = "BLOCKED_AV_BS_RESOURCE: envelope-close retry identity is not covered by frozen report identities"
            if ($null -eq $monitorFailure) {
                $monitorFailure = New-TreeSampleEvidence `
                    1 $null "control_provenance_exception" $null `
                    "CONTROL_ENVELOPE_CLOSE_RETRY_IDENTITY_UNCOVERED" $null `
                    "control_provenance_exception" $null $null $null
            }
            $monitorError = if ($monitorError) { $monitorError + "; " + $coverageMessage } else { $coverageMessage }
            if (-not $stopReason) { $stopReason = "CONTROL_ENVELOPE_CLOSE_RETRY_IDENTITY_UNCOVERED" }
        }
        $monitorOk = (
            $monitorOkBeforeEnvelopeClose -and -not $stopReason -and -not $monitorError -and
            $null -eq $monitorFailure -and $controlTreeSampleRetryEvidenceComplete -and
            $closeOnlyRetryIdentityCoveragePass -and
            $terminalSystemSampleAfterVerifiedTerminationOrNoSpawn
        )
        $mandatoryGate = (
            $monitorOk -and $exitAllowed -and $reportedExitMatches -and $highPrePostFloorsPass -and
            $stdoutExists -and $stderrExists -and
            $stdoutBytes -le $streamStopBytes -and $stderrBytes -le $streamStopBytes -and
            $wallSeconds -le $controlPlaneWallStopSeconds -and
            $peak.tree_working_set_bytes -le $treeWorkingSetStop -and
            $peak.tree_summed_process_lifetime_peak_working_set_bytes -le $treeWorkingSetStop -and
            $peak.tree_private_commit_bytes -le $treePrivateStop -and
            $peak.tree_committed_pagefile_bytes -le $treeCommitStop -and
            $peak.tree_summed_process_lifetime_peak_commit_bytes -le $treeCommitStop -and
            $peak.system_commit_headroom_min_bytes -ge $commitHeadroomFloor -and
            $peak.available_physical_min_bytes -ge $availablePhysicalFloor
        )
        $envelopeClose = [ordered]@{
            schema = $controlPlaneEnvelopeCloseSchema
            program = $program
            case_id = "AV-BS1-CIRCLE-PRIMARY"
            stage = "control-plane"
            operation = $Operation
            invocation_id = $invocationId
            report_path = [IO.Path]::GetFullPath($reportPath)
            report_sha256 = $reportHash
            preclose_session_index_path = $preCloseIndexPath
            preclose_session_index_sha256 = $preCloseIndexSha256
            target_argv_json_sha256 = $targetArgvHash
            process_argv_json_sha256 = $processArgvHash
            attempt_provenance_before_sha256 = $attemptProvenanceBeforeHash
            attempt_provenance_after_sha256 = $attemptProvenanceAfterHash
            execution_resource_scope_sha256 = $script:executionResourceScopeSha256
            execution_resource_scope_binding_semantics = $scopeBindingSemantics
            resource_envelope_includes_report_and_preclose_index_work = $true
            resource_envelope_excluded_tail = @(
                "this_envelope_close_materialization_and_hash",
                "final_session_index_materialization_and_hash",
                "stdout_return_text_read_and_return_packaging",
                "caller_side_processing_after_return",
                "session_bootstrap_and_helper_script_materialization_before_first_invocation",
                "candidate_scope_seed_read_before_preflight_invocation",
                "runner_emergency_token_replacement_after_failed_consumer_invocation",
                "pre_exit_intent_evidence_materialization",
                "temporary_attempt_evidence_cleanup_or_quarantine"
            )
            process_membership_semantics = "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_helper_descendants_created_and_exited_between_samples_not_claimed"
            simultaneous_current_interval_semantics = "sampled_current_outer_observer_plus_inner_runner_plus_helper_tree_during_this_control_invocation"
            summed_os_lifetime_peak_semantics = "conservative_stop_gate_includes_outer_and_inner_work_before_this_control_invocation_not_invocation_only_peak"
            thresholds = [ordered]@{
                tree_ws_stop_bytes = $treeWorkingSetStop
                tree_private_stop_bytes = $treePrivateStop
                tree_commit_stop_bytes = $treeCommitStop
                commit_headroom_floor_bytes = $commitHeadroomFloor
                available_physical_floor_bytes = $availablePhysicalFloor
                high_pre_post_commit_headroom_floor_bytes = $minimumCommitHeadroomBeforeSpawn
                high_pre_post_available_physical_floor_bytes = $minimumAvailablePhysicalBeforeSpawn
            }
            pre_spawn_system = $preSpawnSystem
            pre_helper_after_provenance_system = $preHelperSystemAfterProvenance
            high_system_floor_recheck_before_and_after = $highPrePostFloorsPass
            terminal_system_sample_after_verified_termination_or_no_spawn = $terminalSystemSampleAfterVerifiedTerminationOrNoSpawn
            envelope_close_runner_tree_sample = $closeTree
            final_system = $finalSystem
            peak = $peak
            tree_sample_max_attempts = [int]$treeSampleMaximumAttempts
            tree_sample_confirmed_disappearance_count = [int]$controlTreeSampleDiagnostics.confirmed_disappearance_count
            tree_sample_retry_events = @($canonicalControlTreeSampleRetryEvents)
            tree_sample_retry_events_truncated = [bool]$controlTreeSampleDiagnostics.retry_events_truncated
            monitor_ok = $monitorOk
            monitor_error = $monitorError
            monitor_failure = $monitorFailure
            stop_reason = $stopReason
            cleanup_verified = $cleanupVerified
            started_utc = $startedUtc.ToString("o")
            ended_utc = $endedUtc.ToString("o")
            wall_seconds = $wallSeconds
            wall_clock_kind = "System.Diagnostics.Stopwatch_frozen_once_before_close_materialization"
            wall_stop_seconds = $controlPlaneWallStopSeconds
            mandatory_control_plane_gate_pass = $mandatoryGate
            authorization_effect = "bounded_control_plane_evidence_only"
            consume_after_every_owned_claim_outcome = $false
            external_runner_or_machine_kill_terminal_state_guaranteed = $false
        }
        Write-AtomicUtf8NoBom $envelopeClosePath ($envelopeClose | ConvertTo-Json -Depth 12 -Compress)
        $envelopeCloseSha256 = Get-Sha256 $envelopeClosePath

        if ($null -ne $controlPlaneReportReferences) {
            $controlPlaneReportReferences[$currentReportReferenceIndex] = [ordered]@{
                operation = $Operation
                invocation_id = $invocationId
                report_path = [IO.Path]::GetFullPath($reportPath)
                report_sha256 = $reportHash
                target_argv_json_sha256 = $targetArgvHash
                process_argv_json_sha256 = $processArgvHash
                attempt_provenance_before_sha256 = $attemptProvenanceBeforeHash
                attempt_provenance_after_sha256 = $attemptProvenanceAfterHash
                envelope_close_path = [IO.Path]::GetFullPath($envelopeClosePath)
                envelope_close_sha256 = $envelopeCloseSha256
                mandatory_control_plane_gate_pass = $mandatoryGate
            }
            $script:controlPlaneIndexSequence += 1
            $finalIndexLeaf = "session-index-{0:D4}.json" -f $script:controlPlaneIndexSequence
            $finalIndexPath = [IO.Path]::GetFullPath((Join-Path $controlPlaneSessionRoot $finalIndexLeaf))
            $finalIndexPayload = [ordered]@{
                schema = "AV-BS1-h4-p0r-control-plane-session-index-v1"
                program = $program
                case_id = "AV-BS1-CIRCLE-PRIMARY"
                session_id = [IO.Path]::GetFileName($controlPlaneSessionRoot)
                sequence = $script:controlPlaneIndexSequence
                index_role = "final_binds_resource_envelope_close"
                previous_index_path = $preCloseIndexPath
                previous_index_sha256 = $preCloseIndexSha256
                runner_sha256 = Get-Sha256 $runnerScriptPath
                bootstrap_sha256 = Get-Sha256 $controlPlaneBootstrapPath
                canonical_hash_helper_sha256 = Get-Sha256 $controlPlaneCanonicalHashPath
                execution_resource_scope_sha256 = $script:executionResourceScopeSha256
                execution_resource_scope_binding_semantics = $scopeBindingSemantics
                report_references = @($controlPlaneReportReferences)
                envelope_close_path = [IO.Path]::GetFullPath($envelopeClosePath)
                envelope_close_sha256 = $envelopeCloseSha256
                mandatory_control_plane_gate_pass = $mandatoryGate
                this_index_materialization_inside_resource_envelope = $false
                consume_after_every_owned_claim_outcome = $false
                external_runner_or_machine_kill_terminal_state_guaranteed = $false
            }
            Write-AtomicUtf8NoBom $finalIndexPath ($finalIndexPayload | ConvertTo-Json -Depth 10 -Compress)
            $script:controlPlaneLatestIndexPath = $finalIndexPath
            $script:controlPlaneLatestIndexSha256 = Get-Sha256 $finalIndexPath
        }
        if ($stdoutExists -and $stdoutBytes -le $streamStopBytes) {
            $stdoutText = Get-Content -LiteralPath $stdout -Raw -Encoding utf8
        }
    }

    if (-not $mandatoryGate) {
        throw (
            "BLOCKED_AV_BS_RESOURCE: bounded control-plane operation failed: " +
            $Operation + "; report=" + $reportPath
        )
    }
    return [pscustomobject]@{
        operation = $Operation
        stdout_text = $stdoutText
        exit_code = [int]$actualExitCode
        report_path = [IO.Path]::GetFullPath($reportPath)
        report_sha256 = $reportHash
        session_index_path = $script:controlPlaneLatestIndexPath
        session_index_sha256 = $script:controlPlaneLatestIndexSha256
        envelope_close_path = [IO.Path]::GetFullPath($envelopeClosePath)
        envelope_close_sha256 = $envelopeCloseSha256
    }
}

function Get-OuterObserverSessionPaths([string]$SessionPath, [string]$ExpectedNonce) {
    $root = [IO.Path]::GetFullPath((Join-Path $validationRoot "outer-observer"))
    $session = [IO.Path]::GetFullPath($SessionPath)
    $leaf = [IO.Path]::GetFileName($session)
    $expectedRelativePath = "validation-output/av-bs1/outer-observer/session-" + $ExpectedNonce
    if (
        $session -cne $SessionPath -or
        -not $session.StartsWith(($root.TrimEnd('\') + '\'), [StringComparison]::Ordinal) -or
        [IO.Path]::GetDirectoryName($session).TrimEnd('\') -cne $root.TrimEnd('\') -or
        $ExpectedNonce -notmatch '^[0-9a-f]{32}$' -or
        $leaf -cne ("session-" + $ExpectedNonce) -or
        (Get-RepositoryRelativePath $session) -cne $expectedRelativePath
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer session path is not canonical"
    }
    if (
        -not (Test-Path -LiteralPath $session -PathType Container) -or
        [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $session).ProviderPath) -cne $session
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer session on-disk path is not canonical"
    }
    return [pscustomobject]@{
        root = $root
        session = $session
        session_relative_path = Get-RepositoryRelativePath $session
        ready = Join-Path $session "inner-ready.json"
        start_release = Join-Path $session "outer-start-release.json"
        complete = Join-Path $session "inner-complete.json"
        exit_release = Join-Path $session "outer-exit-release.json"
        envelope_close = Join-Path $session "outer-resource-envelope-close.json"
        stdout = Join-Path $session "inner.stdout.txt"
        stderr = Join-Path $session "inner.stderr.txt"
    }
}

function Resolve-RepositoryRelativePath([string]$RelativePath) {
    if (
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.Contains('\') -or
        $RelativePath.StartsWith('/') -or
        $RelativePath.EndsWith('/')
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: repository-relative evidence path is invalid"
    }
    foreach ($segment in @($RelativePath.Split('/'))) {
        if ([string]::IsNullOrEmpty($segment) -or $segment -in @('.', '..')) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: repository-relative evidence path contains an invalid segment"
        }
    }
    $full = [IO.Path]::GetFullPath((Join-Path $repositoryRoot ($RelativePath.Replace('/', '\'))))
    $rootPrefix = $repositoryRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: repository-relative evidence path escaped the checkout"
    }
    if ((Get-RepositoryRelativePath $full) -cne $RelativePath) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: repository-relative evidence path case or round-trip mismatch"
    }
    if (Test-Path -LiteralPath $full) {
        $resolvedExisting = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $full).ProviderPath)
        if (
            -not $resolvedExisting.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            (Get-RepositoryRelativePath $resolvedExisting) -cne $RelativePath
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: repository-relative evidence path target or on-disk case mismatch"
        }
        return $resolvedExisting
    }
    return $full
}

function Get-OuterObserverTerminalSealPath(
    [string]$ReviewTokenId,
    [string]$ExpectedRelativePath
) {
    if ($ReviewTokenId -notmatch '^[0-9a-f]{32}$') {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer review token id is invalid"
    }
    $expected = "validation-output/av-bs1/outer-observer-terminal-seals/" + $ReviewTokenId + ".json"
    if ($ExpectedRelativePath -cne $expected) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer terminal seal relative path mismatch"
    }
    $root = [IO.Path]::GetFullPath((Join-Path $validationRoot "outer-observer-terminal-seals"))
    $path = [IO.Path]::GetFullPath((Join-Path $repositoryRoot ($ExpectedRelativePath.Replace('/', '\'))))
    if (
        -not $path.StartsWith(($root.TrimEnd('\') + '\'), [StringComparison]::Ordinal) -or
        [IO.Path]::GetDirectoryName($path).TrimEnd('\') -cne $root.TrimEnd('\') -or
        [IO.Path]::GetFileName($path) -cne ($ReviewTokenId + ".json") -or
        (Get-RepositoryRelativePath $path) -cne $ExpectedRelativePath
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer terminal seal path escaped its root"
    }
    return [pscustomobject]@{ root = $root; path = $path; relative_path = $expected }
}

function New-OuterObserverHandshakePrefix(
    [string]$ReadyRelativePath,
    [string]$ReadySha256,
    [string]$StartReleaseRelativePath,
    [string]$StartReleaseSha256
) {
    foreach ($hash in @($ReadySha256, $StartReleaseSha256)) {
        if ($hash -notmatch '^[0-9a-f]{64}$') {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer handshake prefix hash is invalid"
        }
    }
    # Keys are inserted in ordinal lexical order. With these ASCII-only scalar
    # values, compressed ConvertTo-Json bytes equal the Python canonical-JSON
    # encoding (sorted keys, UTF-8, no insignificant whitespace).
    $value = [ordered]@{
        inner_ready_relative_path = $ReadyRelativePath
        inner_ready_sha256 = $ReadySha256
        outer_start_release_relative_path = $StartReleaseRelativePath
        outer_start_release_sha256 = $StartReleaseSha256
        schema = $outerHandshakePrefixSchema
    }
    $json = $value | ConvertTo-Json -Depth 4 -Compress
    return [pscustomobject]@{ value = $value; sha256 = Get-Utf8TextSha256 $json }
}

function Get-OuterObserverFailureCode([string]$StopReason) {
    # Keep this exact allowlist as the single category authority for both the
    # emergency tombstone and the public no-seal failure prefix. Unknown,
    # handshake, schema, exit, and token-classification states fail as schema.
    $resourceStopReasons = @(
        "OUTER_PRESPAWN_COMMIT_HEADROOM_STOP",
        "OUTER_PRESPAWN_AVAILABLE_PHYSICAL_STOP",
        "OUTER_TREE_WS_STOP",
        "OUTER_TREE_LIFETIME_PEAK_WS_STOP",
        "OUTER_TREE_PRIVATE_STOP",
        "OUTER_TREE_COMMIT_STOP",
        "OUTER_TREE_LIFETIME_PEAK_COMMIT_STOP",
        "OUTER_SYSTEM_COMMIT_HEADROOM_STOP",
        "OUTER_AVAILABLE_PHYSICAL_STOP",
        "OUTER_WALL_TIME_STOP",
        "OUTER_STDOUT_SIZE_STOP",
        "OUTER_STDERR_SIZE_STOP",
        "OUTER_INNER_CLEANUP_FAILED",
        "OUTER_POSTEXIT_COMMIT_HEADROOM_STOP",
        "OUTER_POSTEXIT_AVAILABLE_PHYSICAL_STOP",
        "OUTER_TERMINAL_SAMPLE_FAILED",
        "OUTER_RESOURCE_EXCEPTION"
    )
    if ($StopReason -in $resourceStopReasons) {
        return "BLOCKED_AV_BS_RESOURCE"
    }
    return "BLOCKED_AV_BS_RESULT_SCHEMA"
}

function Wait-ForOuterObserverMarker([string]$Path, [int]$TimeoutSeconds, [string]$Label) {
    $wait = [Diagnostics.Stopwatch]::StartNew()
    while ($wait.Elapsed.TotalSeconds -le $TimeoutSeconds) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $wait.Stop()
            return Read-BoundedJsonObject $Path
        }
        Start-Sleep -Milliseconds 25
    }
    $wait.Stop()
    throw ("BLOCKED_AV_BS_RESOURCE: outer observer " + $Label + " timed out")
}

function Initialize-OuterObservedInner {
    if (
        [string]::IsNullOrWhiteSpace($OuterObserverSessionPath) -or
        $OuterObserverNonce -notmatch '^[0-9a-f]{32}$' -or
        $OuterObserverParentProcessId -le 0 -or
        $OuterObserverParentBirthUtcTicks -le 0 -or
        $OuterObserverRunnerSha256 -notmatch '^[0-9a-f]{64}$' -or
        $OuterObserverReviewTokenSha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner observer inputs are incomplete"
    }
    $paths = Get-OuterObserverSessionPaths $OuterObserverSessionPath $OuterObserverNonce
    if (-not (Test-Path -LiteralPath $paths.session -PathType Container)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner observer session is missing"
    }
    foreach ($marker in @($paths.ready, $paths.start_release, $paths.complete, $paths.exit_release)) {
        if (Test-Path -LiteralPath $marker) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner observer marker existed before readiness"
        }
    }

    $runnerSha256 = Get-Sha256 $runnerScriptPath
    $tokenSha256 = Get-Sha256 $reviewTokenPath
    if ($runnerSha256 -ne $OuterObserverRunnerSha256 -or $tokenSha256 -ne $OuterObserverReviewTokenSha256) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner runner or token bytes differ from dispatcher"
    }
    $candidateToken = Read-BoundedJsonObject $reviewTokenPath
    if (
        $null -eq $candidateToken -or
        $candidateToken.schema -ne "AV-BS1-h4-p0r-review-token-v1" -or
        $candidateToken.uses_remaining -isnot [int] -or
        $candidateToken.uses_remaining -ne 1 -or
        [string]$candidateToken.review_token_id -notmatch '^[0-9a-f]{32}$' -or
        $candidateToken.runner_sha256 -ne $runnerSha256 -or
        [string]$candidateToken.outer_observer_contract_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $candidateToken.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $candidateToken.terminal_seal_required_for_authoritative_disposition -ne $true
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner candidate token lacks outer observer bindings"
    }
    [void](Get-OuterObserverTerminalSealPath `
        ([string]$candidateToken.review_token_id) `
        ([string]$candidateToken.expected_terminal_seal_relative_path))

    $parents = [AvBsH4P0RNativeV1]::ProcessParents()
    if (
        -not $parents.ContainsKey([int]$PID) -or
        [int]$parents[[int]$PID] -ne $OuterObserverParentProcessId -or
        (Get-ProcessBirthTicks $OuterObserverParentProcessId) -ne $OuterObserverParentBirthUtcTicks
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner real parent identity mismatch"
    }
    $innerBirthTicks = Get-ProcessBirthTicks $PID
    if (
        [int]$PID -eq [int]$OuterObserverParentProcessId -or
        [int64]$innerBirthTicks -lt [int64]$OuterObserverParentBirthUtcTicks
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner identity chronology is invalid"
    }
    $ready = [ordered]@{
        schema = $outerInnerReadySchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        stage = "primary-h4-p0r"
        internal_mode = $outerInternalModeName
        observer_session_relative_path = $paths.session_relative_path
        observer_nonce = $OuterObserverNonce
        outer_process_id = [int]$OuterObserverParentProcessId
        outer_process_birth_utc_ticks = [int64]$OuterObserverParentBirthUtcTicks
        inner_process_id = [int]$PID
        inner_process_birth_utc_ticks = [int64]$innerBirthTicks
        runner_relative_path = Get-RepositoryRelativePath $runnerScriptPath
        runner_sha256 = $runnerSha256
        review_token_relative_path = Get-RepositoryRelativePath $reviewTokenPath
        review_token_sha256 = $tokenSha256
        review_token_id = [string]$candidateToken.review_token_id
        outer_observer_contract_sha256 = [string]$candidateToken.outer_observer_contract_sha256
        expected_terminal_seal_relative_path = [string]$candidateToken.expected_terminal_seal_relative_path
        terminal_seal_required_for_authoritative_disposition = $true
        ready_written_before_preflight_and_claim = $true
        external_runner_or_machine_kill_terminal_state_guaranteed = $false
        monotonic_ns = Get-MonotonicNanoseconds
        created_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-AtomicUtf8NoBom $paths.ready ($ready | ConvertTo-Json -Depth 8 -Compress)
    $readySha256 = Get-Sha256 $paths.ready
    $startRelease = Wait-ForOuterObserverMarker $paths.start_release 60 "start release"
    $startFields = @(
        "schema", "program", "case_id", "stage",
        "observer_session_relative_path", "observer_nonce",
        "outer_process_id", "outer_process_birth_utc_ticks",
        "inner_process_id", "inner_process_birth_utc_ticks",
        "runner_sha256", "review_token_sha256", "review_token_id",
        "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "inner_ready_relative_path", "inner_ready_sha256",
        "release_scope", "claim_handshake_prefix_ready",
        "terminal_seal_still_pending",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns", "released_utc"
    )
    if (
        $null -eq $startRelease -or
        -not (Test-ExactJsonFieldSet $startRelease $startFields) -or
        $startRelease.schema -ne $outerStartReleaseSchema -or
        $startRelease.program -ne $program -or
        $startRelease.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
        $startRelease.stage -ne "primary-h4-p0r" -or
        $startRelease.observer_session_relative_path -ne $paths.session_relative_path -or
        $startRelease.observer_nonce -ne $OuterObserverNonce -or
        $startRelease.outer_process_id -ne [int]$OuterObserverParentProcessId -or
        $startRelease.outer_process_birth_utc_ticks -ne [int64]$OuterObserverParentBirthUtcTicks -or
        $startRelease.inner_process_id -ne [int]$PID -or
        $startRelease.inner_process_birth_utc_ticks -ne [int64]$innerBirthTicks -or
        $startRelease.runner_sha256 -ne $runnerSha256 -or
        $startRelease.review_token_sha256 -ne $tokenSha256 -or
        $startRelease.review_token_id -ne $candidateToken.review_token_id -or
        $startRelease.outer_observer_contract_sha256 -ne $candidateToken.outer_observer_contract_sha256 -or
        $startRelease.expected_terminal_seal_relative_path -ne $candidateToken.expected_terminal_seal_relative_path -or
        $startRelease.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $startRelease.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $startRelease.inner_ready_relative_path -ne (Get-RepositoryRelativePath $paths.ready) -or
        $startRelease.inner_ready_sha256 -ne $readySha256 -or
        $startRelease.release_scope -ne "permit_inner_preflight_and_claim_only" -or
        $startRelease.claim_handshake_prefix_ready -isnot [bool] -or
        $startRelease.claim_handshake_prefix_ready -ne $true -or
        $startRelease.terminal_seal_still_pending -isnot [bool] -or
        $startRelease.terminal_seal_still_pending -ne $true -or
        $startRelease.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
        $startRelease.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false -or
        $startRelease.monotonic_ns -le $ready.monotonic_ns
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner start release mismatch"
    }
    Assert-StrictJsonClrFieldTypes $startRelease `
        @(
            "schema", "program", "case_id", "stage",
            "observer_session_relative_path", "observer_nonce",
            "runner_sha256", "review_token_sha256", "review_token_id",
            "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
            "inner_ready_relative_path", "inner_ready_sha256", "release_scope",
            "released_utc"
        ) `
        @("outer_process_id", "inner_process_id") `
        @("outer_process_birth_utc_ticks", "inner_process_birth_utc_ticks", "monotonic_ns") `
        @(
            "terminal_seal_required_for_authoritative_disposition",
            "claim_handshake_prefix_ready", "terminal_seal_still_pending",
            "external_runner_or_machine_kill_terminal_state_guaranteed"
        ) `
        "outer start-release marker"
    $readyUtc = ConvertFrom-StrictOuterMarkerUtc $ready.created_utc "outer inner-ready"
    $startReleaseUtc = ConvertFrom-StrictOuterMarkerUtc $startRelease.released_utc "outer start-release"
    if ($startReleaseUtc -lt $readyUtc) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer start-release UTC precedes inner-ready"
    }
    $startReleaseSha256 = Get-Sha256 $paths.start_release
    $prefix = New-OuterObserverHandshakePrefix `
        (Get-RepositoryRelativePath $paths.ready) $readySha256 `
        (Get-RepositoryRelativePath $paths.start_release) $startReleaseSha256

    $script:outerObserverInnerActive = $true
    $script:outerObserverSessionRoot = $paths.session
    $script:outerObserverSessionRelativePath = $paths.session_relative_path
    $script:outerObserverNonceValue = $OuterObserverNonce
    $script:outerObserverParentPidValue = [int]$OuterObserverParentProcessId
    $script:outerObserverParentBirthTicksValue = [int64]$OuterObserverParentBirthUtcTicks
    $script:outerObserverRunnerSha256Value = $runnerSha256
    $script:outerObserverOriginalTokenSha256 = $tokenSha256
    $script:outerObserverOriginalTokenId = [string]$candidateToken.review_token_id
    $script:outerObserverContractSha256 = [string]$candidateToken.outer_observer_contract_sha256
    $script:outerObserverExpectedTerminalSealRelativePath = [string]$candidateToken.expected_terminal_seal_relative_path
    $script:outerObserverReadyPath = $paths.ready
    $script:outerObserverReadySha256 = $readySha256
    $script:outerObserverStartReleasePath = $paths.start_release
    $script:outerObserverStartReleaseSha256 = $startReleaseSha256
    $script:outerObserverCompletePath = $paths.complete
    $script:outerObserverExitReleasePath = $paths.exit_release
    $script:outerObserverHandshakePrefix = $prefix.value
    $script:outerObserverHandshakePrefixSha256 = $prefix.sha256
    $script:executionTreeRootProcessId = [int]$OuterObserverParentProcessId
    $script:executionTreeRootBirthUtcTicks = [int64]$OuterObserverParentBirthUtcTicks
    $script:innerRunnerProcessId = [int]$PID
    $script:innerRunnerBirthUtcTicks = [int64]$innerBirthTicks
}

function Assert-OuterInnerReadyMarker(
    [object]$Ready,
    [object]$Paths,
    [object]$CandidateToken,
    [string]$RunnerSha256,
    [string]$OriginalTokenSha256,
    [string]$ObserverNonce,
    [int]$OuterProcessId,
    [int64]$OuterBirthTicks,
    [int]$InnerProcessId,
    [int64]$InnerBirthTicks
) {
    $fields = @(
        "schema", "program", "case_id", "stage", "internal_mode",
        "observer_session_relative_path", "observer_nonce",
        "outer_process_id", "outer_process_birth_utc_ticks",
        "inner_process_id", "inner_process_birth_utc_ticks",
        "runner_relative_path", "runner_sha256",
        "review_token_relative_path", "review_token_sha256", "review_token_id",
        "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "ready_written_before_preflight_and_claim",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns", "created_utc"
    )
    if (
        $null -eq $Ready -or
        -not (Test-ExactJsonFieldSet $Ready $fields) -or
        $Ready.schema -ne $outerInnerReadySchema -or
        $Ready.program -ne $program -or
        $Ready.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
        $Ready.stage -ne "primary-h4-p0r" -or
        $Ready.internal_mode -ne $outerInternalModeName -or
        $Ready.observer_session_relative_path -ne $Paths.session_relative_path -or
        $Ready.observer_nonce -ne $ObserverNonce -or
        $Ready.outer_process_id -ne [int]$OuterProcessId -or
        $Ready.outer_process_birth_utc_ticks -ne [int64]$OuterBirthTicks -or
        $Ready.inner_process_id -ne [int]$InnerProcessId -or
        $Ready.inner_process_birth_utc_ticks -ne [int64]$InnerBirthTicks -or
        $Ready.inner_process_id -eq $Ready.outer_process_id -or
        $Ready.inner_process_birth_utc_ticks -lt $Ready.outer_process_birth_utc_ticks -or
        $Ready.runner_relative_path -ne (Get-RepositoryRelativePath $runnerScriptPath) -or
        $Ready.runner_sha256 -ne $RunnerSha256 -or
        $Ready.review_token_relative_path -ne (Get-RepositoryRelativePath $reviewTokenPath) -or
        $Ready.review_token_sha256 -ne $OriginalTokenSha256 -or
        $Ready.review_token_id -ne $CandidateToken.review_token_id -or
        $Ready.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
        $Ready.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
        $Ready.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $Ready.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $Ready.ready_written_before_preflight_and_claim -isnot [bool] -or
        $Ready.ready_written_before_preflight_and_claim -ne $true -or
        $Ready.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
        $Ready.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false -or
        $Ready.monotonic_ns -isnot [long]
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer inner-ready marker mismatch"
    }
    Assert-StrictJsonClrFieldTypes $Ready `
        @(
            "schema", "program", "case_id", "stage", "internal_mode",
            "observer_session_relative_path", "observer_nonce",
            "runner_relative_path", "runner_sha256", "review_token_relative_path",
            "review_token_sha256", "review_token_id",
            "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
            "created_utc"
        ) `
        @("outer_process_id", "inner_process_id") `
        @("outer_process_birth_utc_ticks", "inner_process_birth_utc_ticks", "monotonic_ns") `
        @(
            "terminal_seal_required_for_authoritative_disposition",
            "ready_written_before_preflight_and_claim",
            "external_runner_or_machine_kill_terminal_state_guaranteed"
        ) `
        "outer inner-ready marker"
    [void](ConvertFrom-StrictOuterMarkerUtc $Ready.created_utc "outer inner-ready")
}

function Assert-QuarantinedFactorMonitorMarkerBindings(
    [object]$Complete,
    [string]$ContextLabel
) {
    $monitorReadyPresent = $null -ne $Complete.monitor_ready_marker_sha256
    $factorCompletePresent = $null -ne $Complete.factor_complete_marker_sha256
    $monitorReleasePresent = $null -ne $Complete.monitor_release_marker_sha256
    if (
        ($factorCompletePresent -and -not $monitorReadyPresent) -or
        ($monitorReleasePresent -and -not $factorCompletePresent)
    ) {
        throw ("BLOCKED_AV_BS_RESULT_SCHEMA: factor-monitor marker hash prefix is invalid: " + $ContextLabel)
    }

    $anyMarkerPresent = $monitorReadyPresent -or $factorCompletePresent -or $monitorReleasePresent
    if ($Complete.temporary_attempt_cleanup_disposition -eq "removed") {
        if ($anyMarkerPresent) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: removed attempt evidence retains a factor-monitor marker hash: " + $ContextLabel)
        }
        return
    }
    if (
        $Complete.temporary_attempt_cleanup_disposition -ne "quarantined" -or
        [string]::IsNullOrWhiteSpace([string]$Complete.attempt_evidence_relative_path) -or
        [string]$Complete.attempt_evidence_relative_path -notmatch '^validation-output/av-bs1/quarantine/[0-9a-f]{32}-[0-9a-f]{32}$'
    ) {
        throw ("BLOCKED_AV_BS_RESULT_SCHEMA: factor-monitor marker hashes lack canonical quarantined attempt evidence: " + $ContextLabel)
    }

    $attemptPath = Resolve-RepositoryRelativePath ([string]$Complete.attempt_evidence_relative_path)
    if (-not (Test-Path -LiteralPath $attemptPath -PathType Container)) {
        throw ("BLOCKED_AV_BS_RESULT_SCHEMA: quarantined factor-monitor marker directory is missing: " + $ContextLabel)
    }
    foreach ($binding in @(
        [pscustomobject]@{ field = "monitor_ready_marker_sha256"; leaf = "monitor-ready.json" },
        [pscustomobject]@{ field = "factor_complete_marker_sha256"; leaf = "factor-complete.json" },
        [pscustomobject]@{ field = "monitor_release_marker_sha256"; leaf = "monitor-release.json" }
    )) {
        $markerPath = [IO.Path]::GetFullPath((Join-Path $attemptPath ([string]$binding.leaf)))
        if (
            [IO.Path]::GetDirectoryName($markerPath).TrimEnd('\') -ne $attemptPath.TrimEnd('\') -or
            [IO.Path]::GetFileName($markerPath) -cne [string]$binding.leaf
        ) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: factor-monitor marker path is not canonical: " + [string]$binding.field)
        }
        $expectedSha256 = $Complete.([string]$binding.field)
        if ($null -eq $expectedSha256) {
            if (Test-Path -LiteralPath $markerPath) {
                throw ("BLOCKED_AV_BS_RESULT_SCHEMA: factor-monitor marker file/hash null-pair mismatch: " + [string]$binding.field)
            }
            continue
        }
        if (
            [string]$expectedSha256 -notmatch '^[0-9a-f]{64}$' -or
            -not (Test-Path -LiteralPath $markerPath -PathType Leaf)
        ) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: factor-monitor marker file/hash binding is invalid: " + [string]$binding.field)
        }
        $markerSha256Before = Get-Sha256 $markerPath
        $markerSha256After = Get-Sha256 $markerPath
        if ($markerSha256Before -ne $expectedSha256 -or $markerSha256After -ne $expectedSha256) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: factor-monitor marker bytes changed: " + [string]$binding.field)
        }
    }
}

function Assert-OuterInnerCompleteMarker(
    [object]$Complete,
    [object]$Paths,
    [object]$CandidateToken,
    [string]$RunnerSha256,
    [string]$OriginalTokenSha256,
    [string]$ObserverNonce,
    [int]$OuterProcessId,
    [int64]$OuterBirthTicks,
    [int]$InnerProcessId,
    [int64]$InnerBirthTicks,
    [object]$HandshakePrefix,
    [string]$HandshakePrefixSha256,
    [string]$ReadySha256,
    [string]$StartReleaseSha256
) {
    $fields = @(
        "schema", "program", "case_id", "stage", "internal_mode",
        "observer_session_relative_path", "observer_nonce",
        "outer_process_id", "outer_process_birth_utc_ticks",
        "inner_process_id", "inner_process_birth_utc_ticks",
        "runner_sha256", "review_token_id", "original_review_token_sha256",
        "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "inner_ready_relative_path", "inner_ready_sha256",
        "outer_start_release_relative_path", "outer_start_release_sha256",
        "outer_observer_handshake_prefix",
        "outer_observer_handshake_prefix_sha256",
        "claim_relative_path", "claim_sha256", "claim_canonical_sha256",
        "guard_sha256", "guard_canonical_sha256",
        "resource_report_sha256", "result_file_sha256",
        "child_stdout_sha256", "child_stderr_sha256",
        "monitor_ready_marker_sha256", "factor_complete_marker_sha256",
        "monitor_release_marker_sha256",
        "factor_prefix_one_sha256", "factor_prefix_two_sha256",
        "published_result_relative_path", "published_result_sha256",
        "tombstone_relative_path", "tombstone_sha256", "tombstone_schema",
        "pre_exit_evidence_relative_path", "pre_exit_evidence_sha256",
        "pre_exit_evidence_schema",
        "final_control_plane_session_index_relative_path",
        "final_control_plane_session_index_sha256",
        "intended_inner_exit_code", "provisional_inner_disposition",
        "inner_exit_observed", "temporary_attempt_cleanup_disposition",
        "attempt_evidence_relative_path", "terminal_evidence_complete",
        "authorization_blocker",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns", "completed_utc"
    )
    if (
        $null -eq $Complete -or
        -not (Test-ExactJsonFieldSet $Complete $fields) -or
        $Complete.schema -ne $outerInnerCompleteSchema -or
        $Complete.program -ne $program -or
        $Complete.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
        $Complete.stage -ne "primary-h4-p0r" -or
        $Complete.internal_mode -ne $outerInternalModeName -or
        $Complete.observer_session_relative_path -ne $Paths.session_relative_path -or
        $Complete.observer_nonce -ne $ObserverNonce -or
        $Complete.outer_process_id -ne [int]$OuterProcessId -or
        $Complete.outer_process_birth_utc_ticks -ne [int64]$OuterBirthTicks -or
        $Complete.inner_process_id -ne [int]$InnerProcessId -or
        $Complete.inner_process_birth_utc_ticks -ne [int64]$InnerBirthTicks -or
        $Complete.runner_sha256 -ne $RunnerSha256 -or
        $Complete.review_token_id -ne $CandidateToken.review_token_id -or
        $Complete.original_review_token_sha256 -ne $OriginalTokenSha256 -or
        $Complete.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
        $Complete.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
        $Complete.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $Complete.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $Complete.inner_ready_relative_path -ne (Get-RepositoryRelativePath $Paths.ready) -or
        $Complete.inner_ready_sha256 -ne $ReadySha256 -or
        $Complete.outer_start_release_relative_path -ne (Get-RepositoryRelativePath $Paths.start_release) -or
        $Complete.outer_start_release_sha256 -ne $StartReleaseSha256 -or
        -not (Test-StrictJsonValueEqual $Complete.outer_observer_handshake_prefix $HandshakePrefix) -or
        $Complete.outer_observer_handshake_prefix_sha256 -ne $HandshakePrefixSha256 -or
        $Complete.intended_inner_exit_code -notin @(0, 2) -or
        $Complete.inner_exit_observed -isnot [bool] -or
        $Complete.inner_exit_observed -ne $false -or
        $Complete.temporary_attempt_cleanup_disposition -notin @("removed", "quarantined") -or
        $Complete.terminal_evidence_complete -isnot [bool] -or
        $Complete.terminal_evidence_complete -ne $false -or
        $Complete.authorization_blocker -isnot [bool] -or
        $Complete.authorization_blocker -ne $true -or
        $Complete.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
        $Complete.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false -or
        $Complete.monotonic_ns -isnot [long]
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer inner-complete marker mismatch"
    }
    Assert-StrictJsonClrFieldTypes $Complete `
        @(
            "schema", "program", "case_id", "stage", "internal_mode",
            "observer_session_relative_path", "observer_nonce", "runner_sha256",
            "review_token_id", "original_review_token_sha256",
            "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
            "inner_ready_relative_path", "inner_ready_sha256",
            "outer_start_release_relative_path", "outer_start_release_sha256",
            "outer_observer_handshake_prefix_sha256", "claim_relative_path",
            "claim_sha256", "claim_canonical_sha256", "guard_sha256",
            "guard_canonical_sha256", "tombstone_relative_path", "tombstone_sha256",
            "tombstone_schema", "pre_exit_evidence_relative_path",
            "pre_exit_evidence_sha256", "pre_exit_evidence_schema",
            "final_control_plane_session_index_relative_path",
            "final_control_plane_session_index_sha256", "provisional_inner_disposition",
            "temporary_attempt_cleanup_disposition", "completed_utc"
        ) `
        @("outer_process_id", "inner_process_id", "intended_inner_exit_code") `
        @("outer_process_birth_utc_ticks", "inner_process_birth_utc_ticks", "monotonic_ns") `
        @(
            "terminal_seal_required_for_authoritative_disposition", "inner_exit_observed",
            "terminal_evidence_complete", "authorization_blocker",
            "external_runner_or_machine_kill_terminal_state_guaranteed"
        ) `
        "outer inner-complete marker"
    foreach ($optionalStringField in @(
        "resource_report_sha256", "result_file_sha256", "child_stdout_sha256",
        "child_stderr_sha256", "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256", "monitor_release_marker_sha256",
        "factor_prefix_one_sha256", "factor_prefix_two_sha256",
        "published_result_relative_path", "published_result_sha256",
        "attempt_evidence_relative_path"
    )) {
        if ($null -ne $Complete.$optionalStringField -and $Complete.$optionalStringField -isnot [string]) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer inner-complete optional string type mismatch: " + $optionalStringField)
        }
    }
    $readyCurrent = Read-BoundedJsonObject $Paths.ready
    $startCurrent = Read-BoundedJsonObject $Paths.start_release
    if (
        $null -eq $readyCurrent -or $null -eq $startCurrent -or
        (Get-Sha256 $Paths.ready) -ne $ReadySha256 -or
        (Get-Sha256 $Paths.start_release) -ne $StartReleaseSha256 -or
        $Complete.monotonic_ns -le $startCurrent.monotonic_ns -or
        $startCurrent.monotonic_ns -le $readyCurrent.monotonic_ns
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer marker chain chronology or bytes mismatch"
    }
    $readyUtc = ConvertFrom-StrictOuterMarkerUtc $readyCurrent.created_utc "outer inner-ready"
    $startUtc = ConvertFrom-StrictOuterMarkerUtc $startCurrent.released_utc "outer start-release"
    $completeUtc = ConvertFrom-StrictOuterMarkerUtc $Complete.completed_utc "outer inner-complete"
    if ($startUtc -lt $readyUtc -or $completeUtc -lt $startUtc) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer ready/start/complete UTC chronology is invalid"
    }
    foreach ($field in @(
        "claim_sha256", "claim_canonical_sha256", "guard_sha256",
        "guard_canonical_sha256", "pre_exit_evidence_sha256",
        "final_control_plane_session_index_sha256", "tombstone_sha256"
    )) {
        if ([string]$Complete.$field -notmatch '^[0-9a-f]{64}$') {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: inner completion hash is invalid: " + $field)
        }
    }
    foreach ($field in @(
        "resource_report_sha256", "result_file_sha256", "child_stdout_sha256",
        "child_stderr_sha256", "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256", "monitor_release_marker_sha256",
        "factor_prefix_one_sha256",
        "factor_prefix_two_sha256", "published_result_sha256"
    )) {
        if ($null -ne $Complete.$field -and [string]$Complete.$field -notmatch '^[0-9a-f]{64}$') {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: optional inner completion hash is invalid: " + $field)
        }
    }

    $expectedClaimRelativePath = "validation-output/av-bs1/claims/" + [string]$CandidateToken.review_token_id + ".json"
    $expectedPreExitRelativePath = "validation-output/av-bs1/control-plane-pre-exit-evidence/" + [string]$CandidateToken.review_token_id + ".json"
    if (
        [string]$Complete.claim_relative_path -cne $expectedClaimRelativePath -or
        [string]$Complete.pre_exit_evidence_relative_path -cne $expectedPreExitRelativePath -or
        [string]$Complete.final_control_plane_session_index_relative_path -notmatch '^validation-output/av-bs1/control-plane/session-[0-9a-f]{32}/session-index-[0-9]{4}\.json$'
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion canonical durable path mismatch"
    }
    $claimPathCurrent = Resolve-RepositoryRelativePath ([string]$Complete.claim_relative_path)
    $preExitPathCurrent = Resolve-RepositoryRelativePath ([string]$Complete.pre_exit_evidence_relative_path)
    $finalIndexPathCurrent = Resolve-RepositoryRelativePath ([string]$Complete.final_control_plane_session_index_relative_path)
    if (
        -not (Test-Path -LiteralPath $claimPathCurrent -PathType Leaf) -or
        (Get-Sha256 $claimPathCurrent) -ne $Complete.claim_sha256 -or
        -not (Test-Path -LiteralPath $preExitPathCurrent -PathType Leaf) -or
        (Get-Sha256 $preExitPathCurrent) -ne $Complete.pre_exit_evidence_sha256 -or
        -not (Test-Path -LiteralPath $finalIndexPathCurrent -PathType Leaf) -or
        (Get-Sha256 $finalIndexPathCurrent) -ne $Complete.final_control_plane_session_index_sha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion durable provenance hash mismatch"
    }
    $claim = Read-BoundedJsonObject $claimPathCurrent
    if (
        $null -eq $claim -or
        $claim.review_token_id -ne $CandidateToken.review_token_id -or
        $claim.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
        $claim.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
        $claim.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $claim.terminal_seal_required_for_authoritative_disposition -ne $true -or
        -not (Test-StrictJsonValueEqual $claim.outer_observer_handshake_prefix $HandshakePrefix) -or
        $claim.outer_observer_handshake_prefix_sha256 -ne $HandshakePrefixSha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion claim lacks observer prefix"
    }
    $preExit = Read-BoundedJsonObject $preExitPathCurrent
    $currentTombstoneSha256 = Get-Sha256 $reviewTokenPath
    $currentTombstone = Read-BoundedJsonObject $reviewTokenPath
    if (
        $null -eq $preExit -or
        $preExit.schema -ne $preExitControlPlaneEvidenceSchema -or
        $Complete.pre_exit_evidence_schema -ne $preExitControlPlaneEvidenceSchema -or
        $preExit.review_token_id -ne $CandidateToken.review_token_id -or
        $preExit.original_review_token_sha256 -ne $OriginalTokenSha256 -or
        $preExit.intended_runner_exit_code -ne $Complete.intended_inner_exit_code -or
        $preExit.runner_exit_observed -ne $false -or
        $preExit.terminal_evidence_complete -ne $false -or
        $preExit.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
        $preExit.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
        -not (Test-StrictJsonValueEqual $preExit.outer_observer_handshake_prefix $HandshakePrefix) -or
        $preExit.outer_observer_handshake_prefix_sha256 -ne $HandshakePrefixSha256 -or
        $preExit.claim_sha256 -ne $Complete.claim_sha256 -or
        $preExit.claim_canonical_sha256 -ne $Complete.claim_canonical_sha256 -or
        $preExit.guard_sha256 -ne $Complete.guard_sha256 -or
        $preExit.guard_canonical_sha256 -ne $Complete.guard_canonical_sha256 -or
        $null -eq $currentTombstone -or
        $currentTombstoneSha256 -ne $Complete.tombstone_sha256 -or
        $preExit.tombstone_sha256 -ne $Complete.tombstone_sha256 -or
        $currentTombstone.schema -ne $Complete.tombstone_schema -or
        $currentTombstone.authorization_state -ne "consumed" -or
        $currentTombstone.uses_remaining -isnot [int] -or
        $currentTombstone.uses_remaining -ne 0 -or
        $currentTombstone.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
        $currentTombstone.outer_observer_handshake_prefix_sha256 -ne $HandshakePrefixSha256 -or
        $currentTombstone.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
        $currentTombstone.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $currentTombstone.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $currentTombstone.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
        $currentTombstone.terminal_evidence_complete -isnot [bool] -or
        $currentTombstone.terminal_evidence_complete -ne $false -or
        $currentTombstone.authoritative_stage_pass -isnot [bool] -or
        $currentTombstone.authoritative_stage_pass -ne $false -or
        $Complete.tombstone_relative_path -ne (Get-RepositoryRelativePath $reviewTokenPath)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion pre-exit or tombstone evidence mismatch"
    }
    foreach ($mapping in @(
        @("resource_report_sha256", "resource_report_sha256"),
        @("result_file_sha256", "result_file_sha256"),
        @("child_stdout_sha256", "child_stdout_sha256"),
        @("child_stderr_sha256", "child_stderr_sha256"),
        @("monitor_ready_marker_sha256", "monitor_ready_marker_sha256"),
        @("factor_complete_marker_sha256", "factor_complete_marker_sha256"),
        @("monitor_release_marker_sha256", "monitor_release_marker_sha256"),
        @("factor_prefix_one_sha256", "factor_prefix_one_sha256"),
        @("factor_prefix_two_sha256", "factor_prefix_two_sha256")
    )) {
        if ($Complete.($mapping[0]) -ne $preExit.($mapping[1])) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: inner completion artifact differs from pre-exit evidence: " + $mapping[0])
        }
    }
    if (
        $Complete.intended_inner_exit_code -eq 0 -and (
            $Complete.provisional_inner_disposition -ne "provisional_pass_pending_outer_observed_exit" -or
            $Complete.tombstone_schema -ne $tombstoneSchema -or
            $preExit.normal_pass_outer_evidence_reconciliation_pass -ne $true -or
            $null -eq $Complete.resource_report_sha256 -or
            $null -eq $Complete.result_file_sha256 -or
            $null -eq $Complete.child_stdout_sha256 -or
            $null -eq $Complete.child_stderr_sha256 -or
            $null -eq $Complete.monitor_ready_marker_sha256 -or
            $null -eq $Complete.factor_complete_marker_sha256 -or
            $null -eq $Complete.monitor_release_marker_sha256 -or
            $null -eq $Complete.factor_prefix_one_sha256 -or
            $null -eq $Complete.factor_prefix_two_sha256 -or
            $null -eq $Complete.published_result_relative_path -or
            $null -eq $Complete.published_result_sha256
        )
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion pass disposition is not provisional and reconciled"
    }
    if ($Complete.intended_inner_exit_code -eq 0) {
        foreach ($requiredPassFlag in @(
            "claim_evidence_valid", "guard_evidence_valid", "resource_evidence_valid",
            "result_evidence_valid", "child_stdout_evidence_valid",
            "factor_prefix_evidence_valid", "resource_gate_pass",
            "consumption_validated_pass", "mandatory_stage_pass",
            "factorization_attempted", "factorization_performed"
        )) {
            if ($currentTombstone.$requiredPassFlag -isnot [bool] -or $currentTombstone.$requiredPassFlag -ne $true) {
                throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer success requires tombstone flag: " + $requiredPassFlag)
            }
        }
        if (
            $currentTombstone.attempt_status -ne "completed_pass" -or
            $currentTombstone.effective_attempt_status -ne "completed_pass" -or
            @($currentTombstone.failure_codes).Count -ne 0 -or
            @($currentTombstone.resource_gate_recheck_failures).Count -ne 0 -or
            @($currentTombstone.evidence_validation_errors).Count -ne 0 -or
            $null -eq $currentTombstone.claim_evidence -or
            -not (Test-StrictJsonValueEqual $currentTombstone.claim_evidence $claim) -or
            $null -eq $currentTombstone.resource_evidence -or
            $currentTombstone.resource_evidence.mandatory_resource_gate_pass -isnot [bool] -or
            $currentTombstone.resource_evidence.mandatory_resource_gate_pass -ne $true -or
            @($currentTombstone.factor_prefix_evidence).Count -ne 2
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer success tombstone evidence is incomplete"
        }
    }
    if (
        $Complete.intended_inner_exit_code -eq 2 -and
        $Complete.provisional_inner_disposition -ne "provisional_failure_pending_outer_observed_exit"
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion failure disposition mismatch"
    }
    if ($Complete.temporary_attempt_cleanup_disposition -eq "removed" -and $null -ne $Complete.attempt_evidence_relative_path) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: removed attempt evidence unexpectedly has a path"
    }
    if (($null -eq $Complete.published_result_relative_path) -ne ($null -eq $Complete.published_result_sha256)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: published result path/hash null-pair mismatch"
    }
    if ($Complete.temporary_attempt_cleanup_disposition -eq "quarantined") {
        $expectedAttemptRelativePath = "validation-output/av-bs1/quarantine/" + [string]$CandidateToken.review_token_id + "-" + [string]$claim.guard_nonce
        if ([string]$Complete.attempt_evidence_relative_path -cne $expectedAttemptRelativePath) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: quarantined attempt evidence path mismatch"
        }
        $attemptPath = Resolve-RepositoryRelativePath ([string]$Complete.attempt_evidence_relative_path)
        if (-not (Test-Path -LiteralPath $attemptPath -PathType Container)) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: quarantined attempt evidence is missing"
        }
    }
    Assert-QuarantinedFactorMonitorMarkerBindings $Complete "outer inner-complete"
    if ($null -ne $Complete.published_result_relative_path) {
        if ([string]$Complete.published_result_relative_path -notmatch '^validation-output/av-bs1/av-bs1-primary-h4-p0r-[0-9]{8}T[0-9]{6}Z\.json$') {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: published result path is not canonical"
        }
        $publishedPath = Resolve-RepositoryRelativePath ([string]$Complete.published_result_relative_path)
        if (
            -not (Test-Path -LiteralPath $publishedPath -PathType Leaf) -or
            (Get-Sha256 $publishedPath) -ne $Complete.published_result_sha256 -or
            $null -eq $Complete.result_file_sha256 -or
            $Complete.published_result_sha256 -ne $Complete.result_file_sha256
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: published result binding mismatch"
        }
        $publishedWrapper = Read-BoundedJsonObject $publishedPath
        if (
            $null -eq $publishedWrapper -or
            -not (Test-ExactJsonFieldSet $publishedWrapper @("payload", "payload_sha256"))
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: published result wrapper is invalid"
        }
        if ($publishedWrapper.payload.schema -eq "AV-BS1-h4-p0r-result-v2") {
            if (
                $publishedWrapper.payload.outer_observer_contract_sha256 -ne $CandidateToken.outer_observer_contract_sha256 -or
                $publishedWrapper.payload.outer_observer_handshake_prefix_sha256 -ne $HandshakePrefixSha256 -or
                $publishedWrapper.payload.expected_terminal_seal_relative_path -ne $CandidateToken.expected_terminal_seal_relative_path -or
                $publishedWrapper.payload.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
                $publishedWrapper.payload.terminal_seal_required_for_authoritative_disposition -ne $true -or
                $publishedWrapper.payload.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
                $publishedWrapper.payload.terminal_evidence_complete -isnot [bool] -or
                $publishedWrapper.payload.terminal_evidence_complete -ne $false -or
                $publishedWrapper.payload.authoritative_stage_pass -isnot [bool] -or
                $publishedWrapper.payload.authoritative_stage_pass -ne $false
            ) {
                throw "BLOCKED_AV_BS_RESULT_SCHEMA: published result-v2 terminal bindings are invalid"
            }
        }
        elseif ($publishedWrapper.payload.schema -ne "AV-BS1-h4-p0r-failure-v1") {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: published result schema is invalid"
        }
        if (
            $Complete.intended_inner_exit_code -eq 0 -and (
                $publishedWrapper.payload.schema -ne "AV-BS1-h4-p0r-result-v2" -or
                $publishedWrapper.payload.mandatory_stage_pass -isnot [bool] -or
                $publishedWrapper.payload.mandatory_stage_pass -ne $true -or
                $publishedWrapper.payload_sha256 -ne $currentTombstone.consumed_result_payload_sha256 -or
                -not (Test-StrictJsonValueEqual $publishedWrapper.payload $currentTombstone.result_evidence)
            )
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: provisional pass result-v2 binding is invalid"
        }
    }
}

function Complete-OuterObservedInner([int]$IntendedExitCode) {
    if (-not $script:outerObserverInnerActive -or $IntendedExitCode -notin @(0, 2)) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer-observed inner completion state is invalid"
    }
    if (
        $null -eq $script:outerObserverPreExitEvidenceReference -or
        $script:outerObserverAttemptCleanupDisposition -notin @("removed", "quarantined") -or
        (Test-Path -LiteralPath $tempDirectory)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion preceded pre-exit evidence or cleanup"
    }
    $paths = Get-OuterObserverSessionPaths $script:outerObserverSessionRoot $script:outerObserverNonceValue
    if (Test-Path -LiteralPath $paths.complete) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion marker already exists"
    }
    $preExitPath = [IO.Path]::GetFullPath([string]$script:outerObserverPreExitEvidenceReference.path)
    $preExitSha256 = Get-Sha256 $preExitPath
    if ($preExitSha256 -ne [string]$script:outerObserverPreExitEvidenceReference.sha256) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence changed before inner completion"
    }
    $preExit = Read-BoundedJsonObject $preExitPath
    if (
        $null -eq $preExit -or
        $preExit.schema -ne $preExitControlPlaneEvidenceSchema -or
        $preExit.review_token_id -ne $script:outerObserverOriginalTokenId -or
        $preExit.original_review_token_sha256 -ne $script:outerObserverOriginalTokenSha256 -or
        $preExit.intended_runner_exit_code -ne $IntendedExitCode -or
        $preExit.runner_exit_observed -isnot [bool] -or
        $preExit.runner_exit_observed -ne $false -or
        $preExit.terminal_evidence_complete -isnot [bool] -or
        $preExit.terminal_evidence_complete -ne $false -or
        $preExit.external_observer_required_for_terminal_exit -isnot [bool] -or
        $preExit.external_observer_required_for_terminal_exit -ne $true -or
        $preExit.authorization_blocker -isnot [bool] -or
        $preExit.authorization_blocker -ne $true -or
        $preExit.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
        $preExit.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
        $preExit.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $preExit.terminal_seal_required_for_authoritative_disposition -ne $true -or
        -not (Test-StrictJsonValueEqual $preExit.outer_observer_handshake_prefix $script:outerObserverHandshakePrefix) -or
        $preExit.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence lacks the outer observer prefix"
    }

    $tombstone = Read-BoundedJsonObject $reviewTokenPath
    $tombstoneSha256 = Get-Sha256 $reviewTokenPath
    if (
        $null -eq $tombstone -or
        $tombstone.schema -notin @($tombstoneSchema, $emergencyTombstoneSchema) -or
        $tombstone.authorization_state -ne "consumed" -or
        $tombstone.uses_remaining -ne 0 -or
        $tombstone.consumed_review_token_id -ne $script:outerObserverOriginalTokenId -or
        $tombstone.consumed_review_token_sha256 -ne $script:outerObserverOriginalTokenSha256 -or
        $tombstone.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
        $tombstone.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
        $tombstone.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
        $tombstone.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $tombstone.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $tombstone.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
        $tombstone.terminal_evidence_complete -isnot [bool] -or
        $tombstone.terminal_evidence_complete -ne $false -or
        $tombstone.authoritative_stage_pass -isnot [bool] -or
        $tombstone.authoritative_stage_pass -ne $false -or
        $preExit.tombstone_sha256 -ne $tombstoneSha256 -or
        $preExit.tombstone_schema -ne $tombstone.schema
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: current tombstone differs from pre-exit evidence"
    }
    if (
        $IntendedExitCode -eq 0 -and (
            $tombstone.schema -ne $tombstoneSchema -or
            $preExit.intended_runner_disposition -ne "success_intent_pending_external_exit_observation" -or
            $preExit.normal_pass_outer_evidence_reconciliation_pass -isnot [bool] -or
            $preExit.normal_pass_outer_evidence_reconciliation_pass -ne $true
        )
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: zero exit intent lacks a reconciled normal tombstone"
    }
    if (
        $null -eq $pendingTerminalArtifactHashes -or
        $null -eq $script:controlPlaneLatestIndexPath -or
        $null -eq $script:controlPlaneLatestIndexSha256 -or
        -not (Test-Path -LiteralPath $script:controlPlaneLatestIndexPath -PathType Leaf) -or
        (Get-Sha256 $script:controlPlaneLatestIndexPath) -ne $script:controlPlaneLatestIndexSha256 -or
        -not (Test-Path -LiteralPath $claimPath -PathType Leaf) -or
        (Get-Sha256 $claimPath) -ne $claimHash
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion provenance is incomplete"
    }
    $publishedResultRelativePath = $null
    $publishedResultSha256 = $null
    $outputVariable = Get-Variable -Name outputPath -Scope Script -ErrorAction SilentlyContinue
    if ($null -ne $outputVariable -and -not [string]::IsNullOrWhiteSpace([string]$outputVariable.Value)) {
        $publishedPath = [IO.Path]::GetFullPath([string]$outputVariable.Value)
        if (Test-Path -LiteralPath $publishedPath -PathType Leaf) {
            $publishedResultRelativePath = Get-RepositoryRelativePath $publishedPath
            $publishedResultSha256 = Get-Sha256 $publishedPath
        }
    }
    $complete = [ordered]@{
        schema = $outerInnerCompleteSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        stage = "primary-h4-p0r"
        internal_mode = $outerInternalModeName
        observer_session_relative_path = $script:outerObserverSessionRelativePath
        observer_nonce = $script:outerObserverNonceValue
        outer_process_id = [int]$script:outerObserverParentPidValue
        outer_process_birth_utc_ticks = [int64]$script:outerObserverParentBirthTicksValue
        inner_process_id = [int]$PID
        inner_process_birth_utc_ticks = [int64]$script:innerRunnerBirthUtcTicks
        runner_sha256 = $script:outerObserverRunnerSha256Value
        review_token_id = $script:outerObserverOriginalTokenId
        original_review_token_sha256 = $script:outerObserverOriginalTokenSha256
        outer_observer_contract_sha256 = $script:outerObserverContractSha256
        expected_terminal_seal_relative_path = $script:outerObserverExpectedTerminalSealRelativePath
        terminal_seal_required_for_authoritative_disposition = $true
        inner_ready_relative_path = Get-RepositoryRelativePath $script:outerObserverReadyPath
        inner_ready_sha256 = $script:outerObserverReadySha256
        outer_start_release_relative_path = Get-RepositoryRelativePath $script:outerObserverStartReleasePath
        outer_start_release_sha256 = $script:outerObserverStartReleaseSha256
        outer_observer_handshake_prefix = $script:outerObserverHandshakePrefix
        outer_observer_handshake_prefix_sha256 = $script:outerObserverHandshakePrefixSha256
        claim_relative_path = $claimRelativePath
        claim_sha256 = $claimHash
        claim_canonical_sha256 = $claimCanonicalHash
        guard_sha256 = $guardHash
        guard_canonical_sha256 = $guardCanonicalHash
        resource_report_sha256 = $pendingTerminalArtifactHashes["resource_report_sha256"]
        result_file_sha256 = $pendingTerminalArtifactHashes["result_file_sha256"]
        child_stdout_sha256 = $pendingTerminalArtifactHashes["child_stdout_sha256"]
        child_stderr_sha256 = $pendingTerminalArtifactHashes["child_stderr_sha256"]
        monitor_ready_marker_sha256 = $pendingTerminalArtifactHashes["monitor_ready_marker_sha256"]
        factor_complete_marker_sha256 = $pendingTerminalArtifactHashes["factor_complete_marker_sha256"]
        monitor_release_marker_sha256 = $pendingTerminalArtifactHashes["monitor_release_marker_sha256"]
        factor_prefix_one_sha256 = $pendingTerminalArtifactHashes["factor_prefix_one_sha256"]
        factor_prefix_two_sha256 = $pendingTerminalArtifactHashes["factor_prefix_two_sha256"]
        published_result_relative_path = $publishedResultRelativePath
        published_result_sha256 = $publishedResultSha256
        tombstone_relative_path = Get-RepositoryRelativePath $reviewTokenPath
        tombstone_sha256 = $tombstoneSha256
        tombstone_schema = [string]$tombstone.schema
        pre_exit_evidence_relative_path = Get-RepositoryRelativePath $preExitPath
        pre_exit_evidence_sha256 = $preExitSha256
        pre_exit_evidence_schema = $preExitControlPlaneEvidenceSchema
        final_control_plane_session_index_relative_path = Get-RepositoryRelativePath $script:controlPlaneLatestIndexPath
        final_control_plane_session_index_sha256 = $script:controlPlaneLatestIndexSha256
        intended_inner_exit_code = [int]$IntendedExitCode
        provisional_inner_disposition = if ($IntendedExitCode -eq 0) { "provisional_pass_pending_outer_observed_exit" } else { "provisional_failure_pending_outer_observed_exit" }
        inner_exit_observed = $false
        temporary_attempt_cleanup_disposition = $script:outerObserverAttemptCleanupDisposition
        attempt_evidence_relative_path = $script:outerObserverAttemptEvidenceRelativePath
        terminal_evidence_complete = $false
        authorization_blocker = $true
        external_runner_or_machine_kill_terminal_state_guaranteed = $false
        monotonic_ns = Get-MonotonicNanoseconds
        completed_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-AtomicUtf8NoBom $paths.complete ($complete | ConvertTo-Json -Depth 16 -Compress)
    $completeSha256 = Get-Sha256 $paths.complete
    $exitRelease = Wait-ForOuterObserverMarker $paths.exit_release 60 "exit release"
    $exitFields = @(
        "schema", "program", "case_id", "stage",
        "observer_session_relative_path", "observer_nonce",
        "outer_process_id", "outer_process_birth_utc_ticks",
        "inner_process_id", "inner_process_birth_utc_ticks",
        "runner_sha256", "review_token_id", "original_review_token_sha256",
        "outer_observer_contract_sha256",
        "expected_terminal_seal_relative_path",
        "terminal_seal_required_for_authoritative_disposition",
        "inner_complete_relative_path", "inner_complete_sha256",
        "outer_observer_handshake_prefix_sha256",
        "intended_inner_exit_code", "release_inner_to_exit",
        "outer_resource_gate_pass_before_inner_exit",
        "terminal_seal_still_pending",
        "external_runner_or_machine_kill_terminal_state_guaranteed",
        "monotonic_ns", "released_utc"
    )
    if (
        $null -eq $exitRelease -or
        -not (Test-ExactJsonFieldSet $exitRelease $exitFields) -or
        $exitRelease.schema -ne $outerExitReleaseSchema -or
        $exitRelease.program -ne $program -or
        $exitRelease.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
        $exitRelease.stage -ne "primary-h4-p0r" -or
        $exitRelease.observer_session_relative_path -ne $script:outerObserverSessionRelativePath -or
        $exitRelease.observer_nonce -ne $script:outerObserverNonceValue -or
        $exitRelease.outer_process_id -ne [int]$script:outerObserverParentPidValue -or
        $exitRelease.outer_process_birth_utc_ticks -ne [int64]$script:outerObserverParentBirthTicksValue -or
        $exitRelease.inner_process_id -ne [int]$PID -or
        $exitRelease.inner_process_birth_utc_ticks -ne [int64]$script:innerRunnerBirthUtcTicks -or
        $exitRelease.runner_sha256 -ne $script:outerObserverRunnerSha256Value -or
        $exitRelease.review_token_id -ne $script:outerObserverOriginalTokenId -or
        $exitRelease.original_review_token_sha256 -ne $script:outerObserverOriginalTokenSha256 -or
        $exitRelease.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
        $exitRelease.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
        $exitRelease.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $exitRelease.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $exitRelease.inner_complete_relative_path -ne (Get-RepositoryRelativePath $paths.complete) -or
        $exitRelease.inner_complete_sha256 -ne $completeSha256 -or
        $exitRelease.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
        $exitRelease.intended_inner_exit_code -ne [int]$IntendedExitCode -or
        $exitRelease.release_inner_to_exit -isnot [bool] -or
        $exitRelease.release_inner_to_exit -ne $true -or
        $exitRelease.outer_resource_gate_pass_before_inner_exit -isnot [bool] -or
        $exitRelease.outer_resource_gate_pass_before_inner_exit -ne $true -or
        $exitRelease.terminal_seal_still_pending -isnot [bool] -or
        $exitRelease.terminal_seal_still_pending -ne $true -or
        $exitRelease.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
        $exitRelease.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false -or
        $exitRelease.monotonic_ns -le $complete.monotonic_ns
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: hidden inner exit release mismatch"
    }
    Assert-StrictJsonClrFieldTypes $exitRelease `
        @(
            "schema", "program", "case_id", "stage",
            "observer_session_relative_path", "observer_nonce", "runner_sha256",
            "review_token_id", "original_review_token_sha256",
            "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
            "inner_complete_relative_path", "inner_complete_sha256",
            "outer_observer_handshake_prefix_sha256", "released_utc"
        ) `
        @("outer_process_id", "inner_process_id", "intended_inner_exit_code") `
        @("outer_process_birth_utc_ticks", "inner_process_birth_utc_ticks", "monotonic_ns") `
        @(
            "terminal_seal_required_for_authoritative_disposition",
            "release_inner_to_exit", "outer_resource_gate_pass_before_inner_exit",
            "terminal_seal_still_pending",
            "external_runner_or_machine_kill_terminal_state_guaranteed"
        ) `
        "outer exit-release marker"
    $completeUtc = ConvertFrom-StrictOuterMarkerUtc $complete.completed_utc "outer inner-complete"
    $exitReleaseUtc = ConvertFrom-StrictOuterMarkerUtc $exitRelease.released_utc "outer exit-release"
    if ($exitReleaseUtc -lt $completeUtc) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer exit-release UTC precedes inner-complete"
    }
}

function Assert-OuterTerminalSealSourcesCurrent(
    [object]$Paths,
    [object]$ExpectedReady,
    [object]$ExpectedStartRelease,
    [object]$ExpectedComplete,
    [object]$ExpectedExitRelease,
    [object]$ExpectedEnvelopeClose,
    [string]$RunnerSha256,
    [string]$ReadySha256,
    [string]$StartReleaseSha256,
    [string]$CompleteSha256,
    [string]$ExitReleaseSha256,
    [string]$EnvelopeCloseRawSha256,
    [string]$EnvelopeCloseCanonicalSha256,
    [int64]$StdoutBytes,
    [string]$StdoutSha256,
    [int64]$StderrBytes,
    [string]$StderrSha256
) {
    if ((Get-Sha256 $runnerScriptPath) -ne $RunnerSha256) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: runner bytes changed before terminal seal"
    }
    Assert-QuarantinedFactorMonitorMarkerBindings $ExpectedComplete "outer terminal seal source current-byte recheck"
    $markerBindings = @(
        [pscustomobject]@{ label = "inner-ready"; path = $Paths.ready; sha256 = $ReadySha256; value = $ExpectedReady },
        [pscustomobject]@{ label = "outer-start-release"; path = $Paths.start_release; sha256 = $StartReleaseSha256; value = $ExpectedStartRelease },
        [pscustomobject]@{ label = "inner-complete"; path = $Paths.complete; sha256 = $CompleteSha256; value = $ExpectedComplete },
        [pscustomobject]@{ label = "outer-exit-release"; path = $Paths.exit_release; sha256 = $ExitReleaseSha256; value = $ExpectedExitRelease }
    )
    foreach ($binding in $markerBindings) {
        $relativePath = Get-RepositoryRelativePath ([string]$binding.path)
        $resolvedPath = Resolve-RepositoryRelativePath $relativePath
        $beforeSha256 = Get-Sha256 $resolvedPath
        $currentValue = Read-BoundedJsonObject $resolvedPath
        $afterSha256 = Get-Sha256 $resolvedPath
        if (
            $beforeSha256 -ne [string]$binding.sha256 -or
            $afterSha256 -ne [string]$binding.sha256 -or
            $null -eq $currentValue -or
            -not (Test-StrictJsonValueEqual $currentValue $binding.value)
        ) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal source changed: " + [string]$binding.label)
        }
    }

    $finalIndexPath = Resolve-RepositoryRelativePath ([string]$ExpectedComplete.final_control_plane_session_index_relative_path)
    $finalIndexSha256Before = Get-Sha256 $finalIndexPath
    $finalIndexValue = Read-BoundedJsonObject $finalIndexPath
    $finalIndexSha256After = Get-Sha256 $finalIndexPath
    if (
        $null -eq $finalIndexValue -or
        $finalIndexSha256Before -ne [string]$ExpectedComplete.final_control_plane_session_index_sha256 -or
        $finalIndexSha256After -ne [string]$ExpectedComplete.final_control_plane_session_index_sha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: final control-plane index changed before terminal seal"
    }

    $claimPathCurrent = Resolve-RepositoryRelativePath ([string]$ExpectedComplete.claim_relative_path)
    $preExitPathCurrent = Resolve-RepositoryRelativePath ([string]$ExpectedComplete.pre_exit_evidence_relative_path)
    $preExitSha256Before = Get-Sha256 $preExitPathCurrent
    $currentPreExit = Read-BoundedJsonObject $preExitPathCurrent
    $preExitSha256After = Get-Sha256 $preExitPathCurrent
    $currentTombstone = Read-BoundedJsonObject $reviewTokenPath
    if (
        -not (Test-Path -LiteralPath $claimPathCurrent -PathType Leaf) -or
        (Get-Sha256 $claimPathCurrent) -ne [string]$ExpectedComplete.claim_sha256 -or
        -not (Test-Path -LiteralPath $preExitPathCurrent -PathType Leaf) -or
        $null -eq $currentPreExit -or
        $preExitSha256Before -ne [string]$ExpectedComplete.pre_exit_evidence_sha256 -or
        $preExitSha256After -ne [string]$ExpectedComplete.pre_exit_evidence_sha256 -or
        $currentPreExit.terminal_evidence_complete -isnot [bool] -or
        $currentPreExit.terminal_evidence_complete -ne $false -or
        $null -eq $currentTombstone -or
        (Get-Sha256 $reviewTokenPath) -ne [string]$ExpectedComplete.tombstone_sha256 -or
        $currentTombstone.schema -ne $ExpectedComplete.tombstone_schema -or
        $currentTombstone.consumed_review_token_id -ne $ExpectedComplete.review_token_id -or
        $currentTombstone.outer_observer_contract_sha256 -ne $ExpectedComplete.outer_observer_contract_sha256 -or
        $currentTombstone.outer_observer_handshake_prefix_sha256 -ne $ExpectedComplete.outer_observer_handshake_prefix_sha256 -or
        $currentTombstone.expected_terminal_seal_relative_path -ne $ExpectedComplete.expected_terminal_seal_relative_path -or
        $currentTombstone.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $currentTombstone.terminal_seal_required_for_authoritative_disposition -ne $true -or
        $currentTombstone.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
        $currentTombstone.terminal_evidence_complete -isnot [bool] -or
        $currentTombstone.terminal_evidence_complete -ne $false -or
        $currentTombstone.authoritative_stage_pass -isnot [bool] -or
        $currentTombstone.authoritative_stage_pass -ne $false
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal durable claim/tombstone source changed"
    }
    foreach ($markerHashField in @(
        "monitor_ready_marker_sha256",
        "factor_complete_marker_sha256",
        "monitor_release_marker_sha256"
    )) {
        if ($currentPreExit.$markerHashField -ne $ExpectedComplete.$markerHashField) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal pre-exit/complete marker binding changed: " + $markerHashField)
        }
    }

    $consumerTerminalReferenceComplete = (
        $currentPreExit.consumer_control_gate_pass -is [bool] -and
        $currentPreExit.consumer_report_relative_path -is [string] -and
        -not [string]::IsNullOrWhiteSpace([string]$currentPreExit.consumer_report_relative_path) -and
        [string]$currentPreExit.consumer_report_sha256 -match '^[0-9a-f]{64}$' -and
        $currentPreExit.consumer_envelope_close_relative_path -is [string] -and
        -not [string]::IsNullOrWhiteSpace([string]$currentPreExit.consumer_envelope_close_relative_path) -and
        [string]$currentPreExit.consumer_envelope_close_sha256 -match '^[0-9a-f]{64}$'
    )
    if (-not $consumerTerminalReferenceComplete) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: token-consumer control report/close reference is missing; terminal seal withheld"
    }
    $consumerReportPathCurrent = Resolve-RepositoryRelativePath ([string]$currentPreExit.consumer_report_relative_path)
    $consumerEnvelopeClosePathCurrent = Resolve-RepositoryRelativePath ([string]$currentPreExit.consumer_envelope_close_relative_path)
    if (
        -not (Test-Path -LiteralPath $consumerReportPathCurrent -PathType Leaf) -or
        -not (Test-Path -LiteralPath $consumerEnvelopeClosePathCurrent -PathType Leaf)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: token-consumer control report/close source is missing; terminal seal withheld"
    }
    $consumerReportSha256Before = Get-Sha256 $consumerReportPathCurrent
    $consumerEnvelopeCloseSha256Before = Get-Sha256 $consumerEnvelopeClosePathCurrent
    $consumerEnvelopeCloseCurrent = Read-BoundedJsonObject $consumerEnvelopeClosePathCurrent
    $consumerReportSha256After = Get-Sha256 $consumerReportPathCurrent
    $consumerEnvelopeCloseSha256After = Get-Sha256 $consumerEnvelopeClosePathCurrent
    if (
        $consumerReportSha256Before -ne [string]$currentPreExit.consumer_report_sha256 -or
        $consumerReportSha256After -ne [string]$currentPreExit.consumer_report_sha256 -or
        $consumerEnvelopeCloseSha256Before -ne [string]$currentPreExit.consumer_envelope_close_sha256 -or
        $consumerEnvelopeCloseSha256After -ne [string]$currentPreExit.consumer_envelope_close_sha256 -or
        $null -eq $consumerEnvelopeCloseCurrent -or
        $consumerEnvelopeCloseCurrent.schema -ne $controlPlaneEnvelopeCloseSchema -or
        $consumerEnvelopeCloseCurrent.operation -ne "token_consumer" -or
        $consumerEnvelopeCloseCurrent.report_path -ne $consumerReportPathCurrent -or
        $consumerEnvelopeCloseCurrent.report_sha256 -ne [string]$currentPreExit.consumer_report_sha256 -or
        $consumerEnvelopeCloseCurrent.mandatory_control_plane_gate_pass -isnot [bool] -or
        $consumerEnvelopeCloseCurrent.mandatory_control_plane_gate_pass -ne $currentPreExit.consumer_control_gate_pass -or
        $finalIndexValue.schema -ne "AV-BS1-h4-p0r-control-plane-session-index-v1" -or
        $finalIndexValue.index_role -ne "final_binds_resource_envelope_close" -or
        $finalIndexValue.envelope_close_path -ne $consumerEnvelopeClosePathCurrent -or
        $finalIndexValue.envelope_close_sha256 -ne [string]$currentPreExit.consumer_envelope_close_sha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: token-consumer control report/close source changed; terminal seal withheld"
    }
    if (
        ($null -eq $ExpectedComplete.published_result_relative_path) -ne
        ($null -eq $ExpectedComplete.published_result_sha256)
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal published result path/hash null-pair mismatch"
    }
    if ($null -ne $ExpectedComplete.published_result_relative_path) {
        $publishedPath = Resolve-RepositoryRelativePath ([string]$ExpectedComplete.published_result_relative_path)
        if (
            -not (Test-Path -LiteralPath $publishedPath -PathType Leaf) -or
            (Get-Sha256 $publishedPath) -ne [string]$ExpectedComplete.published_result_sha256 -or
            $null -eq $ExpectedComplete.result_file_sha256 -or
            $ExpectedComplete.published_result_sha256 -ne $ExpectedComplete.result_file_sha256
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal published result source changed"
        }
        $publishedWrapper = Read-BoundedJsonObject $publishedPath
        if (
            $null -eq $publishedWrapper -or
            -not (Test-ExactJsonFieldSet $publishedWrapper @("payload", "payload_sha256")) -or
            $publishedWrapper.payload.schema -notin @(
                "AV-BS1-h4-p0r-result-v2", "AV-BS1-h4-p0r-failure-v1"
            )
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal published result wrapper changed"
        }
        if ($publishedWrapper.payload.schema -eq "AV-BS1-h4-p0r-result-v2") {
            if (
                $publishedWrapper.payload.outer_observer_contract_sha256 -ne $ExpectedComplete.outer_observer_contract_sha256 -or
                $publishedWrapper.payload.outer_observer_handshake_prefix_sha256 -ne $ExpectedComplete.outer_observer_handshake_prefix_sha256 -or
                $publishedWrapper.payload.expected_terminal_seal_relative_path -ne $ExpectedComplete.expected_terminal_seal_relative_path -or
                $publishedWrapper.payload.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
                $publishedWrapper.payload.terminal_seal_required_for_authoritative_disposition -ne $true -or
                $publishedWrapper.payload.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
                $publishedWrapper.payload.terminal_evidence_complete -isnot [bool] -or
                $publishedWrapper.payload.terminal_evidence_complete -ne $false -or
                $publishedWrapper.payload.authoritative_stage_pass -isnot [bool] -or
                $publishedWrapper.payload.authoritative_stage_pass -ne $false
            ) {
                throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal result-v2 pending state changed"
            }
        }
        if (
            $ExpectedComplete.intended_inner_exit_code -eq 0 -and (
                $publishedWrapper.payload.schema -ne "AV-BS1-h4-p0r-result-v2" -or
                $publishedWrapper.payload.mandatory_stage_pass -isnot [bool] -or
                $publishedWrapper.payload.mandatory_stage_pass -ne $true -or
                $publishedWrapper.payload_sha256 -ne $currentTombstone.consumed_result_payload_sha256 -or
                -not (Test-StrictJsonValueEqual $publishedWrapper.payload $currentTombstone.result_evidence)
            )
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal provisional result-v2 pass is not strict"
        }
    }

    $envelopeRelativePath = Get-RepositoryRelativePath $Paths.envelope_close
    $envelopePath = Resolve-RepositoryRelativePath $envelopeRelativePath
    $envelopeItemBefore = Get-Item -LiteralPath $envelopePath
    if ($envelopeItemBefore.Length -le 0 -or $envelopeItemBefore.Length -gt 16MB) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer resource envelope close size is invalid"
    }
    $envelopeRawSha256Before = Get-Sha256 $envelopePath
    $envelopeCurrent = Read-BoundedJsonObject $envelopePath
    $envelopeText = [IO.File]::ReadAllText($envelopePath, (New-Object System.Text.UTF8Encoding($false)))
    $envelopeRawSha256After = Get-Sha256 $envelopePath
    if (
        $null -eq $envelopeCurrent -or
        -not (Test-StrictJsonValueEqual $envelopeCurrent $ExpectedEnvelopeClose) -or
        $envelopeRawSha256Before -ne $EnvelopeCloseRawSha256 -or
        $envelopeRawSha256After -ne $EnvelopeCloseRawSha256 -or
        (Get-Utf8TextSha256 $envelopeText) -ne $EnvelopeCloseCanonicalSha256 -or
        $envelopeCurrent.schema -ne $outerObserverEnvelopeCloseSchema -or
        $envelopeCurrent.raw_bytes_are_canonical_json -isnot [bool] -or
        $envelopeCurrent.raw_bytes_are_canonical_json -ne $true -or
        $envelopeCurrent.mandatory_outer_resource_gate_pass -isnot [bool] -or
        $envelopeCurrent.mandatory_outer_resource_gate_pass -ne $true
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer resource envelope close changed before terminal seal"
    }

    foreach ($stream in @(
        [pscustomobject]@{ label = "stdout"; path = $Paths.stdout; bytes = $StdoutBytes; sha256 = $StdoutSha256 },
        [pscustomobject]@{ label = "stderr"; path = $Paths.stderr; bytes = $StderrBytes; sha256 = $StderrSha256 }
    )) {
        if (-not (Test-Path -LiteralPath $stream.path -PathType Leaf)) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer inner stream disappeared: " + [string]$stream.label)
        }
        $sizeBefore = [int64](Get-Item -LiteralPath $stream.path).Length
        $sha256Before = Get-Sha256 $stream.path
        $sizeAfter = [int64](Get-Item -LiteralPath $stream.path).Length
        $sha256After = Get-Sha256 $stream.path
        if (
            $sizeBefore -ne [int64]$stream.bytes -or
            $sizeAfter -ne [int64]$stream.bytes -or
            $sizeAfter -gt 16MB -or
            $sha256Before -ne [string]$stream.sha256 -or
            $sha256After -ne [string]$stream.sha256
        ) {
            throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer inner stream changed: " + [string]$stream.label)
        }
    }
}

function Invoke-OuterObserverPrimary {
    if (-not [string]::IsNullOrEmpty($InternalMode) -or $observerInputsPresent) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: public primary dispatcher inputs are invalid"
    }
    $startedUtc = [DateTimeOffset]::UtcNow
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $stopReason = $null
    $currentOuterOperation = "outer_initial_identity"
    $monitorFailure = $null
    $treeSampleDiagnostics = New-TreeSampleDiagnostics $treeSampleRetryEventLimit
    $outerBirthTicks = Get-ProcessBirthTicks $PID
    $outerObservedIds = New-Object 'System.Collections.Generic.HashSet[int]'
    $outerObservedBirthTicks = New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
    $outerObservedBirthTicks.Add([int]$PID, [int64]$outerBirthTicks)
    [void]$outerObservedIds.Add([int]$PID)
    $peak = [ordered]@{
        tree_working_set_bytes = [int64]0
        tree_summed_process_lifetime_peak_working_set_bytes = [int64]0
        tree_private_commit_bytes = [int64]0
        tree_committed_pagefile_bytes = [int64]0
        tree_summed_process_lifetime_peak_commit_bytes = [int64]0
        tree_nonprivate_working_set_proxy_bytes = [int64]0
        tree_page_fault_count = [int64]0
        system_commit_total_bytes = [int64]0
        system_commit_headroom_min_bytes = [int64]::MaxValue
        available_physical_min_bytes = [int64]::MaxValue
    }
    $currentOuterOperation = "outer_initial_system_sample"
    $preSystem = Get-SystemSample
    $currentOuterOperation = "outer_initial_tree_sample"
    $preTree = Get-TreeSample `
        $PID $outerBirthTicks $outerObservedIds $outerObservedBirthTicks `
        "outer_initial_tree_sample" $treeSampleDiagnostics $null $treeSampleMaximumAttempts
    Update-ControlPlanePeak $peak $preTree $preSystem
    if ($preSystem.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) {
        throw "BLOCKED_AV_BS_RESOURCE: outer initial commit headroom is below the high floor"
    }
    if ($preSystem.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) {
        throw "BLOCKED_AV_BS_RESOURCE: outer initial physical memory is below the high floor"
    }
    if (
        $preTree.working_set_bytes -gt $treeWorkingSetStop -or
        $preTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop -or
        $preTree.private_commit_bytes -gt $treePrivateStop -or
        $preTree.committed_pagefile_bytes -gt $treeCommitStop -or
        $preTree.summed_process_peak_commit_bytes -gt $treeCommitStop
    ) {
        throw "BLOCKED_AV_BS_RESOURCE: outer initial process envelope failed"
    }
    $runnerSha256 = Get-Sha256 $runnerScriptPath
    $tokenSha256Before = Get-Sha256 $reviewTokenPath
    $originalTokenBytes = [IO.File]::ReadAllBytes($reviewTokenPath)
    $originalTokenSha256 = Get-Sha256 $reviewTokenPath
    if ($tokenSha256Before -ne $originalTokenSha256) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: public dispatcher token changed during capture"
    }
    $candidateToken = Read-BoundedJsonObject $reviewTokenPath
    if (
        $null -eq $candidateToken -or
        $candidateToken.schema -ne "AV-BS1-h4-p0r-review-token-v1" -or
        $candidateToken.authorization_state -ne "authorized" -or
        $candidateToken.uses_remaining -isnot [int] -or
        $candidateToken.uses_remaining -ne 1 -or
        [string]$candidateToken.review_token_id -notmatch '^[0-9a-f]{32}$' -or
        $candidateToken.runner_sha256 -ne $runnerSha256 -or
        [string]$candidateToken.outer_observer_contract_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $candidateToken.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
        $candidateToken.terminal_seal_required_for_authoritative_disposition -ne $true
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: public dispatcher candidate token lacks observer bindings"
    }
    $sealPathInfo = Get-OuterObserverTerminalSealPath `
        ([string]$candidateToken.review_token_id) `
        ([string]$candidateToken.expected_terminal_seal_relative_path)
    if (Test-Path -LiteralPath $sealPathInfo.path) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal seal already exists"
    }

    New-Item -ItemType Directory -Path $validationRoot -Force | Out-Null
    $outerRoot = [IO.Path]::GetFullPath((Join-Path $validationRoot "outer-observer"))
    [IO.Directory]::CreateDirectory($outerRoot) | Out-Null
    [IO.Directory]::CreateDirectory($sealPathInfo.root) | Out-Null
    $observerNonce = [guid]::NewGuid().ToString("N")
    $sessionPath = [IO.Path]::GetFullPath((Join-Path $outerRoot ("session-" + $observerNonce)))
    if (Test-Path -LiteralPath $sessionPath) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer session collision"
    }
    New-Item -ItemType Directory -Path $sessionPath -ErrorAction Stop | Out-Null
    $paths = Get-OuterObserverSessionPaths $sessionPath $observerNonce
    $hostProcess = Get-Process -Id $PID -ErrorAction Stop
    $hostExecutable = [IO.Path]::GetFullPath([string]$hostProcess.Path)
    if (-not (Test-Path -LiteralPath $hostExecutable -PathType Leaf)) {
        throw "BLOCKED_AV_BS_RESOURCE: current PowerShell executable is unavailable"
    }

    $process = $null
    $processLaunched = $false
    $innerProcessId = $null
    $innerBirthTicks = $null
    $innerParentIdentityVerified = $false
    $retainedHandle = [IntPtr]::Zero
    $retainedHandleAcquired = $false
    $readyObserved = $false
    $startReleased = $false
    $completeObserved = $false
    $exitReleased = $false
    $readySha256 = $null
    $startReleaseSha256 = $null
    $completeSha256 = $null
    $exitReleaseSha256 = $null
    $readyValue = $null
    $startRelease = $null
    $completeValue = $null
    $exitRelease = $null
    $handshakePrefix = $null
    $handshakePrefixSha256 = $null
    $reportedExitCode = $null
    $actualExitCode = $null
    $outerFailureMessage = $null
    $monitorErrorPresent = $false
    $cleanupAttempted = $false
    $cleanupVerified = $false
    $innerProcessLiveAfterCleanup = $null
    $ownedDescendantSurvivors = @()
    $sampleCount = 0
    $innerVisibleSampleCount = 0
    $finalStdoutBytes = $null
    $finalStdoutSha256 = $null
    $finalStderrBytes = $null
    $finalStderrSha256 = $null
    $innerOwnedIds = New-Object 'System.Collections.Generic.HashSet[int]'
    # Share identity evidence across outer and inner sampling contexts while
    # retaining separate ID sets as the sole cleanup-ownership boundaries.
    $innerOwnedBirthTicks = $outerObservedBirthTicks
    $preSpawnSystem = $null
    $finalSystem = $null
    $finalTree = $null
    try {
        $currentOuterOperation = "outer_pre_spawn_system_sample"
        $preSpawnSystem = Get-SystemSample
        $currentOuterOperation = "outer_pre_spawn_tree_sample"
        $preSpawnTree = Get-TreeSample `
            $PID $outerBirthTicks $outerObservedIds $outerObservedBirthTicks `
            "outer_pre_spawn_tree_sample" $treeSampleDiagnostics $null $treeSampleMaximumAttempts
        Update-ControlPlanePeak $peak $preSpawnTree $preSpawnSystem
        if ($preSpawnSystem.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) { $stopReason = "OUTER_PRESPAWN_COMMIT_HEADROOM_STOP" }
        elseif ($preSpawnSystem.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) { $stopReason = "OUTER_PRESPAWN_AVAILABLE_PHYSICAL_STOP" }
        elseif ($preSpawnTree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "OUTER_TREE_WS_STOP" }
        elseif ($preSpawnTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "OUTER_TREE_LIFETIME_PEAK_WS_STOP" }
        elseif ($preSpawnTree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "OUTER_TREE_PRIVATE_STOP" }
        elseif ($preSpawnTree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "OUTER_TREE_COMMIT_STOP" }
        elseif ($preSpawnTree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "OUTER_TREE_LIFETIME_PEAK_COMMIT_STOP" }
        elseif ($stopwatch.Elapsed.TotalSeconds -gt $wallStopSeconds) { $stopReason = "OUTER_WALL_TIME_STOP" }
        if ($stopReason) { throw "BLOCKED_AV_BS_RESOURCE: outer pre-spawn gate failed after setup" }
        $arguments = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", (Quote-Argument $runnerScriptPath),
            "-Stage", "primary-h4-p0r",
            "-InternalMode", $outerInternalModeName,
            "-OuterObserverSessionPath", (Quote-Argument $paths.session),
            "-OuterObserverNonce", $observerNonce,
            "-OuterObserverParentProcessId", [string]$PID,
            "-OuterObserverParentBirthUtcTicks", [string]$outerBirthTicks,
            "-OuterObserverRunnerSha256", $runnerSha256,
            "-OuterObserverReviewTokenSha256", $originalTokenSha256
        )
        $currentOuterOperation = "outer_inner_spawn"
        $process = Start-Process -FilePath $hostExecutable `
            -ArgumentList ($arguments -join " ") -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $paths.stdout -RedirectStandardError $paths.stderr
        $processLaunched = $true
        $innerProcessId = [int]$process.Id
        $innerBirthTicks = [int64]$process.StartTime.ToUniversalTime().Ticks
        if ($innerProcessId -eq [int]$PID -or $innerBirthTicks -lt [int64]$outerBirthTicks) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: spawned inner identity chronology is invalid"
        }
        $innerOwnedBirthTicks.Add($innerProcessId, $innerBirthTicks)
        [void]$innerOwnedIds.Add($innerProcessId)
        [void]$outerObservedIds.Add($innerProcessId)
        $retainedHandle = $process.Handle
        if ($retainedHandle -eq [IntPtr]::Zero) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer observer did not retain the inner process handle"
        }
        $retainedHandleAcquired = $true
        $parents = [AvBsH4P0RNativeV1]::ProcessParents()
        if (-not $parents.ContainsKey($innerProcessId) -or [int]$parents[$innerProcessId] -ne [int]$PID) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: spawned inner process is not a real outer child"
        }
        $innerParentIdentityVerified = $true

        while ($true) {
            $process.Refresh()
            if ($process.HasExited) { break }
            $currentOuterOperation = "outer_tree_sample"
            $outerTree = Get-TreeSample `
                $PID $outerBirthTicks $outerObservedIds $outerObservedBirthTicks `
                "outer_tree_sample" $treeSampleDiagnostics $null $treeSampleMaximumAttempts
            $currentOuterOperation = "inner_tree_sample"
            [void](Get-TreeSample `
                $innerProcessId $innerBirthTicks $innerOwnedIds $innerOwnedBirthTicks `
                "inner_tree_sample" $treeSampleDiagnostics $null $treeSampleMaximumAttempts)
            $currentOuterOperation = "outer_system_sample"
            $system = Get-SystemSample
            Update-ControlPlanePeak $peak $outerTree $system
            $sampleCount += 1
            if (@($outerTree.process_ids) -contains $innerProcessId) { $innerVisibleSampleCount += 1 }
            if ($outerTree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "OUTER_TREE_WS_STOP" }
            elseif ($outerTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "OUTER_TREE_LIFETIME_PEAK_WS_STOP" }
            elseif ($outerTree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "OUTER_TREE_PRIVATE_STOP" }
            elseif ($outerTree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "OUTER_TREE_COMMIT_STOP" }
            elseif ($outerTree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "OUTER_TREE_LIFETIME_PEAK_COMMIT_STOP" }
            elseif ($system.commit_headroom_bytes -lt $commitHeadroomFloor) { $stopReason = "OUTER_SYSTEM_COMMIT_HEADROOM_STOP" }
            elseif ($system.available_physical_bytes -lt $availablePhysicalFloor) { $stopReason = "OUTER_AVAILABLE_PHYSICAL_STOP" }
            elseif ($stopwatch.Elapsed.TotalSeconds -gt $wallStopSeconds) { $stopReason = "OUTER_WALL_TIME_STOP" }
            elseif ((Test-Path -LiteralPath $paths.stdout -PathType Leaf) -and (Get-Item -LiteralPath $paths.stdout).Length -gt 16MB) { $stopReason = "OUTER_STDOUT_SIZE_STOP" }
            elseif ((Test-Path -LiteralPath $paths.stderr -PathType Leaf) -and (Get-Item -LiteralPath $paths.stderr).Length -gt 16MB) { $stopReason = "OUTER_STDERR_SIZE_STOP" }
            if ($stopReason) { throw "BLOCKED_AV_BS_RESOURCE: outer observer resource envelope stopped the inner"
            }

            if (-not $readyObserved -and (Test-Path -LiteralPath $paths.ready -PathType Leaf)) {
                $currentOuterOperation = "outer_ready_handshake"
                if (
                    (Get-Sha256 $runnerScriptPath) -ne $runnerSha256 -or
                    (Get-Sha256 $reviewTokenPath) -ne $originalTokenSha256 -or
                    -not (Test-ByteArrayEqual ([IO.File]::ReadAllBytes($reviewTokenPath)) $originalTokenBytes)
                ) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: runner or token changed before outer start release"
                }
                $readyValue = Read-BoundedJsonObject $paths.ready
                Assert-OuterInnerReadyMarker `
                    $readyValue $paths $candidateToken $runnerSha256 $originalTokenSha256 `
                    $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks
                $readySha256 = Get-Sha256 $paths.ready
                $startRelease = [ordered]@{
                    schema = $outerStartReleaseSchema
                    program = $program
                    case_id = "AV-BS1-CIRCLE-PRIMARY"
                    stage = "primary-h4-p0r"
                    observer_session_relative_path = $paths.session_relative_path
                    observer_nonce = $observerNonce
                    outer_process_id = [int]$PID
                    outer_process_birth_utc_ticks = [int64]$outerBirthTicks
                    inner_process_id = [int]$innerProcessId
                    inner_process_birth_utc_ticks = [int64]$innerBirthTicks
                    runner_sha256 = $runnerSha256
                    review_token_sha256 = $originalTokenSha256
                    review_token_id = [string]$candidateToken.review_token_id
                    outer_observer_contract_sha256 = [string]$candidateToken.outer_observer_contract_sha256
                    expected_terminal_seal_relative_path = [string]$candidateToken.expected_terminal_seal_relative_path
                    terminal_seal_required_for_authoritative_disposition = $true
                    inner_ready_relative_path = Get-RepositoryRelativePath $paths.ready
                    inner_ready_sha256 = $readySha256
                    release_scope = "permit_inner_preflight_and_claim_only"
                    claim_handshake_prefix_ready = $true
                    terminal_seal_still_pending = $true
                    external_runner_or_machine_kill_terminal_state_guaranteed = $false
                    monotonic_ns = Get-MonotonicNanoseconds
                    released_utc = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-AtomicUtf8NoBom $paths.start_release ($startRelease | ConvertTo-Json -Depth 8 -Compress)
                $startReleaseSha256 = Get-Sha256 $paths.start_release
                $prefixReference = New-OuterObserverHandshakePrefix `
                    (Get-RepositoryRelativePath $paths.ready) $readySha256 `
                    (Get-RepositoryRelativePath $paths.start_release) $startReleaseSha256
                $handshakePrefix = $prefixReference.value
                $handshakePrefixSha256 = $prefixReference.sha256
                $readyObserved = $true
                $startReleased = $true
            }

            if (-not $completeObserved -and (Test-Path -LiteralPath $paths.complete -PathType Leaf)) {
                $currentOuterOperation = "outer_complete_handshake"
                if (-not $startReleased) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner completion appeared before start release"
                }
                $completeValue = Read-BoundedJsonObject $paths.complete
                Assert-OuterInnerCompleteMarker `
                    $completeValue $paths $candidateToken $runnerSha256 $originalTokenSha256 `
                    $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks `
                    $handshakePrefix $handshakePrefixSha256 $readySha256 $startReleaseSha256
                $completeSha256 = Get-Sha256 $paths.complete
                $reportedExitCode = [int]$completeValue.intended_inner_exit_code
                $exitRelease = [ordered]@{
                    schema = $outerExitReleaseSchema
                    program = $program
                    case_id = "AV-BS1-CIRCLE-PRIMARY"
                    stage = "primary-h4-p0r"
                    observer_session_relative_path = $paths.session_relative_path
                    observer_nonce = $observerNonce
                    outer_process_id = [int]$PID
                    outer_process_birth_utc_ticks = [int64]$outerBirthTicks
                    inner_process_id = [int]$innerProcessId
                    inner_process_birth_utc_ticks = [int64]$innerBirthTicks
                    runner_sha256 = $runnerSha256
                    review_token_id = [string]$candidateToken.review_token_id
                    original_review_token_sha256 = $originalTokenSha256
                    outer_observer_contract_sha256 = [string]$candidateToken.outer_observer_contract_sha256
                    expected_terminal_seal_relative_path = [string]$candidateToken.expected_terminal_seal_relative_path
                    terminal_seal_required_for_authoritative_disposition = $true
                    inner_complete_relative_path = Get-RepositoryRelativePath $paths.complete
                    inner_complete_sha256 = $completeSha256
                    outer_observer_handshake_prefix_sha256 = $handshakePrefixSha256
                    intended_inner_exit_code = [int]$reportedExitCode
                    release_inner_to_exit = $true
                    outer_resource_gate_pass_before_inner_exit = $true
                    terminal_seal_still_pending = $true
                    external_runner_or_machine_kill_terminal_state_guaranteed = $false
                    monotonic_ns = Get-MonotonicNanoseconds
                    released_utc = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-AtomicUtf8NoBom $paths.exit_release ($exitRelease | ConvertTo-Json -Depth 8 -Compress)
                $exitReleaseSha256 = Get-Sha256 $paths.exit_release
                $completeObserved = $true
                $exitReleased = $true
            }
            Start-Sleep -Milliseconds $pollMilliseconds
        }
        $currentOuterOperation = "outer_retained_inner_exit"
        $process.WaitForExit()
        $actualExitCode = [int]$process.ExitCode
        if (-not $completeObserved -or -not $exitReleased) {
            $stopReason = "OUTER_INNER_EXIT_BEFORE_COMPLETE_HANDSHAKE"
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: inner exited before the complete/exit-release handshake"
        }
        if ($actualExitCode -ne $reportedExitCode) {
            $stopReason = "OUTER_INNER_EXIT_CODE_MISMATCH"
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: retained inner actual exit differs from completion"
        }
    }
    catch {
        $outerFailureMessage = $_.Exception.Message
        $monitorFailure = Get-NormalizedOuterMonitorFailure `
            $_.Exception ([string]$currentOuterOperation)
        $monitorErrorPresent = $true
        if (-not $stopReason) {
            $stopReason = if ($outerFailureMessage -like "BLOCKED_AV_BS_RESOURCE:*") {
                "OUTER_RESOURCE_EXCEPTION"
            } else {
                "OUTER_OBSERVER_EXCEPTION"
            }
        }
    }
    finally {
        $currentOuterOperation = "outer_cleanup"
        if ($processLaunched -and $null -ne $process) {
            try {
                $process.Refresh()
                $liveOwnedBeforeCleanup = @()
                if ($null -ne $innerBirthTicks -and $innerOwnedBirthTicks.ContainsKey([int]$innerProcessId)) {
                    if (-not $process.HasExited) {
                        try {
                            [void](Get-TreeSample `
                                $innerProcessId $innerBirthTicks $innerOwnedIds $innerOwnedBirthTicks `
                                "inner_cleanup_tree_sample" $treeSampleDiagnostics $null $treeSampleMaximumAttempts)
                        }
                        catch {
                            if ($null -eq $monitorFailure) {
                                $monitorFailure = Get-NormalizedOuterMonitorFailure `
                                    $_.Exception "inner_cleanup_tree_sample"
                            }
                            $monitorErrorPresent = $true
                            if (-not $stopReason) {
                                $stopReason = "OUTER_INNER_CLEANUP_FAILED"
                            }
                        }
                    }
                    $liveOwnedBeforeCleanup = @(Get-LiveObservedProcessIds $innerOwnedIds $innerOwnedBirthTicks)
                }
                if ((-not $process.HasExited) -or $liveOwnedBeforeCleanup.Count -gt 0) {
                    $cleanupAttempted = $true
                    if ($null -ne $innerBirthTicks -and $innerOwnedBirthTicks.ContainsKey([int]$innerProcessId)) {
                        Stop-ProcessTree $innerProcessId $innerOwnedIds $innerOwnedBirthTicks
                    }
                    elseif (-not $process.HasExited) {
                        $process.Kill()
                    }
                }
            }
            catch {
                $monitorErrorPresent = $true
                $stopReason = "OUTER_INNER_CLEANUP_FAILED"
                try {
                    $process.Refresh()
                    if (-not $process.HasExited) { $process.Kill() }
                } catch { }
            }
            try {
                if (-not $process.HasExited) { [void]$process.WaitForExit(10000) }
                $process.Refresh()
                if ($process.HasExited -and $null -eq $actualExitCode) {
                    $actualExitCode = [int]$process.ExitCode
                }
            }
            catch {
                $monitorErrorPresent = $true
                if (-not $stopReason) { $stopReason = "OUTER_INNER_EXIT_OBSERVATION_FAILED" }
            }
            if ($innerOwnedBirthTicks.ContainsKey([int]$innerProcessId)) {
                try {
                    $liveOwnedAfter = @(Get-LiveObservedProcessIds $innerOwnedIds $innerOwnedBirthTicks)
                    $innerProcessLiveAfterCleanup = @($liveOwnedAfter | Where-Object { $_ -eq $innerProcessId }).Count -gt 0
                    $ownedDescendantSurvivors = @($liveOwnedAfter | Where-Object { $_ -ne $innerProcessId })
                }
                catch {
                    $monitorErrorPresent = $true
                    $innerProcessLiveAfterCleanup = $true
                    $ownedDescendantSurvivors = @(-1)
                }
            }
            $cleanupVerified = (
                $innerProcessLiveAfterCleanup -eq $false -and
                @($ownedDescendantSurvivors).Count -eq 0
            )
        }
        else {
            $cleanupVerified = $true
            $innerProcessLiveAfterCleanup = $false
            $ownedDescendantSurvivors = @()
        }

        if ($cleanupVerified) {
            try {
                if (
                    -not (Test-Path -LiteralPath $paths.stdout -PathType Leaf) -or
                    -not (Test-Path -LiteralPath $paths.stderr -PathType Leaf)
                ) {
                    throw "outer inner redirected stream is missing after exit"
                }
                $finalStdoutBytes = [int64](Get-Item -LiteralPath $paths.stdout).Length
                $finalStderrBytes = [int64](Get-Item -LiteralPath $paths.stderr).Length
                $finalStdoutSha256 = Get-Sha256 $paths.stdout
                $finalStderrSha256 = Get-Sha256 $paths.stderr
                if ($finalStdoutBytes -gt 16MB) { $stopReason = "OUTER_STDOUT_SIZE_STOP" }
                elseif ($finalStderrBytes -gt 16MB) { $stopReason = "OUTER_STDERR_SIZE_STOP" }
                $currentOuterOperation = "outer_final_tree_sample"
                $finalTree = Get-TreeSample `
                    $PID $outerBirthTicks $outerObservedIds $outerObservedBirthTicks `
                    "outer_final_tree_sample" $treeSampleDiagnostics $null $treeSampleMaximumAttempts
                $currentOuterOperation = "outer_final_system_sample"
                $finalSystem = Get-SystemSample
                Update-ControlPlanePeak $peak $finalTree $finalSystem
                if ($finalTree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "OUTER_TREE_WS_STOP" }
                elseif ($finalTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "OUTER_TREE_LIFETIME_PEAK_WS_STOP" }
                elseif ($finalTree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "OUTER_TREE_PRIVATE_STOP" }
                elseif ($finalTree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "OUTER_TREE_COMMIT_STOP" }
                elseif ($finalTree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "OUTER_TREE_LIFETIME_PEAK_COMMIT_STOP" }
                elseif ($finalSystem.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) { $stopReason = "OUTER_POSTEXIT_COMMIT_HEADROOM_STOP" }
                elseif ($finalSystem.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) { $stopReason = "OUTER_POSTEXIT_AVAILABLE_PHYSICAL_STOP" }
            }
            catch {
                if ($null -eq $monitorFailure) {
                    $monitorFailure = Get-NormalizedOuterMonitorFailure `
                        $_.Exception ([string]$currentOuterOperation)
                }
                $monitorErrorPresent = $true
                if (-not $stopReason) { $stopReason = "OUTER_TERMINAL_SAMPLE_FAILED" }
            }
        }
        if ($stopwatch.IsRunning) { $stopwatch.Stop() }
    }

    $wallElapsedNanoseconds = [int64][Math]::Floor(
        [double]$stopwatch.ElapsedTicks * 1000000000.0 / [double][Diagnostics.Stopwatch]::Frequency
    )
    $endedUtc = [DateTimeOffset]::UtcNow
    $postCleanupClaimRelativePath = $null
    $postCleanupClaimSha256 = $null
    $postCleanupTokenSha256 = $null
    $postCleanupEmergencyReplacementPerformed = $false
    $postCleanupRecoveryState = "no_claim_no_recovery_required"
    $retainedInnerActualExitObserved = $false
    if (
        $processLaunched -and $retainedHandleAcquired -and
        $null -ne $process -and $null -ne $actualExitCode
    ) {
        try {
            $process.Refresh()
            $retainedInnerActualExitObserved = [bool]$process.HasExited
        }
        catch {
            $retainedInnerActualExitObserved = $false
        }
    }
    $outerRecoveryMutationGate = (
        $retainedInnerActualExitObserved -and
        $cleanupVerified -and
        $innerProcessLiveAfterCleanup -eq $false -and
        @($ownedDescendantSurvivors).Count -eq 0
    )
    $expectedClaimRelativePath = "validation-output/av-bs1/claims/" + [string]$candidateToken.review_token_id + ".json"
    $expectedClaimPath = [IO.Path]::GetFullPath((Join-Path $repositoryRoot ($expectedClaimRelativePath.Replace('/', '\'))))
    $currentOuterOperation = "outer_post_cleanup_classification"
    try {
        if (Test-Path -LiteralPath $expectedClaimPath -PathType Leaf) {
            $postCleanupClaimRelativePath = $expectedClaimRelativePath
            $postCleanupClaimSha256 = Get-Sha256 $expectedClaimPath
            $postCleanupRecoveryState = "claim_present_token_missing_or_unclassified_no_terminal_seal"
            $claimCandidate = Read-BoundedJsonObject $expectedClaimPath
            $outerExpectedClaimFields = @(
                "schema", "program", "case_id", "stage", "claim_relative_path",
                "review_token_id", "review_token_sha256",
                "review_token_canonical_sha256", "review_binding_sha256",
                "fixture_sha256", "runner_sha256", "p0r_preregistration_commit",
                "git_head", "preflight_payload_sha256", "manifest_payload_sha256",
                "matrix_contract_sha256", "resource_policy_sha256",
                "execution_resource_scope_sha256",
                "control_plane_preflight_report_relative_path",
                "control_plane_preflight_report_sha256",
                "control_plane_preflight_session_index_relative_path",
                "control_plane_preflight_session_index_sha256",
                "outer_observer_contract_sha256", "expected_terminal_seal_relative_path",
                "terminal_seal_required_for_authoritative_disposition",
                "outer_observer_handshake_prefix",
                "outer_observer_handshake_prefix_sha256", "guard_nonce", "parent_pid",
                "parent_pid_birth_utc_ticks", "created_utc"
            )
            $claimCandidateMatchesObserver = (
                $null -ne $claimCandidate -and
                (Test-ExactJsonFieldSet $claimCandidate $outerExpectedClaimFields) -and
                $claimCandidate.schema -eq "AV-BS1-h4-p0r-token-claim-v1" -and
                $claimCandidate.program -eq $program -and
                $claimCandidate.case_id -eq "AV-BS1-CIRCLE-PRIMARY" -and
                $claimCandidate.stage -eq "primary-h4-p0r" -and
                $claimCandidate.claim_relative_path -eq $expectedClaimRelativePath -and
                $claimCandidate.review_token_id -eq $candidateToken.review_token_id -and
                $claimCandidate.review_token_sha256 -eq $originalTokenSha256 -and
                [string]$claimCandidate.review_token_canonical_sha256 -match '^[0-9a-f]{64}$' -and
                [string]$claimCandidate.review_binding_sha256 -match '^[0-9a-f]{64}$' -and
                $claimCandidate.fixture_sha256 -eq $candidateToken.fixture_sha256 -and
                $claimCandidate.runner_sha256 -eq $runnerSha256 -and
                $claimCandidate.p0r_preregistration_commit -eq $candidateToken.p0r_preregistration_commit -and
                [string]$claimCandidate.git_head -match '^[0-9a-f]{40}$' -and
                [string]$claimCandidate.preflight_payload_sha256 -match '^[0-9a-f]{64}$' -and
                $claimCandidate.manifest_payload_sha256 -eq $candidateToken.manifest_payload_sha256 -and
                $claimCandidate.matrix_contract_sha256 -eq $candidateToken.matrix_contract_sha256 -and
                $claimCandidate.resource_policy_sha256 -eq $candidateToken.resource_policy_sha256 -and
                $claimCandidate.execution_resource_scope_sha256 -eq $candidateToken.execution_resource_scope_sha256 -and
                $claimCandidate.outer_observer_contract_sha256 -eq $candidateToken.outer_observer_contract_sha256 -and
                $claimCandidate.expected_terminal_seal_relative_path -eq $candidateToken.expected_terminal_seal_relative_path -and
                $claimCandidate.terminal_seal_required_for_authoritative_disposition -is [bool] -and
                $claimCandidate.terminal_seal_required_for_authoritative_disposition -eq $true -and
                $null -ne $handshakePrefix -and
                -not [string]::IsNullOrEmpty($handshakePrefixSha256) -and
                (Test-StrictJsonValueEqual $claimCandidate.outer_observer_handshake_prefix $handshakePrefix) -and
                $claimCandidate.outer_observer_handshake_prefix_sha256 -eq $handshakePrefixSha256 -and
                $claimCandidate.parent_pid -is [int] -and
                $claimCandidate.parent_pid -eq $innerProcessId -and
                $claimCandidate.parent_pid_birth_utc_ticks -eq $innerBirthTicks -and
                [string]$claimCandidate.guard_nonce -match '^[0-9a-f]{32}$'
            )
            if (Test-Path -LiteralPath $reviewTokenPath -PathType Leaf) {
                $currentTokenItem = Get-Item -LiteralPath $reviewTokenPath
                if ($currentTokenItem.Length -gt 0 -and $currentTokenItem.Length -le 16MB) {
                    $postCleanupTokenSha256 = Get-Sha256 $reviewTokenPath
                    $currentTokenBytes = [IO.File]::ReadAllBytes($reviewTokenPath)
                    if (
                        $claimCandidateMatchesObserver -and
                        $postCleanupTokenSha256 -eq $originalTokenSha256 -and
                        (Test-ByteArrayEqual $currentTokenBytes $originalTokenBytes) -and
                        $outerRecoveryMutationGate
                    ) {
                        $recoveryFailureMessage = if (
                            $null -ne $monitorFailure -and
                            -not [string]::IsNullOrEmpty([string]$monitorFailure.message_code)
                        ) {
                            "controlled outer observer failure: " + [string]$monitorFailure.message_code
                        } elseif (-not [string]::IsNullOrEmpty($stopReason)) {
                            "controlled outer observer failure: " + $stopReason
                        } else {
                            "controlled outer observer post-claim lifecycle did not reach a validated tombstone"
                        }
                        $recoveryFailureCode = Get-OuterObserverFailureCode ([string]$stopReason)
                        [void](Set-OuterObserverEmergencyConsumedTombstone `
                            $candidateToken $originalTokenBytes $originalTokenSha256 $runnerSha256 `
                            $claimCandidate $expectedClaimRelativePath $postCleanupClaimSha256 `
                            $handshakePrefix $handshakePrefixSha256 $observerNonce `
                            $retainedInnerActualExitObserved $cleanupVerified `
                            ([int]@($ownedDescendantSurvivors).Count) `
                            $recoveryFailureMessage $recoveryFailureCode)
                        $postCleanupEmergencyReplacementPerformed = $true
                        $postCleanupTokenSha256 = Get-Sha256 $reviewTokenPath
                        $postCleanupRecoveryState = "claim_present_exact_original_authorized_token_replaced_by_postvalidated_emergency_v2_no_terminal_seal"
                    }
                    elseif (
                        $claimCandidateMatchesObserver -and
                        $postCleanupTokenSha256 -eq $originalTokenSha256 -and
                        (Test-ByteArrayEqual $currentTokenBytes $originalTokenBytes)
                    ) {
                        $postCleanupRecoveryState = "claim_present_exact_original_authorized_token_outer_cleanup_unverified_no_mutation_no_terminal_seal"
                    }
                    elseif ($claimCandidateMatchesObserver) {
                        $currentPendingTombstone = Read-BoundedJsonObject $reviewTokenPath
                        if (
                            $null -ne $currentPendingTombstone -and
                            $currentPendingTombstone.schema -in @($tombstoneSchema, $emergencyTombstoneSchema) -and
                            $currentPendingTombstone.authorization_state -eq "consumed" -and
                            $currentPendingTombstone.uses_remaining -is [int] -and
                            $currentPendingTombstone.uses_remaining -eq 0 -and
                            $currentPendingTombstone.consumed_review_token_id -eq $candidateToken.review_token_id -and
                            $currentPendingTombstone.consumed_review_token_sha256 -eq $originalTokenSha256 -and
                            $currentPendingTombstone.claim_relative_path -eq $expectedClaimRelativePath -and
                            $currentPendingTombstone.claim_sha256 -eq $postCleanupClaimSha256 -and
                            $currentPendingTombstone.outer_observer_contract_sha256 -eq $candidateToken.outer_observer_contract_sha256 -and
                            $currentPendingTombstone.outer_observer_handshake_prefix_sha256 -eq $claimCandidate.outer_observer_handshake_prefix_sha256 -and
                            $currentPendingTombstone.expected_terminal_seal_relative_path -eq $candidateToken.expected_terminal_seal_relative_path -and
                            $currentPendingTombstone.terminal_seal_required_for_authoritative_disposition -is [bool] -and
                            $currentPendingTombstone.terminal_seal_required_for_authoritative_disposition -eq $true -and
                            $currentPendingTombstone.terminal_seal_state -eq "pending_outer_observed_inner_exit" -and
                            $currentPendingTombstone.terminal_evidence_complete -is [bool] -and
                            $currentPendingTombstone.terminal_evidence_complete -eq $false -and
                            $currentPendingTombstone.authoritative_stage_pass -is [bool] -and
                            $currentPendingTombstone.authoritative_stage_pass -eq $false
                        ) {
                            $postCleanupRecoveryState = "claim_present_v2_shaped_unverified_tombstone_remains_provisional_no_terminal_seal"
                        }
                    }
                }
            }
            if ($completeObserved -and -not $postCleanupEmergencyReplacementPerformed) {
                $currentCompleteForRecovery = Read-BoundedJsonObject $paths.complete
                Assert-OuterInnerCompleteMarker `
                    $currentCompleteForRecovery $paths $candidateToken $runnerSha256 $originalTokenSha256 `
                    $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks `
                    $handshakePrefix $handshakePrefixSha256 $readySha256 $startReleaseSha256
                if ((Get-Sha256 $paths.complete) -ne $completeSha256) {
                    throw "inner-complete bytes changed during post-cleanup recovery classification"
                }
                $postCleanupTokenSha256 = Get-Sha256 $reviewTokenPath
                $postCleanupRecoveryState = "claim_present_same_attempt_tombstone_validated_by_inner_complete"
            }
        }
        else {
            if (Test-Path -LiteralPath $reviewTokenPath -PathType Leaf) {
                $currentTokenItem = Get-Item -LiteralPath $reviewTokenPath
                if ($currentTokenItem.Length -gt 0 -and $currentTokenItem.Length -le 16MB) {
                    $currentTokenSha256BeforeByteCapture = Get-Sha256 $reviewTokenPath
                    $currentTokenBytes = [IO.File]::ReadAllBytes($reviewTokenPath)
                    $postCleanupTokenSha256 = Get-Sha256 $reviewTokenPath
                    if (
                        $currentTokenSha256BeforeByteCapture -eq $postCleanupTokenSha256 -and
                        $postCleanupTokenSha256 -eq $originalTokenSha256 -and
                        (Test-ByteArrayEqual $currentTokenBytes $originalTokenBytes)
                    ) {
                        $postCleanupRecoveryState = "no_claim_exact_original_authorized_token_retained_public_attempt_spent_no_recovery_performed"
                    }
                    else {
                        $postCleanupRecoveryState = "no_claim_token_present_but_drifted_no_recovery_no_terminal_seal"
                    }
                }
                else {
                    $postCleanupRecoveryState = "no_claim_token_present_but_drifted_no_recovery_no_terminal_seal"
                }
            }
            else {
                $postCleanupRecoveryState = "no_claim_token_absent_no_recovery_no_terminal_seal"
            }
        }
    }
    catch {
        if ($null -eq $monitorFailure) {
            $monitorFailure = Get-NormalizedOuterMonitorFailure `
                $_.Exception ([string]$currentOuterOperation)
        }
        $monitorErrorPresent = $true
        if (-not $stopReason) { $stopReason = "OUTER_POSTCLEANUP_TOKEN_CLASSIFICATION_FAILED" }
        $postCleanupClaimRelativePath = $null
        $postCleanupClaimSha256 = $null
        $postCleanupTokenSha256 = $null
        $postCleanupRecoveryState = "post_cleanup_token_or_claim_classification_failed_no_terminal_seal"
    }
    $highPrePostFloorsPass = (
        $null -ne $preSystem -and $null -ne $preSpawnSystem -and $null -ne $finalSystem -and
        $preSystem.commit_headroom_bytes -ge $minimumCommitHeadroomBeforeSpawn -and
        $preSystem.available_physical_bytes -ge $minimumAvailablePhysicalBeforeSpawn -and
        $preSpawnSystem.commit_headroom_bytes -ge $minimumCommitHeadroomBeforeSpawn -and
        $preSpawnSystem.available_physical_bytes -ge $minimumAvailablePhysicalBeforeSpawn -and
        $finalSystem.commit_headroom_bytes -ge $minimumCommitHeadroomBeforeSpawn -and
        $finalSystem.available_physical_bytes -ge $minimumAvailablePhysicalBeforeSpawn
    )
    $mandatoryOuterGate = (
        -not $monitorErrorPresent -and $null -eq $monitorFailure -and -not $stopReason -and
        -not [bool]$treeSampleDiagnostics.retry_events_truncated -and
        $processLaunched -and $retainedHandleAcquired -and
        $readyObserved -and $startReleased -and $completeObserved -and $exitReleased -and
        $actualExitCode -in @(0, 2) -and $actualExitCode -eq $reportedExitCode -and
        $postCleanupRecoveryState -eq "claim_present_same_attempt_tombstone_validated_by_inner_complete" -and
        -not $postCleanupEmergencyReplacementPerformed -and
        $cleanupVerified -and $sampleCount -ge 2 -and $innerVisibleSampleCount -ge 2 -and
        $null -ne $finalStdoutBytes -and $null -ne $finalStderrBytes -and
        $finalStdoutBytes -le 16MB -and $finalStderrBytes -le 16MB -and
        [string]$finalStdoutSha256 -match '^[0-9a-f]{64}$' -and
        [string]$finalStderrSha256 -match '^[0-9a-f]{64}$' -and
        $highPrePostFloorsPass -and
        $wallElapsedNanoseconds -le ([int64]$wallStopSeconds * 1000000000L) -and
        $peak.tree_working_set_bytes -le $treeWorkingSetStop -and
        $peak.tree_summed_process_lifetime_peak_working_set_bytes -le $treeWorkingSetStop -and
        $peak.tree_private_commit_bytes -le $treePrivateStop -and
        $peak.tree_committed_pagefile_bytes -le $treeCommitStop -and
        $peak.tree_summed_process_lifetime_peak_commit_bytes -le $treeCommitStop -and
        $peak.system_commit_headroom_min_bytes -ge $commitHeadroomFloor -and
        $peak.available_physical_min_bytes -ge $availablePhysicalFloor
    )
    $identityEvidence = @()
    foreach ($id in @($outerObservedBirthTicks.Keys | Sort-Object)) {
        $identityEvidence += [ordered]@{
            birth_utc_ticks = [int64]$outerObservedBirthTicks[[int]$id]
            process_id = [int]$id
        }
    }
    $canonicalPeak = [ordered]@{
        available_physical_min_bytes = [int64]$peak.available_physical_min_bytes
        system_commit_headroom_min_bytes = [int64]$peak.system_commit_headroom_min_bytes
        system_commit_total_bytes = [int64]$peak.system_commit_total_bytes
        tree_committed_pagefile_bytes = [int64]$peak.tree_committed_pagefile_bytes
        tree_nonprivate_working_set_proxy_bytes = [int64]$peak.tree_nonprivate_working_set_proxy_bytes
        tree_page_fault_count = [int64]$peak.tree_page_fault_count
        tree_private_commit_bytes = [int64]$peak.tree_private_commit_bytes
        tree_summed_process_lifetime_peak_commit_bytes = [int64]$peak.tree_summed_process_lifetime_peak_commit_bytes
        tree_summed_process_lifetime_peak_working_set_bytes = [int64]$peak.tree_summed_process_lifetime_peak_working_set_bytes
        tree_working_set_bytes = [int64]$peak.tree_working_set_bytes
    }
    $canonicalThresholds = [ordered]@{
        available_physical_floor_bytes = [int64]$availablePhysicalFloor
        high_available_physical_floor_bytes = [int64]$minimumAvailablePhysicalBeforeSpawn
        high_commit_headroom_floor_bytes = [int64]$minimumCommitHeadroomBeforeSpawn
        system_commit_headroom_floor_bytes = [int64]$commitHeadroomFloor
        tree_commit_stop_bytes = [int64]$treeCommitStop
        tree_private_stop_bytes = [int64]$treePrivateStop
        tree_working_set_stop_bytes = [int64]$treeWorkingSetStop
        wall_stop_seconds = [int]$wallStopSeconds
    }
    $canonicalTreeSampleRetryEvents = @()
    foreach ($event in @($treeSampleDiagnostics.retry_events)) {
        $canonicalTreeSampleRetryEvents += (New-TreeSampleEvidence `
            ([int]$event.attempt) $event.confirmation ([string]$event.context) `
            $event.expected_birth_utc_ticks ([string]$event.message_code) `
            $event.observed_birth_utc_ticks ([string]$event.operation) `
            $event.process_id $event.process_role $event.win32_error_code)
    }
    # Every key, nested key, string, Boolean, and number is emitted in Python
    # canonical order/form. Thus this UTF-8 raw file hash is also its canonical
    # JSON hash without launching an unbounded post-exit hash helper.
    $envelopeClose = [ordered]@{
        authorization_blocker = $true
        available_physical_min_bytes = [int64]$peak.available_physical_min_bytes
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        cleanup_verified = [bool]$cleanupVerified
        commit_headroom_min_bytes = [int64]$peak.system_commit_headroom_min_bytes
        ended_utc = $endedUtc.ToString("o")
        excluded_head = @(
            "public_PowerShell_process_startup_and_script_parse",
            "function_and_native_monitor_type_initialization",
            "immutable_runner_path_and_argument_discovery_before_dispatcher_stopwatch"
        )
        excluded_tail = @(
            "post_cleanup_token_and_claim_recovery_classification",
            "outer_resource_envelope_close_materialization_and_readback",
            "terminal_seal_source_rechecks_materialization_and_readback",
            "inner_stdout_text_return_packaging",
            "outer_observer_exit"
        )
        execution_tree_root_birth_utc_ticks = [int64]$outerBirthTicks
        execution_tree_root_pid = [int]$PID
        external_runner_or_machine_kill_terminal_state_guaranteed = $false
        high_post_available_physical_bytes = if ($null -ne $finalSystem) { [int64]$finalSystem.available_physical_bytes } else { $null }
        high_post_commit_headroom_bytes = if ($null -ne $finalSystem) { [int64]$finalSystem.commit_headroom_bytes } else { $null }
        high_pre_available_physical_bytes = if ($null -ne $preSystem) { [int64]$preSystem.available_physical_bytes } else { $null }
        high_pre_commit_headroom_bytes = if ($null -ne $preSystem) { [int64]$preSystem.commit_headroom_bytes } else { $null }
        high_pre_spawn_available_physical_bytes = if ($null -ne $preSpawnSystem) { [int64]$preSpawnSystem.available_physical_bytes } else { $null }
        high_pre_spawn_commit_headroom_bytes = if ($null -ne $preSpawnSystem) { [int64]$preSpawnSystem.commit_headroom_bytes } else { $null }
        inner_actual_exit_code = $actualExitCode
        inner_complete_relative_path = if ($completeObserved) { Get-RepositoryRelativePath $paths.complete } else { $null }
        inner_complete_sha256 = $completeSha256
        inner_exit_release_relative_path = if ($exitReleased) { Get-RepositoryRelativePath $paths.exit_release } else { $null }
        inner_exit_release_sha256 = $exitReleaseSha256
        inner_parent_identity_verified = [bool]$innerParentIdentityVerified
        inner_parent_process_birth_utc_ticks = if ($processLaunched) { [int64]$outerBirthTicks } else { $null }
        inner_parent_process_id = if ($processLaunched) { [int]$PID } else { $null }
        inner_process_birth_utc_ticks = $innerBirthTicks
        inner_process_id = $innerProcessId
        inner_ready_relative_path = if ($readyObserved) { Get-RepositoryRelativePath $paths.ready } else { $null }
        inner_ready_sha256 = $readySha256
        inner_stderr_bytes = $finalStderrBytes
        inner_stderr_sha256 = $finalStderrSha256
        inner_stdout_bytes = $finalStdoutBytes
        inner_stdout_sha256 = $finalStdoutSha256
        inner_visible_sample_count = [int]$innerVisibleSampleCount
        mandatory_outer_resource_gate_pass = [bool]$mandatoryOuterGate
        monitor_error_present = [bool]$monitorErrorPresent
        monitor_failure = $monitorFailure
        observed_lifecycle_scope = "post_dispatch_stopwatch_start_before_candidate_token_capture_through_inner_actual_exit_verified_cleanup_terminal_stream_hash_and_system_tree_sample"
        observer_nonce = $observerNonce
        observer_session_relative_path = $paths.session_relative_path
        original_review_token_sha256 = $originalTokenSha256
        outer_observer_contract_sha256 = [string]$candidateToken.outer_observer_contract_sha256
        peak = $canonicalPeak
        post_cleanup_claim_relative_path = $postCleanupClaimRelativePath
        post_cleanup_claim_sha256 = $postCleanupClaimSha256
        post_cleanup_emergency_replacement_performed = [bool]$postCleanupEmergencyReplacementPerformed
        post_cleanup_recovery_state = $postCleanupRecoveryState
        post_cleanup_terminal_seal_pending = $true
        post_cleanup_token_sha256 = $postCleanupTokenSha256
        process_membership_semantics = "sampled_not_Job_Object_outer_observer_plus_inner_runner_plus_identity_bound_descendants_between_samples_not_claimed"
        program = $program
        raw_bytes_are_canonical_json = $true
        review_token_id = [string]$candidateToken.review_token_id
        reviewed_contract_git_commit = [string]$candidateToken.p0r_preregistration_commit
        runner_sha256 = $runnerSha256
        sample_count = [int]$sampleCount
        sampled_process_identities = @($identityEvidence)
        schema = $outerObserverEnvelopeCloseSchema
        simultaneous_current_factor_interval_semantics = "inner_factor_resource_reports_measure_sampled_current_outer_plus_inner_plus_primary_helper_tree"
        started_utc = $startedUtc.ToString("o")
        stop_reason = $stopReason
        summed_os_lifetime_peak_semantics = "conservative_stop_gate_includes_outer_inner_preflight_control_and_factor_work_not_factor_only_peak"
        terminal_sample_after_inner_actual_exit_and_verified_cleanup = ($null -ne $finalTree -and $null -ne $finalSystem -and $cleanupVerified)
        thresholds = $canonicalThresholds
        tree_sample_confirmed_disappearance_count = [int]$treeSampleDiagnostics.confirmed_disappearance_count
        tree_sample_max_attempts = [int]$treeSampleMaximumAttempts
        tree_sample_retry_events = @($canonicalTreeSampleRetryEvents)
        tree_sample_retry_events_truncated = [bool]$treeSampleDiagnostics.retry_events_truncated
        wall_elapsed_nanoseconds = $wallElapsedNanoseconds
        wall_stop_seconds = [int]$wallStopSeconds
    }
    $envelopeCloseJson = $envelopeClose | ConvertTo-Json -Depth 16 -Compress
    Write-AtomicUtf8NoBom $paths.envelope_close $envelopeCloseJson
    $envelopeCloseRawSha256 = Get-Sha256 $paths.envelope_close
    $envelopeCloseCanonicalSha256 = Get-Utf8TextSha256 $envelopeCloseJson
    if ($envelopeCloseRawSha256 -ne $envelopeCloseCanonicalSha256) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer envelope close raw/canonical hash mismatch"
    }
    if (-not $mandatoryOuterGate) {
        $outerGateFailureCode = Get-OuterObserverFailureCode ([string]$stopReason)
        throw (
            $outerGateFailureCode + ": outer observer gate failed; missing terminal seal remains provisional; close=" +
            $paths.envelope_close
        )
    }

    # Revalidate every durable seal source after the retained inner has exited.
    # Only this outer process writes the seal, and no cached hash alone is
    # sufficient to authorize its materialization.
    Assert-OuterInnerReadyMarker `
        (Read-BoundedJsonObject $paths.ready) $paths $candidateToken $runnerSha256 $originalTokenSha256 `
        $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks
    Assert-OuterInnerCompleteMarker `
        (Read-BoundedJsonObject $paths.complete) $paths $candidateToken $runnerSha256 $originalTokenSha256 `
        $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks `
        $handshakePrefix $handshakePrefixSha256 $readySha256 $startReleaseSha256
    Assert-OuterTerminalSealSourcesCurrent `
        $paths $readyValue $startRelease $completeValue $exitRelease $envelopeClose `
        $runnerSha256 $readySha256 $startReleaseSha256 $completeSha256 $exitReleaseSha256 `
        $envelopeCloseRawSha256 $envelopeCloseCanonicalSha256 `
        $finalStdoutBytes $finalStdoutSha256 $finalStderrBytes $finalStderrSha256
    $terminalSourceRechecksPassed = $true
    $currentComplete = Read-BoundedJsonObject $paths.complete
    $currentTombstoneSha256 = Get-Sha256 $reviewTokenPath
    $currentTombstone = Read-BoundedJsonObject $reviewTokenPath
    $preExitPath = Resolve-RepositoryRelativePath ([string]$currentComplete.pre_exit_evidence_relative_path)
    $preExitSha256 = Get-Sha256 $preExitPath
    if (
        $null -eq $currentTombstone -or
        $currentTombstoneSha256 -ne $currentComplete.tombstone_sha256 -or
        $currentTombstone.schema -ne $currentComplete.tombstone_schema -or
        $preExitSha256 -ne $currentComplete.pre_exit_evidence_sha256
    ) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: terminal seal source evidence changed after close"
    }
    $currentPreExit = Read-BoundedJsonObject $preExitPath
    $tombstoneProvisionalSuccess = (
        $currentTombstone.schema -eq $tombstoneSchema -and
        $currentTombstone.attempt_status -eq "completed_pass" -and
        $currentTombstone.effective_attempt_status -eq "completed_pass" -and
        $currentTombstone.consumption_validated_pass -is [bool] -and
        $currentTombstone.consumption_validated_pass -eq $true -and
        $currentTombstone.mandatory_stage_pass -is [bool] -and
        $currentTombstone.mandatory_stage_pass -eq $true -and
        @($currentTombstone.failure_codes).Count -eq 0 -and
        $currentTombstone.terminal_evidence_complete -is [bool] -and
        $currentTombstone.terminal_evidence_complete -eq $false -and
        $currentTombstone.authoritative_stage_pass -is [bool] -and
        $currentTombstone.authoritative_stage_pass -eq $false -and
        $currentTombstone.outer_observer_contract_sha256 -eq $candidateToken.outer_observer_contract_sha256 -and
        $currentTombstone.outer_observer_handshake_prefix_sha256 -eq $handshakePrefixSha256 -and
        $currentTombstone.expected_terminal_seal_relative_path -eq $sealPathInfo.relative_path -and
        $currentTombstone.terminal_seal_required_for_authoritative_disposition -is [bool] -and
        $currentTombstone.terminal_seal_required_for_authoritative_disposition -eq $true -and
        $currentTombstone.terminal_seal_state -eq "pending_outer_observed_inner_exit" -and
        $currentTombstone.claim_evidence_valid -eq $true -and
        $currentTombstone.guard_evidence_valid -eq $true -and
        $currentTombstone.resource_evidence_valid -eq $true -and
        $currentTombstone.result_evidence_valid -is [bool] -and
        $currentTombstone.result_evidence_valid -eq $true -and
        $currentTombstone.child_stdout_evidence_valid -eq $true -and
        $currentTombstone.factor_prefix_evidence_valid -eq $true -and
        $currentTombstone.resource_gate_pass -eq $true -and
        $currentTombstone.factorization_attempted -eq $true -and
        $currentTombstone.factorization_performed -eq $true -and
        $null -ne $currentPreExit -and
        $currentPreExit.normal_pass_outer_evidence_reconciliation_pass -eq $true -and
        $null -ne $currentTombstone.result_evidence -and
        $currentTombstone.result_evidence.schema -eq "AV-BS1-h4-p0r-result-v2" -and
        $currentTombstone.result_evidence.outer_observer_contract_sha256 -eq $candidateToken.outer_observer_contract_sha256 -and
        $currentTombstone.result_evidence.outer_observer_handshake_prefix_sha256 -eq $handshakePrefixSha256 -and
        $currentTombstone.result_evidence.expected_terminal_seal_relative_path -eq $sealPathInfo.relative_path -and
        $currentTombstone.result_evidence.terminal_seal_required_for_authoritative_disposition -eq $true -and
        $currentTombstone.result_evidence.terminal_seal_state -eq "pending_outer_observed_inner_exit" -and
        $currentTombstone.result_evidence.mandatory_stage_pass -is [bool] -and
        $currentTombstone.result_evidence.mandatory_stage_pass -eq $true -and
        $currentTombstone.result_evidence.terminal_evidence_complete -is [bool] -and
        $currentTombstone.result_evidence.terminal_evidence_complete -eq $false -and
        $currentTombstone.result_evidence.authoritative_stage_pass -is [bool] -and
        $currentTombstone.result_evidence.authoritative_stage_pass -eq $false -and
        $currentComplete.published_result_relative_path -is [string] -and
        $currentComplete.published_result_sha256 -is [string] -and
        $currentComplete.result_file_sha256 -eq $currentComplete.published_result_sha256
    )
    $authoritativeStagePass = (
        $mandatoryOuterGate -and
        $terminalSourceRechecksPassed -and
        $tombstoneProvisionalSuccess -and
        $reportedExitCode -eq 0 -and
        $actualExitCode -eq 0 -and
        $currentComplete.intended_inner_exit_code -eq 0
    )
    $seal = [ordered]@{
        schema = $outerTerminalSealSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        stage = "primary-h4-p0r"
        seal_kind = "outer_observer_retained_inner_actual_exit"
        review_token_id = [string]$candidateToken.review_token_id
        original_review_token_sha256 = $originalTokenSha256
        runner_sha256 = $runnerSha256
        outer_observer_contract_sha256 = [string]$candidateToken.outer_observer_contract_sha256
        expected_terminal_seal_relative_path = $sealPathInfo.relative_path
        terminal_seal_required_for_authoritative_disposition = $true
        observer_session_relative_path = $paths.session_relative_path
        observer_nonce = $observerNonce
        outer_process_id = [int]$PID
        outer_process_birth_utc_ticks = [int64]$outerBirthTicks
        inner_process_id = [int]$innerProcessId
        inner_process_birth_utc_ticks = [int64]$innerBirthTicks
        retained_inner_process_handle_acquired = $true
        inner_ready_relative_path = Get-RepositoryRelativePath $paths.ready
        inner_ready_sha256 = $readySha256
        outer_start_release_relative_path = Get-RepositoryRelativePath $paths.start_release
        outer_start_release_sha256 = $startReleaseSha256
        outer_observer_handshake_prefix = $handshakePrefix
        outer_observer_handshake_prefix_sha256 = $handshakePrefixSha256
        artifact_hash_source = "current_inner_complete_exact_matches_validated_pre_exit_evidence"
        claim_relative_path = [string]$currentComplete.claim_relative_path
        claim_sha256 = [string]$currentComplete.claim_sha256
        claim_canonical_sha256 = [string]$currentComplete.claim_canonical_sha256
        guard_sha256 = [string]$currentComplete.guard_sha256
        guard_canonical_sha256 = [string]$currentComplete.guard_canonical_sha256
        resource_report_sha256 = $currentComplete.resource_report_sha256
        result_file_sha256 = $currentComplete.result_file_sha256
        child_stdout_sha256 = $currentComplete.child_stdout_sha256
        child_stderr_sha256 = $currentComplete.child_stderr_sha256
        monitor_ready_marker_sha256 = $currentComplete.monitor_ready_marker_sha256
        factor_complete_marker_sha256 = $currentComplete.factor_complete_marker_sha256
        monitor_release_marker_sha256 = $currentComplete.monitor_release_marker_sha256
        factor_prefix_one_sha256 = $currentComplete.factor_prefix_one_sha256
        factor_prefix_two_sha256 = $currentComplete.factor_prefix_two_sha256
        published_result_relative_path = $currentComplete.published_result_relative_path
        published_result_sha256 = $currentComplete.published_result_sha256
        inner_complete_relative_path = Get-RepositoryRelativePath $paths.complete
        inner_complete_sha256 = $completeSha256
        outer_exit_release_relative_path = Get-RepositoryRelativePath $paths.exit_release
        outer_exit_release_sha256 = $exitReleaseSha256
        inner_stdout_relative_path = Get-RepositoryRelativePath $paths.stdout
        inner_stdout_bytes = $finalStdoutBytes
        inner_stdout_sha256 = $finalStdoutSha256
        inner_stderr_relative_path = Get-RepositoryRelativePath $paths.stderr
        inner_stderr_bytes = $finalStderrBytes
        inner_stderr_sha256 = $finalStderrSha256
        outer_resource_envelope_close_schema = $outerObserverEnvelopeCloseSchema
        outer_resource_envelope_close_relative_path = Get-RepositoryRelativePath $paths.envelope_close
        outer_resource_envelope_close_raw_sha256 = $envelopeCloseRawSha256
        outer_resource_envelope_close_canonical_sha256 = $envelopeCloseCanonicalSha256
        outer_resource_envelope_mandatory_gate_pass = $true
        tombstone_relative_path = Get-RepositoryRelativePath $reviewTokenPath
        tombstone_sha256 = $currentTombstoneSha256
        tombstone_schema = [string]$currentTombstone.schema
        pre_exit_evidence_relative_path = Get-RepositoryRelativePath $preExitPath
        pre_exit_evidence_sha256 = $preExitSha256
        final_control_plane_session_index_relative_path = [string]$currentComplete.final_control_plane_session_index_relative_path
        final_control_plane_session_index_sha256 = [string]$currentComplete.final_control_plane_session_index_sha256
        inner_reported_exit_code = [int]$reportedExitCode
        inner_actual_exit_code = [int]$actualExitCode
        inner_exit_observed = $true
        inner_exit_code_matches_completion = $true
        authoritative_inner_disposition = if ($actualExitCode -eq 0) { "authoritative_inner_pass_observed" } else { "authoritative_inner_failure_observed" }
        terminal_evidence_complete = $true
        tombstone_success_was_provisional_without_this_seal = [bool]$tombstoneProvisionalSuccess
        seal_observes = "inner_runner_actual_exit_only"
        seal_materialization_and_readback_excluded_from_outer_resource_envelope = $true
        outer_process_exit_observed = $false
        seal_written_before_outer_exit = $true
        authorization_blocker = $true
        authorization_effect = "authoritative_inner_disposition_only_no_h4_p1_or_next_stage_authorization"
        next_stage_authorized = $false
        external_runner_or_machine_kill_terminal_state_guaranteed = $false
        authoritative_stage_pass = [bool]$authoritativeStagePass
        supersedes_provisional_tombstone_and_any_present_result_disposition = $true
        sealed_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $terminalSealAccepted = $false
    try {
        Write-AtomicUtf8NoBom $sealPathInfo.path ($seal | ConvertTo-Json -Depth 16 -Compress)
        $sealReadback = Read-BoundedJsonObject $sealPathInfo.path
        $sealJson = $seal | ConvertTo-Json -Depth 16 -Compress
        $sealReadbackJson = if ($null -ne $sealReadback) { $sealReadback | ConvertTo-Json -Depth 16 -Compress } else { $null }
        if (
        $null -eq $sealReadback -or
        -not (Test-ExactJsonFieldSet $sealReadback @($seal.Keys)) -or
        $sealReadbackJson -cne $sealJson -or
        $sealReadback.schema -ne $outerTerminalSealSchema -or
        $sealReadback.inner_exit_observed -isnot [bool] -or
        $sealReadback.inner_exit_observed -ne $true -or
        $sealReadback.outer_process_exit_observed -isnot [bool] -or
        $sealReadback.outer_process_exit_observed -ne $false -or
        $sealReadback.terminal_evidence_complete -isnot [bool] -or
        $sealReadback.terminal_evidence_complete -ne $true -or
        $sealReadback.outer_resource_envelope_mandatory_gate_pass -isnot [bool] -or
        $sealReadback.outer_resource_envelope_mandatory_gate_pass -ne $true -or
        $sealReadback.tombstone_success_was_provisional_without_this_seal -isnot [bool] -or
        $sealReadback.tombstone_success_was_provisional_without_this_seal -ne $tombstoneProvisionalSuccess -or
        $sealReadback.authoritative_stage_pass -isnot [bool] -or
        $sealReadback.authoritative_stage_pass -ne $authoritativeStagePass -or
        $sealReadback.supersedes_provisional_tombstone_and_any_present_result_disposition -isnot [bool] -or
        $sealReadback.supersedes_provisional_tombstone_and_any_present_result_disposition -ne $true -or
        $sealReadback.authorization_blocker -isnot [bool] -or
        $sealReadback.authorization_blocker -ne $true -or
        $sealReadback.next_stage_authorized -isnot [bool] -or
        $sealReadback.next_stage_authorized -ne $false -or
        $sealReadback.authorization_effect -ne "authoritative_inner_disposition_only_no_h4_p1_or_next_stage_authorization" -or
        $sealReadback.seal_materialization_and_readback_excluded_from_outer_resource_envelope -isnot [bool] -or
        $sealReadback.seal_materialization_and_readback_excluded_from_outer_resource_envelope -ne $true -or
        $sealReadback.external_runner_or_machine_kill_terminal_state_guaranteed -isnot [bool] -or
        $sealReadback.external_runner_or_machine_kill_terminal_state_guaranteed -ne $false -or
        (($null -eq $sealReadback.published_result_relative_path) -ne ($null -eq $sealReadback.published_result_sha256))
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal seal readback mismatch"
        }
        foreach ($markerSealHashField in @(
            "monitor_ready_marker_sha256",
            "factor_complete_marker_sha256",
            "monitor_release_marker_sha256"
        )) {
            if (
                ($null -ne $sealReadback.$markerSealHashField -and (
                    $sealReadback.$markerSealHashField -isnot [string] -or
                    [string]$sealReadback.$markerSealHashField -notmatch '^[0-9a-f]{64}$'
                )) -or
                $sealReadback.$markerSealHashField -ne $currentComplete.$markerSealHashField
            ) {
                throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal seal factor-monitor marker binding mismatch: " + $markerSealHashField)
            }
        }
        if (
            ($null -ne $sealReadback.factor_complete_marker_sha256 -and $null -eq $sealReadback.monitor_ready_marker_sha256) -or
            ($null -ne $sealReadback.monitor_release_marker_sha256 -and $null -eq $sealReadback.factor_complete_marker_sha256)
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer terminal seal factor-monitor marker prefix is invalid"
        }
        if ($authoritativeStagePass) {
            foreach ($requiredPassSealHash in @(
            "claim_sha256", "claim_canonical_sha256", "guard_sha256",
            "guard_canonical_sha256", "resource_report_sha256",
            "result_file_sha256", "child_stdout_sha256", "child_stderr_sha256",
            "monitor_ready_marker_sha256", "factor_complete_marker_sha256",
            "monitor_release_marker_sha256",
            "factor_prefix_one_sha256", "factor_prefix_two_sha256",
            "published_result_sha256"
            )) {
                if (
                    $sealReadback.$requiredPassSealHash -isnot [string] -or
                    $sealReadback.$requiredPassSealHash -notmatch '^[0-9a-f]{64}$'
                ) {
                    throw ("BLOCKED_AV_BS_RESULT_SCHEMA: outer pass seal lacks mandatory artifact hash: " + $requiredPassSealHash)
                }
            }
        }
        # The seal readback is accepted only while every bound source still has
        # the same bytes and semantics. This second pass closes replacement
        # races without claiming observation of this outer's exit.
        Assert-OuterInnerReadyMarker `
            (Read-BoundedJsonObject $paths.ready) $paths $candidateToken $runnerSha256 $originalTokenSha256 `
            $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks
        Assert-OuterInnerCompleteMarker `
            (Read-BoundedJsonObject $paths.complete) $paths $candidateToken $runnerSha256 $originalTokenSha256 `
            $observerNonce $PID $outerBirthTicks $innerProcessId $innerBirthTicks `
            $handshakePrefix $handshakePrefixSha256 $readySha256 $startReleaseSha256
        Assert-OuterTerminalSealSourcesCurrent `
            $paths $readyValue $startRelease $completeValue $exitRelease $envelopeClose `
            $runnerSha256 $readySha256 $startReleaseSha256 $completeSha256 $exitReleaseSha256 `
            $envelopeCloseRawSha256 $envelopeCloseCanonicalSha256 `
            $finalStdoutBytes $finalStdoutSha256 $finalStderrBytes $finalStderrSha256
        if ((Get-Item -LiteralPath $paths.stdout).Length -le 16MB) {
            $stdoutReturnSha256Before = Get-Sha256 $paths.stdout
            $innerStdout = Get-Content -LiteralPath $paths.stdout -Raw -Encoding utf8
            $stdoutReturnSha256After = Get-Sha256 $paths.stdout
            if (
                $stdoutReturnSha256Before -ne $finalStdoutSha256 -or
                $stdoutReturnSha256After -ne $finalStdoutSha256 -or
                [int64](Get-Item -LiteralPath $paths.stdout).Length -ne $finalStdoutBytes
            ) {
                throw "BLOCKED_AV_BS_RESULT_SCHEMA: outer stdout changed during return packaging"
            }
            if (-not [string]::IsNullOrEmpty($innerStdout)) {
                [Console]::Out.WriteLine($innerStdout.TrimEnd("`r", "`n"))
            }
        }
        $terminalSealAccepted = $true
    }
    catch {
        $sealFailureMessage = $_.Exception.Message
        if (-not $terminalSealAccepted -and (Test-Path -LiteralPath $sealPathInfo.path -PathType Leaf)) {
            $invalidSealPath = Join-Path $paths.session "invalidated-outer-terminal-seal.json"
            $sealSha256BeforeQuarantine = Get-Sha256 $sealPathInfo.path
            if (Test-Path -LiteralPath $invalidSealPath) {
                throw ("BLOCKED_AV_BS_RESULT_SCHEMA: terminal seal validation failed and quarantine collided: " + $sealFailureMessage)
            }
            try {
                [IO.File]::Move($sealPathInfo.path, $invalidSealPath)
                if (
                    (Test-Path -LiteralPath $sealPathInfo.path) -or
                    -not (Test-Path -LiteralPath $invalidSealPath -PathType Leaf) -or
                    (Get-Sha256 $invalidSealPath) -ne $sealSha256BeforeQuarantine
                ) {
                    throw "terminal seal quarantine readback mismatch"
                }
            }
            catch {
                throw (
                    "BLOCKED_AV_BS_RESULT_SCHEMA: terminal seal validation failed and canonical seal quarantine was not proven: " +
                    $sealFailureMessage + "; quarantine error: " + $_.Exception.Message
                )
            }
        }
        throw $sealFailureMessage
    }
    return [int]$actualExitCode
}

if ([string]::IsNullOrEmpty($InternalMode)) {
    try { $outerObservedExitCode = Invoke-OuterObserverPrimary }
    catch {
        Write-Error $_.Exception.Message -ErrorAction Continue
        exit 2
    }
    exit ([int]$outerObservedExitCode)
}
Initialize-OuterObservedInner

New-Item -ItemType Directory -Path $validationRoot -Force | Out-Null
$controlPlaneRoot = [IO.Path]::GetFullPath((Join-Path $validationRoot "control-plane"))
[IO.Directory]::CreateDirectory($controlPlaneRoot) | Out-Null
$controlPlaneSessionLeaf = "session-" + [guid]::NewGuid().ToString("N")
$controlPlaneSessionRoot = [IO.Path]::GetFullPath((Join-Path $controlPlaneRoot $controlPlaneSessionLeaf))
if (
    -not $controlPlaneSessionRoot.StartsWith($controlPlaneRoot, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetDirectoryName($controlPlaneSessionRoot).TrimEnd('\') -ne $controlPlaneRoot.TrimEnd('\') -or
    [IO.Path]::GetFileName($controlPlaneSessionRoot) -ne $controlPlaneSessionLeaf
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: control-plane session path validation failed"
}
[IO.Directory]::CreateDirectory($controlPlaneSessionRoot) | Out-Null
$controlPlaneReportReferences = New-Object System.Collections.ArrayList
$controlPlaneBootstrapPath = Join-Path $controlPlaneSessionRoot "bounded-bootstrap.py"
$controlPlaneCanonicalHashPath = Join-Path $controlPlaneSessionRoot "canonical-json-sha256.py"
$controlPlaneBootstrapSource = @'
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback


def write_new(path: Path, payload: dict[str, object]) -> None:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def wait_for(path: Path, timeout_seconds: float = 175.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"control-plane release marker timed out: {path.name}")
        time.sleep(0.01)


ready_path = Path(sys.argv[1])
start_release_path = Path(sys.argv[2])
completion_path = Path(sys.argv[3])
exit_release_path = Path(sys.argv[4])
target_path = Path(sys.argv[5]).resolve(strict=True)
target_arguments = list(sys.argv[6:])

write_new(
    ready_path,
    {
        "schema": "AV-BS1-h4-p0r-control-plane-bootstrap-ready-v1",
        "process_id": os.getpid(),
        "monotonic_ns": time.monotonic_ns(),
    },
)
wait_for(start_release_path)

exit_code = 0
try:
    sys.argv = [str(target_path), *target_arguments]
    runpy.run_path(str(target_path), run_name="__main__")
except SystemExit as exc:
    if exc.code is None:
        exit_code = 0
    elif isinstance(exc.code, int):
        exit_code = int(exc.code)
    else:
        print(exc.code, file=sys.stderr)
        exit_code = 1
except BaseException:
    traceback.print_exc()
    exit_code = 1
finally:
    sys.stdout.flush()
    sys.stderr.flush()

write_new(
    completion_path,
    {
        "schema": "AV-BS1-h4-p0r-control-plane-target-complete-v1",
        "process_id": os.getpid(),
        "exit_code": exit_code,
        "monotonic_ns": time.monotonic_ns(),
    },
)
wait_for(exit_release_path)
raise SystemExit(exit_code)
'@
$controlPlaneCanonicalHashSource = @'
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys


value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
raw = json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
print(sha256(raw).hexdigest())
'@
Write-AtomicUtf8NoBom $controlPlaneBootstrapPath $controlPlaneBootstrapSource
Write-AtomicUtf8NoBom $controlPlaneCanonicalHashPath $controlPlaneCanonicalHashSource

# Break the preflight-report scope cycle without treating token bytes as
# authorization: seed the monitor report with only the candidate 64-hex scope
# hash, then require the bounded preflight to validate that identical value.
$candidateScopeToken = Read-BoundedJsonObject $reviewTokenPath
if (
    $null -eq $candidateScopeToken -or
    $candidateScopeToken.schema -ne "AV-BS1-h4-p0r-review-token-v1" -or
    [string]$candidateScopeToken.execution_resource_scope_sha256 -notmatch '^[0-9a-f]{64}$'
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: candidate review token lacks a reportable execution scope"
}
$executionResourceScopeSha256 = [string]$candidateScopeToken.execution_resource_scope_sha256

# Validate the complete H1/P0/P1 lineage and clean tracked token inside the
# independently bounded control-plane supervisor before any claim exists.
$preflightInvocation = Invoke-ControlPlanePython `
    -Operation "preflight" `
    -TargetScriptPath $fixturePath `
    -ScriptArguments @("--stage", "preflight-primary-h4-p0r", "--review-token", $reviewTokenPath) `
    -AllowedExitCodes @(0) `
    -AttemptArtifactPaths ([ordered]@{ review_token = $reviewTokenPath })
if (
    [string]::IsNullOrWhiteSpace([string]$preflightInvocation.report_path) -or
    [string]$preflightInvocation.report_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]::IsNullOrWhiteSpace([string]$preflightInvocation.session_index_path) -or
    [string]$preflightInvocation.session_index_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]::IsNullOrWhiteSpace([string]$preflightInvocation.envelope_close_path) -or
    [string]$preflightInvocation.envelope_close_sha256 -notmatch '^[0-9a-f]{64}$' -or
    -not (Test-Path -LiteralPath $preflightInvocation.report_path -PathType Leaf) -or
    -not (Test-Path -LiteralPath $preflightInvocation.session_index_path -PathType Leaf) -or
    -not (Test-Path -LiteralPath $preflightInvocation.envelope_close_path -PathType Leaf) -or
    (Get-Sha256 $preflightInvocation.report_path) -ne $preflightInvocation.report_sha256 -or
    (Get-Sha256 $preflightInvocation.session_index_path) -ne $preflightInvocation.session_index_sha256 -or
    (Get-Sha256 $preflightInvocation.envelope_close_path) -ne $preflightInvocation.envelope_close_sha256
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: bounded preflight report/index/envelope-close bindings are incomplete"
}
$preflightFinalIndex = Read-BoundedJsonObject $preflightInvocation.session_index_path
if (
    $null -eq $preflightFinalIndex -or
    $preflightFinalIndex.index_role -ne "final_binds_resource_envelope_close" -or
    $preflightFinalIndex.envelope_close_path -ne [IO.Path]::GetFullPath([string]$preflightInvocation.envelope_close_path) -or
    $preflightFinalIndex.envelope_close_sha256 -ne [string]$preflightInvocation.envelope_close_sha256
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: bounded preflight final index does not bind its envelope close"
}
$preflightControlPlaneReportRelativePath = Get-RepositoryRelativePath $preflightInvocation.report_path
$preflightControlPlaneReportSha256 = [string]$preflightInvocation.report_sha256
$preflightControlPlaneSessionIndexRelativePath = Get-RepositoryRelativePath $preflightInvocation.session_index_path
$preflightControlPlaneSessionIndexSha256 = [string]$preflightInvocation.session_index_sha256
$preflightText = ([string]$preflightInvocation.stdout_text).Trim()
$preflightWrapper = $preflightText | ConvertFrom-Json
if (
    $preflightWrapper.payload.schema -ne "AV-BS1-h4-p0r-execution-fixture-v1" -or
    $preflightWrapper.payload.program -ne $program -or
    $preflightWrapper.payload.case_id -ne "AV-BS1-CIRCLE-PRIMARY" -or
    $preflightWrapper.payload.stage -ne "preflight-primary-h4-p0r" -or
    $preflightWrapper.payload.status -ne "preflight_pass_token_gated_no_factor" -or
    $preflightWrapper.payload.authorization_state -ne "authorized" -or
    $preflightWrapper.payload.preflight_pass -ne $true -or
    $preflightWrapper.payload.factorization_performed -ne $false -or
    $preflightWrapper.payload.physics_solve_performed -ne $false -or
    $preflightWrapper.payload.next_stage_authorized -ne $false -or
    [string]$preflightWrapper.payload.outer_observer_contract_sha256 -notmatch '^[0-9a-f]{64}$' -or
    $preflightWrapper.payload.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
    $preflightWrapper.payload.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
    $preflightWrapper.payload.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
    $preflightWrapper.payload.terminal_seal_required_for_authoritative_disposition -ne $true -or
    [string]$preflightWrapper.payload_sha256 -notmatch '^[0-9a-f]{64}$'
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: primary-h4-p0r preflight wrapper mismatch"
}
$token = Get-Content -LiteralPath $reviewTokenPath -Raw -Encoding utf8 | ConvertFrom-Json
if (
    [string]$token.review_token_id -notmatch '^[0-9a-f]{32}$' -or
    $token.uses_remaining -isnot [int] -or
    $token.uses_remaining -ne 1 -or
    $preflightWrapper.payload.review_token_id -ne $token.review_token_id -or
    $preflightWrapper.payload.review_token_sha256 -ne (Get-Sha256 $reviewTokenPath) -or
    $preflightWrapper.payload.p0r_preregistration_commit -ne $token.p0r_preregistration_commit -or
    [string]$preflightWrapper.payload.manifest_payload_sha256 -notmatch '^[0-9a-f]{64}$' -or
    $preflightWrapper.payload.manifest_bindings.manifest_payload_sha256 -ne $token.manifest_payload_sha256 -or
    $preflightWrapper.payload.manifest_bindings.fixture_sha256 -ne $token.fixture_sha256 -or
    $preflightWrapper.payload.manifest_bindings.runner_sha256 -ne $token.runner_sha256 -or
    $preflightWrapper.payload.manifest_bindings.matrix_contract_sha256 -ne $token.matrix_contract_sha256 -or
    $preflightWrapper.payload.resource_policy_sha256 -ne $token.resource_policy_sha256 -or
    [string]$preflightWrapper.payload.execution_resource_scope_sha256 -notmatch '^[0-9a-f]{64}$' -or
    $preflightWrapper.payload.execution_resource_scope_sha256 -ne $executionResourceScopeSha256 -or
    $preflightWrapper.payload.execution_resource_scope_sha256 -ne $token.execution_resource_scope_sha256 -or
    $token.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
    $preflightWrapper.payload.outer_observer_contract_sha256 -ne $token.outer_observer_contract_sha256 -or
    $token.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
    $preflightWrapper.payload.expected_terminal_seal_relative_path -ne $token.expected_terminal_seal_relative_path -or
    $token.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
    $token.terminal_seal_required_for_authoritative_disposition -ne $true
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: invalid one-use review token"
}
$executionResourceScopeSha256 = [string]$preflightWrapper.payload.execution_resource_scope_sha256
$reviewTokenHashBeforeByteCapture = Get-Sha256 $reviewTokenPath
$reviewTokenAuthorizedBytes = [IO.File]::ReadAllBytes($reviewTokenPath)
$reviewTokenHash = Get-Sha256 $reviewTokenPath
if ($reviewTokenHashBeforeByteCapture -ne $reviewTokenHash) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: review token changed during exact-byte capture"
}
$reviewTokenCanonicalHash = Get-CanonicalJsonSha256 `
    $reviewTokenPath `
    ([ordered]@{ review_token = $reviewTokenPath })

$runnerHash = Get-Sha256 $runnerScriptPath
if ($runnerHash -ne $token.runner_sha256) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: runner hash differs from the reviewed token"
}
$baseline = Get-SystemSample
$system = $baseline
if ($baseline.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) {
    throw "BLOCKED_AV_BS_RESOURCE: pre-spawn commit headroom is below 5618345703 bytes"
}
if ($baseline.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) {
    throw "BLOCKED_AV_BS_RESOURCE: pre-spawn available physical memory is below 5081474791 bytes"
}
$parentBirthTicks = Get-ProcessBirthTicks $PID
if (
    [int]$executionTreeRootProcessId -le 0 -or
    [int64]$executionTreeRootBirthUtcTicks -le 0 -or
    [int]$executionTreeRootProcessId -eq [int]$PID -or
    [int]$innerRunnerProcessId -ne [int]$PID -or
    [int64]$innerRunnerBirthUtcTicks -ne [int64]$parentBirthTicks -or
    [int64]$parentBirthTicks -lt [int64]$executionTreeRootBirthUtcTicks -or
    (Get-ProcessBirthTicks ([int]$executionTreeRootProcessId)) -ne [int64]$executionTreeRootBirthUtcTicks
) {
    throw "BLOCKED_AV_BS_RESOURCE: inner/outer execution-tree identity chain is invalid"
}

New-Item -ItemType Directory -Path $validationRoot -Force | Out-Null
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempLeaf = "av-bs1-" + [guid]::NewGuid().ToString("N")
$tempDirectory = [IO.Path]::GetFullPath((Join-Path $tempRoot $tempLeaf))
if (
    -not $tempDirectory.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetDirectoryName($tempDirectory).TrimEnd('\') -ne $tempRoot.TrimEnd('\') -or
    [IO.Path]::GetFileName($tempDirectory) -ne $tempLeaf
) {
    throw "BLOCKED_AV_BS_RESOURCE: temporary path escaped the system temp root"
}
New-Item -ItemType Directory -Path $tempDirectory | Out-Null

# Claim only after native monitor initialization, system preflight, and temp setup.
$claimsRoot = Join-Path $validationRoot "claims"
[IO.Directory]::CreateDirectory($claimsRoot) | Out-Null
$claimRelativePath = "validation-output/av-bs1/claims/" + $token.review_token_id + ".json"
$claimPath = Join-Path $claimsRoot ($token.review_token_id + ".json")
$nonce = [guid]::NewGuid().ToString("N")
$guardPath = Join-Path $tempDirectory "guard.json"
$stdoutPath = Join-Path $tempDirectory "numerical.json"
$stderrPath = Join-Path $tempDirectory "child.stderr.txt"
$resourcePath = Join-Path $tempDirectory "resource.json"
$finalPath = Join-Path $tempDirectory "final.json"
$monitorReadyPath = Join-Path $tempDirectory "monitor-ready.json"
$factorCompletePath = Join-Path $tempDirectory "factor-complete.json"
$monitorReleasePath = Join-Path $tempDirectory "monitor-release.json"
$factorPrefixOnePath = Join-Path $tempDirectory "factor-prefix-1.json"
$factorPrefixTwoPath = Join-Path $tempDirectory "factor-prefix-2.json"
$claimHash = $null
$claimCanonicalHash = $null
$guardHash = $null
$guardCanonicalHash = $null
$guard = $null
$immediatePreSpawn = $null
$finalSystem = $null
$startedUtc = [DateTime]::UtcNow
$wallStopwatch = [Diagnostics.Stopwatch]::StartNew()
$stopReason = $null
$monitorError = $null
$peak = [ordered]@{
    tree_working_set_bytes = [int64]0
    tree_summed_process_lifetime_peak_working_set_bytes = [int64]0
    tree_private_commit_bytes = [int64]0
    tree_committed_pagefile_bytes = [int64]0
    tree_summed_process_lifetime_peak_commit_bytes = [int64]0
    tree_nonprivate_working_set_proxy_bytes = [int64]0
    tree_page_fault_count = [int64]0
    system_commit_total_bytes = [int64]$baseline.commit_total_bytes
    system_commit_headroom_min_bytes = [int64]$baseline.commit_headroom_bytes
    available_physical_min_bytes = [int64]$baseline.available_physical_bytes
}
$process = $null
$successfulTreeSampleCount = 0
$childVisibleTreeSampleCount = 0
# Resource evidence samples the outer-rooted execution tree. Termination remains
# restricted to the distinct child-owned identity set below; the outer observer
# and this inner runner must never become cleanup targets.
$factorEvidenceProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'
$factorEvidenceBirthTicks = New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$factorEvidenceBirthTicks.Add([int]$executionTreeRootProcessId, [int64]$executionTreeRootBirthUtcTicks)
$factorEvidenceBirthTicks.Add([int]$PID, [int64]$parentBirthTicks)
[void]$factorEvidenceProcessIds.Add([int]$executionTreeRootProcessId)
[void]$factorEvidenceProcessIds.Add([int]$PID)
$observedChildProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'
$observedChildBirthTicks = New-Object 'System.Collections.Generic.Dictionary[int, Int64]'
$claimCreated = $false
$attemptStarted = $false
$attemptStatus = "runner_exception"
$runnerExitCode = $null
$childLaunched = $false
$childProcessHandleAcquired = $false
$resourceEvidenceWriteError = $null
$consumptionSucceeded = $false
$preserveAttemptEvidence = $false
$factorRootExitObserved = $false
$factorCleanupVerified = $false
$factorOwnedSurvivorsAfterCleanup = @()
$endedWallSeconds = $null
$wallClockFrozen = $false
$firstChildVisibleSamplePerfCounterNs = $null
$firstChildVisibleSampleUtc = $null
$lastChildVisibleSamplePerfCounterNs = $null
$lastChildVisibleSampleUtc = $null
$monitorReadyHash = $null
$factorCompleteHash = $null
$monitorReleaseHash = $null
$factorPrefixOneHash = $null
$factorPrefixTwoHash = $null

$claimPayload = [ordered]@{
    schema = "AV-BS1-h4-p0r-token-claim-v1"
    program = $program
    case_id = "AV-BS1-CIRCLE-PRIMARY"
    stage = "primary-h4-p0r"
    claim_relative_path = $claimRelativePath
    review_token_id = $token.review_token_id
    review_token_sha256 = $reviewTokenHash
    review_token_canonical_sha256 = $reviewTokenCanonicalHash
    review_binding_sha256 = $preflightWrapper.payload.review_binding_sha256
    fixture_sha256 = $token.fixture_sha256
    runner_sha256 = $runnerHash
    p0r_preregistration_commit = $token.p0r_preregistration_commit
    git_head = $preflightWrapper.payload.git_head
    preflight_payload_sha256 = $preflightWrapper.payload_sha256
    manifest_payload_sha256 = $preflightWrapper.payload.manifest_bindings.manifest_payload_sha256
    matrix_contract_sha256 = $preflightWrapper.payload.manifest_bindings.matrix_contract_sha256
    resource_policy_sha256 = $preflightWrapper.payload.resource_policy_sha256
    execution_resource_scope_sha256 = $executionResourceScopeSha256
    control_plane_preflight_report_relative_path = $preflightControlPlaneReportRelativePath
    control_plane_preflight_report_sha256 = $preflightControlPlaneReportSha256
    control_plane_preflight_session_index_relative_path = $preflightControlPlaneSessionIndexRelativePath
    control_plane_preflight_session_index_sha256 = $preflightControlPlaneSessionIndexSha256
    outer_observer_contract_sha256 = $script:outerObserverContractSha256
    expected_terminal_seal_relative_path = $script:outerObserverExpectedTerminalSealRelativePath
    terminal_seal_required_for_authoritative_disposition = $true
    outer_observer_handshake_prefix = $script:outerObserverHandshakePrefix
    outer_observer_handshake_prefix_sha256 = $script:outerObserverHandshakePrefixSha256
    guard_nonce = $nonce
    parent_pid = $PID
    parent_pid_birth_utc_ticks = $parentBirthTicks
    created_utc = [DateTimeOffset]::UtcNow.ToString("o")
}
$claimJson = $claimPayload | ConvertTo-Json -Depth 8 -Compress
$buildGuardPayload = {
    param([string]$ClaimSha256, [string]$ClaimCanonicalSha256)
    [ordered]@{
        schema = $guardSchema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        stage = "primary-h4-p0r"
        nonce = $nonce
        parent_pid = $PID
        parent_pid_birth_utc_ticks = $parentBirthTicks
        monitor_ok = $true
        poll_interval_ms = $pollMilliseconds
        wall_stop_seconds = $wallStopSeconds
        tree_ws_stop_bytes = $treeWorkingSetStop
        tree_private_stop_bytes = $treePrivateStop
        tree_commit_stop_bytes = $treeCommitStop
        commit_headroom_floor_bytes = $commitHeadroomFloor
        available_physical_floor_bytes = $availablePhysicalFloor
        minimum_commit_headroom_before_spawn_bytes = $minimumCommitHeadroomBeforeSpawn
        minimum_available_physical_before_spawn_bytes = $minimumAvailablePhysicalBeforeSpawn
        fixture_sha256 = $token.fixture_sha256
        review_token_id = $token.review_token_id
        review_token_sha256 = $reviewTokenHash
        review_token_canonical_sha256 = $reviewTokenCanonicalHash
        claim_relative_path = $claimRelativePath
        claim_sha256 = $ClaimSha256
        claim_canonical_sha256 = $ClaimCanonicalSha256
        preflight_payload_sha256 = $preflightWrapper.payload_sha256
        resource_guard_policy_sha256 = $token.resource_policy_sha256
        execution_resource_scope_sha256 = $executionResourceScopeSha256
        baseline_commit_headroom_bytes = $baseline.commit_headroom_bytes
        baseline_available_physical_bytes = $baseline.available_physical_bytes
        pre_spawn_resource_gate_pass = $true
        runner_sha256 = $runnerHash
    }
}
try {
    $expiresUtc = [DateTimeOffset]::Parse(
        [string]$token.expires_utc,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    ).ToUniversalTime()
}
catch { throw "BLOCKED_AV_BS_RESULT_SCHEMA: review token expiry is invalid immediately before claim" }
if ([DateTimeOffset]::UtcNow -ge $expiresUtc) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: review token expired immediately before claim"
}
$claimPayload.created_utc = [DateTimeOffset]::UtcNow.ToString("o")
$claimJson = $claimPayload | ConvertTo-Json -Depth 8 -Compress
if ([DateTimeOffset]::Parse($claimPayload.created_utc).ToUniversalTime() -ge $expiresUtc) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: review token expired at claim timestamp"
}
try { $claimStream = [IO.File]::Open($claimPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None) }
catch { throw "BLOCKED_AV_BS_RESULT_SCHEMA: token claim already exists" }
$claimCreated = $true
try {
    try {
        $claimBytes = [Text.Encoding]::UTF8.GetBytes($claimJson)
        $claimStream.Write($claimBytes, 0, $claimBytes.Length)
        $claimStream.Flush($true)
    }
    finally { $claimStream.Dispose() }
    $claimHash = Get-Sha256 $claimPath
    $claimCanonicalHash = Get-CanonicalJsonSha256 `
        $claimPath `
        ([ordered]@{ review_token = $reviewTokenPath; claim = $claimPath })

    $guard = & $buildGuardPayload $claimHash $claimCanonicalHash
    Write-AtomicUtf8NoBom $guardPath ($guard | ConvertTo-Json -Depth 8 -Compress)
    $guardHash = Get-Sha256 $guardPath
    $guardCanonicalHash = Get-CanonicalJsonSha256 `
        $guardPath `
        ([ordered]@{ review_token = $reviewTokenPath; claim = $claimPath; guard = $guardPath })

    # A claimed token is consumed even if process creation itself fails.
    $attemptStarted = $true
    $immediatePreSpawn = Get-SystemSample
    if ($immediatePreSpawn.commit_headroom_bytes -lt $minimumCommitHeadroomBeforeSpawn) {
        $stopReason = "PRESPAWN_COMMIT_HEADROOM_STOP"
        throw "BLOCKED_AV_BS_RESOURCE: immediate pre-spawn commit headroom is below 5618345703 bytes"
    }
    if ($immediatePreSpawn.available_physical_bytes -lt $minimumAvailablePhysicalBeforeSpawn) {
        $stopReason = "PRESPAWN_AVAILABLE_PHYSICAL_STOP"
        throw "BLOCKED_AV_BS_RESOURCE: immediate pre-spawn available physical memory is below 5081474791 bytes"
    }
    $argumentList = @(
        (Quote-Argument $fixturePath), "--stage", "primary-h4-p0r",
        "--guard-contract", (Quote-Argument $guardPath),
        "--guard-nonce", $nonce,
        "--review-token", (Quote-Argument $reviewTokenPath),
        "--claim-file", (Quote-Argument $claimPath),
        "--monitor-ready", (Quote-Argument $monitorReadyPath),
        "--monitor-complete", (Quote-Argument $factorCompletePath),
        "--monitor-release", (Quote-Argument $monitorReleasePath)
    ) -join " "
    $process = Start-Process -FilePath $pythonPath -ArgumentList $argumentList -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $childLaunched = $true
    # Start-Process on Windows PowerShell can lose ExitCode after a manually
    # monitored redirected child exits unless its native handle is acquired
    # while it is still alive.  Retain the handle before entering the poll loop.
    try {
        $childProcessHandle = $process.Handle
        if ($childProcessHandle -eq [IntPtr]::Zero) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: child process handle was not acquired"
        }
        $childBirthTicks = [int64]$process.StartTime.ToUniversalTime().Ticks
        if ($childBirthTicks -lt $parentBirthTicks) {
            throw "BLOCKED_AV_BS_RESOURCE: child creation predates the tree root"
        }
        $observedChildBirthTicks.Add([int]$process.Id, $childBirthTicks)
        [void]$observedChildProcessIds.Add([int]$process.Id)
        $factorEvidenceBirthTicks.Add([int]$process.Id, $childBirthTicks)
        [void]$factorEvidenceProcessIds.Add([int]$process.Id)
    }
    catch {
        $bindingError = $_
        $terminationError = $null
        try {
            if (-not $process.HasExited) {
                $process.Kill()
                $process.WaitForExit()
                $process.Refresh()
            }
            if (-not $process.HasExited) {
                throw "retained child handle did not reach the exited state"
            }
        }
        catch { $terminationError = $_.Exception.Message }
        if ($terminationError) {
            $stopReason = "OBSERVED_PROCESS_TERMINATION_FAILED"
            $monitorError = $terminationError
            throw "BLOCKED_AV_BS_RESOURCE: child identity registration failed and retained-handle termination was not verified"
        }
        throw $bindingError
    }
    $childProcessHandleAcquired = $true
    while (-not $process.HasExited) {
        $terminationRequired = $false
        try {
            $tree = Get-TreeSample $executionTreeRootProcessId $executionTreeRootBirthUtcTicks $factorEvidenceProcessIds $factorEvidenceBirthTicks
            $cleanupTree = Get-TreeSample $process.Id $childBirthTicks $observedChildProcessIds $observedChildBirthTicks
            $system = Get-SystemSample
            $successfulTreeSampleCount += 1
            if (
                $tree.process_ids -contains $process.Id -and
                $cleanupTree.process_ids -contains $process.Id
            ) { $childVisibleTreeSampleCount += 1 }
            $peak.tree_working_set_bytes = [math]::Max($peak.tree_working_set_bytes, $tree.working_set_bytes)
            $peak.tree_summed_process_lifetime_peak_working_set_bytes = [math]::Max($peak.tree_summed_process_lifetime_peak_working_set_bytes, $tree.summed_process_peak_working_set_bytes)
            $peak.tree_private_commit_bytes = [math]::Max($peak.tree_private_commit_bytes, $tree.private_commit_bytes)
            $peak.tree_committed_pagefile_bytes = [math]::Max($peak.tree_committed_pagefile_bytes, $tree.committed_pagefile_bytes)
            $peak.tree_summed_process_lifetime_peak_commit_bytes = [math]::Max($peak.tree_summed_process_lifetime_peak_commit_bytes, $tree.summed_process_peak_commit_bytes)
            $peak.tree_nonprivate_working_set_proxy_bytes = [math]::Max($peak.tree_nonprivate_working_set_proxy_bytes, $tree.nonprivate_working_set_proxy_bytes)
            $peak.tree_page_fault_count = [math]::Max($peak.tree_page_fault_count, $tree.page_fault_count)
            $peak.system_commit_total_bytes = [math]::Max($peak.system_commit_total_bytes, $system.commit_total_bytes)
            $peak.system_commit_headroom_min_bytes = [math]::Min($peak.system_commit_headroom_min_bytes, $system.commit_headroom_bytes)
            $peak.available_physical_min_bytes = [math]::Min($peak.available_physical_min_bytes, $system.available_physical_bytes)
            if ($tree.process_ids -contains $process.Id) {
                $samplePerfCounterNs = Get-MonotonicNanoseconds
                $sampleUtc = [DateTimeOffset]::UtcNow.ToString("o")
                if ($null -eq $firstChildVisibleSamplePerfCounterNs) {
                    $firstChildVisibleSamplePerfCounterNs = $samplePerfCounterNs
                    $firstChildVisibleSampleUtc = $sampleUtc
                    $monitorReady = [ordered]@{
                        schema = "AV-BS1-h4-p0r-monitor-ready-v1"
                        claim_sha256 = $claimHash
                        child_process_id = $process.Id
                        sample_perf_counter_ns = $samplePerfCounterNs
                        sample_utc = $sampleUtc
                    }
                    Write-AtomicUtf8NoBom $monitorReadyPath ($monitorReady | ConvertTo-Json -Depth 4 -Compress)
                    $monitorReadyHash = Get-Sha256 $monitorReadyPath
                }
                $lastChildVisibleSamplePerfCounterNs = $samplePerfCounterNs
                $lastChildVisibleSampleUtc = $sampleUtc

                # The child writes factor-complete only after all available
                # certificates have been finalized. Take one additional real
                # tree sample after observing it, then release the child.
                if ((Test-Path -LiteralPath $factorCompletePath -PathType Leaf) -and -not (Test-Path -LiteralPath $monitorReleasePath)) {
                    $postFactorTree = Get-TreeSample $executionTreeRootProcessId $executionTreeRootBirthUtcTicks $factorEvidenceProcessIds $factorEvidenceBirthTicks
                    $postFactorCleanupTree = Get-TreeSample $process.Id $childBirthTicks $observedChildProcessIds $observedChildBirthTicks
                    $postFactorSystem = Get-SystemSample
                    $successfulTreeSampleCount += 1
                    if (
                        -not ($postFactorTree.process_ids -contains $process.Id) -or
                        -not ($postFactorCleanupTree.process_ids -contains $process.Id)
                    ) {
                        throw "BLOCKED_AV_BS_RESOURCE: factor-complete child vanished before post-factor sample"
                    }
                    $childVisibleTreeSampleCount += 1
                    $peak.tree_working_set_bytes = [math]::Max($peak.tree_working_set_bytes, $postFactorTree.working_set_bytes)
                    $peak.tree_summed_process_lifetime_peak_working_set_bytes = [math]::Max($peak.tree_summed_process_lifetime_peak_working_set_bytes, $postFactorTree.summed_process_peak_working_set_bytes)
                    $peak.tree_private_commit_bytes = [math]::Max($peak.tree_private_commit_bytes, $postFactorTree.private_commit_bytes)
                    $peak.tree_committed_pagefile_bytes = [math]::Max($peak.tree_committed_pagefile_bytes, $postFactorTree.committed_pagefile_bytes)
                    $peak.tree_summed_process_lifetime_peak_commit_bytes = [math]::Max($peak.tree_summed_process_lifetime_peak_commit_bytes, $postFactorTree.summed_process_peak_commit_bytes)
                    $peak.tree_nonprivate_working_set_proxy_bytes = [math]::Max($peak.tree_nonprivate_working_set_proxy_bytes, $postFactorTree.nonprivate_working_set_proxy_bytes)
                    $peak.tree_page_fault_count = [math]::Max($peak.tree_page_fault_count, $postFactorTree.page_fault_count)
                    $peak.system_commit_total_bytes = [math]::Max($peak.system_commit_total_bytes, $postFactorSystem.commit_total_bytes)
                    $peak.system_commit_headroom_min_bytes = [math]::Min($peak.system_commit_headroom_min_bytes, $postFactorSystem.commit_headroom_bytes)
                    $peak.available_physical_min_bytes = [math]::Min($peak.available_physical_min_bytes, $postFactorSystem.available_physical_bytes)
                    if ($postFactorTree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "TREE_WS_STOP" }
                    elseif ($postFactorTree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "TREE_LIFETIME_PEAK_WS_STOP" }
                    elseif ($postFactorTree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "TREE_PRIVATE_STOP" }
                    elseif ($postFactorTree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "TREE_COMMIT_STOP" }
                    elseif ($postFactorTree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "TREE_LIFETIME_PEAK_COMMIT_STOP" }
                    elseif ($postFactorSystem.commit_headroom_bytes -lt $commitHeadroomFloor) { $stopReason = "SYSTEM_COMMIT_HEADROOM_STOP" }
                    elseif ($postFactorSystem.available_physical_bytes -lt $availablePhysicalFloor) { $stopReason = "AVAILABLE_PHYSICAL_STOP" }
                    if (-not $stopReason) {
                        $releasePerfCounterNs = Get-MonotonicNanoseconds
                        $releaseUtc = [DateTimeOffset]::UtcNow.ToString("o")
                        $lastChildVisibleSamplePerfCounterNs = $releasePerfCounterNs
                        $lastChildVisibleSampleUtc = $releaseUtc
                        $factorCompleteHash = Get-Sha256 $factorCompletePath
                        $monitorRelease = [ordered]@{
                            schema = "AV-BS1-h4-p0r-monitor-release-v1"
                            claim_sha256 = $claimHash
                            completion_marker_sha256 = $factorCompleteHash
                            child_process_id = $process.Id
                            sample_perf_counter_ns = $releasePerfCounterNs
                            sample_utc = $releaseUtc
                        }
                        Write-AtomicUtf8NoBom $monitorReleasePath ($monitorRelease | ConvertTo-Json -Depth 4 -Compress)
                        $monitorReleaseHash = Get-Sha256 $monitorReleasePath
                    }
                }
            }
            if ($tree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "TREE_WS_STOP" }
            elseif ($tree.summed_process_peak_working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "TREE_LIFETIME_PEAK_WS_STOP" }
            elseif ($tree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "TREE_PRIVATE_STOP" }
            elseif ($tree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "TREE_COMMIT_STOP" }
            elseif ($tree.summed_process_peak_commit_bytes -gt $treeCommitStop) { $stopReason = "TREE_LIFETIME_PEAK_COMMIT_STOP" }
            elseif ($system.commit_headroom_bytes -lt $commitHeadroomFloor) { $stopReason = "SYSTEM_COMMIT_HEADROOM_STOP" }
            elseif ($system.available_physical_bytes -lt $availablePhysicalFloor) { $stopReason = "AVAILABLE_PHYSICAL_STOP" }
            elseif ($wallStopwatch.Elapsed.TotalSeconds -gt $wallStopSeconds) { $stopReason = "WALL_TIME_STOP" }
            if ($stopReason) {
                $terminationRequired = $true
            }
        }
        catch {
            $monitorError = $_.Exception.Message
            $stopReason = "MONITOR_QUERY_FAILED"
            $process.Refresh()
            if ($process.HasExited) {
                throw
            }
            else {
                $terminationRequired = $true
            }
        }
        if ($terminationRequired) {
            try {
                Stop-ProcessTree $process.Id $observedChildProcessIds $observedChildBirthTicks
            }
            catch {
                $monitorError = $_.Exception.Message
                $stopReason = "OBSERVED_PROCESS_TERMINATION_FAILED"
                throw
            }
            break
        }
        Start-Sleep -Milliseconds $pollMilliseconds
        $process.Refresh()
    }
    $process.WaitForExit()
    $process.Refresh()
    # After root exit, never rediscover by the released PID. Only processes
    # identity-bound while the verified root was alive remain eligible.
    [void](Get-LiveObservedProcessIds $observedChildProcessIds $observedChildBirthTicks)
    $liveObservedAfterExit = @(Get-LiveObservedProcessIds $observedChildProcessIds $observedChildBirthTicks)
    if ($liveObservedAfterExit.Count -gt 0) {
        $stopReason = "ORPHANED_OBSERVED_PROCESS"
        try {
            Stop-ProcessTree $process.Id $observedChildProcessIds $observedChildBirthTicks
        }
        catch {
            $monitorError = $_.Exception.Message
            $stopReason = "OBSERVED_PROCESS_TERMINATION_FAILED"
            throw
        }
    }
    $rawChildExitCode = $process.ExitCode
    if ($null -eq $rawChildExitCode) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: child exit code was not retained"
    }
    $childExitCode = [int]$rawChildExitCode
    if ($null -eq $monitorReadyHash -and (Test-Path -LiteralPath $monitorReadyPath -PathType Leaf)) { $monitorReadyHash = Get-Sha256 $monitorReadyPath }
    if ($null -eq $factorCompleteHash -and (Test-Path -LiteralPath $factorCompletePath -PathType Leaf)) { $factorCompleteHash = Get-Sha256 $factorCompletePath }
    if ($null -eq $monitorReleaseHash -and (Test-Path -LiteralPath $monitorReleasePath -PathType Leaf)) { $monitorReleaseHash = Get-Sha256 $monitorReleasePath }
    if (Test-Path -LiteralPath $factorPrefixOnePath -PathType Leaf) { $factorPrefixOneHash = Get-Sha256 $factorPrefixOnePath }
    if (Test-Path -LiteralPath $factorPrefixTwoPath -PathType Leaf) { $factorPrefixTwoHash = Get-Sha256 $factorPrefixTwoPath }
    $factorMonitorHandshakeComplete = (
        $null -ne $firstChildVisibleSamplePerfCounterNs -and
        $null -ne $lastChildVisibleSamplePerfCounterNs -and
        $null -ne $monitorReadyHash -and
        $null -ne $factorCompleteHash -and
        $null -ne $monitorReleaseHash
    )
    $finalSystem = Get-SystemSample
    $peak.system_commit_total_bytes = [math]::Max($peak.system_commit_total_bytes, $finalSystem.commit_total_bytes)
    $peak.system_commit_headroom_min_bytes = [math]::Min($peak.system_commit_headroom_min_bytes, $finalSystem.commit_headroom_bytes)
    $peak.available_physical_min_bytes = [math]::Min($peak.available_physical_min_bytes, $finalSystem.available_physical_bytes)
    if (-not $wallClockFrozen) {
        $wallStopwatch.Stop()
        $endedWallSeconds = [double]$wallStopwatch.Elapsed.TotalSeconds
        $endedUtc = [DateTime]::UtcNow
        $wallClockFrozen = $true
    }
    $resourceGate = (-not $stopReason) -and (-not $monitorError) -and ($successfulTreeSampleCount -ge 1)
    $resource = [ordered]@{
        schema = $schema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        stage = "primary-h4-p0r"
        runner_sha256 = $runnerHash
        fixture_sha256 = $token.fixture_sha256
        review_token_id = $token.review_token_id
        review_token_sha256 = $reviewTokenHash
        review_token_canonical_sha256 = $reviewTokenCanonicalHash
        resource_guard_policy_sha256 = $token.resource_policy_sha256
        execution_resource_scope_sha256 = $executionResourceScopeSha256
        guard_contract_sha256 = $guardHash
        guard_canonical_sha256 = $guardCanonicalHash
        parent_pid = $PID
        parent_pid_birth_utc_ticks = $parentBirthTicks
        monitor_kind = "Win32_outer_observer_plus_inner_runner_plus_factor_child_tree_Toolhelp32_Psapi_GetPerformanceInfo_100ms"
        execution_tree_root_pid = [int]$executionTreeRootProcessId
        execution_tree_root_birth_utc_ticks = [int64]$executionTreeRootBirthUtcTicks
        execution_tree_includes_runner = $true
        child_process_id = $process.Id
        child_process_handle_acquired = $childProcessHandleAcquired
        observed_child_process_ids = @($factorEvidenceProcessIds | Sort-Object)
        observed_process_identities = @(Get-ObservedProcessIdentities $factorEvidenceBirthTicks)
        simultaneous_current_factor_interval_semantics = "sampled_outer_observer_plus_inner_runner_plus_factor_child_tree_current_working_set_and_commit_during_nested_factor_interval"
        summed_os_lifetime_peak_semantics = "conservative_stop_gate_includes_outer_inner_preflight_control_and_factor_work_not_factor_only_peak"
        monitor_ok = (-not $monitorError)
        monitor_error = $monitorError
        stop_reason = $stopReason
        successful_tree_sample_count = $successfulTreeSampleCount
        child_visible_tree_sample_count = $childVisibleTreeSampleCount
        first_child_visible_sample_perf_counter_ns = $firstChildVisibleSamplePerfCounterNs
        first_child_visible_sample_utc = $firstChildVisibleSampleUtc
        last_child_visible_sample_perf_counter_ns = $lastChildVisibleSamplePerfCounterNs
        last_child_visible_sample_utc = $lastChildVisibleSampleUtc
        monitor_ready_marker_sha256 = $monitorReadyHash
        factor_complete_marker_sha256 = $factorCompleteHash
        monitor_release_marker_sha256 = $monitorReleaseHash
        factor_monitor_handshake_complete = $factorMonitorHandshakeComplete
        factor_prefix_one_sha256 = $factorPrefixOneHash
        factor_prefix_two_sha256 = $factorPrefixTwoHash
        baseline = $baseline
        immediate_pre_spawn = $immediatePreSpawn
        peak = $peak
        thresholds = [ordered]@{
            tree_ws_stop_bytes = $treeWorkingSetStop
            tree_private_stop_bytes = $treePrivateStop
            tree_commit_stop_bytes = $treeCommitStop
            commit_headroom_floor_bytes = $commitHeadroomFloor
            available_physical_floor_bytes = $availablePhysicalFloor
            minimum_commit_headroom_before_spawn_bytes = $minimumCommitHeadroomBeforeSpawn
            minimum_available_physical_before_spawn_bytes = $minimumAvailablePhysicalBeforeSpawn
        }
        started_utc = $startedUtc.ToString("o")
        ended_utc = $endedUtc.ToString("o")
        wall_seconds = $endedWallSeconds
        wall_clock_kind = "System.Diagnostics.Stopwatch"
        wall_stop_seconds = $wallStopSeconds
        claim_relative_path = $claimRelativePath
        claim_sha256 = $claimHash
        claim_canonical_sha256 = $claimCanonicalHash
        preflight_payload_sha256 = $preflightWrapper.payload_sha256
        final_system = $finalSystem
        final_system_sample_valid = $true
        child_exit_code = $childExitCode
        child_stdout_sha256 = if (Test-Path $stdoutPath) { Get-Sha256 $stdoutPath } else { $null }
        child_stderr_sha256 = if (Test-Path $stderrPath) { Get-Sha256 $stderrPath } else { $null }
        mandatory_resource_gate_pass = ($resourceGate -and $factorMonitorHandshakeComplete -and $childProcessHandleAcquired -and $childVisibleTreeSampleCount -ge 2 -and ($endedWallSeconds -le $wallStopSeconds) -and $peak.tree_working_set_bytes -le $treeWorkingSetStop -and $peak.tree_summed_process_lifetime_peak_working_set_bytes -le $treeWorkingSetStop -and $peak.tree_private_commit_bytes -le $treePrivateStop -and $peak.tree_committed_pagefile_bytes -le $treeCommitStop -and $peak.tree_summed_process_lifetime_peak_commit_bytes -le $treeCommitStop -and $peak.system_commit_headroom_min_bytes -ge $commitHeadroomFloor -and $peak.available_physical_min_bytes -ge $availablePhysicalFloor -and $finalSystem.commit_headroom_bytes -ge $commitHeadroomFloor -and $finalSystem.available_physical_bytes -ge $availablePhysicalFloor)
    }
    Write-AtomicUtf8NoBom $resourcePath ($resource | ConvertTo-Json -Depth 12 -Compress)
    $attemptStatus = if ($stopReason) { "resource_stop" } else { "finalizer_failure" }
    $finalArguments = @(
        $fixturePath, "--stage", "finalize-primary-h4-p0r",
        "--resource-report", $resourcePath,
        "--review-token", $reviewTokenPath,
        "--guard-contract", $guardPath,
        "--guard-nonce", $nonce,
        "--claim-file", $claimPath
    )
    if ((Test-Path -LiteralPath $stdoutPath) -and (Get-Item -LiteralPath $stdoutPath).Length -gt 0) {
        $finalArguments += @("--child-stdout", $stdoutPath)
    }
    $finalInvocation = Invoke-ControlPlanePython `
        -Operation "finalizer" `
        -TargetScriptPath $fixturePath `
        -ScriptArguments $finalArguments[1..($finalArguments.Count - 1)] `
        -AllowedExitCodes @(0, 2) `
        -AttemptArtifactPaths ([ordered]@{
            review_token = $reviewTokenPath
            claim = $claimPath
            guard = $guardPath
            resource = $resourcePath
            result = $finalPath
        })
    $finalExitCode = [int]$finalInvocation.exit_code
    Write-Utf8NoBom $finalPath ([string]$finalInvocation.stdout_text).Trim()
    if ($finalExitCode -notin @(0, 2)) { throw "BLOCKED_AV_BS_RESULT_SCHEMA: finalizer exit code is invalid" }
    $result = Get-Content -LiteralPath $finalPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($result.payload.schema -eq "AV-BS1-h4-p0r-result-v2") {
        if (
            $result.payload.outer_observer_contract_sha256 -ne $script:outerObserverContractSha256 -or
            $result.payload.outer_observer_handshake_prefix_sha256 -ne $script:outerObserverHandshakePrefixSha256 -or
            $result.payload.expected_terminal_seal_relative_path -ne $script:outerObserverExpectedTerminalSealRelativePath -or
            $result.payload.terminal_seal_required_for_authoritative_disposition -isnot [bool] -or
            $result.payload.terminal_seal_required_for_authoritative_disposition -ne $true -or
            $result.payload.terminal_seal_state -ne "pending_outer_observed_inner_exit" -or
            $result.payload.terminal_evidence_complete -isnot [bool] -or
            $result.payload.terminal_evidence_complete -ne $false -or
            $result.payload.authoritative_stage_pass -isnot [bool] -or
            $result.payload.authoritative_stage_pass -ne $false
        ) {
            throw "BLOCKED_AV_BS_RESULT_SCHEMA: finalizer result-v2 terminal bindings are invalid"
        }
    }
    if (
        $finalExitCode -eq 0 -and
        $result.payload.schema -eq "AV-BS1-h4-p0r-result-v2" -and
        $result.payload.mandatory_stage_pass -eq $true
    ) {
        $attemptStatus = "completed_pass"
        $runnerExitCode = 0
    }
    else {
        if (-not $stopReason) {
            if ($result.payload.schema -eq "AV-BS1-h4-p0r-result-v2") {
                $attemptStatus = "completed_failure"
            }
            elseif ($result.payload.schema -eq "AV-BS1-h4-p0r-failure-v1") {
                $attemptStatus = "finalizer_failure"
            }
            else {
                throw "BLOCKED_AV_BS_RESULT_SCHEMA: finalizer output schema is invalid"
            }
        }
        $runnerExitCode = 2
    }
    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $outputPath = Join-Path $validationRoot ("av-bs1-primary-h4-p0r-" + $timestamp + ".json")
    if (Test-Path -LiteralPath $outputPath) { throw "BLOCKED_AV_BS_RESULT_SCHEMA: output collision" }
    [IO.File]::WriteAllText($outputPath, (Get-Content -LiteralPath $finalPath -Raw -Encoding utf8).TrimEnd("`r", "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Output ("AV-BS1 result: " + $outputPath)
    Get-Content -LiteralPath $outputPath -Raw -Encoding utf8
}
catch {
    $monitorError = $_.Exception.Message
    if (-not $stopReason) { $stopReason = "RUNNER_EXCEPTION" }
    $attemptStatus = if ($stopReason -like "PRESPAWN_*") { "resource_stop" } elseif ($attemptStarted -and -not $childLaunched) { "spawn_failure" } else { "runner_exception" }
    # Retry only the deterministic evidence materialization needed to consume a
    # claim when failure occurred before child creation. The retry never starts
    # a child and never relaxes any binding.
    try {
        if ($claimCreated) {
            # CreateNew proves this process owns the path. If the first durable
            # write failed, replace only those owned bytes with the frozen
            # payload so the token can still be consumed fail-closed.
            $repairStream = [IO.File]::Open($claimPath, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $repairBytes = [Text.Encoding]::UTF8.GetBytes($claimJson)
                $repairStream.SetLength(0)
                $repairStream.Write($repairBytes, 0, $repairBytes.Length)
                $repairStream.Flush($true)
            }
            finally { $repairStream.Dispose() }
            $claimHash = Get-Sha256 $claimPath
            $claimCanonicalHash = Get-CanonicalJsonSha256 `
                $claimPath `
                ([ordered]@{ review_token = $reviewTokenPath; claim = $claimPath })
        }
        if ($null -ne $claimHash -and $null -ne $claimCanonicalHash) {
            if ($null -eq $guard) {
                $guard = & $buildGuardPayload $claimHash $claimCanonicalHash
            }
            if (-not (Test-Path -LiteralPath $guardPath -PathType Leaf)) {
                Write-AtomicUtf8NoBom $guardPath ($guard | ConvertTo-Json -Depth 8 -Compress)
            }
            if ($null -eq $guardHash) { $guardHash = Get-Sha256 $guardPath }
            if ($null -eq $guardCanonicalHash) {
                $guardCanonicalHash = Get-CanonicalJsonSha256 `
                    $guardPath `
                    ([ordered]@{ review_token = $reviewTokenPath; claim = $claimPath; guard = $guardPath })
            }
        }
    }
    catch {
        $monitorError = $monitorError + "; evidence recovery failed: " + $_.Exception.Message
    }
    # Materialize failure resource evidence only after owned-process cleanup
    # has either been verified or has itself become the recorded failure.
    $failureTerminationVerified = $false
    $liveAfterFailureSeal = @()
    try {
        $liveBeforeFailureSeal = @(Get-LiveObservedProcessIds $observedChildProcessIds $observedChildBirthTicks)
        if ($process -and ((-not $process.HasExited) -or $liveBeforeFailureSeal.Count -gt 0)) {
            Stop-ProcessTree $process.Id $observedChildProcessIds $observedChildBirthTicks
        }
        $liveAfterFailureSeal = @(Get-LiveObservedProcessIds $observedChildProcessIds $observedChildBirthTicks)
        if ($liveAfterFailureSeal.Count -gt 0) {
            throw "observed process survived failure cleanup"
        }
        if ($process) {
            $process.Refresh()
            $failureTerminationVerified = [bool]$process.HasExited
        }
        else {
            $failureTerminationVerified = $true
        }
        if (-not $failureTerminationVerified) {
            throw "child root exit was not verified"
        }
    }
    catch {
        $stopReason = "OBSERVED_PROCESS_TERMINATION_FAILED"
        $monitorError = if ($monitorError) { $monitorError + "; " + $_.Exception.Message } else { $_.Exception.Message }
    }
    $caughtChildExitCode = $null
    if ($process) {
        try {
            $process.Refresh()
            if ($process.HasExited) {
                $process.WaitForExit()
                $caughtChildExitCode = [int]$process.ExitCode
            }
        }
        catch { $caughtChildExitCode = $null }
    }
    $caughtStdoutHash = if ((Test-Path -LiteralPath $stdoutPath -PathType Leaf) -and (Get-Item -LiteralPath $stdoutPath).Length -gt 0) { Get-Sha256 $stdoutPath } else { $null }
    $caughtStderrHash = if (Test-Path -LiteralPath $stderrPath -PathType Leaf) { Get-Sha256 $stderrPath } else { $null }
    $monitorReadyHash = if (Test-Path -LiteralPath $monitorReadyPath -PathType Leaf) { Get-Sha256 $monitorReadyPath } else { $null }
    $factorCompleteHash = if (Test-Path -LiteralPath $factorCompletePath -PathType Leaf) { Get-Sha256 $factorCompletePath } else { $null }
    $monitorReleaseHash = if (Test-Path -LiteralPath $monitorReleasePath -PathType Leaf) { Get-Sha256 $monitorReleasePath } else { $null }
    $factorPrefixOneHash = if (Test-Path -LiteralPath $factorPrefixOnePath -PathType Leaf) { Get-Sha256 $factorPrefixOnePath } else { $null }
    $factorPrefixTwoHash = if (Test-Path -LiteralPath $factorPrefixTwoPath -PathType Leaf) { Get-Sha256 $factorPrefixTwoPath } else { $null }
    $caughtFactorMonitorHandshakeComplete = (
        $null -ne $firstChildVisibleSamplePerfCounterNs -and
        $null -ne $lastChildVisibleSamplePerfCounterNs -and
        $null -ne $monitorReadyHash -and
        $null -ne $factorCompleteHash -and
        $null -ne $monitorReleaseHash
    )
    $caughtFinalSystem = $null
    $caughtFinalSystemValid = $false
    if ($failureTerminationVerified -and $wallClockFrozen) {
        # The factor-fit interval already ended after verified child exit and
        # its final system sample. Do not mutate the frozen peak/wall evidence
        # merely because later finalizer/control-plane materialization failed.
        $caughtFinalSystem = $finalSystem
        $caughtFinalSystemValid = ($null -ne $caughtFinalSystem)
    }
    elseif ($failureTerminationVerified) {
        try {
            $caughtFinalTree = Get-TreeSample $executionTreeRootProcessId $executionTreeRootBirthUtcTicks $factorEvidenceProcessIds $factorEvidenceBirthTicks
            $caughtFinalSystem = Get-SystemSample
            Update-ControlPlanePeak $peak $caughtFinalTree $caughtFinalSystem
            $caughtFinalSystemValid = $true
        }
        catch {
            $monitorError = if ($monitorError) { $monitorError + "; final post-termination sample failed: " + $_.Exception.Message } else { "final post-termination sample failed: " + $_.Exception.Message }
        }
    }
    else {
        $monitorError = if ($monitorError) { $monitorError + "; final sample refused before verified termination" } else { "final sample refused before verified termination" }
    }
    if (-not $wallClockFrozen) {
        if ($wallStopwatch.IsRunning) { $wallStopwatch.Stop() }
        $endedWallSeconds = [double]$wallStopwatch.Elapsed.TotalSeconds
        $endedUtc = [DateTime]::UtcNow
        $wallClockFrozen = $true
    }
    $resource = [ordered]@{
        schema = $schema; program = $program; case_id = "AV-BS1-CIRCLE-PRIMARY"; stage = "primary-h4-p0r"
        runner_sha256 = $runnerHash; fixture_sha256 = $token.fixture_sha256; review_token_id = $token.review_token_id
        review_token_sha256 = $reviewTokenHash; review_token_canonical_sha256 = $reviewTokenCanonicalHash
        resource_guard_policy_sha256 = $token.resource_policy_sha256
        execution_resource_scope_sha256 = $executionResourceScopeSha256
        guard_contract_sha256 = $guardHash; guard_canonical_sha256 = $guardCanonicalHash
        parent_pid = $PID; parent_pid_birth_utc_ticks = $parentBirthTicks
        monitor_kind = "Win32_outer_observer_plus_inner_runner_plus_factor_child_tree_Toolhelp32_Psapi_GetPerformanceInfo_100ms"
        execution_tree_root_pid = [int]$executionTreeRootProcessId; execution_tree_root_birth_utc_ticks = [int64]$executionTreeRootBirthUtcTicks; execution_tree_includes_runner = $true
        child_process_id = if ($process) { $process.Id } else { $null }; child_process_handle_acquired = $childProcessHandleAcquired
        observed_child_process_ids = @($factorEvidenceProcessIds | Sort-Object)
        observed_process_identities = @(Get-ObservedProcessIdentities $factorEvidenceBirthTicks)
        simultaneous_current_factor_interval_semantics = "sampled_outer_observer_plus_inner_runner_plus_factor_child_tree_current_working_set_and_commit_during_nested_factor_interval"
        summed_os_lifetime_peak_semantics = "conservative_stop_gate_includes_outer_inner_preflight_control_and_factor_work_not_factor_only_peak"
        monitor_ok = $false; monitor_error = $monitorError; stop_reason = $stopReason
        successful_tree_sample_count = $successfulTreeSampleCount; child_visible_tree_sample_count = $childVisibleTreeSampleCount
        first_child_visible_sample_perf_counter_ns=$firstChildVisibleSamplePerfCounterNs; first_child_visible_sample_utc=$firstChildVisibleSampleUtc; last_child_visible_sample_perf_counter_ns=$lastChildVisibleSamplePerfCounterNs; last_child_visible_sample_utc=$lastChildVisibleSampleUtc
        monitor_ready_marker_sha256=$monitorReadyHash; factor_complete_marker_sha256=$factorCompleteHash; monitor_release_marker_sha256=$monitorReleaseHash; factor_monitor_handshake_complete=$caughtFactorMonitorHandshakeComplete
        factor_prefix_one_sha256=$factorPrefixOneHash; factor_prefix_two_sha256=$factorPrefixTwoHash
        baseline = $baseline; immediate_pre_spawn = $immediatePreSpawn; peak = $peak; thresholds = [ordered]@{ tree_ws_stop_bytes=$treeWorkingSetStop; tree_private_stop_bytes=$treePrivateStop; tree_commit_stop_bytes=$treeCommitStop; commit_headroom_floor_bytes=$commitHeadroomFloor; available_physical_floor_bytes=$availablePhysicalFloor; minimum_commit_headroom_before_spawn_bytes=$minimumCommitHeadroomBeforeSpawn; minimum_available_physical_before_spawn_bytes=$minimumAvailablePhysicalBeforeSpawn }
        started_utc = $startedUtc.ToString("o"); ended_utc = $endedUtc.ToString("o"); wall_seconds = $endedWallSeconds; wall_clock_kind = "System.Diagnostics.Stopwatch"; wall_stop_seconds = $wallStopSeconds
        claim_relative_path=$claimRelativePath; claim_sha256=$claimHash; claim_canonical_sha256=$claimCanonicalHash; preflight_payload_sha256=$preflightWrapper.payload_sha256; final_system=$caughtFinalSystem; final_system_sample_valid=$caughtFinalSystemValid
        child_exit_code=$caughtChildExitCode; child_stdout_sha256=$caughtStdoutHash; child_stderr_sha256=$caughtStderrHash; mandatory_resource_gate_pass=$false; failure_code="BLOCKED_AV_BS_RESOURCE"
    }
    if ($failureTerminationVerified -and $caughtFinalSystemValid -and $wallClockFrozen) {
        try { Write-AtomicUtf8NoBom $resourcePath ($resource | ConvertTo-Json -Depth 12 -Compress) }
        catch { $resourceEvidenceWriteError = $_.Exception.Message }
    }
    else {
        $resourceEvidenceWriteError = "failure resource withheld before verified termination and final sampling"
    }
    $runnerExitCode = 2
}
finally {
    try {
        $liveObservedInFinally = @(Get-LiveObservedProcessIds $observedChildProcessIds $observedChildBirthTicks)
        if ($process -and ((-not $process.HasExited) -or $liveObservedInFinally.Count -gt 0)) {
            Stop-ProcessTree $process.Id $observedChildProcessIds $observedChildBirthTicks
        }
        if ($process) {
            if (-not $process.HasExited) { [void]$process.WaitForExit(10000) }
            $process.Refresh()
            $factorRootExitObserved = [bool]$process.HasExited
            if (
                $factorRootExitObserved -and
                $childProcessHandleAcquired -and
                $observedChildBirthTicks.ContainsKey([int]$process.Id)
            ) {
                $factorOwnedSurvivorsAfterCleanup = @(
                    Get-LiveObservedProcessIds $observedChildProcessIds $observedChildBirthTicks
                )
                $factorCleanupVerified = ($factorOwnedSurvivorsAfterCleanup.Count -eq 0)
            }
        }
    }
    catch {
        $monitorError = $_.Exception.Message
        $stopReason = "OBSERVED_PROCESS_TERMINATION_FAILED"
        $runnerExitCode = 2
        $preserveAttemptEvidence = $true
        $factorRootExitObserved = $false
        $factorCleanupVerified = $false
        $factorOwnedSurvivorsAfterCleanup = @(-1)
    }
    finally {
        $consumerControlGatePassed = $false
        $consumerReportReference = $null
        $pendingTerminalTombstone = $null
        $pendingTerminalArtifactHashes = $null
        $pendingTerminalConsumerControlGatePassed = $false
        $pendingTerminalConsumerReportReference = $null
        try {
            if ($claimCreated -and $factorRootExitObserved -and $factorCleanupVerified -and @($factorOwnedSurvivorsAfterCleanup).Count -eq 0) {
                $consumeArguments = @(
                    $fixturePath, "--stage", "consume-primary-h4-p0r-token",
                    "--review-token", $reviewTokenPath,
                    "--claim-file", $claimPath,
                    "--guard-contract", $guardPath,
                    "--guard-nonce", $nonce,
                    "--attempt-status", $attemptStatus
                )
                if ((Test-Path -LiteralPath $finalPath) -and (Get-Item -LiteralPath $finalPath).Length -gt 0) {
                    $consumeArguments += @("--result-file", $finalPath)
                }
                if ((Test-Path -LiteralPath $stdoutPath) -and (Get-Item -LiteralPath $stdoutPath).Length -gt 0) {
                    $consumeArguments += @("--child-stdout", $stdoutPath)
                }
                if ((Test-Path -LiteralPath $resourcePath) -and (Get-Item -LiteralPath $resourcePath).Length -gt 0) {
                    $consumeArguments += @("--resource-report", $resourcePath)
                }
                $consumeInvocation = Invoke-ControlPlanePython `
                    -Operation "token_consumer" `
                    -TargetScriptPath $fixturePath `
                    -ScriptArguments $consumeArguments[1..($consumeArguments.Count - 1)] `
                    -AllowedExitCodes @(0) `
                    -AttemptArtifactPaths ([ordered]@{
                        review_token = $reviewTokenPath
                        claim = $claimPath
                        guard = $guardPath
                        resource = $resourcePath
                        result = $finalPath
                    })
                if ($controlPlaneReportReferences.Count -lt 1) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: consumer control report reference is missing"
                }
                $consumerReportReference = $controlPlaneReportReferences[$controlPlaneReportReferences.Count - 1]
                if (
                    $consumerReportReference.operation -ne "token_consumer" -or
                    $consumerReportReference.report_path -ne $consumeInvocation.report_path -or
                    $consumerReportReference.report_sha256 -ne $consumeInvocation.report_sha256 -or
                    $consumerReportReference.envelope_close_path -ne $consumeInvocation.envelope_close_path -or
                    $consumerReportReference.envelope_close_sha256 -ne $consumeInvocation.envelope_close_sha256 -or
                    $consumerReportReference.mandatory_control_plane_gate_pass -ne $true
                ) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: consumer control report reference mismatch"
                }
                $consumerControlGatePassed = $true
                $consumeWrapper = ([string]$consumeInvocation.stdout_text).Trim() | ConvertFrom-Json
                if (
                    $consumeWrapper.payload.schema -ne $tombstoneSchema -or
                    $consumeWrapper.payload.authorization_state -ne "consumed" -or
                    $consumeWrapper.payload.uses_remaining -ne 0 -or
                    $consumeWrapper.payload.next_stage_authorized -ne $false
                ) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: consumed token wrapper mismatch"
                }
                if (
                    $runnerExitCode -eq 0 -and (
                        $consumeWrapper.payload.consumption_validated_pass -ne $true -or
                        $consumeWrapper.payload.mandatory_stage_pass -ne $true -or
                        @($consumeWrapper.payload.failure_codes).Count -ne 0
                    )
                ) {
                    $runnerExitCode = 2
                }
                $matchingTombstone = Get-MatchingNormalConsumedTombstone $reviewTokenPath
                if ($null -eq $matchingTombstone) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: consumed token file does not match wrapper attempt"
                }
                $pendingTerminalTombstone = $matchingTombstone
                $pendingTerminalArtifactHashes = Get-CurrentTerminalArtifactHashes
                $normalPassOuterAudit = Get-NormalPassEvidenceAudit $matchingTombstone $pendingTerminalArtifactHashes
                if ($runnerExitCode -eq 0 -and -not $normalPassOuterAudit.pass) {
                    $runnerExitCode = 2
                }
                $pendingTerminalConsumerControlGatePassed = $true
                $pendingTerminalConsumerReportReference = $consumerReportReference
                $consumptionSucceeded = $true
                # The complete marker retains terminal hashes. Preserve the
                # exact guard/resource/result/stream/prefix bytes on pass too.
                $preserveAttemptEvidence = $true
            }
            elseif ($claimCreated) {
                $runnerExitCode = 2
                $preserveAttemptEvidence = $true
                Write-Output "AV-BS1 token mutation withheld: factor-root exit and zero-survivor cleanup were not verified"
            }
        }
        catch {
            if (-not $claimCreated) { throw }
            $consumerFailureMessage = $_.Exception.Message
            if ($null -eq $consumerReportReference -and $controlPlaneReportReferences.Count -gt 0) {
                $candidateConsumerReport = $controlPlaneReportReferences[$controlPlaneReportReferences.Count - 1]
                if ($candidateConsumerReport.operation -eq "token_consumer") {
                    $consumerReportReference = $candidateConsumerReport
                }
            }
            $consumerReportReferenceComplete = (
                $null -ne $consumerReportReference -and
                $consumerReportReference.operation -eq "token_consumer" -and
                $consumerReportReference.report_path -is [string] -and
                -not [string]::IsNullOrWhiteSpace([string]$consumerReportReference.report_path) -and
                [string]$consumerReportReference.report_sha256 -match '^[0-9a-f]{64}$' -and
                $consumerReportReference.envelope_close_path -is [string] -and
                -not [string]::IsNullOrWhiteSpace([string]$consumerReportReference.envelope_close_path) -and
                [string]$consumerReportReference.envelope_close_sha256 -match '^[0-9a-f]{64}$'
            )
            if (-not $consumerReportReferenceComplete) {
                $consumerReportReference = $null
                $consumerControlGatePassed = $false
            }
            # Independently validate the canonical token bytes even when the
            # wrapper/envelope gate failed. A matching same-attempt normal
            # tombstone is already fail-closed and must never be overwritten.
            if (-not ($factorRootExitObserved -and $factorCleanupVerified -and @($factorOwnedSurvivorsAfterCleanup).Count -eq 0)) {
                $runnerExitCode = 2
                $preserveAttemptEvidence = $true
            }
            else {
                $matchingTombstone = Get-MatchingNormalConsumedTombstone $reviewTokenPath
                if ($null -ne $matchingTombstone) {
                # The durable token file validates independently. A failed
                # wrapper/envelope gate still downgrades the runner disposition.
                if (-not $consumerControlGatePassed) { $runnerExitCode = 2 }
                if (
                    $runnerExitCode -eq 0 -and (
                        $matchingTombstone.consumption_validated_pass -ne $true -or
                        $matchingTombstone.mandatory_stage_pass -ne $true -or
                        @($matchingTombstone.failure_codes).Count -ne 0
                    )
                ) { $runnerExitCode = 2 }
                $pendingTerminalTombstone = $matchingTombstone
                $pendingTerminalArtifactHashes = Get-CurrentTerminalArtifactHashes
                $normalPassOuterAudit = Get-NormalPassEvidenceAudit $matchingTombstone $pendingTerminalArtifactHashes
                if ($runnerExitCode -eq 0 -and -not $normalPassOuterAudit.pass) {
                    $runnerExitCode = 2
                }
                $pendingTerminalConsumerControlGatePassed = $consumerControlGatePassed
                $pendingTerminalConsumerReportReference = $consumerReportReference
                $consumptionSucceeded = $true
                $preserveAttemptEvidence = $true
                }
                else {
                # A failed consumer control gate cannot leave an authorized token
                # or an untrusted success tombstone. Replacement is idempotently
                # tied to the original reviewed token and this owned claim.
                $runnerExitCode = 2
                $replacementTombstone = Set-EmergencyConsumedTombstone `
                    -ConsumerFailureMessage $consumerFailureMessage `
                    -ConsumerControlGatePassed $consumerControlGatePassed `
                    -FactorRootExitObserved $factorRootExitObserved `
                    -FactorCleanupVerified $factorCleanupVerified `
                    -OwnedSurvivorCount ([int]@($factorOwnedSurvivorsAfterCleanup).Count)
                $pendingTerminalTombstone = $replacementTombstone
                $pendingTerminalArtifactHashes = Get-CurrentTerminalArtifactHashes
                $pendingTerminalConsumerControlGatePassed = $consumerControlGatePassed
                $pendingTerminalConsumerReportReference = $consumerReportReference
                $consumptionSucceeded = $true
                $preserveAttemptEvidence = $true
                if ($replacementTombstone.schema -eq $emergencyTombstoneSchema) {
                    Write-Output "AV-BS1 token emergency-consumed after control-plane failure"
                }
                else {
                    Write-Output "AV-BS1 accepted a raced matching normal consumed tombstone"
                }
                }
            }
        }
        finally {
            $preExitEvidenceSucceeded = $false
            $preExitEvidenceError = $null
            if ($claimCreated -and $consumptionSucceeded) {
                try {
                    $script:outerObserverPreExitEvidenceReference = Write-PreExitControlPlaneEvidence `
                        -ValidatedTombstone $pendingTerminalTombstone `
                        -ConsumerControlGatePassed $pendingTerminalConsumerControlGatePassed `
                        -ConsumerReportReference $pendingTerminalConsumerReportReference `
                        -ArtifactHashes $pendingTerminalArtifactHashes
                    $preExitEvidenceSucceeded = $true
                }
                catch {
                    $preExitEvidenceError = $_.Exception.Message
                    $runnerExitCode = 2
                    $preserveAttemptEvidence = $true
                }
            }

            $terminalArtifactHashPresent = $false
            if ($null -ne $pendingTerminalArtifactHashes) {
                foreach ($artifactHashField in @(
                    "resource_report_sha256", "result_file_sha256",
                    "child_stdout_sha256", "child_stderr_sha256",
                    "monitor_ready_marker_sha256", "factor_complete_marker_sha256",
                    "monitor_release_marker_sha256",
                    "factor_prefix_one_sha256", "factor_prefix_two_sha256"
                )) {
                    if ([string]$pendingTerminalArtifactHashes[$artifactHashField] -match '^[0-9a-f]{64}$') {
                        $terminalArtifactHashPresent = $true
                    }
                }
            }
            if ($claimCreated -and $terminalArtifactHashPresent) {
                $preserveAttemptEvidence = $true
            }

            $resolvedTemp = [IO.Path]::GetFullPath($tempDirectory)
            $tempIsOwned = (
                $resolvedTemp.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
                [IO.Path]::GetDirectoryName($resolvedTemp).TrimEnd('\') -eq $tempRoot.TrimEnd('\') -and
                [IO.Path]::GetFileName($resolvedTemp) -eq $tempLeaf -and
                (Test-Path -LiteralPath $resolvedTemp)
            )
            if (
                $tempIsOwned -and (
                    (-not $claimCreated) -or
                    (
                        $consumptionSucceeded -and $preExitEvidenceSucceeded -and
                        -not $preserveAttemptEvidence -and -not $terminalArtifactHashPresent
                    )
                )
            ) {
                Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
                $script:outerObserverAttemptCleanupDisposition = "removed"
                $script:outerObserverAttemptEvidenceRelativePath = $null
            }
            elseif ($tempIsOwned) {
                $quarantineRoot = [IO.Path]::GetFullPath((Join-Path $validationRoot "quarantine"))
                [IO.Directory]::CreateDirectory($quarantineRoot) | Out-Null
                $quarantineLeaf = $token.review_token_id + "-" + $nonce
                $quarantinePath = [IO.Path]::GetFullPath((Join-Path $quarantineRoot $quarantineLeaf))
                if (
                    -not $quarantinePath.StartsWith($quarantineRoot, [StringComparison]::OrdinalIgnoreCase) -or
                    [IO.Path]::GetDirectoryName($quarantinePath).TrimEnd('\') -ne $quarantineRoot.TrimEnd('\') -or
                    (Test-Path -LiteralPath $quarantinePath)
                ) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: quarantine path validation failed"
                }
                Move-Item -LiteralPath $resolvedTemp -Destination $quarantinePath
                $script:outerObserverAttemptCleanupDisposition = "quarantined"
                $script:outerObserverAttemptEvidenceRelativePath = Get-RepositoryRelativePath $quarantinePath
                Write-Output ("AV-BS1 retained attempt evidence quarantine: " + $quarantinePath)
            }
            if ($null -ne $preExitEvidenceError) {
                throw ("BLOCKED_AV_BS_RESULT_SCHEMA: pre-exit evidence failed; attempt evidence preserved: " + $preExitEvidenceError)
            }
        }
    }
}

if ($null -eq $runnerExitCode) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: runner completed without a terminal exit code"
}
if ($script:outerObserverInnerActive) {
    try {
        Complete-OuterObservedInner ([int]$runnerExitCode)
    }
    catch {
        Write-Error $_.Exception.Message -ErrorAction Continue
        exit 2
    }
}
exit $runnerExitCode
