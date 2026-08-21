param(
    [ValidateSet("manifest", "primary-h2")]
    [string]$Stage = "manifest"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$program = "SPD Decap PI Evaluator v0.22.0"
$schema = "AV-BS1-h2-p1-resource-report-v1"
$guardSchema = "AV-BS1-h2-p1-resource-guard-v1"
$tombstoneSchema = "AV-BS1-h2-p1-consumed-review-token-v1"
$pollMilliseconds = 100
$wallStopSeconds = 900
$oneFactorPerFrequency = $true
$boundaryRhsBatch = 4
$treeWorkingSetStop = [int64](4GB)
$treePrivateStop = [int64](5GB)
$treeCommitStop = [int64](5GB)
$commitHeadroomFloor = [int64](2GB)
$availablePhysicalFloor = [int64](1.5GB)

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$fixturePath = Join-Path $scriptDirectory "av_bs1_boundary_schur_h2_p1.py"
$reviewTokenPath = Join-Path $scriptDirectory "av_bs1_h2_p1_review_token.json"
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $scriptDirectory "..\.."))
$validationRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot "validation-output\av-bs1"))
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonPath = $pythonCommand.Source

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Quote-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

if ($Stage -eq "manifest") {
    & $pythonPath $fixturePath --stage manifest
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $reviewTokenPath -PathType Leaf)) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: reviewed primary-h2 token is missing"
}

# Validate the complete H1/P0/P1 lineage and clean tracked token before any claim.
$preflightLines = @(& $pythonPath $fixturePath --stage preflight-primary-h2 --review-token $reviewTokenPath)
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_AV_BS_RESULT_SCHEMA: primary-h2 complete preflight failed" }
$preflightText = ($preflightLines -join "`n").Trim()
$preflightWrapper = $preflightText | ConvertFrom-Json
if (
    $preflightWrapper.payload.stage -ne "preflight-primary-h2" -or
    $preflightWrapper.payload.preflight_pass -ne $true -or
    $preflightWrapper.payload.factorization_performed -ne $false -or
    $preflightWrapper.payload.physics_solve_performed -ne $false -or
    [string]$preflightWrapper.payload_sha256 -notmatch '^[0-9a-f]{64}$'
) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: primary-h2 preflight wrapper mismatch"
}
$token = Get-Content -LiteralPath $reviewTokenPath -Raw -Encoding utf8 | ConvertFrom-Json
if ([string]$token.review_token_id -notmatch '^[0-9a-f]{32}$' -or $token.uses_remaining -ne 1) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: invalid one-use review token"
}

if (-not ("AvBsNativeV1" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class AvBsNativeV1 {
    const uint TH32CS_SNAPPROCESS = 0x00000002;
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    const uint PROCESS_VM_READ = 0x0010;
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

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool Process32FirstW(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool Process32NextW(IntPtr snapshot, ref PROCESSENTRY32 entry);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint processId);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    [DllImport("psapi.dll", SetLastError=true)]
    static extern bool GetProcessMemoryInfo(IntPtr process, ref PROCESS_MEMORY_COUNTERS_EX2 counters, uint size);
    [DllImport("psapi.dll", SetLastError=true)]
    static extern bool GetPerformanceInfo(ref PERFORMANCE_INFORMATION information, uint size);

    static long U(UIntPtr value) { return unchecked((long)value.ToUInt64()); }

    public static Dictionary<int,int> ProcessParents() {
        var result = new Dictionary<int,int>();
        IntPtr snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snapshot == INVALID_HANDLE_VALUE) throw new InvalidOperationException("CreateToolhelp32Snapshot failed");
        try {
            var entry = new PROCESSENTRY32();
            entry.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            if (!Process32FirstW(snapshot, ref entry)) throw new InvalidOperationException("Process32First failed");
            do {
                result[(int)entry.th32ProcessID] = (int)entry.th32ParentProcessID;
                entry.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            } while (Process32NextW(snapshot, ref entry));
        } finally { CloseHandle(snapshot); }
        return result;
    }

    public static long[] ProcessMetrics(int processId) {
        IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, false, (uint)processId);
        if (process == IntPtr.Zero) return new long[0];
        try {
            var counters = new PROCESS_MEMORY_COUNTERS_EX2();
            counters.cb = (uint)Marshal.SizeOf(typeof(PROCESS_MEMORY_COUNTERS_EX2));
            if (!GetProcessMemoryInfo(process, ref counters, counters.cb)) return new long[0];
            return new long[] {
                U(counters.WorkingSetSize), U(counters.PeakWorkingSetSize),
                U(counters.PagefileUsage), U(counters.PeakPagefileUsage),
                U(counters.PrivateUsage), U(counters.PrivateWorkingSetSize),
                U(counters.SharedCommitUsage), counters.PageFaultCount
            };
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
}

function Get-DescendantIds([int]$RootProcessId) {
    $parents = [AvBsNativeV1]::ProcessParents()
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
    $values = [AvBsNativeV1]::SystemMetrics()
    if ($values.Length -ne 4) { throw "BLOCKED_AV_BS_RESOURCE: system counter query failed" }
    return [ordered]@{
        commit_total_bytes = [int64]$values[0]
        commit_limit_bytes = [int64]$values[1]
        commit_headroom_bytes = [int64]($values[1] - $values[0])
        available_physical_bytes = [int64]$values[2]
        page_size_bytes = [int64]$values[3]
    }
}

function Get-TreeSample([int]$RootProcessId) {
    $ids = Get-DescendantIds $RootProcessId
    if ($ids.Count -lt 1) { throw "BLOCKED_AV_BS_RESOURCE: child process tree disappeared" }
    [int64]$working = 0
    [int64]$peakWorking = 0
    [int64]$commit = 0
    [int64]$peakCommit = 0
    [int64]$private = 0
    [int64]$privateWorking = 0
    [int64]$sharedCommit = 0
    [int64]$pageFaults = 0
    foreach ($id in $ids) {
        $metric = [AvBsNativeV1]::ProcessMetrics([int]$id)
        if ($metric.Length -ne 8) { throw "BLOCKED_AV_BS_RESOURCE: process counter query failed for PID $id" }
        $working += $metric[0]
        $peakWorking += $metric[1]
        $commit += $metric[2]
        $peakCommit += $metric[3]
        $private += $metric[4]
        $privateWorking += $metric[5]
        $sharedCommit += $metric[6]
        $pageFaults += $metric[7]
    }
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

function Stop-ProcessTree(
    [int]$RootProcessId,
    [System.Collections.Generic.HashSet[int]]$ObservedProcessIds
) {
    [void]$ObservedProcessIds.Add($RootProcessId)
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        try {
            foreach ($id in @(Get-DescendantIds $RootProcessId)) {
                [void]$ObservedProcessIds.Add([int]$id)
            }
        }
        catch { }
        foreach ($id in @($ObservedProcessIds | Where-Object { $_ -ne $RootProcessId })) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 50
        $survivors = @(
            $ObservedProcessIds | Where-Object {
                Get-Process -Id $_ -ErrorAction SilentlyContinue
            }
        )
        if ($survivors.Count -eq 0) { return }
    }
    throw (
        "BLOCKED_AV_BS_RESOURCE: observed child process termination could not be verified: " +
        (($ObservedProcessIds | Sort-Object) -join ",")
    )
}

$runnerHash = Get-Sha256 $MyInvocation.MyCommand.Path
$baseline = Get-SystemSample
$system = $baseline
if ($baseline.commit_headroom_bytes -lt $commitHeadroomFloor) {
    throw "BLOCKED_AV_BS_RESOURCE: baseline commit headroom is below 2 GiB"
}
if ($baseline.available_physical_bytes -lt $availablePhysicalFloor) {
    throw "BLOCKED_AV_BS_RESOURCE: baseline available physical memory is below 1.5 GiB"
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
$claimPayload = [ordered]@{
    schema = "AV-BS1-h2-p1-token-claim-v1"
    program = $program
    case_id = "AV-BS1-CIRCLE-PRIMARY"
    stage = "primary-h2"
    claim_relative_path = $claimRelativePath
    token_id = $token.review_token_id
    review_token_sha256 = Get-Sha256 $reviewTokenPath
    review_binding_sha256 = $preflightWrapper.payload.review_binding_sha256
    fixture_sha256 = $token.p1_fixture_sha256
    runner_sha256 = $runnerHash
    p1_preregistration_commit = $token.p1_preregistration_commit
    git_head = $preflightWrapper.payload.git_head
    preflight_payload_sha256 = $preflightWrapper.payload_sha256
    claimed_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
}
try { $claimStream = [IO.File]::Open($claimPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None) }
catch { throw "BLOCKED_AV_BS_RESULT_SCHEMA: token claim already exists" }
try {
    $claimBytes = [Text.Encoding]::UTF8.GetBytes(($claimPayload | ConvertTo-Json -Depth 8 -Compress))
    $claimStream.Write($claimBytes, 0, $claimBytes.Length)
}
finally { $claimStream.Dispose() }
$claimHash = Get-Sha256 $claimPath

$nonce = [guid]::NewGuid().ToString("N")
$guardPath = Join-Path $tempDirectory "guard.json"
$stdoutPath = Join-Path $tempDirectory "numerical.json"
$stderrPath = Join-Path $tempDirectory "child.stderr.txt"
$resourcePath = Join-Path $tempDirectory "resource.json"
$finalPath = Join-Path $tempDirectory "final.json"
$guard = [ordered]@{
    schema = $guardSchema
    program = $program
    case_id = "AV-BS1-CIRCLE-PRIMARY"
    stage = "primary-h2"
    nonce = $nonce
    parent_pid = $PID
    monitor_ok = $true
    poll_interval_ms = $pollMilliseconds
    wall_stop_seconds = $wallStopSeconds
    tree_ws_stop_bytes = $treeWorkingSetStop
    tree_private_stop_bytes = $treePrivateStop
    tree_commit_stop_bytes = $treeCommitStop
    commit_headroom_floor_bytes = $commitHeadroomFloor
    available_physical_floor_bytes = $availablePhysicalFloor
    fixture_sha256 = $token.p1_fixture_sha256
    review_token_id = $token.review_token_id
    review_token_sha256 = Get-Sha256 $reviewTokenPath
    claim_relative_path = $claimRelativePath
    claim_sha256 = $claimHash
    preflight_payload_sha256 = $preflightWrapper.payload_sha256
    resource_guard_policy_sha256 = $token.h2_p1_resource_guard_policy_sha256
    baseline_commit_headroom_bytes = $baseline.commit_headroom_bytes
    baseline_available_physical_bytes = $baseline.available_physical_bytes
    runner_sha256 = $runnerHash
}
Write-Utf8NoBom $guardPath ($guard | ConvertTo-Json -Depth 8 -Compress)

$startedUtc = [DateTime]::UtcNow
$stopReason = $null
$monitorError = $null
$peak = [ordered]@{
    tree_working_set_bytes = [int64]0
    tree_private_commit_bytes = [int64]0
    tree_committed_pagefile_bytes = [int64]0
    tree_nonprivate_working_set_proxy_bytes = [int64]0
    tree_page_fault_count = [int64]0
    system_commit_total_bytes = [int64]$baseline.commit_total_bytes
    system_commit_headroom_min_bytes = [int64]$baseline.commit_headroom_bytes
    available_physical_min_bytes = [int64]$baseline.available_physical_bytes
}
$process = $null
$successfulTreeSampleCount = 0
$observedChildProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'
$attemptStarted = $false
$attemptStatus = "runner_exception"
$runnerExitCode = $null

try {
    $argumentList = @(
        (Quote-Argument $fixturePath), "--stage", "primary-h2",
        "--guard-contract", (Quote-Argument $guardPath),
        "--guard-nonce", $nonce,
        "--review-token", (Quote-Argument $reviewTokenPath),
        "--claim-file", (Quote-Argument $claimPath)
    ) -join " "
    $process = Start-Process -FilePath $pythonPath -ArgumentList $argumentList -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $attemptStarted = $true
    # Start-Process on Windows PowerShell can lose ExitCode after a manually
    # monitored redirected child exits unless its native handle is acquired
    # while it is still alive.  Retain the handle before entering the poll loop.
    $childProcessHandle = $process.Handle
    if ($childProcessHandle -eq [IntPtr]::Zero) {
        throw "BLOCKED_AV_BS_RESULT_SCHEMA: child process handle was not acquired"
    }
    [void]$observedChildProcessIds.Add($process.Id)
    while (-not $process.HasExited) {
        $terminationRequired = $false
        try {
            $tree = Get-TreeSample $PID
            $system = Get-SystemSample
            $successfulTreeSampleCount += 1
            foreach ($observedId in $tree.process_ids) {
                if ([int]$observedId -ne $PID) {
                    [void]$observedChildProcessIds.Add([int]$observedId)
                }
            }
            $peak.tree_working_set_bytes = [math]::Max($peak.tree_working_set_bytes, $tree.working_set_bytes)
            $peak.tree_private_commit_bytes = [math]::Max($peak.tree_private_commit_bytes, $tree.private_commit_bytes)
            $peak.tree_committed_pagefile_bytes = [math]::Max($peak.tree_committed_pagefile_bytes, $tree.committed_pagefile_bytes)
            $peak.tree_nonprivate_working_set_proxy_bytes = [math]::Max($peak.tree_nonprivate_working_set_proxy_bytes, $tree.nonprivate_working_set_proxy_bytes)
            $peak.tree_page_fault_count = [math]::Max($peak.tree_page_fault_count, $tree.page_fault_count)
            $peak.system_commit_total_bytes = [math]::Max($peak.system_commit_total_bytes, $system.commit_total_bytes)
            $peak.system_commit_headroom_min_bytes = [math]::Min($peak.system_commit_headroom_min_bytes, $system.commit_headroom_bytes)
            $peak.available_physical_min_bytes = [math]::Min($peak.available_physical_min_bytes, $system.available_physical_bytes)
            if ($tree.working_set_bytes -gt $treeWorkingSetStop) { $stopReason = "TREE_WS_STOP" }
            elseif ($tree.private_commit_bytes -gt $treePrivateStop) { $stopReason = "TREE_PRIVATE_STOP" }
            elseif ($tree.committed_pagefile_bytes -gt $treeCommitStop) { $stopReason = "TREE_COMMIT_STOP" }
            elseif ($system.commit_headroom_bytes -lt $commitHeadroomFloor) { $stopReason = "SYSTEM_COMMIT_HEADROOM_STOP" }
            elseif ($system.available_physical_bytes -lt $availablePhysicalFloor) { $stopReason = "AVAILABLE_PHYSICAL_STOP" }
            elseif (([DateTime]::UtcNow - $startedUtc).TotalSeconds -gt $wallStopSeconds) { $stopReason = "WALL_TIME_STOP" }
            if ($stopReason) {
                $terminationRequired = $true
            }
        }
        catch {
            $process.Refresh()
            if ($process.HasExited) {
                break
            }
            else {
                $monitorError = $_.Exception.Message
                $stopReason = "MONITOR_QUERY_FAILED"
                $terminationRequired = $true
            }
        }
        if ($terminationRequired) {
            try {
                Stop-ProcessTree $process.Id $observedChildProcessIds
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
    $liveObservedAfterExit = @(
        $observedChildProcessIds | Where-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        }
    )
    if ($liveObservedAfterExit.Count -gt 0) {
        $stopReason = "ORPHANED_OBSERVED_PROCESS"
        try {
            Stop-ProcessTree $process.Id $observedChildProcessIds
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
    $finalSystem = Get-SystemSample
    $peak.system_commit_total_bytes = [math]::Max($peak.system_commit_total_bytes, $finalSystem.commit_total_bytes)
    $peak.system_commit_headroom_min_bytes = [math]::Min($peak.system_commit_headroom_min_bytes, $finalSystem.commit_headroom_bytes)
    $peak.available_physical_min_bytes = [math]::Min($peak.available_physical_min_bytes, $finalSystem.available_physical_bytes)
    $endedUtc = [DateTime]::UtcNow
    $resourceGate = (-not $stopReason) -and (-not $monitorError) -and ($successfulTreeSampleCount -ge 1)
    $resource = [ordered]@{
        schema = $schema
        program = $program
        case_id = "AV-BS1-CIRCLE-PRIMARY"
        stage = "primary-h2"
        runner_sha256 = $runnerHash
        fixture_sha256 = $token.p1_fixture_sha256
        review_token_id = $token.review_token_id
        review_token_sha256 = Get-Sha256 $reviewTokenPath
        resource_guard_policy_sha256 = $token.h2_p1_resource_guard_policy_sha256
        guard_contract_sha256 = Get-Sha256 $guardPath
        monitor_kind = "Win32_runner_plus_child_tree_Toolhelp32_Psapi_GetPerformanceInfo_100ms"
        execution_tree_root_pid = $PID
        execution_tree_includes_runner = $true
        child_process_id = $process.Id
        child_process_handle_acquired = $true
        observed_child_process_ids = @($observedChildProcessIds | Sort-Object)
        monitor_ok = (-not $monitorError)
        monitor_error = $monitorError
        stop_reason = $stopReason
        successful_tree_sample_count = $successfulTreeSampleCount
        baseline = $baseline
        peak = $peak
        thresholds = [ordered]@{
            tree_ws_stop_bytes = $treeWorkingSetStop
            tree_private_stop_bytes = $treePrivateStop
            tree_commit_stop_bytes = $treeCommitStop
            commit_headroom_floor_bytes = $commitHeadroomFloor
            available_physical_floor_bytes = $availablePhysicalFloor
        }
        started_utc = $startedUtc.ToString("o")
        ended_utc = $endedUtc.ToString("o")
        wall_seconds = ($endedUtc - $startedUtc).TotalSeconds
        wall_stop_seconds = $wallStopSeconds
        claim_relative_path = $claimRelativePath
        claim_sha256 = $claimHash
        preflight_payload_sha256 = $preflightWrapper.payload_sha256
        final_system = $finalSystem
        child_exit_code = $childExitCode
        child_stdout_sha256 = if (Test-Path $stdoutPath) { Get-Sha256 $stdoutPath } else { $null }
        child_stderr_sha256 = if (Test-Path $stderrPath) { Get-Sha256 $stderrPath } else { $null }
        mandatory_resource_gate_pass = ($resourceGate -and (($endedUtc - $startedUtc).TotalSeconds -le $wallStopSeconds) -and $peak.tree_working_set_bytes -le $treeWorkingSetStop -and $peak.tree_private_commit_bytes -le $treePrivateStop -and $peak.tree_committed_pagefile_bytes -le $treeCommitStop -and $peak.system_commit_headroom_min_bytes -ge $commitHeadroomFloor -and $peak.available_physical_min_bytes -ge $availablePhysicalFloor -and $finalSystem.commit_headroom_bytes -ge $commitHeadroomFloor -and $finalSystem.available_physical_bytes -ge $availablePhysicalFloor)
    }
    Write-Utf8NoBom $resourcePath ($resource | ConvertTo-Json -Depth 12 -Compress)
    $attemptStatus = if ($stopReason) { "resource_stop" } else { "finalizer_failure" }
    $finalArguments = @(
        $fixturePath, "--stage", "finalize-primary-h2",
        "--resource-report", $resourcePath,
        "--review-token", $reviewTokenPath,
        "--guard-contract", $guardPath,
        "--guard-nonce", $nonce
    )
    if ((Test-Path -LiteralPath $stdoutPath) -and (Get-Item -LiteralPath $stdoutPath).Length -gt 0) {
        $finalArguments += @("--child-stdout", $stdoutPath)
    }
    $finalLines = @(& $pythonPath @finalArguments)
    $finalExitCode = $LASTEXITCODE
    Write-Utf8NoBom $finalPath (($finalLines -join "`n").Trim())
    if ($finalExitCode -notin @(0, 2)) { throw "BLOCKED_AV_BS_RESULT_SCHEMA: finalizer exit code is invalid" }
    $result = Get-Content -LiteralPath $finalPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($finalExitCode -eq 0 -and $result.payload.mandatory_stage_pass -eq $true) {
        $attemptStatus = "completed_pass"
        $runnerExitCode = 0
    }
    else {
        if (-not $stopReason) { $attemptStatus = "completed_failure" }
        $runnerExitCode = 2
    }
    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $outputPath = Join-Path $validationRoot ("av-bs1-primary-h2-" + $timestamp + ".json")
    if (Test-Path -LiteralPath $outputPath) { throw "BLOCKED_AV_BS_RESULT_SCHEMA: output collision" }
    [IO.File]::WriteAllText($outputPath, (Get-Content -LiteralPath $finalPath -Raw -Encoding utf8).TrimEnd("`r", "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Output ("AV-BS1 result: " + $outputPath)
    Get-Content -LiteralPath $outputPath -Raw -Encoding utf8
}
finally {
    try {
        $liveObservedInFinally = @(
            $observedChildProcessIds | Where-Object {
                Get-Process -Id $_ -ErrorAction SilentlyContinue
            }
        )
        if ($process -and ((-not $process.HasExited) -or $liveObservedInFinally.Count -gt 0)) {
            Stop-ProcessTree $process.Id $observedChildProcessIds
        }
    }
    finally {
        try {
            if ($attemptStarted) {
                $consumeArguments = @(
                    $fixturePath, "--stage", "consume-primary-h2-token",
                    "--review-token", $reviewTokenPath,
                    "--claim-file", $claimPath,
                    "--guard-contract", $guardPath,
                    "--guard-nonce", $nonce,
                    "--attempt-status", $attemptStatus
                )
                if ((Test-Path -LiteralPath $finalPath) -and (Get-Item -LiteralPath $finalPath).Length -gt 0) {
                    $consumeArguments += @("--result-file", $finalPath)
                }
                if ((Test-Path -LiteralPath $resourcePath) -and (Get-Item -LiteralPath $resourcePath).Length -gt 0) {
                    $consumeArguments += @("--resource-report", $resourcePath)
                }
                $consumeLines = @(& $pythonPath @consumeArguments)
                if ($LASTEXITCODE -ne 0) {
                    throw "BLOCKED_AV_BS_RESULT_SCHEMA: post-attempt token consumption failed"
                }
                $consumeWrapper = (($consumeLines -join "`n").Trim()) | ConvertFrom-Json
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
                        $consumeWrapper.payload.failure_codes.Count -ne 0
                    )
                ) {
                    $runnerExitCode = 2
                }
            }
        }
        finally {
            $resolvedTemp = [IO.Path]::GetFullPath($tempDirectory)
            if (
                $resolvedTemp.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
                [IO.Path]::GetDirectoryName($resolvedTemp).TrimEnd('\') -eq $tempRoot.TrimEnd('\') -and
                [IO.Path]::GetFileName($resolvedTemp) -eq $tempLeaf -and
                (Test-Path -LiteralPath $resolvedTemp)
            ) {
                Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
            }
        }
    }
}

if ($null -eq $runnerExitCode) {
    throw "BLOCKED_AV_BS_RESULT_SCHEMA: runner completed without a terminal exit code"
}
exit $runnerExitCode
