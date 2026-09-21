[CmdletBinding()]
param(
    [switch]$SelfCheck,
    [string]$ApprovalPath,
    [string]$HqAuthorizationPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Product = 'SPD Decap PI Evaluator'
$Version = '0.23.1'
$Banner = "$Product v$Version"
$ApprovalSchema = 'd117-wp3-selected-via-source-authority-approval-v1'
$HqSchema = 'd117-wp3-selected-via-source-authority-hq-authorization-v2'
$ApprovalStatus = 'PENDING_HQ_REVIEW'
$HqStatus = 'SOL_HQ_APPROVED_SINGLE_EXECUTION'
$ExpectedCode = 'STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT'
$AuditorRelative = 'tools/research/audit_source_l29_l30_port_window.py'
$SyntheticRelative = 'tests/test_audit_source_l29_l30_port_window.py'
$ReceiptName = 'd117_wp3_selected_via_source_authority_receipt.json'
$TokenName = 'd117_wp3_selected_via_source_authority_attempt.json'
$HardWallSeconds = 1900
$DeadlineSeconds = 1800
$CaptureCap = 65536
$ChunkChars = 4096
$RequiredRepo = 'C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator'
$RequiredHead = 'e2f219e71d8c8a397009f72242cce10d78cfc7ab'
$RequiredBranch = 'main'
$RequiredReceiptSizeBytes = [int64]1321085
$RequiredReceiptSha256 = '5800f801467680af8e21f8638650e238df0394296d3aa8d3caf6084a39183371'
$RequiredWindow = [int64[]]@(-12000000000,12000000000,-11000000000,13000000000)
$RequiredPort = 'Port44_SITE0::ADC_VDD_180_VQPS_SYS_1_AON/0'
$RequiredPositiveCount = 3
$RequiredNegativeCount = 10919
$ApprovalLeaf = 'd117_wp3_selected_via_source_authority_approval.json'
$HqLeaf = 'd117_wp3_selected_via_source_authority_hq_authorization.json'
$TerminalRoot = 'D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp3-selected-via-source-authority-02'

try { $Host.UI.RawUI.WindowTitle = $Banner } catch { }

try { if ($null -eq ('D117StreamCapture' -as [type])) { Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
public sealed class D117StreamCapture {
    public long Total;
    public long Stored;
    public bool Truncated;
    public readonly StringBuilder Text = new StringBuilder();
    public async Task DrainAsync(StreamReader reader, long cap, int chunk) {
        var buffer = new char[chunk];
        int count;
        while ((count = await reader.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) > 0) {
            Total += count;
            var take = Math.Min((long)count, Math.Max(0L, cap - Stored));
            if (take > 0) { Text.Append(buffer, 0, (int)take); Stored += take; }
            if (Total > Stored) Truncated = true;
        }
    }
}
'@ } } catch { [Console]::Error.WriteLine("$Banner REFUSED: stream-capture runtime unavailable"); exit 2 }

function Fail([string]$Message) { throw [InvalidOperationException]::new($Message) }

function Ensure-SystemTextJson {
    if ($null -ne ('System.Text.Json.JsonDocument' -as [type])) { return }
    $roots = @($env:ProgramFiles, $env:ProgramW6432) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
    foreach ($base in $roots) {
        $sdkRoot = Join-Path $base 'dotnet\sdk'
        foreach ($sdk in @(Get-ChildItem -LiteralPath $sdkRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            $candidate = Join-Path $sdk.FullName 'Sdks\Microsoft.NET.Sdk\tools\net472\System.Text.Json.dll'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $assemblyDir = Split-Path -Parent $candidate; $unsafePath = Join-Path $assemblyDir 'System.Runtime.CompilerServices.Unsafe.dll'; $unsafeLoaded = $null; if (Test-Path -LiteralPath $unsafePath -PathType Leaf) { try { $unsafeLoaded = [Reflection.Assembly]::LoadFrom($unsafePath) } catch { } }; $resolverScript = { param($sender,$event); if ($event.Name -like 'System.Runtime.CompilerServices.Unsafe*' -and $null -ne $unsafeLoaded) { return $unsafeLoaded } }.GetNewClosure(); $resolver = [ResolveEventHandler]$resolverScript
                [AppDomain]::CurrentDomain.add_AssemblyResolve($resolver)
                $ready = $false
                try { foreach ($dependency in @('System.Runtime.CompilerServices.Unsafe.dll','System.Memory.dll','System.Buffers.dll','System.Text.Encodings.Web.dll','Microsoft.Bcl.AsyncInterfaces.dll','System.Threading.Tasks.Extensions.dll','System.ValueTuple.dll','System.Numerics.Vectors.dll')) { $dependencyPath = Join-Path $assemblyDir $dependency; if (Test-Path -LiteralPath $dependencyPath -PathType Leaf) { [Reflection.Assembly]::LoadFrom($dependencyPath) | Out-Null } }; [Reflection.Assembly]::LoadFrom($candidate) | Out-Null; if ($null -ne ('System.Text.Json.JsonDocument' -as [type])) { $probe = [Text.Json.JsonDocument]::Parse('{"probe":true}'); $probe.Dispose(); $probeOptions = [Text.Json.JsonSerializerOptions]::new(); [Text.Json.JsonSerializer]::Serialize([ordered]@{ probe = $true }, $probeOptions) | Out-Null; $ready = $true } } catch { }
                [AppDomain]::CurrentDomain.remove_AssemblyResolve($resolver)
                if ($ready) { return }
            }
        }
    }
    [Console]::Error.WriteLine("$Banner REFUSED: System.Text.Json runtime unavailable"); exit 2
}

Ensure-SystemTextJson

function Resolve-Absolute([string]$Path) {
    try {
        if ([string]::IsNullOrWhiteSpace($Path)) { Fail "absolute path required: $Path" }
        $root = [IO.Path]::GetPathRoot($Path)
        if ([string]::IsNullOrEmpty($root) -or $root -eq '\' -or ($root.Length -eq 2 -and $root[1] -eq ':')) { Fail "absolute path required: $Path" }
        $full = [IO.Path]::GetFullPath($Path); $fullRoot = [IO.Path]::GetPathRoot($full); if ([string]::IsNullOrEmpty($fullRoot) -or $fullRoot -eq '\' -or ($fullRoot.Length -eq 2 -and $fullRoot[1] -eq ':')) { Fail "absolute path required: $Path" }
        if ($full.Length -gt $fullRoot.Length) { $full = $full.TrimEnd('\','/') }; return $full
    } catch { if ($_.Exception -is [InvalidOperationException]) { throw }; Fail "invalid path: $Path" }
}

function Assert-NoReparse([string]$Path, [bool]$RequireFinal = $true) {
    $full = Resolve-Absolute $Path
    $root = [IO.Path]::GetPathRoot($full)
    $current = $root
    $tail = $full.Substring($root.Length).Trim('\','/')
    foreach ($part in @($tail -split '[\\/]' | Where-Object { $_ })) {
        $current = [IO.Path]::Combine($current, $part)
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail "reparse point rejected: $full" }
        } elseif ($RequireFinal -and ([IO.Path]::GetFullPath($current) -ieq $full)) {
            Fail "missing path: $full"
        }
    }
    if ($RequireFinal -and -not (Test-Path -LiteralPath $full)) { Fail "missing path: $full" }
    return $full
}

function Assert-NotTerminalRoot([string]$Path) {
    $full = (Resolve-Absolute $Path).TrimEnd('\'); $terminal = $TerminalRoot.TrimEnd('\')
    if ($full -ieq $terminal -or $full.StartsWith($terminal + '\', [StringComparison]::OrdinalIgnoreCase)) { Fail 'known terminal -02 root is forbidden' }
}

function Get-FileIdentity([string]$Path) {
    $full = Assert-NoReparse $Path $true
    $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer) { Fail "ordinary file required: $full" }
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $sha = [Security.Cryptography.SHA256]::Create()
    $buffer = [byte[]]::new(1048576)
    [int64]$count = 0
    try {
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) { [void]$sha.TransformBlock($buffer, 0, $read, $null, 0); $count += $read }
        [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
        return [pscustomobject]@{ Path = $full; SizeBytes = $count; Sha256 = ([BitConverter]::ToString($sha.Hash) -replace '-', '').ToLowerInvariant() }
    } finally { $sha.Dispose(); $stream.Dispose() }
}

function Get-Directory([string]$Path) {
    $full = Assert-NoReparse $Path $true
    $item = Get-Item -LiteralPath $full -Force
    if (-not $item.PSIsContainer) { Fail "directory required: $full" }
    return $full.TrimEnd('\')
}

function Read-FileBytes([string]$Path) {
    $full = Assert-NoReparse $Path $true
    return [IO.File]::ReadAllBytes($full)
}

function Read-BoundJsonFile([string]$Path, [string]$Label) {
    $full = Assert-NoReparse $Path $true; $item = Get-Item -LiteralPath $full -Force; if ($item.PSIsContainer) { Fail "$Label ordinary bounded file required" }
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read); if ($stream.Length -gt [int32]::MaxValue) { $stream.Dispose(); Fail "$Label ordinary bounded file required" }; $bytes = [byte[]]::new([int]$stream.Length); $offset = 0
    try { while ($offset -lt $bytes.Length) { $read = $stream.Read($bytes, $offset, $bytes.Length - $offset); if ($read -le 0) { Fail "$Label changed while reading" }; $offset += $read }; if ($stream.Position -ne $stream.Length) { Fail "$Label changed while reading" } } finally { $stream.Dispose() }
    $identity = [pscustomobject]@{ Path = $full; SizeBytes = [int64]$bytes.Length; Sha256 = ([BitConverter]::ToString((Get-Sha256Bytes $bytes)) -replace '-', '').ToLowerInvariant() }
    return [pscustomobject]@{ Bytes = $bytes; Identity = $identity; Parsed = Read-StrictJsonBytes $bytes $Label }
}

function Convert-JsonElement($Element) {
    switch ($Element.ValueKind) {
        Object {
            $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            $result = [Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal)
            foreach ($property in $Element.EnumerateObject()) {
                if (-not $seen.Add($property.Name)) { Fail "duplicate JSON key: $($property.Name)" }
                $result[$property.Name] = Convert-JsonElement $property.Value
            }
            return $result
        }
        Array {
            $result = [Collections.Generic.List[object]]::new()
            foreach ($item in $Element.EnumerateArray()) { [void]$result.Add((Convert-JsonElement $item)) }
            return ,([object[]]$result.ToArray())
        }
        String { return $Element.GetString() }
        True { return $true }
        False { return $false }
        Null { return $null }
        Number {
            $raw = $Element.GetRawText()
            [long]$integer = 0
            if ([long]::TryParse($raw, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$integer)) { return $integer }
            [decimal]$decimal = 0
            if ([decimal]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$decimal)) { return $decimal }
            Fail "invalid JSON number"
        }
        default { Fail "unsupported JSON value kind: $($Element.ValueKind)" }
    }
}

function Read-StrictJsonBytes([byte[]]$Bytes, [string]$Label) {
    if ($null -eq $Bytes -or $Bytes.Length -eq 0) { Fail "empty $Label JSON" }
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xef -and $Bytes[1] -eq 0xbb -and $Bytes[2] -eq 0xbf) { Fail "BOM rejected in $Label JSON" }
    $encoding = [Text.UTF8Encoding]::new($false, $true)
    try { $text = $encoding.GetString($Bytes) } catch { Fail "invalid UTF-8 in $Label JSON" }
    $options = [Text.Json.JsonDocumentOptions]::new()
    $options.CommentHandling = [Text.Json.JsonCommentHandling]::Disallow
    $options.AllowTrailingCommas = $false
    try { $document = [Text.Json.JsonDocument]::Parse($text, $options) } catch { Fail "invalid $Label JSON" }
    try {
        if ($document.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) { Fail "$Label JSON root must be an object" }
        return Convert-JsonElement $document.RootElement
    } finally { $document.Dispose() }
}

function Read-StrictJsonFile([string]$Path, [string]$Label) { return Read-StrictJsonBytes (Read-FileBytes $Path) $Label }

function Assert-ExactKeys($Object, [string[]]$Keys, [string]$Label) {
    Assert-Object $Object $Label
    $actual = @($Object.Keys | ForEach-Object { [string]$_ })
    $wanted = @($Keys | ForEach-Object { [string]$_ })
    $actualSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $wantedSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($key in $actual) { if (-not $actualSet.Add($key)) { Fail "$Label duplicate key" } }
    foreach ($key in $wanted) { if (-not $wantedSet.Add($key)) { Fail "$Label expected-key definition duplicated" } }
    if ($actual.Count -ne $wanted.Count -or $actualSet.Count -ne $wantedSet.Count) { Fail "$Label keys failed" }
    foreach ($key in $actual) { if (-not $wantedSet.Contains($key)) { Fail "$Label unexpected key: $key" } }
    foreach ($key in $wanted) { if (-not $actualSet.Contains($key)) { Fail "$Label missing key: $key" } }
}

function Assert-String($Value, [string]$Label) { if ($Value -isnot [string] -or [string]::IsNullOrEmpty($Value)) { Fail "$Label must be a non-empty string" } }
function Assert-Boolean($Value, [string]$Label) { if ($Value -isnot [bool]) { Fail "$Label must be Boolean" } }
function Assert-Int($Value, [string]$Label, [int64]$Minimum = [int64]::MinValue) { if ($Value -isnot [int64] -and $Value -isnot [int32]) { Fail "$Label must be Int64" }; if ([int64]$Value -lt $Minimum) { Fail "$Label is out of range" } }
function Assert-Number($Value, [string]$Label) { if ($Value -isnot [int64] -and $Value -isnot [int32] -and $Value -isnot [decimal]) { Fail "$Label must be numeric" } }
function Assert-DecimalExact($Value, [decimal]$Expected, [string]$Label) { if ($Value -isnot [decimal] -or $Value -ne $Expected) { Fail "$Label mismatch" } }
function Assert-NullableString($Value, [string]$Label) { if ($null -ne $Value) { Assert-String $Value $Label } }
function Assert-NullableInt($Value, [string]$Label) { if ($null -ne $Value) { Assert-Int $Value $Label } }
function Assert-NullableNumber($Value, [string]$Label) { if ($null -ne $Value) { Assert-Number $Value $Label } }
function Assert-NumberArray($Value, [string]$Label, [int]$Count = -1) { Assert-Array $Value $Label; if ($Count -ge 0 -and $Value.Count -ne $Count) { Fail "$Label count mismatch" }; foreach ($item in $Value) { Assert-Number $item "$Label item" } }
function Assert-Object($Value, [string]$Label) { if ($null -eq $Value -or $Value -isnot [Collections.IDictionary]) { Fail "$Label must be an object" } }
function Assert-Array($Value, [string]$Label) { if ($null -eq $Value -or $Value -isnot [array]) { Fail "$Label must be an array" } }
function Assert-StringArray($Value, [string]$Label) { Assert-Array $Value $Label; foreach ($item in $Value) { Assert-String $item "$Label item" } }
function Assert-IntArray($Value, [string]$Label, [int64]$Minimum = [int64]::MinValue, [int]$Count = -1) { Assert-Array $Value $Label; if ($Count -ge 0 -and $Value.Count -ne $Count) { Fail "$Label count mismatch" }; foreach ($item in $Value) { Assert-Int $item "$Label item" $Minimum } }
function Assert-ExactBoolean($Value, [bool]$Expected, [string]$Label) { Assert-Boolean $Value $Label; if (-not [bool]::Equals($Value, $Expected)) { Fail "$Label mismatch" } }
function Assert-ExactInt($Value, [int64]$Expected, [string]$Label) { Assert-Int $Value $Label; if ([int64]$Value -ne $Expected) { Fail "$Label mismatch" } }
function Assert-ExactString($Value, [string]$Expected, [string]$Label) { Assert-String $Value $Label; if (-not [StringComparer]::Ordinal.Equals([string]$Value, $Expected)) { Fail "$Label mismatch" } }
function Assert-Sha([string]$Value, [string]$Label) { if ($Value -notmatch '^[0-9a-fA-F]{64}$') { Fail "$Label must be SHA-256" } }
function Assert-OrdinalContainsAll($Values, [string[]]$Required, [string]$Label) {
    $set = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($value in @($Values)) { if ($value -isnot [string] -or -not $set.Add([string]$value)) { Fail "$Label must contain unique strings" } }
    foreach ($name in $Required) { if (-not $set.Contains($name)) { Fail "$Label missing required value: $name" } }
}
function Assert-OrdinalExact($Values, [string[]]$Expected, [string]$Label) {
    $set = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($value in @($Values)) { if ($value -isnot [string] -or -not $set.Add([string]$value)) { Fail "$Label must contain unique strings" } }
    $expectedSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($name in $Expected) { if ($name -isnot [string] -or -not $expectedSet.Add([string]$name)) { Fail "$Label expected values must be unique strings" } }
    if ($set.Count -ne $expectedSet.Count) { Fail "$Label count mismatch" }
    foreach ($name in $expectedSet) { if (-not $set.Contains($name)) { Fail "$Label missing value: $name" } }
}

function Get-RecordIdentity($Record, [string]$Label) {
    Assert-ExactKeys $Record @('path','size_bytes','sha256') $Label
    Assert-String $Record.path "$Label.path"; Assert-Int $Record.size_bytes "$Label.size_bytes" 0; Assert-Sha $Record.sha256 "$Label.sha256"
    $actual = Get-FileIdentity (Resolve-Absolute $Record.path)
    if ($actual.Path -cne (Resolve-Absolute $Record.path) -or $actual.SizeBytes -ne [int64]$Record.size_bytes -or $actual.Sha256 -ine $Record.sha256) { Fail "$Label identity mismatch" }
    return $actual
}

function Assert-PathRecord($Record, [string]$Label) {
    Assert-ExactKeys $Record @('path') $Label; Assert-String $Record.path "$Label.path"; return (Get-Directory $Record.path)
}

function Invoke-GitIdentity([string]$Repo, [string]$ExpectedHead) {
    $root = ((& git -C $Repo rev-parse --show-toplevel 2>$null) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) { Fail 'git root lookup failed' }
    $root = Resolve-Absolute $root
    if ($root -cne (Resolve-Absolute $Repo)) { Fail 'git root mismatch' }
    $head = ((& git -C $Repo rev-parse HEAD 2>$null) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -notmatch '^[0-9a-fA-F]{40}$' -or $head -ine $ExpectedHead) { Fail 'git HEAD mismatch' }
    $branch = ((& git -C $Repo rev-parse --abbrev-ref HEAD 2>$null) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -cne $RequiredBranch) { Fail 'git branch mismatch' }
    return [pscustomobject]@{ Root = $root; Head = $head.ToLowerInvariant(); Branch = $branch }
}

function Assert-RootInventory([string]$Root, [string]$Approval, [string]$Hq, [bool]$PostRun) {
    $items = @(Get-ChildItem -LiteralPath $Root -Force)
    foreach ($item in $items) { [void](Assert-NoReparse $item.FullName $true) }
    $allowed = @((Resolve-Absolute $Approval), (Resolve-Absolute $Hq))
    if ($PostRun) { $allowed += Join-Path $Root $TokenName; $allowed += Join-Path $Root $ReceiptName }
    $actual = @($items.FullName | ForEach-Object { Resolve-Absolute $_ })
    if ($actual.Count -ne $allowed.Count -or @($actual | Where-Object { $_ -cnotin $allowed }).Count -ne 0 -or @($allowed | Where-Object { $_ -cnotin $actual }).Count -ne 0) { Fail 'artifact-root inventory mismatch' }
}

function New-ExpectedArgv($Approval, [string]$Root, [string]$Repo, $InputIds, $Python, [string]$ReceiptPath) {
    $window = @($Approval.parsed_parameters.window_pm | ForEach-Object { [Convert]::ToString($_, [Globalization.CultureInfo]::InvariantCulture) })
    return [string[]]@(
        $Python.Path, '-B', $AuditorRelative,
        '--source', $InputIds.source.Path, '--d103', $InputIds.d103.Path, '--d104', $InputIds.d104_receipt.Path,
        '--d104-root', $InputIds.d104_root, '--d115b', $InputIds.d115b.Path, '--candidate', $InputIds.candidate.Path,
        '--output', $ReceiptPath, '--repo-root', $Repo,
        '--expected-source-sha256', $InputIds.source.Sha256, '--expected-d103-sha256', $InputIds.d103.Sha256,
        '--expected-d104-sha256', $InputIds.d104_receipt.Sha256, '--expected-d115b-sha256', $InputIds.d115b.Sha256,
        '--expected-candidate-sha256', $InputIds.candidate.Sha256, '--expected-head', $Approval.git.head,
        '--window-pm', $window[0], $window[1], $window[2], $window[3], '--port-id', $Approval.parsed_parameters.port_id,
        '--expected-positive-count', ([Convert]::ToString($Approval.parsed_parameters.positive_count, [Globalization.CultureInfo]::InvariantCulture)),
        '--expected-negative-count', ([Convert]::ToString($Approval.parsed_parameters.negative_count, [Globalization.CultureInfo]::InvariantCulture)),
        '--deadline-seconds', ([Convert]::ToString($Approval.parsed_parameters.deadline_seconds, [Globalization.CultureInfo]::InvariantCulture))
    )
}

function Start-DrainTask($Reader, $State, [int64]$Cap) {
    return $State.DrainAsync($Reader, $Cap, $ChunkChars)
}

function New-ProcessRunState {
    return [pscustomobject]@{ Stdout = [D117StreamCapture]::new(); Stderr = [D117StreamCapture]::new(); Stopwatch = [Diagnostics.Stopwatch]::new(); TimeoutSignalElapsedSeconds = $null }
}

function Wait-DrainTask($Task) {
    if ($null -ne $Task) { if (-not $Task.Wait(5000)) { Fail 'stream drain did not terminate' } }
}

function ConvertTo-WindowsCommandLineArgument([string]$Argument) {
    if ($null -eq $Argument) { $Argument = '' }
    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') { return $Argument }
    $builder = [Text.StringBuilder]::new(); [void]$builder.Append('"'); $slashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq [char]0x5c) { $slashes++; continue }
        if ($character -eq [char]0x22) { for ($i = 0; $i -lt (2 * $slashes + 1); $i++) { [void]$builder.Append([char]0x5c) }; [void]$builder.Append([char]0x22); $slashes = 0; continue }
        for ($i = 0; $i -lt $slashes; $i++) { [void]$builder.Append([char]0x5c) }; $slashes = 0; [void]$builder.Append($character)
    }
    for ($i = 0; $i -lt (2 * $slashes); $i++) { [void]$builder.Append([char]0x5c) }; [void]$builder.Append('"'); return $builder.ToString()
}

function Start-ExitWaitTask($Process) {
    $method = $Process.GetType().GetMethod('WaitForExitAsync', [Type[]]@())
    if ($null -ne $method) { return $method.Invoke($Process, $null) }
    return $null
}

function Stop-ProcessTree($Process) {
    try { if ($Process.HasExited) { return $true } } catch { }
    $treeMethod = $Process.GetType().GetMethod('Kill', [Type[]]@([bool]))
    if ($null -ne $treeMethod) {
        try { $treeMethod.Invoke($Process, [object[]]@($true)) | Out-Null } catch { Fail 'whole-tree Kill(true) failed' }
        try { if (-not $Process.WaitForExit(5000) -or -not $Process.HasExited) { Fail 'whole-tree Kill(true) did not terminate child' } } catch { throw }
        return $true
    }
    $taskkill = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::System)) 'taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkill -PathType Leaf)) { Fail 'taskkill.exe unavailable for bounded tree cleanup' }
    $killInfo = [Diagnostics.ProcessStartInfo]::new(); $killInfo.FileName = $taskkill; $killInfo.Arguments = "/PID $($Process.Id) /T /F"; $killInfo.UseShellExecute = $false; $killInfo.CreateNoWindow = $true; $killInfo.RedirectStandardOutput = $true; $killInfo.RedirectStandardError = $true
    $killer = [Diagnostics.Process]::new(); $killer.StartInfo = $killInfo
    try {
        if (-not $killer.Start()) { Fail 'taskkill start failed' }
        $killer.BeginOutputReadLine(); $killer.BeginErrorReadLine()
        if (-not $killer.WaitForExit(5000)) { Fail 'taskkill did not complete within bounded cleanup interval' }
        if ($killer.ExitCode -ne 0) { Fail "taskkill failed with exit code $($killer.ExitCode)" }
    } finally { $killer.Dispose() }
    if (-not $Process.WaitForExit(5000) -or -not $Process.HasExited) { Fail 'taskkill tree cleanup did not terminate child' }
    return $true
}

function New-ConfiguredProcess {
    param([string[]]$Argv, [string]$WorkingDirectory, [bool]$Require44 = $false)
    if ($Require44 -and $Argv.Count -ne 44) { Fail "expected exactly 44 argv tokens, got $($Argv.Count)" }
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $Argv[0]
    if ($null -ne $psi.GetType().GetProperty('ArgumentList')) { if ($Argv.Count -gt 1) { foreach ($arg in $Argv[1..($Argv.Count - 1)]) { [void]$psi.ArgumentList.Add([string]$arg) } } } elseif ($Argv.Count -gt 1) { $psi.Arguments = (($Argv[1..($Argv.Count - 1)] | ForEach-Object { ConvertTo-WindowsCommandLineArgument ([string]$_) }) -join ' ') }
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true; $psi.WorkingDirectory = $WorkingDirectory
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    if ($null -ne $psi.GetType().GetProperty('Environment')) { $psi.Environment['PYTHONDONTWRITEBYTECODE'] = '1' } else { $psi.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1' }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $psi
    return $process
}

function Invoke-BoundedProcess($Process, $State, [double]$HardWallSeconds, [int64]$Cap) {
    [void]$State.Stopwatch.Restart()
    $started = $false; $outTask = $null; $errTask = $null; $waitTask = $null; $timeoutTask = $null; $timedOut = $false
    try {
        if (-not $Process.Start()) { Fail 'direct child start failed' }
        $started = $true
        $outTask = Start-DrainTask $Process.StandardOutput $State.Stdout $Cap
        $errTask = Start-DrainTask $Process.StandardError $State.Stderr $Cap
        $waitTask = Start-ExitWaitTask $Process
        $remainingMs = [int][Math]::Ceiling(($HardWallSeconds * 1000.0) - $State.Stopwatch.Elapsed.TotalMilliseconds)
        if ($null -eq $waitTask) {
            if ($remainingMs -le 0 -or -not $Process.WaitForExit($remainingMs)) { $timedOut = $true; $State.TimeoutSignalElapsedSeconds = $State.Stopwatch.Elapsed.TotalSeconds; [void](Stop-ProcessTree $Process); if (-not $Process.WaitForExit(5000)) { Fail 'child did not exit after bounded timeout kill' } }
        } elseif ($remainingMs -le 0) { $timedOut = $true; $State.TimeoutSignalElapsedSeconds = $State.Stopwatch.Elapsed.TotalSeconds; [void](Stop-ProcessTree $Process); if (-not $waitTask.IsCompleted -and -not $waitTask.Wait(5000)) { Fail 'child did not exit after bounded timeout kill' } }
        else {
            $timeoutTask = [Threading.Tasks.Task]::Delay($remainingMs)
            $winner = [Threading.Tasks.Task]::WhenAny([Threading.Tasks.Task[]]@($waitTask, $timeoutTask)).Result
            $timedOut = $winner -eq $timeoutTask -and -not $waitTask.IsCompleted
            if ($timedOut) { $State.TimeoutSignalElapsedSeconds = $State.Stopwatch.Elapsed.TotalSeconds; [void](Stop-ProcessTree $Process); if (-not $waitTask.IsCompleted -and -not $waitTask.Wait(5000)) { Fail 'child did not exit after bounded timeout kill' } }
            elseif (-not $waitTask.IsCompleted -and -not $waitTask.Wait(5000)) { Fail 'child wait did not complete' }
        }
        if (-not $Process.HasExited) { Fail 'child remained alive after bounded wait' }
        Wait-DrainTask $outTask; Wait-DrainTask $errTask
        return [pscustomobject]@{ ExitCode = $Process.ExitCode; TimedOut = $timedOut; ElapsedSeconds = $State.Stopwatch.Elapsed.TotalSeconds; TimeoutSignalElapsedSeconds = $State.TimeoutSignalElapsedSeconds; Stdout = $State.Stdout; Stderr = $State.Stderr }
    } catch {
        if ($started) {
            [void](Stop-ProcessTree $Process)
            $terminated = $false
            try { if ($null -eq $waitTask) { $terminated = $Process.WaitForExit(5000) } else { $terminated = $waitTask.IsCompleted -or $waitTask.Wait(5000) }; $terminated = $terminated -and $Process.HasExited } catch { }
            try { if ($null -eq $outTask) { $outTask = Start-DrainTask $Process.StandardOutput $State.Stdout $Cap }; Wait-DrainTask $outTask } catch { }
            try { if ($null -eq $errTask) { $errTask = Start-DrainTask $Process.StandardError $State.Stderr $Cap }; Wait-DrainTask $errTask } catch { }
            if (-not $terminated) { throw [InvalidOperationException]::new('child did not terminate after bounded cleanup') }
        }
        throw
    } finally { [void]$State.Stopwatch.Stop() }
}

function Get-CompactJsonBytes($Object) {
    $options = [Text.Json.JsonSerializerOptions]::new(); $options.WriteIndented = $false
    $json = [Text.Json.JsonSerializer]::Serialize($Object, $options)
    return [Text.UTF8Encoding]::new($false, $true).GetBytes($json)
}

function Write-CreateNewBytes([string]$Path, [byte[]]$Bytes) {
    $full = Resolve-Absolute $Path
    $stream = [IO.File]::Open($full, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Compare-Argv([string[]]$Actual, [string[]]$Expected, [string]$Label) {
    if ($Actual.Count -ne $Expected.Count) { Fail "$Label count mismatch" }
    for ($i = 0; $i -lt $Expected.Count; $i++) { if (-not [StringComparer]::Ordinal.Equals($Actual[$i], $Expected[$i])) { Fail "$Label token $i mismatch" } }
}

function Get-FileVersion([string]$Path) {
    $version = [string](Get-Item -LiteralPath (Resolve-Absolute $Path) -Force).VersionInfo.FileVersion
    if ([string]::IsNullOrWhiteSpace($version)) { Fail "file version unavailable: $Path" }
    return $version.Trim()
}

function Get-Sha256Bytes([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return $sha.ComputeHash($Bytes) } finally { $sha.Dispose() }
}

function Assert-SameIdentity($Actual, $Expected, [string]$Label) {
    if ($null -eq $Actual -or $null -eq $Expected -or $Actual.Path -cne $Expected.Path -or [int64]$Actual.SizeBytes -ne [int64]$Expected.SizeBytes -or $Actual.Sha256 -ine $Expected.Sha256) { Fail "$Label identity changed" }
}

function Assert-Approval($Approval, [string]$ApprovalFull, [string]$Root, [string]$Repo, [string]$Cwd, $BoundIdentity = $null) {
    Assert-ExactKeys $Approval @('schema','product','authorization','git','accepted_implementation','synthetic_only_test_evidence','exact_production_inputs','python_binding','argv','parsed_parameters','output_receipt_path','expected_scientific_result','execution_policy','prior_minus_01_resource_observation','prohibitions') 'approval'
    Assert-ExactString $Approval.schema $ApprovalSchema 'approval.schema'
    if ((Resolve-Absolute $Repo) -cne $RequiredRepo) { Fail 'purpose-specific repository path mismatch' }
    Assert-ExactKeys $Approval.product @('name','version') 'approval.product'; Assert-ExactString $Approval.product.name $Product 'approval.product.name'; Assert-ExactString $Approval.product.version $Version 'approval.product.version'
    Assert-ExactKeys $Approval.authorization @('authorization_id','status','execution_authorized') 'approval.authorization'; Assert-String $Approval.authorization.authorization_id 'approval.authorization_id'; Assert-ExactString $Approval.authorization.status $ApprovalStatus 'approval.authorization.status'; Assert-ExactBoolean $Approval.authorization.execution_authorized $false 'approval.authorization.execution_authorized'
    Assert-ExactKeys $Approval.git @('repo_root','head','branch') 'approval.git'; Assert-String $Approval.git.repo_root 'approval.git.repo_root'; Assert-ExactString $Approval.git.head $RequiredHead 'approval.git.head'; Assert-ExactString $Approval.git.branch $RequiredBranch 'approval.git.branch'; if ((Resolve-Absolute $Approval.git.repo_root) -cne $RequiredRepo) { Fail 'approval git binding mismatch' }
    if ((Resolve-Absolute $Cwd) -cne $Repo) { Fail 'current invocation cwd must be repository root' }
    $implementation = Get-RecordIdentity $Approval.accepted_implementation 'approval.accepted_implementation'; if ($implementation.Path -cne (Resolve-Absolute (Join-Path $Repo $AuditorRelative))) { Fail 'accepted auditor path mismatch' }
    Assert-ExactKeys $Approval.synthetic_only_test_evidence @('path','size_bytes','sha256','passed','production_validation') 'approval.synthetic_only_test_evidence'; Assert-ExactInt $Approval.synthetic_only_test_evidence.passed 9 'synthetic_only_test_evidence.passed'; Assert-ExactBoolean $Approval.synthetic_only_test_evidence.production_validation $false 'synthetic_only_test_evidence.production_validation'; $synthetic = Get-RecordIdentity ([ordered]@{path=$Approval.synthetic_only_test_evidence.path;size_bytes=$Approval.synthetic_only_test_evidence.size_bytes;sha256=$Approval.synthetic_only_test_evidence.sha256}) 'approval.synthetic_only_test_evidence'; if ($synthetic.Path -cne (Resolve-Absolute (Join-Path $Repo $SyntheticRelative))) { Fail 'synthetic test path mismatch' }
    Assert-ExactKeys $Approval.exact_production_inputs @('source','d103','d104_receipt','d104_root','d115b','candidate') 'approval.exact_production_inputs'
    $inputs = [ordered]@{}; foreach ($name in @('source','d103','d104_receipt','d115b','candidate')) { $inputs[$name] = Get-RecordIdentity $Approval.exact_production_inputs.$name "approval.exact_production_inputs.$name" }
    Assert-String $Approval.exact_production_inputs.d104_root 'approval.exact_production_inputs.d104_root'; $d104Root = Get-Directory $Approval.exact_production_inputs.d104_root; if ($d104Root -cne (Split-Path -Parent $inputs.d104_receipt.Path).TrimEnd('\')) { Fail 'D104 root mismatch' }; $inputs['d104_root'] = $d104Root
    Assert-ExactKeys $Approval.python_binding @('launcher_token','resolved_executable','file_version','size_bytes','sha256','working_directory','environment','argv_includes') 'approval.python_binding'; Assert-ExactString $Approval.python_binding.launcher_token 'python' 'python_binding.launcher_token'; Assert-String $Approval.python_binding.resolved_executable 'python_binding.resolved_executable'; Assert-String $Approval.python_binding.file_version 'python_binding.file_version'; Assert-Int $Approval.python_binding.size_bytes 'python_binding.size_bytes' 0; Assert-Sha $Approval.python_binding.sha256 'python_binding.sha256'; Assert-String $Approval.python_binding.working_directory 'python_binding.working_directory'; if ((Resolve-Absolute $Approval.python_binding.working_directory) -cne $Repo) { Fail 'python binding working-directory mismatch' }; Assert-StringArray $Approval.python_binding.argv_includes 'python_binding.argv_includes'; Assert-OrdinalContainsAll $Approval.python_binding.argv_includes @('-B') 'python_binding.argv_includes'; $python = Get-FileIdentity $Approval.python_binding.resolved_executable; if ($python.Path -cne (Resolve-Absolute $Approval.python_binding.resolved_executable) -or $python.SizeBytes -ne [int64]$Approval.python_binding.size_bytes -or $python.Sha256 -ine $Approval.python_binding.sha256 -or (Get-FileVersion $python.Path) -cne $Approval.python_binding.file_version.Trim()) { Fail 'python executable identity/version mismatch' }; Assert-ExactKeys $Approval.python_binding.environment @('PYTHONDONTWRITEBYTECODE') 'python_binding.environment'; Assert-ExactString $Approval.python_binding.environment.PYTHONDONTWRITEBYTECODE '1' 'python_binding.environment.PYTHONDONTWRITEBYTECODE'
    Assert-ExactKeys $Approval.parsed_parameters @('window_pm','port_id','positive_count','negative_count','deadline_seconds') 'approval.parsed_parameters'; Assert-IntArray $Approval.parsed_parameters.window_pm 'approval.parsed_parameters.window_pm'; if ($Approval.parsed_parameters.window_pm.Count -ne 4) { Fail 'window must have four values' }; for ($i = 0; $i -lt 4; $i++) { if ([int64]$Approval.parsed_parameters.window_pm[$i] -ne $RequiredWindow[$i]) { Fail "window_pm[$i] mismatch" } }; Assert-ExactString $Approval.parsed_parameters.port_id $RequiredPort 'approval.parsed_parameters.port_id'; Assert-ExactInt $Approval.parsed_parameters.positive_count $RequiredPositiveCount 'approval.parsed_parameters.positive_count'; Assert-ExactInt $Approval.parsed_parameters.negative_count $RequiredNegativeCount 'approval.parsed_parameters.negative_count'; Assert-ExactInt $Approval.parsed_parameters.deadline_seconds $DeadlineSeconds 'approval.parsed_parameters.deadline_seconds'
    Assert-String $Approval.output_receipt_path 'approval.output_receipt_path'; $outputPath = Resolve-Absolute $Approval.output_receipt_path; if ((Split-Path -Parent $outputPath) -cne $Root -or (Split-Path -Leaf $outputPath) -cne $ReceiptName) { Fail 'approval receipt path mismatch' }
    Assert-ExactKeys $Approval.expected_scientific_result @('code','exit_code','strict_receipt_required','unexpected_result_or_absent_receipt','receipt_size_bytes_exact','receipt_sha256_exact') 'approval.expected_scientific_result'; Assert-ExactString $Approval.expected_scientific_result.code $ExpectedCode 'expected_scientific_result.code'; Assert-ExactInt $Approval.expected_scientific_result.exit_code 1 'expected_scientific_result.exit_code'; Assert-ExactBoolean $Approval.expected_scientific_result.strict_receipt_required $true 'expected_scientific_result.strict_receipt_required'; Assert-ExactString $Approval.expected_scientific_result.unexpected_result_or_absent_receipt 'STOP' 'expected_scientific_result.unexpected_result_or_absent_receipt'; Assert-ExactInt $Approval.expected_scientific_result.receipt_size_bytes_exact $RequiredReceiptSizeBytes 'approval expected receipt size'; Assert-ExactString $Approval.expected_scientific_result.receipt_sha256_exact $RequiredReceiptSha256 'approval expected receipt sha'
    Assert-ExactKeys $Approval.execution_policy @('python_child_processes','max_attempts','retry_allowed','forbidden_engines','external_wall_policy_min_seconds') 'approval.execution_policy'; Assert-ExactInt $Approval.execution_policy.python_child_processes 1 'execution_policy.python_child_processes'; Assert-ExactInt $Approval.execution_policy.max_attempts 1 'execution_policy.max_attempts'; Assert-ExactBoolean $Approval.execution_policy.retry_allowed $false 'execution_policy.retry_allowed'; Assert-ExactInt $Approval.execution_policy.external_wall_policy_min_seconds $HardWallSeconds 'execution_policy.external_wall_policy_min_seconds'; Assert-StringArray $Approval.execution_policy.forbidden_engines 'approval.execution_policy.forbidden_engines'; Assert-OrdinalExact $Approval.execution_policy.forbidden_engines @('solver','Triangle','FasterCap','PowerSI') 'approval.execution_policy.forbidden_engines'
    Assert-ExactKeys $Approval.prior_minus_01_resource_observation @('elapsed_seconds','peak_working_set_kib','peak_pagefile_usage_kib','classification') 'approval.prior_minus_01_resource_observation'; Assert-DecimalExact $Approval.prior_minus_01_resource_observation.elapsed_seconds ([decimal]::Parse('941.787198',[Globalization.CultureInfo]::InvariantCulture)) 'prior_minus_01_resource_observation.elapsed_seconds'; Assert-ExactInt $Approval.prior_minus_01_resource_observation.peak_working_set_kib 8677024 'prior_minus_01_resource_observation.peak_working_set_kib'; Assert-ExactInt $Approval.prior_minus_01_resource_observation.peak_pagefile_usage_kib 10122920 'prior_minus_01_resource_observation.peak_pagefile_usage_kib'; Assert-ExactString $Approval.prior_minus_01_resource_observation.classification 'observation_only_not_a_proven_cap' 'prior_minus_01_resource_observation.classification'
    Assert-Array $Approval.prohibitions 'approval.prohibitions'; Assert-StringArray $Approval.prohibitions 'approval.prohibitions'; Assert-OrdinalExact $Approval.prohibitions @('do_not_execute_python_or_the_production_auditor_helper_test_solver_triangle_fastercap_or_powersi','preserve_prior_minus_01_root_exactly_without_read_or_write','do_not_inspect_touch_or_list_accuracy_parse.py','do_not_perform_broad_git_status_or_untracked_enumeration','no_checkout_edits_commit_stage_or_publish','no_second_file_temp_file_retry_or_self_hash') 'approval.prohibitions'
    Assert-StringArray $Approval.argv 'approval.argv'; if ($Approval.argv.Count -ne 44) { Fail 'approval argv shape mismatch' }
    return [pscustomobject]@{ Approval = $Approval; ApprovalIdentity = if ($null -eq $BoundIdentity) { Get-FileIdentity $ApprovalFull } else { $BoundIdentity }; Implementation = $implementation; Synthetic = $synthetic; Inputs = $inputs; D104Root = $d104Root; Python = $python; ReceiptPath = $outputPath }
}

function Assert-DeepEqual($Actual, $Expected, [string]$Label) {
    $options = [Text.Json.JsonSerializerOptions]::new(); $options.WriteIndented = $false
    $left = [Text.Json.JsonSerializer]::Serialize($Actual, $options); $right = [Text.Json.JsonSerializer]::Serialize($Expected, $options)
    if (-not [StringComparer]::Ordinal.Equals($left, $right)) { Fail "$Label immutable reference mismatch" }
}

function Assert-ParentReferenceBindings($Bindings, [string]$ParentPath, [int64]$ParentSize, [string]$ParentSha, [string]$AuthorizationId, [string]$Label) {
    Assert-ExactKeys $Bindings @('binding_statement','no_override','references') $Label
    Assert-String $Bindings.binding_statement "$Label.binding_statement"; Assert-ExactBoolean $Bindings.no_override $true "$Label.no_override"
    Assert-ExactKeys $Bindings.references @('approved_argv','accepted_implementation','synthetic_only_test_evidence','exact_production_inputs','python_binding') "$Label.references"
    $pointers = [ordered]@{ approved_argv = '/argv'; accepted_implementation = '/accepted_implementation'; synthetic_only_test_evidence = '/synthetic_only_test_evidence'; exact_production_inputs = '/exact_production_inputs'; python_binding = '/python_binding' }
    foreach ($name in $pointers.Keys) {
        $reference = $Bindings.references.$name
        Assert-ExactKeys $reference @('absolute_path','size_bytes','sha256','authorization_id','json_pointer','referenced_values_approved_exactly','referenced_values_must_not_be_overridden') "$Label.references.$name"
        Assert-String $reference.absolute_path "$Label.references.$name.absolute_path"; Assert-Int $reference.size_bytes "$Label.references.$name.size_bytes" 0; Assert-Sha $reference.sha256 "$Label.references.$name.sha256"; Assert-String $reference.authorization_id "$Label.references.$name.authorization_id"; Assert-String $reference.json_pointer "$Label.references.$name.json_pointer"; Assert-ExactBoolean $reference.referenced_values_approved_exactly $true "$Label.references.$name.referenced_values_approved_exactly"; Assert-ExactBoolean $reference.referenced_values_must_not_be_overridden $true "$Label.references.$name.referenced_values_must_not_be_overridden"
        if ((Resolve-Absolute $reference.absolute_path) -cne (Resolve-Absolute $ParentPath) -or [int64]$reference.size_bytes -ne $ParentSize -or $reference.sha256 -ine $ParentSha -or $reference.authorization_id -cne $AuthorizationId -or $reference.json_pointer -cne $pointers[$name]) { Fail "$Label.references.$name is not an immutable exact parent binding" }
    }
}

function Assert-ProductionArtifactPolicy($Policy, [string]$ApprovalPath, [string]$HqPath, [string]$TokenPath, [string]$ReceiptPath, [string]$Label = 'HQ production_artifact_policy') {
    Assert-ExactKeys $Policy @('allowed','prohibit_temp','prohibit_logs','prohibit_controller_receipt','prohibit_anything_else') $Label
    Assert-StringArray $Policy.allowed "$Label.allowed"; $allowed = @($Policy.allowed | ForEach-Object { Resolve-Absolute $_ })
    Assert-OrdinalExact $allowed @((Resolve-Absolute $ApprovalPath),(Resolve-Absolute $HqPath),(Resolve-Absolute $TokenPath),(Resolve-Absolute $ReceiptPath)) "$Label.allowed"
    Assert-ExactBoolean $Policy.prohibit_temp $true "$Label.prohibit_temp"; Assert-ExactBoolean $Policy.prohibit_logs $true "$Label.prohibit_logs"; Assert-ExactBoolean $Policy.prohibit_controller_receipt $true "$Label.prohibit_controller_receipt"; Assert-ExactBoolean $Policy.prohibit_anything_else $true "$Label.prohibit_anything_else"
}

function Assert-D103Layer($Layer, [string]$Label) {
    Assert-ExactKeys $Layer @('conductivity_origin','conductivity_s_per_m','conductivity_source_record_id','depth_from_stack_top_um','layer_kind','layer_name','material_name','material_origin','material_source_record_id','ordinal','raw_layer_ordinal','raw_layer_source_record_sha256','thickness_origin','thickness_source_record_id','thickness_um') $Label
    Assert-Int $Layer.ordinal "$Label.ordinal"; Assert-ExactInt $Layer.raw_layer_ordinal $Layer.ordinal "$Label.raw_layer_ordinal"; Assert-String $Layer.layer_name "$Label.layer_name"; Assert-String $Layer.layer_kind "$Label.layer_kind"; Assert-String $Layer.material_name "$Label.material_name"; Assert-String $Layer.material_origin "$Label.material_origin"; Assert-String $Layer.material_source_record_id "$Label.material_source_record_id"; Assert-String $Layer.conductivity_origin "$Label.conductivity_origin"; Assert-String $Layer.thickness_origin "$Label.thickness_origin"; Assert-String $Layer.thickness_source_record_id "$Label.thickness_source_record_id"; Assert-Sha $Layer.raw_layer_source_record_sha256 "$Label.raw_layer_source_record_sha256"; Assert-Number $Layer.thickness_um "$Label.thickness_um"
    if ($Layer.layer_kind -eq 'dielectric') { Assert-NullableNumber $Layer.conductivity_s_per_m "$Label.conductivity_s_per_m"; Assert-NullableString $Layer.conductivity_source_record_id "$Label.conductivity_source_record_id" } else { Assert-Number $Layer.conductivity_s_per_m "$Label.conductivity_s_per_m"; Assert-String $Layer.conductivity_source_record_id "$Label.conductivity_source_record_id" }
    Assert-ExactKeys $Layer.depth_from_stack_top_um @('top','center','bottom') "$Label.depth_from_stack_top_um"; foreach ($name in @('top','center','bottom')) { Assert-Number $Layer.depth_from_stack_top_um.$name "$Label.depth_from_stack_top_um.$name" }
}

function Assert-D103LayerArray($Layers, [string]$Label) { Assert-Array $Layers $Label; foreach ($layer in $Layers) { Assert-D103Layer $layer "$Label item" } }
function Assert-RailRow($Row, [string]$Label) { Assert-ExactKeys $Row @('artwork_net','island_id','layer','logical_net','ordinal','pair_evidence_sha256','rail_id','role','state','surface_id') $Label; Assert-Int $Row.ordinal "$Label.ordinal"; foreach ($name in @('artwork_net','island_id','layer','logical_net','pair_evidence_sha256','rail_id','role','state','surface_id')) { Assert-String $Row.$name "$Label.$name" }; Assert-Sha $Row.pair_evidence_sha256 "$Label.pair_evidence_sha256" }
function Assert-TerminalRow($Row, [string]$Label) { Assert-ExactKeys $Row @('ordinal','terminal_id','owner_kind','rail_id','branch_id','pin_id','role','source_node_record_id','via_record_required','via_record_id','endpoint_node_id','island_id','component_id','layer','padstack_id','paddef_source_record_id','regular_source_record_id','raw_pad_shape_ordinal','raw_pad_shape_sha256','finite_vertex_id','finite_edge_id','via_owner_id','status','issues_json') $Label; Assert-Int $Row.ordinal "$Label.ordinal"; Assert-ExactInt $Row.via_record_required 1 "$Label.via_record_required"; Assert-ExactInt $Row.raw_pad_shape_ordinal 12 "$Label.raw_pad_shape_ordinal"; foreach ($name in @('terminal_id','owner_kind','rail_id','branch_id','pin_id','role','source_node_record_id','via_record_id','endpoint_node_id','island_id','component_id','layer','padstack_id','paddef_source_record_id','regular_source_record_id','finite_vertex_id','finite_edge_id','via_owner_id','status','issues_json')) { Assert-String $Row.$name "$Label.$name" }; Assert-Sha $Row.raw_pad_shape_sha256 "$Label.raw_pad_shape_sha256" }
function Assert-SelectedAuthorityRow($Row, [string]$Label) { Assert-ExactKeys $Row @('terminal_id','pin_id','via_record_id','source_offset','source_end','source_record_sha256','net','selected_plane_layer','source_endpoint_node_id','source_endpoint_layer','opposite_endpoint_node_id','opposite_endpoint_layer','upper_node_id','upper_layer','lower_node_id','lower_layer','padstack_id') $Label; foreach ($name in @('terminal_id','pin_id','via_record_id','net','selected_plane_layer','source_endpoint_node_id','source_endpoint_layer','opposite_endpoint_node_id','opposite_endpoint_layer','upper_node_id','upper_layer','lower_node_id','lower_layer','padstack_id')) { Assert-String $Row.$name "$Label.$name" }; Assert-Int $Row.source_offset "$Label.source_offset" 0; Assert-Int $Row.source_end "$Label.source_end" 1; Assert-Sha $Row.source_record_sha256 "$Label.source_record_sha256" }
function Assert-D104Cell($Cell, [string]$Label) {
    Assert-ExactKeys $Cell @('ordinal','layer','net','island_id','source_wkb','intersection','intersection_wkb_sha256','intersection_wkb_size_bytes','intersection_bbox_um','intersection_area_um2','boundary_contact') $Label; Assert-Int $Cell.ordinal "$Label.ordinal"; foreach ($name in @('layer','net','island_id')) { Assert-String $Cell.$name "$Label.$name" }; Assert-ExactKeys $Cell.source_wkb @('filename','sha256','wkb_sha256','size_bytes','wkb_size_bytes','bounds_um','bbox_um','area_um2') "$Label.source_wkb"; foreach ($name in @('filename','sha256','wkb_sha256')) { Assert-String $Cell.source_wkb.$name "$Label.source_wkb.$name" }; Assert-Sha $Cell.source_wkb.sha256 "$Label.source_wkb.sha256"; Assert-Sha $Cell.source_wkb.wkb_sha256 "$Label.source_wkb.wkb_sha256"; foreach ($name in @('size_bytes','wkb_size_bytes')) { Assert-Int $Cell.source_wkb.$name "$Label.source_wkb.$name" 0 }; Assert-NumberArray $Cell.source_wkb.bounds_um "$Label.source_wkb.bounds_um" 4; Assert-NumberArray $Cell.source_wkb.bbox_um "$Label.source_wkb.bbox_um" 4; Assert-Number $Cell.source_wkb.area_um2 "$Label.source_wkb.area_um2"; Assert-ExactKeys $Cell.intersection @('wkb_sha256','wkb_size_bytes','bbox_um','area_um2','nonempty','boundary_contact') "$Label.intersection"; Assert-NullableString $Cell.intersection.wkb_sha256 "$Label.intersection.wkb_sha256"; if ($null -ne $Cell.intersection.wkb_sha256) { Assert-Sha $Cell.intersection.wkb_sha256 "$Label.intersection.wkb_sha256" }; Assert-NullableInt $Cell.intersection.wkb_size_bytes "$Label.intersection.wkb_size_bytes"; if ($null -ne $Cell.intersection.bbox_um) { Assert-NumberArray $Cell.intersection.bbox_um "$Label.intersection.bbox_um" 4 }; Assert-Number $Cell.intersection.area_um2 "$Label.intersection.area_um2"; Assert-Boolean $Cell.intersection.nonempty "$Label.intersection.nonempty"; Assert-Boolean $Cell.intersection.boundary_contact "$Label.intersection.boundary_contact"; Assert-NullableString $Cell.intersection_wkb_sha256 "$Label.intersection_wkb_sha256"; if ($null -ne $Cell.intersection_wkb_sha256) { Assert-Sha $Cell.intersection_wkb_sha256 "$Label.intersection_wkb_sha256" }; Assert-NullableInt $Cell.intersection_wkb_size_bytes "$Label.intersection_wkb_size_bytes"; if ($null -ne $Cell.intersection_bbox_um) { Assert-NumberArray $Cell.intersection_bbox_um "$Label.intersection_bbox_um" 4 }; Assert-Number $Cell.intersection_area_um2 "$Label.intersection_area_um2"; Assert-Boolean $Cell.boundary_contact "$Label.boundary_contact"
}
function Assert-W0Footprint($Foot, [string]$Label) { Assert-ExactKeys $Foot @('pin_id','source_records','center_pm','radius_pm','diameter_pm','bbox_pm') $Label; Assert-String $Foot.pin_id "$Label.pin_id"; Assert-Array $Foot.source_records "$Label.source_records"; if ($Foot.source_records.Count -ne 3) { Fail "$Label.source_records count mismatch" }; foreach ($ref in $Foot.source_records) { Assert-ExactKeys $ref @('record_id','source_offset','source_end','source_record_sha256') "$Label.source_record"; Assert-String $ref.record_id "$Label.source_record.record_id"; Assert-Int $ref.source_offset "$Label.source_record.source_offset" 0; Assert-Int $ref.source_end "$Label.source_record.source_end" 1; Assert-Sha $ref.source_record_sha256 "$Label.source_record.source_record_sha256" }; Assert-IntArray $Foot.center_pm "$Label.center_pm" ([int64]::MinValue) 2; Assert-Int $Foot.radius_pm "$Label.radius_pm" 1; Assert-Int $Foot.diameter_pm "$Label.diameter_pm" 1; Assert-IntArray $Foot.bbox_pm "$Label.bbox_pm" ([int64]::MinValue) 4 }
function Assert-MembershipViolation($Item, [string]$Label) { Assert-ExactKeys $Item @('pin_id','reason','target_ordinal') $Label; Assert-String $Item.pin_id "$Label.pin_id"; Assert-String $Item.reason "$Label.reason"; Assert-Int $Item.target_ordinal "$Label.target_ordinal" }
function Assert-MembershipTerminal($Item, [string]$Label) { Assert-ExactKeys $Item @('pin_id','role','target_ordinal','center_pm','radius_pm','target_center_covered','target_distance_to_boundary_um','target_proven','other_same_layer_islands') $Label; Assert-String $Item.pin_id "$Label.pin_id"; Assert-String $Item.role "$Label.role"; Assert-Int $Item.target_ordinal "$Label.target_ordinal"; Assert-IntArray $Item.center_pm "$Label.center_pm" ([int64]::MinValue) 2; Assert-Int $Item.radius_pm "$Label.radius_pm" 1; Assert-Boolean $Item.target_center_covered "$Label.target_center_covered"; Assert-Number $Item.target_distance_to_boundary_um "$Label.target_distance_to_boundary_um"; Assert-Boolean $Item.target_proven "$Label.target_proven"; Assert-Array $Item.other_same_layer_islands "$Label.other_same_layer_islands"; foreach ($other in $Item.other_same_layer_islands) { Assert-ExactKeys $other @('ordinal','net','center_distance_um','radius_um','proven') "$Label.other_same_layer_islands item"; Assert-Int $other.ordinal "$Label.other_same_layer_islands.ordinal"; Assert-String $other.net "$Label.other_same_layer_islands.net"; Assert-Number $other.center_distance_um "$Label.other_same_layer_islands.center_distance_um"; Assert-Number $other.radius_um "$Label.other_same_layer_islands.radius_um"; Assert-Boolean $other.proven "$Label.other_same_layer_islands.proven" } }

function Assert-ReceiptSchema($Receipt) {
    Assert-Object $Receipt 'receipt'
    Assert-ExactKeys $Receipt @('product','version','schema','status','git','git_head','git_branch','tracked_clean','inputs','expected','d103','d115b','manifest','port44','rail_bindings','terminal_bindings','selected_via_source_authority','w0','d104_cells','scope','membership','empty_ordinals','coverage_conflict','code') 'receipt'
    Assert-ExactString $Receipt.product $Product 'receipt.product'; Assert-ExactString $Receipt.version $Version 'receipt.version'; Assert-ExactString $Receipt.schema 'source-local-l29-l30-port-window-receipt-v4' 'receipt.schema'; Assert-ExactString $Receipt.status $ExpectedCode 'receipt.status'; Assert-ExactString $Receipt.code $ExpectedCode 'receipt.code'; Assert-ExactString $Receipt.git_head $RequiredHead 'receipt.git_head'; Assert-String $Receipt.git_branch 'receipt.git_branch'; Assert-ExactBoolean $Receipt.tracked_clean $true 'receipt.tracked_clean'
    Assert-ExactKeys $Receipt.git @('root','head','branch','tracked_clean') 'receipt.git'; Assert-String $Receipt.git.root 'receipt.git.root'; Assert-String $Receipt.git.head 'receipt.git.head'; Assert-String $Receipt.git.branch 'receipt.git.branch'; Assert-ExactBoolean $Receipt.git.tracked_clean $true 'receipt.git.tracked_clean'
    Assert-ExactKeys $Receipt.inputs @('source','d103','d104','d104_root','d115b','candidate','repo_root') 'receipt.inputs'
    foreach ($name in @('source','d103','d104','d115b','candidate')) { Assert-ExactKeys $Receipt.inputs.$name @('path','size_bytes','sha256') "receipt.inputs.$name"; Assert-String $Receipt.inputs.$name.path "receipt.inputs.$name.path"; Assert-Int $Receipt.inputs.$name.size_bytes "receipt.inputs.$name.size_bytes" 0; Assert-Sha $Receipt.inputs.$name.sha256 "receipt.inputs.$name.sha256" }
    foreach ($name in @('d104_root','repo_root')) { Assert-ExactKeys $Receipt.inputs.$name @('path') "receipt.inputs.$name"; Assert-String $Receipt.inputs.$name.path "receipt.inputs.$name.path" }
    Assert-ExactKeys $Receipt.expected @('head','window_pm','port_id','positive_count','negative_count') 'receipt.expected'; Assert-ExactString $Receipt.expected.head $RequiredHead 'receipt.expected.head'; Assert-IntArray $Receipt.expected.window_pm 'receipt.expected.window_pm'; if ($Receipt.expected.window_pm.Count -ne 4) { Fail 'receipt.expected.window_pm must have four values' }; Assert-String $Receipt.expected.port_id 'receipt.expected.port_id'; Assert-Int $Receipt.expected.positive_count 'receipt.expected.positive_count' 0; Assert-Int $Receipt.expected.negative_count 'receipt.expected.negative_count' 0
    Assert-ExactKeys $Receipt.d103 @('status','version','layers','dielectric_point','gap_pm','gap_um','stackup_layers') 'receipt.d103'; Assert-String $Receipt.d103.status 'receipt.d103.status'; Assert-String $Receipt.d103.version 'receipt.d103.version'; Assert-D103LayerArray $Receipt.d103.layers 'receipt.d103.layers'; Assert-ExactKeys $Receipt.d103.dielectric_point @('epsilon_origin','epsilon_r','epsilon_source_record_id','frequency_hz','frequency_origin','frequency_source_record_id','layer_name','layer_ordinal','loss_tangent','loss_tangent_origin','loss_tangent_source_record_id','ordinal','point_ordinal') 'receipt.d103.dielectric_point'; foreach ($name in @('epsilon_origin','epsilon_source_record_id','frequency_origin','frequency_source_record_id','layer_name','loss_tangent_origin','loss_tangent_source_record_id')) { Assert-String $Receipt.d103.dielectric_point.$name "receipt.d103.dielectric_point.$name" }; foreach ($name in @('frequency_hz','epsilon_r','loss_tangent')) { Assert-Number $Receipt.d103.dielectric_point.$name "receipt.d103.dielectric_point.$name" }; foreach ($name in @('layer_ordinal','ordinal','point_ordinal')) { Assert-Int $Receipt.d103.dielectric_point.$name "receipt.d103.dielectric_point.$name" }; Assert-Int $Receipt.d103.gap_pm 'receipt.d103.gap_pm' 1; Assert-Int $Receipt.d103.gap_um 'receipt.d103.gap_um' 0; Assert-D103LayerArray $Receipt.d103.stackup_layers 'receipt.d103.stackup_layers'
    Assert-ExactKeys $Receipt.d115b @('status','disposition','contract_head','candidate') 'receipt.d115b'; Assert-ExactString $Receipt.d115b.status 'PASS' 'receipt.d115b.status'; Assert-ExactString $Receipt.d115b.disposition 'PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED' 'receipt.d115b.disposition'; Assert-String $Receipt.d115b.contract_head 'receipt.d115b.contract_head'; Assert-ExactKeys $Receipt.d115b.candidate @('path','present','size_bytes','sha256','rail_count','terminal_count','rail_rows','terminal_rows') 'receipt.d115b.candidate'; Assert-String $Receipt.d115b.candidate.path 'receipt.d115b.candidate.path'; Assert-ExactBoolean $Receipt.d115b.candidate.present $true 'receipt.d115b.candidate.present'; Assert-Int $Receipt.d115b.candidate.size_bytes 'receipt.d115b.candidate.size_bytes' 0; Assert-Sha $Receipt.d115b.candidate.sha256 'receipt.d115b.candidate.sha256'; Assert-Int $Receipt.d115b.candidate.rail_count 'receipt.d115b.candidate.rail_count' 0; Assert-Int $Receipt.d115b.candidate.terminal_count 'receipt.d115b.candidate.terminal_count' 0; Assert-Array $Receipt.d115b.candidate.rail_rows 'receipt.d115b.candidate.rail_rows'; foreach ($row in $Receipt.d115b.candidate.rail_rows) { Assert-RailRow $row 'receipt.d115b.candidate.rail_row' }; Assert-Array $Receipt.d115b.candidate.terminal_rows 'receipt.d115b.candidate.terminal_rows'; foreach ($row in $Receipt.d115b.candidate.terminal_rows) { Assert-TerminalRow $row 'receipt.d115b.candidate.terminal_row' }
    Assert-ExactKeys $Receipt.manifest @('app_version','source_sha256','source_size_bytes','target_rail_id') 'receipt.manifest'; Assert-ExactString $Receipt.manifest.app_version $Version 'receipt.manifest.app_version'; Assert-Sha $Receipt.manifest.source_sha256 'receipt.manifest.source_sha256'; Assert-Int $Receipt.manifest.source_size_bytes 'receipt.manifest.source_size_bytes' 0; Assert-String $Receipt.manifest.target_rail_id 'receipt.manifest.target_rail_id'
    Assert-ExactKeys $Receipt.port44 @('port_id','header','header_line','header_line_start','header_line_end','header_byte_start','header_byte_end','header_sha256','section_line_start','section_line_end','section_line_count','section_byte_start','section_byte_end','section_byte_bounds','section_sha256','section_terminator','positive_terminals','positive_count','positive_sequence_sha256','positive_set_sha256','negative_count','negative_sequence_sha256','negative_set_sha256','selected_negative_terminals','selected_negative_count') 'receipt.port44'; Assert-String $Receipt.port44.port_id 'receipt.port44.port_id'; Assert-String $Receipt.port44.header 'receipt.port44.header'; foreach ($name in @('header_line','header_line_start','header_line_end','header_byte_start','header_byte_end','section_line_start','section_line_end','section_line_count','section_byte_start','section_byte_end','positive_count','negative_count','selected_negative_count')) { Assert-Int $Receipt.port44.$name "receipt.port44.$name" 0 }; Assert-Sha $Receipt.port44.header_sha256 'receipt.port44.header_sha256'; Assert-IntArray $Receipt.port44.section_byte_bounds 'receipt.port44.section_byte_bounds' 0; if ($Receipt.port44.section_byte_bounds.Count -ne 2) { Fail 'receipt.port44.section_byte_bounds must have two values' }; Assert-Sha $Receipt.port44.section_sha256 'receipt.port44.section_sha256'; Assert-String $Receipt.port44.section_terminator 'receipt.port44.section_terminator'; Assert-StringArray $Receipt.port44.positive_terminals 'receipt.port44.positive_terminals'; Assert-StringArray $Receipt.port44.selected_negative_terminals 'receipt.port44.selected_negative_terminals'; Assert-Sha $Receipt.port44.positive_sequence_sha256 'receipt.port44.positive_sequence_sha256'; Assert-Sha $Receipt.port44.positive_set_sha256 'receipt.port44.positive_set_sha256'; Assert-Sha $Receipt.port44.negative_sequence_sha256 'receipt.port44.negative_sequence_sha256'; Assert-Sha $Receipt.port44.negative_set_sha256 'receipt.port44.negative_set_sha256'
    Assert-Array $Receipt.rail_bindings 'receipt.rail_bindings'; foreach ($row in $Receipt.rail_bindings) { Assert-RailRow $row 'receipt.rail_bindings row' }; Assert-Array $Receipt.terminal_bindings 'receipt.terminal_bindings'; foreach ($row in $Receipt.terminal_bindings) { Assert-TerminalRow $row 'receipt.terminal_bindings row' }; Assert-Array $Receipt.d104_cells 'receipt.d104_cells'; foreach ($cell in $Receipt.d104_cells) { Assert-D104Cell $cell 'receipt.d104_cells item' }
    Assert-ExactKeys $Receipt.selected_via_source_authority @('status','row_count','target_layer_traversal_proven','scope','rows') 'receipt.selected_via_source_authority'; Assert-String $Receipt.selected_via_source_authority.status 'receipt.selected_via_source_authority.status'; Assert-Int $Receipt.selected_via_source_authority.row_count 'receipt.selected_via_source_authority.row_count' 0; Assert-ExactBoolean $Receipt.selected_via_source_authority.target_layer_traversal_proven $false 'receipt.selected_via_source_authority.target_layer_traversal_proven'; Assert-ExactKeys $Receipt.selected_via_source_authority.scope @('barrel_proven','antipad_proven','land_proven','intermediate_access_proven','l29_l30_physical_pad_proven','three_dimensional_geometry_proven') 'receipt.selected_via_source_authority.scope'; foreach ($name in $Receipt.selected_via_source_authority.scope.Keys) { Assert-Boolean $Receipt.selected_via_source_authority.scope[$name] "receipt.selected_via_source_authority.scope.$name" }; Assert-Array $Receipt.selected_via_source_authority.rows 'receipt.selected_via_source_authority.rows'; foreach ($row in $Receipt.selected_via_source_authority.rows) { Assert-SelectedAuthorityRow $row 'receipt.selected_via_source_authority.row' }
    Assert-ExactKeys $Receipt.w0 @('gap_pm','gap_um','padding_pm','padding_policy','largest_diameter_pm','raw_bounds_pm','grid_pm','snapped_bounds_pm','footprint_count','footprints') 'receipt.w0'; foreach ($name in @('gap_pm','gap_um','padding_pm','largest_diameter_pm','grid_pm','footprint_count')) { Assert-Int $Receipt.w0.$name "receipt.w0.$name" 0 }; Assert-String $Receipt.w0.padding_policy 'receipt.w0.padding_policy'; Assert-IntArray $Receipt.w0.raw_bounds_pm 'receipt.w0.raw_bounds_pm' ([int64]::MinValue) 4; Assert-IntArray $Receipt.w0.snapped_bounds_pm 'receipt.w0.snapped_bounds_pm' ([int64]::MinValue) 4; Assert-Int $Receipt.w0.footprint_count 'receipt.w0.footprint_count' 0; Assert-Array $Receipt.w0.footprints 'receipt.w0.footprints'; foreach ($foot in $Receipt.w0.footprints) { Assert-W0Footprint $foot 'receipt.w0.footprints item' }
    Assert-ExactKeys $Receipt.scope @('source_local_shadow_only','solver_executed','powersi_executed','generic_four_layer_model','c_res') 'receipt.scope'; foreach ($name in $Receipt.scope.Keys) { Assert-Boolean $Receipt.scope[$name] "receipt.scope.$name" }
    Assert-ExactKeys $Receipt.membership @('evaluated','passed','violations','terminals') 'receipt.membership'; Assert-ExactBoolean $Receipt.membership.evaluated $true 'receipt.membership.evaluated'; Assert-ExactBoolean $Receipt.membership.passed $false 'receipt.membership.passed'; Assert-Array $Receipt.membership.violations 'receipt.membership.violations'; foreach ($item in $Receipt.membership.violations) { Assert-MembershipViolation $item 'receipt.membership.violation' }; Assert-Array $Receipt.membership.terminals 'receipt.membership.terminals'; foreach ($item in $Receipt.membership.terminals) { Assert-MembershipTerminal $item 'receipt.membership.terminal' }
    Assert-Array $Receipt.empty_ordinals 'receipt.empty_ordinals'; Assert-IntArray $Receipt.empty_ordinals 'receipt.empty_ordinals'; if ($Receipt.empty_ordinals.Count -eq 0) { Fail 'receipt.empty_ordinals must be non-empty' }
    Assert-ExactKeys $Receipt.coverage_conflict @('empty_ordinals','required_ordinals','all_eight_evaluated') 'receipt.coverage_conflict'; Assert-IntArray $Receipt.coverage_conflict.empty_ordinals 'receipt.coverage_conflict.empty_ordinals'; Assert-IntArray $Receipt.coverage_conflict.required_ordinals 'receipt.coverage_conflict.required_ordinals'; Assert-ExactBoolean $Receipt.coverage_conflict.all_eight_evaluated $true 'receipt.coverage_conflict.all_eight_evaluated'
}

function Assert-Hq($Hq, [string]$HqFull, $ApprovalInfo, [string]$ControllerPath, $ControllerIdentity, [string]$Root, $BoundIdentity = $null) {
    Assert-ExactKeys $Hq @('schema','product','authorization','parent_anchor','controller_binding','approved_parent_anchor_bindings','output_receipt_path','execution','forbidden_engines','attempt_token','production_artifact_policy') 'HQ authorization'
    Assert-ExactString $Hq.schema $HqSchema 'HQ schema'; Assert-ExactKeys $Hq.product @('name','version') 'HQ product'; Assert-ExactString $Hq.product.name $Product 'HQ product.name'; Assert-ExactString $Hq.product.version $Version 'HQ product.version'
    Assert-ExactKeys $Hq.authorization @('authorization_id','status','execution_authorized') 'HQ authorization.authorization'; Assert-String $Hq.authorization.authorization_id 'HQ authorization.authorization_id'; Assert-ExactString $Hq.authorization.authorization_id $ApprovalInfo.Approval.authorization.authorization_id 'HQ authorization.authorization_id'; Assert-ExactString $Hq.authorization.status $HqStatus 'HQ authorization.status'; Assert-ExactBoolean $Hq.authorization.execution_authorized $true 'HQ authorization.execution_authorized'
    Assert-ExactKeys $Hq.parent_anchor @('absolute_path','size_bytes','sha256','authorization_id','immutable') 'HQ parent_anchor'; Assert-String $Hq.parent_anchor.absolute_path 'HQ parent_anchor.absolute_path'; Assert-Int $Hq.parent_anchor.size_bytes 'HQ parent_anchor.size_bytes' 0; Assert-Sha $Hq.parent_anchor.sha256 'HQ parent_anchor.sha256'; Assert-String $Hq.parent_anchor.authorization_id 'HQ parent_anchor.authorization_id'; Assert-ExactBoolean $Hq.parent_anchor.immutable $true 'HQ parent_anchor.immutable'; if ((Resolve-Absolute $Hq.parent_anchor.absolute_path) -cne $ApprovalInfo.ApprovalIdentity.Path -or [int64]$Hq.parent_anchor.size_bytes -ne $ApprovalInfo.ApprovalIdentity.SizeBytes -or $Hq.parent_anchor.sha256 -ine $ApprovalInfo.ApprovalIdentity.Sha256 -or $Hq.parent_anchor.authorization_id -cne $ApprovalInfo.Approval.authorization.authorization_id) { Fail 'HQ parent anchor is not immutable' }
    Assert-ExactKeys $Hq.controller_binding @('absolute_path','size_bytes','sha256') 'HQ controller_binding'; Assert-String $Hq.controller_binding.absolute_path 'HQ controller_binding.absolute_path'; Assert-Int $Hq.controller_binding.size_bytes 'HQ controller_binding.size_bytes' 0; Assert-Sha $Hq.controller_binding.sha256 'HQ controller_binding.sha256'; if ((Resolve-Absolute $Hq.controller_binding.absolute_path) -cne $ControllerPath -or [int64]$Hq.controller_binding.size_bytes -ne $ControllerIdentity.SizeBytes -or $Hq.controller_binding.sha256 -ine $ControllerIdentity.Sha256) { Fail 'HQ controller binding mismatch' }
    Assert-ParentReferenceBindings $Hq.approved_parent_anchor_bindings $ApprovalInfo.ApprovalIdentity.Path $ApprovalInfo.ApprovalIdentity.SizeBytes $ApprovalInfo.ApprovalIdentity.Sha256 $ApprovalInfo.Approval.authorization.authorization_id 'HQ approved_parent_anchor_bindings'
    Assert-String $Hq.output_receipt_path 'HQ output_receipt_path'; if ((Resolve-Absolute $Hq.output_receipt_path) -cne $ApprovalInfo.ReceiptPath) { Fail 'HQ output receipt path mismatch' }
    Assert-ExactKeys $Hq.execution @('external_wall_seconds_exact','internal_deadline_seconds_exact','python_processes_exactly','attempts_exactly','retries_exactly','environment','expected_result') 'HQ execution'; Assert-ExactInt $Hq.execution.external_wall_seconds_exact $HardWallSeconds 'HQ execution.external_wall_seconds_exact'; Assert-ExactInt $Hq.execution.internal_deadline_seconds_exact $DeadlineSeconds 'HQ execution.internal_deadline_seconds_exact'; Assert-ExactInt $Hq.execution.python_processes_exactly 1 'HQ execution.python_processes_exactly'; Assert-ExactInt $Hq.execution.attempts_exactly 1 'HQ execution.attempts_exactly'; Assert-ExactInt $Hq.execution.retries_exactly 0 'HQ execution.retries_exactly'; Assert-ExactKeys $Hq.execution.environment @('PYTHONDONTWRITEBYTECODE') 'HQ execution.environment'; Assert-ExactString $Hq.execution.environment.PYTHONDONTWRITEBYTECODE '1' 'HQ execution.environment.PYTHONDONTWRITEBYTECODE'; Assert-ExactKeys $Hq.execution.expected_result @('exit_code','code','strict_receipt_required','unexpected_result_or_missing_or_invalid_receipt','receipt_size_bytes_exact','receipt_sha256_exact') 'HQ expected result'; Assert-ExactInt $Hq.execution.expected_result.exit_code 1 'HQ expected_result.exit_code'; Assert-ExactString $Hq.execution.expected_result.code $ExpectedCode 'HQ expected_result.code'; Assert-ExactBoolean $Hq.execution.expected_result.strict_receipt_required $true 'HQ expected_result.strict_receipt_required'; Assert-ExactString $Hq.execution.expected_result.unexpected_result_or_missing_or_invalid_receipt 'STOP' 'HQ expected_result.unexpected_result_or_missing_or_invalid_receipt'; Assert-ExactInt $Hq.execution.expected_result.receipt_size_bytes_exact $RequiredReceiptSizeBytes 'HQ expected receipt size'; Assert-ExactString $Hq.execution.expected_result.receipt_sha256_exact $RequiredReceiptSha256 'HQ expected receipt sha'; Assert-ExactInt $Hq.execution.expected_result.receipt_size_bytes_exact $ApprovalInfo.Approval.expected_scientific_result.receipt_size_bytes_exact 'HQ/approval receipt size'; Assert-ExactString $Hq.execution.expected_result.receipt_sha256_exact $ApprovalInfo.Approval.expected_scientific_result.receipt_sha256_exact 'HQ/approval receipt sha'
    Assert-StringArray $Hq.forbidden_engines 'HQ forbidden_engines'; Assert-OrdinalExact $Hq.forbidden_engines @('Triangle','C1','FasterCap','solver','PowerSI') 'HQ forbidden_engines'
    Assert-ExactKeys $Hq.attempt_token @('absolute_path','file_mode','atomic_no_clobber','creation_timing','permanent_retention') 'HQ attempt_token'; Assert-String $Hq.attempt_token.absolute_path 'HQ attempt_token.absolute_path'; Assert-ExactString $Hq.attempt_token.file_mode 'System.IO.FileMode.CreateNew' 'HQ attempt_token.file_mode'; Assert-ExactBoolean $Hq.attempt_token.atomic_no_clobber $true 'HQ attempt_token.atomic_no_clobber'; Assert-ExactString $Hq.attempt_token.creation_timing 'immediately_before_process_creation' 'HQ attempt_token.creation_timing'; Assert-ExactBoolean $Hq.attempt_token.permanent_retention $true 'HQ attempt_token.permanent_retention'; if ((Resolve-Absolute $Hq.attempt_token.absolute_path) -cne (Join-Path $Root $TokenName)) { Fail 'HQ attempt token policy mismatch' }
    Assert-ProductionArtifactPolicy $Hq.production_artifact_policy $ApprovalInfo.ApprovalIdentity.Path $HqFull (Join-Path $Root $TokenName) $ApprovalInfo.ReceiptPath
    if ($null -eq $BoundIdentity) { $BoundIdentity = Get-FileIdentity $HqFull }
    return $BoundIdentity
}

function Assert-Receipt($Receipt, [string]$Path, $Approval, $InputIds, [string]$Repo, [string]$ExpectedHead, [string]$D104Root) {
    Assert-ReceiptSchema $Receipt
    Assert-ExactString $Receipt.git_head $ExpectedHead 'receipt.git_head'; Assert-ExactString $Receipt.git.head $ExpectedHead 'receipt.git.head'; if ((Resolve-Absolute $Receipt.git.root) -cne (Resolve-Absolute $Repo)) { Fail 'receipt git root mismatch' }
    Assert-ExactString $Receipt.expected.head $ExpectedHead 'receipt.expected.head'; Assert-ExactString $Receipt.expected.port_id $Approval.parsed_parameters.port_id 'receipt.expected.port_id'; Assert-ExactInt $Receipt.expected.positive_count $Approval.parsed_parameters.positive_count 'receipt.expected.positive_count'; Assert-ExactInt $Receipt.expected.negative_count $Approval.parsed_parameters.negative_count 'receipt.expected.negative_count'; for ($i = 0; $i -lt 4; $i++) { Assert-ExactInt $Receipt.expected.window_pm[$i] $Approval.parsed_parameters.window_pm[$i] "receipt window[$i]" }
    Assert-ExactString $Receipt.port44.port_id $Approval.parsed_parameters.port_id 'receipt.port44.port_id'; Assert-ExactInt $Receipt.port44.positive_count $Approval.parsed_parameters.positive_count 'receipt.port44.positive_count'; Assert-ExactInt $Receipt.port44.negative_count $Approval.parsed_parameters.negative_count 'receipt.port44.negative_count'; Assert-ExactString $Receipt.d103.status 'PASS' 'receipt.d103.status'; Assert-ExactString $Receipt.d103.version $Version 'receipt.d103.version'; Assert-ExactString $Receipt.d115b.status 'PASS' 'receipt.d115b.status'; Assert-ExactString $Receipt.d115b.disposition 'PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED' 'receipt.d115b.disposition'; Assert-ExactString $Receipt.d115b.contract_head $ExpectedHead 'receipt.d115b.contract_head'; if ((Resolve-Absolute $Receipt.d115b.candidate.path) -cne $InputIds.candidate.Path -or [int64]$Receipt.d115b.candidate.size_bytes -ne $InputIds.candidate.SizeBytes -or $Receipt.d115b.candidate.sha256 -ine $InputIds.candidate.Sha256) { Fail 'receipt candidate evidence mismatch' }; if ($Receipt.d103.layers.Count -ne $Receipt.d103.stackup_layers.Count) { Fail 'receipt D103 layer count mismatch' }; $cellOrdinals = [int64[]](259,260,261,262,263,264,265,266); for ($i = 0; $i -lt $cellOrdinals.Count; $i++) { Assert-ExactInt $Receipt.d104_cells[$i].ordinal $cellOrdinals[$i] "receipt d104 cell ordinal[$i]" }
    Assert-ExactString $Receipt.manifest.source_sha256 $InputIds.source.Sha256 'receipt.manifest.source_sha256'; Assert-ExactInt $Receipt.manifest.source_size_bytes $InputIds.source.SizeBytes 'receipt.manifest.source_size_bytes'; if ($Receipt.port44.positive_terminals.Count -ne $Receipt.port44.positive_count -or $Receipt.port44.selected_negative_terminals.Count -ne $Receipt.port44.selected_negative_count) { Fail 'receipt.port44 count/list mismatch' }; Assert-ExactInt $Receipt.d115b.candidate.rail_count 2 'receipt.d115b.candidate.rail_count'; Assert-ExactInt $Receipt.d115b.candidate.terminal_count 6 'receipt.d115b.candidate.terminal_count'; if ($Receipt.d115b.candidate.rail_rows.Count -ne 2 -or $Receipt.d115b.candidate.terminal_rows.Count -ne 6 -or $Receipt.d103.layers.Count -ne 3 -or $Receipt.d103.stackup_layers.Count -ne 3 -or $Receipt.rail_bindings.Count -ne 2 -or $Receipt.terminal_bindings.Count -ne 6 -or $Receipt.d104_cells.Count -ne 8 -or $Receipt.selected_via_source_authority.row_count -ne 6 -or $Receipt.selected_via_source_authority.rows.Count -ne 6 -or $Receipt.w0.footprint_count -ne 6 -or $Receipt.w0.footprints.Count -ne 6 -or $Receipt.membership.terminals.Count -ne 6) { Fail 'receipt evidence cardinality mismatch' }
    Assert-DeepEqual $Receipt.d103.layers $Receipt.d103.stackup_layers 'receipt d103 layers/stackup_layers'; Assert-DeepEqual $Receipt.d115b.candidate.rail_rows $Receipt.rail_bindings 'receipt rail_rows/rail_bindings'; Assert-DeepEqual $Receipt.d115b.candidate.terminal_rows $Receipt.terminal_bindings 'receipt terminal_rows/terminal_bindings'
    $d103Ordinals = [int64[]](56,57,58); $d103Kinds = [string[]]@('conductor','dielectric','conductor'); for ($i = 0; $i -lt $d103Ordinals.Count; $i++) { Assert-ExactInt $Receipt.d103.layers[$i].ordinal $d103Ordinals[$i] "receipt d103 ordinal[$i]"; Assert-ExactString $Receipt.d103.layers[$i].layer_kind $d103Kinds[$i] "receipt d103 kind[$i]" }
    $railRoles = [string[]]@('power','ground'); for ($i = 0; $i -lt 2; $i++) { Assert-ExactInt $Receipt.d115b.candidate.rail_rows[$i].ordinal ([int64]$i) "receipt rail ordinal[$i]"; Assert-ExactString $Receipt.d115b.candidate.rail_rows[$i].role $railRoles[$i] "receipt rail role[$i]"; Assert-ExactString $Receipt.d115b.candidate.rail_rows[$i].state 'source_bound' "receipt rail state[$i]" }
    $terminalRoles = [string[]]@('ground','power','ground','power','ground','power'); for ($i = 0; $i -lt 6; $i++) { $t = $Receipt.d115b.candidate.terminal_rows[$i]; Assert-ExactInt $t.ordinal ([int64]$i) "receipt terminal ordinal[$i]"; Assert-ExactString $t.role $terminalRoles[$i] "receipt terminal role[$i]"; Assert-ExactString $t.owner_kind 'device' "receipt terminal owner_kind[$i]"; Assert-ExactString $t.status 'complete' "receipt terminal status[$i]"; Assert-ExactString $t.issues_json '[]' "receipt terminal issues_json[$i]" }
    for ($i = 0; $i -lt 6; $i++) {
        $t = $Receipt.d115b.candidate.terminal_rows[$i]; $s = $Receipt.selected_via_source_authority.rows[$i]; $m = $Receipt.membership.terminals[$i]; $f = $Receipt.w0.footprints[$i]
        Assert-ExactString $s.terminal_id $t.terminal_id "receipt terminal binding terminal_id[$i]"; Assert-ExactString $s.pin_id $t.pin_id "receipt terminal binding pin_id[$i]"; Assert-ExactString $s.via_record_id $t.via_record_id "receipt terminal binding via[$i]"; Assert-ExactString $s.padstack_id $t.padstack_id "receipt terminal binding padstack[$i]"; Assert-ExactString $s.selected_plane_layer $t.layer "receipt terminal binding layer[$i]"; Assert-ExactString $s.source_endpoint_node_id $t.endpoint_node_id "receipt terminal binding endpoint[$i]"
        Assert-ExactString $m.pin_id $t.pin_id "receipt membership pin_id[$i]"; Assert-ExactString $m.role $t.role "receipt membership role[$i]"; Assert-ExactString $f.pin_id $t.pin_id "receipt footprint pin_id[$i]"; Assert-ExactString $f.source_records[0].record_id $t.source_node_record_id "receipt footprint node source[$i]"; Assert-ExactString $f.source_records[1].record_id $t.paddef_source_record_id "receipt footprint paddef source[$i]"; Assert-ExactString $f.source_records[2].record_id $t.regular_source_record_id "receipt footprint regular source[$i]"
    }
    Assert-ExactString $Receipt.git.branch $Receipt.git_branch 'receipt git branch alias'; for ($i = 0; $i -lt 2; $i++) { $sectionBound = [int64]$Receipt.port44.section_byte_start; if ($i -eq 1) { $sectionBound = [int64]$Receipt.port44.section_byte_end }; Assert-ExactInt $Receipt.port44.section_byte_bounds[$i] $sectionBound "receipt port section bound[$i]" }
    Assert-ExactInt $Receipt.d103.gap_pm $Receipt.w0.gap_pm 'receipt d103/w0 gap_pm'; Assert-ExactInt $Receipt.d103.gap_um $Receipt.w0.gap_um 'receipt d103/w0 gap_um'
    foreach ($cell in $Receipt.d104_cells) {
        $label = "receipt d104[$($cell.ordinal)]"; Assert-ExactString $cell.source_wkb.sha256 $cell.source_wkb.wkb_sha256 "$label source sha"; Assert-ExactInt $cell.source_wkb.size_bytes $cell.source_wkb.wkb_size_bytes "$label source size"; Assert-DeepEqual $cell.source_wkb.bounds_um $cell.source_wkb.bbox_um "$label source bounds/bbox"; if (($null -eq $cell.intersection_wkb_sha256) -ne ($null -eq $cell.intersection.wkb_sha256)) { Fail "$label intersection sha nullness mismatch" }; if ($null -ne $cell.intersection_wkb_sha256) { Assert-ExactString $cell.intersection_wkb_sha256 $cell.intersection.wkb_sha256 "$label intersection sha" }; if (($null -eq $cell.intersection_wkb_size_bytes) -ne ($null -eq $cell.intersection.wkb_size_bytes)) { Fail "$label intersection size nullness mismatch" }; if ($null -ne $cell.intersection_wkb_size_bytes) { Assert-ExactInt $cell.intersection_wkb_size_bytes $cell.intersection.wkb_size_bytes "$label intersection size" }; if (($null -eq $cell.intersection_bbox_um) -ne ($null -eq $cell.intersection.bbox_um)) { Fail "$label intersection bbox nullness mismatch" }; if ($null -ne $cell.intersection_bbox_um) { Assert-DeepEqual $cell.intersection_bbox_um $cell.intersection.bbox_um "$label intersection bbox" }; if ($cell.intersection_area_um2 -ne $cell.intersection.area_um2) { Fail "$label intersection area mismatch" }; Assert-ExactBoolean $cell.boundary_contact $cell.intersection.boundary_contact "$label boundary contact"
        if ($cell.intersection.nonempty) { if ($null -eq $cell.intersection.wkb_sha256 -or $null -eq $cell.intersection.wkb_size_bytes -or $null -eq $cell.intersection.bbox_um) { Fail "$label nonempty intersection missing evidence" }; if ($cell.intersection.area_um2 -le 0) { Fail "$label nonempty intersection area invalid" } } else { if ($null -ne $cell.intersection.wkb_sha256 -or $null -ne $cell.intersection.wkb_size_bytes -or $null -ne $cell.intersection.bbox_um -or $cell.intersection.area_um2 -ne 0 -or $cell.boundary_contact) { Fail "$label empty intersection coherence mismatch" } }
    }
    foreach ($f in $Receipt.w0.footprints) {
        $radius = [int64]$f.radius_pm; if ($radius -gt ([int64]::MaxValue / 2)) { Fail 'receipt footprint radius overflow' }; $diameter = [int64]($radius + $radius); if ([int64]$f.diameter_pm -ne $diameter) { Fail "receipt footprint diameter mismatch: $($f.pin_id)" }; $cx = [int64]$f.center_pm[0]; $cy = [int64]$f.center_pm[1]; if ($cx -gt [int64]::MaxValue - $radius -or $cx -lt [int64]::MinValue + $radius -or $cy -gt [int64]::MaxValue - $radius -or $cy -lt [int64]::MinValue + $radius) { Fail "receipt footprint bbox overflow: $($f.pin_id)" }; if ([int64]$f.bbox_pm[0] -ne [int64]($cx - $radius) -or [int64]$f.bbox_pm[2] -ne [int64]($cx + $radius) -or [int64]$f.bbox_pm[1] -ne [int64]($cy - $radius) -or [int64]$f.bbox_pm[3] -ne [int64]($cy + $radius)) { Fail "receipt footprint bbox mismatch: $($f.pin_id)" }
    }
    if ((Resolve-Absolute $Receipt.inputs.repo_root.path) -cne (Resolve-Absolute $Repo) -or (Resolve-Absolute $Receipt.inputs.d104_root.path) -cne (Resolve-Absolute $D104Root)) { Fail 'receipt roots mismatch' }
    foreach ($name in @('source','d103','d115b','candidate')) { $r = $Receipt.inputs.$name; if ((Resolve-Absolute $r.path) -cne $InputIds.$name.Path -or [int64]$r.size_bytes -ne $InputIds.$name.SizeBytes -or $r.sha256 -ine $InputIds.$name.Sha256) { Fail "receipt input mismatch: $name" } }
    $d104 = $Receipt.inputs.d104; if ((Resolve-Absolute $d104.path) -cne $InputIds.d104_receipt.Path -or [int64]$d104.size_bytes -ne $InputIds.d104_receipt.SizeBytes -or $d104.sha256 -ine $InputIds.d104_receipt.Sha256) { Fail 'receipt input mismatch: d104' }
    Assert-ExactBoolean $Receipt.scope.source_local_shadow_only $true 'receipt.scope.source_local_shadow_only'; Assert-ExactBoolean $Receipt.scope.solver_executed $false 'receipt.scope.solver_executed'; Assert-ExactBoolean $Receipt.scope.powersi_executed $false 'receipt.scope.powersi_executed'; Assert-ExactBoolean $Receipt.scope.generic_four_layer_model $false 'receipt.scope.generic_four_layer_model'; Assert-ExactBoolean $Receipt.scope.c_res $false 'receipt.scope.c_res'; Assert-ExactBoolean $Receipt.coverage_conflict.all_eight_evaluated $true 'receipt coverage contract'
    $required = [int64[]](259,260,261,262,263,264,265,266); $actualRequired = $Receipt.coverage_conflict.required_ordinals; if ($actualRequired.Count -ne $required.Count) { Fail 'receipt required ordinal count mismatch' }; for ($i = 0; $i -lt $required.Count; $i++) { Assert-ExactInt $actualRequired[$i] $required[$i] "receipt required ordinal[$i]" }
    $empty = $Receipt.empty_ordinals; $nestedEmpty = $Receipt.coverage_conflict.empty_ordinals; if ($empty.Count -eq 0 -or $empty.Count -ne $nestedEmpty.Count) { Fail 'receipt empty ordinal contract mismatch' }; for ($i = 0; $i -lt $empty.Count; $i++) { if ($i -gt 0 -and $empty[$i] -le $empty[$i - 1]) { Fail 'receipt empty ordinals must be strictly ascending' }; $inRequired = $false; foreach ($ordinal in $required) { if ($ordinal -eq $empty[$i]) { $inRequired = $true; break } }; if ($empty[$i] -ne $nestedEmpty[$i] -or -not $inRequired) { Fail 'receipt empty ordinal mismatch' } }
    return $true
}

function Assert-Token([string]$TokenPath, [byte[]]$ExpectedBytes, $ExpectedObject) {
    $first = Get-FileIdentity $TokenPath; $bound = Read-BoundJsonFile $TokenPath 'attempt token postflight'; $bytes = $bound.Bytes; $parsed = $bound.Parsed; $second = $bound.Identity
    $expectedHash = ([BitConverter]::ToString((Get-Sha256Bytes $ExpectedBytes)) -replace '-', '').ToLowerInvariant()
    if ($first.Sha256 -ine $second.Sha256 -or $second.Sha256 -ine $expectedHash -or $second.SizeBytes -ne $ExpectedBytes.Length) { Fail 'attempt token identity changed' }
    Assert-DeepEqual $parsed $ExpectedObject 'attempt token'; return $second
}

function Get-RunEvidence($Run, $TokenIdentity = $null, $ReceiptIdentity = $null, $Receipt = $null, [string]$Result = 'RUN_EVIDENCE') {
    $conflict = $null; if ($null -ne $Receipt) { try { $conflict = [ordered]@{ status = $Receipt.status; code = $Receipt.code; empty_ordinals = @($Receipt.empty_ordinals); required_ordinals = @($Receipt.coverage_conflict.required_ordinals); all_eight_evaluated = $Receipt.coverage_conflict.all_eight_evaluated } } catch { $conflict = [ordered]@{ unavailable = $true } } }
    return [ordered]@{ banner = $Banner; result = $Result; raw_auditor_exit = $Run.ExitCode; expected_code = $ExpectedCode; process = [ordered]@{ exit_code = $Run.ExitCode; elapsed_seconds = $Run.ElapsedSeconds; timeout_signal_elapsed_seconds = $Run.TimeoutSignalElapsedSeconds; timed_out = $Run.TimedOut }; stdout = [ordered]@{ text = $Run.Stdout.Text.ToString(); total = $Run.Stdout.Total; stored = $Run.Stdout.Stored; truncated = $Run.Stdout.Truncated }; stderr = [ordered]@{ text = $Run.Stderr.Text.ToString(); total = $Run.Stderr.Total; stored = $Run.Stderr.Stored; truncated = $Run.Stderr.Truncated }; token = if ($null -eq $TokenIdentity) { $null } else { [ordered]@{ size_bytes = $TokenIdentity.SizeBytes; sha256 = $TokenIdentity.Sha256 } }; receipt = if ($null -eq $ReceiptIdentity) { $null } else { [ordered]@{ size_bytes = $ReceiptIdentity.SizeBytes; sha256 = $ReceiptIdentity.Sha256 } }; conflict = $conflict }
}

function Write-RunEvidence($Run, $TokenIdentity = $null, $ReceiptIdentity = $null, $Receipt = $null, [bool]$ErrorStream = $false, [string]$Result = 'RUN_EVIDENCE') {
    $line = (Get-RunEvidence $Run $TokenIdentity $ReceiptIdentity $Receipt $Result | ConvertTo-Json -Compress -Depth 20)
    if ($ErrorStream) { [Console]::Error.WriteLine($line) } else { [Console]::Out.WriteLine($line) }
}

function Get-CurrentPowerShellPath {
    $property = [Environment].GetProperty('ProcessPath')
    if ($null -ne $property) { $path = [string]$property.GetValue($null, $null); if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path -LiteralPath $path -PathType Leaf)) { return Resolve-Absolute $path } }
    $pwshPath = Join-Path $PSHOME 'pwsh.exe'; if (-not (Test-Path -LiteralPath $pwshPath -PathType Leaf)) { $pwshPath = Join-Path $PSHOME 'powershell.exe' }
    return Resolve-Absolute $pwshPath
}

function Invoke-SelfCheck {
    $temp = Resolve-Absolute ([IO.Path]::Combine([IO.Path]::GetTempPath(), "spd-d117-selfcheck-$([Guid]::NewGuid().ToString('N'))"))
    $tempRoot = Resolve-Absolute ([IO.Path]::GetTempPath()).TrimEnd('\')
    if (-not $temp.StartsWith($tempRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { Fail 'self-check deletion target escaped temp' }
    New-Item -ItemType Directory -Path $temp -Force | Out-Null
    try {
        [void](Assert-NoReparse $temp $true)
        $d103LayerFixture = [ordered]@{ conductivity_origin='material_model'; conductivity_s_per_m=[decimal]59590000; conductivity_source_record_id='material:COPPER'; depth_from_stack_top_um=[ordered]@{ top=[decimal]1947; center=[decimal]1957; bottom=[decimal]1967 }; layer_kind='conductor'; layer_name='Signal$L29(DGND)'; material_name='COPPER'; material_origin='material_model'; material_source_record_id='material:COPPER'; ordinal=[int64]56; raw_layer_ordinal=[int64]56; raw_layer_source_record_sha256='acc505db4297df7cd88af4080a72a79aed1ace6f0d82b81505829cf2fbd8a102'; thickness_origin='source'; thickness_source_record_id='layer:Signal$L29(DGND)'; thickness_um=[decimal]20 }
        $railRowFixture = [ordered]@{ artwork_net='ADC_VDD_180_VQPS_SYS_1_AON/0'; island_id='spd-surface-island:cb8510a79529b7f6f4f4afd4'; layer='Signal$L30(OTHER_POWER1)'; logical_net='ADC_VDD_180_VQPS_SYS_1_AON/0'; ordinal=[int64]0; pair_evidence_sha256='c594a8c61f5e85dd65407ec13bdb2817a0e3bb0b416a9ecdaed9635f0d6cf8c9'; rail_id='ADC_VDD_180_VQPS_SYS_1_AON/0'; role='power'; state='source_bound'; surface_id='surface:e16876928b48b860499f9440e96a2ad93cefa31a97d6c42b61f45a7903846b77' }
        $caseRejected = $false; try { Assert-ExactKeys ([ordered]@{ Name = 1 }) @('name') 'self-check case-sensitive keys' } catch [InvalidOperationException] { $caseRejected = $true }; if (-not $caseRejected) { Fail 'case-sensitive key rejection failed' }
        $sourceText = [Text.UTF8Encoding]::new($false).GetBytes('{"ok":true,"n":1}')
        $obj = Read-StrictJsonBytes $sourceText 'self-check valid'; if ($obj.ok -ne $true -or $obj.n -ne 1) { Fail 'strict valid-object parse failed' }
        $arrayObj = Read-StrictJsonBytes ([Text.UTF8Encoding]::new($false).GetBytes('{"empty":[],"single":[7]}')) 'self-check arrays'; Assert-Array $arrayObj.empty 'self-check empty array'; Assert-Array $arrayObj.single 'self-check singleton array'; if ($arrayObj.empty.Count -ne 0 -or $arrayObj.single.Count -ne 1 -or $arrayObj.single[0] -ne 7) { Fail 'array preservation failed' }; $typeRejected = $false; try { Assert-ExactBoolean 'true' $true 'self-check boolean type' } catch [InvalidOperationException] { $typeRejected = $true }; if (-not $typeRejected) { Fail 'coercive boolean accepted' }
        foreach ($bad in @('{"a":1,"a":2}','{/*x*/"a":1}','{"a":1,}')) { $rejected = $false; try { Read-StrictJsonBytes ([Text.UTF8Encoding]::new($false).GetBytes($bad)) 'self-check invalid' } catch [InvalidOperationException] { $rejected = $true }; if (-not $rejected) { Fail 'invalid JSON accepted' } }
        $syntheticFlags = [ordered]@{ passed = [int64]9; production_validation = $false }; Assert-Int $syntheticFlags.passed 'self-check synthetic passed' 1; if ($syntheticFlags.passed -ne 9 -or $syntheticFlags.production_validation -ne $false) { Fail 'authoritative synthetic passed=9 semantics failed' }
        $duplicateForbiddenRejected = $false; try { Assert-OrdinalExact @('solver','solver') @('solver') 'self-check forbidden duplicate' } catch [InvalidOperationException] { $duplicateForbiddenRejected = $true }; if (-not $duplicateForbiddenRejected) { Fail 'duplicate forbidden-set value accepted' }
        $parentPath = Join-Path $temp 'approval.json'; $parentSha = ('a' * 64) -join ''; $parentAuth = 'self-check-auth'; $pointerMap = [ordered]@{ approved_argv = '/argv'; accepted_implementation = '/accepted_implementation'; synthetic_only_test_evidence = '/synthetic_only_test_evidence'; exact_production_inputs = '/exact_production_inputs'; python_binding = '/python_binding' }; $parentRefs = [ordered]@{ binding_statement = 'Approved parent values are immutable and must not be overridden.'; no_override = $true; references = [ordered]@{} }; foreach ($name in $pointerMap.Keys) { $parentRefs.references[$name] = [ordered]@{ absolute_path = $parentPath; size_bytes = [int64]3; sha256 = $parentSha; authorization_id = $parentAuth; json_pointer = $pointerMap[$name]; referenced_values_approved_exactly = $true; referenced_values_must_not_be_overridden = $true } }; Assert-ParentReferenceBindings $parentRefs $parentPath 3 $parentSha $parentAuth 'self-check parent references'
        $parentRefs.references.approved_argv.json_pointer = '/wrong'; $wrongPointerRejected = $false; try { Assert-ParentReferenceBindings $parentRefs $parentPath 3 $parentSha $parentAuth 'self-check wrong pointer' } catch [InvalidOperationException] { $wrongPointerRejected = $true }; if (-not $wrongPointerRejected) { Fail 'wrong parent reference pointer accepted' }; $parentRefs.references.approved_argv.json_pointer = '/argv'; $parentRefs.no_override = $false; $noOverrideRejected = $false; try { Assert-ParentReferenceBindings $parentRefs $parentPath 3 $parentSha $parentAuth 'self-check no override' } catch [InvalidOperationException] { $noOverrideRejected = $true }; if (-not $noOverrideRejected) { Fail 'parent reference override accepted' }
        $hqPath = Join-Path $temp 'hq.json'; $tokenPath = Join-Path $temp $TokenName; $receiptPath = Join-Path $temp $ReceiptName; $allowPolicy = [ordered]@{ allowed = @($parentPath,$hqPath,$tokenPath,$receiptPath); prohibit_temp = $true; prohibit_logs = $true; prohibit_controller_receipt = $true; prohibit_anything_else = $true }; Assert-ProductionArtifactPolicy $allowPolicy $parentPath $hqPath $tokenPath $receiptPath 'self-check artifact policy'; $allowPolicy.allowed = @($parentPath,$parentPath,$tokenPath,$receiptPath); $allowRejected = $false; try { Assert-ProductionArtifactPolicy $allowPolicy $parentPath $hqPath $tokenPath $receiptPath 'self-check artifact duplicate' } catch [InvalidOperationException] { $allowRejected = $true }; if (-not $allowRejected) { Fail 'artifact policy accepted duplicate approval/HQ path' }
        $receiptFixture = [ordered]@{ product=$Product; version=$Version; schema='source-local-l29-l30-port-window-receipt-v4'; status=$ExpectedCode; git=[ordered]@{root=$RequiredRepo;head=$RequiredHead;branch='main';tracked_clean=$true}; git_head=$RequiredHead; git_branch='main'; tracked_clean=$true; inputs=[ordered]@{source=[ordered]@{path=$parentPath;size_bytes=[int64]3;sha256=$parentSha};d103=[ordered]@{path=$parentPath;size_bytes=[int64]3;sha256=$parentSha};d104=[ordered]@{path=$parentPath;size_bytes=[int64]3;sha256=$parentSha};d104_root=[ordered]@{path=$temp};d115b=[ordered]@{path=$parentPath;size_bytes=[int64]3;sha256=$parentSha};candidate=[ordered]@{path=$parentPath;size_bytes=[int64]3;sha256=$parentSha};repo_root=[ordered]@{path=$RequiredRepo}}; expected=[ordered]@{head=$RequiredHead;window_pm=[int64[]]@(-12000000000,12000000000,-11000000000,13000000000);port_id=$RequiredPort;positive_count=[int64]3;negative_count=[int64]10919}; d103=[ordered]@{status='PASS';version=$Version;layers=@();dielectric_point=[ordered]@{layer_name='L';frequency_hz=[decimal]1;epsilon_r=[decimal]1;epsilon_origin='o';epsilon_source_record_id='s';frequency_origin='o';frequency_source_record_id='s'};gap_pm=[int64]20;gap_um=[int64]0;stackup_layers=@()}; d115b=[ordered]@{status='PASS';disposition='PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED';contract_head=$RequiredHead;candidate=[ordered]@{path=$parentPath;present=$true;size_bytes=[int64]3;sha256=$parentSha;rail_count=[int64]0;terminal_count=[int64]0;rail_rows=@();terminal_rows=@()}}; manifest=[ordered]@{app_version=$Version;source_sha256=$parentSha;source_size_bytes=[int64]3;target_rail_id='rail'}; port44=[ordered]@{port_id='p';header='h';header_line=[int64]1;header_line_start=[int64]1;header_line_end=[int64]1;header_byte_start=[int64]0;header_byte_end=[int64]1;header_sha256=$parentSha;section_line_start=[int64]1;section_line_end=[int64]1;section_line_count=[int64]1;section_byte_start=[int64]0;section_byte_end=[int64]1;section_byte_bounds=[int64[]]@(0,1);section_sha256=$parentSha;section_terminator='end_port';positive_terminals=@();positive_count=[int64]0;positive_sequence_sha256=$parentSha;positive_set_sha256=$parentSha;negative_count=[int64]0;negative_sequence_sha256=$parentSha;negative_set_sha256=$parentSha;selected_negative_terminals=@();selected_negative_count=[int64]0}; rail_bindings=@(); terminal_bindings=@(); selected_via_source_authority=[ordered]@{status='PARTIAL';row_count=[int64]0;target_layer_traversal_proven=$false;scope=[ordered]@{barrel_proven=$false;antipad_proven=$false;land_proven=$false;intermediate_access_proven=$false;l29_l30_physical_pad_proven=$false;three_dimensional_geometry_proven=$false};rows=@()}; w0=[ordered]@{gap_pm=[int64]20;gap_um=[int64]0;padding_pm=[int64]0;padding_policy='p';largest_diameter_pm=[int64]0;raw_bounds_pm=[int64[]]@(0,0,0,0);grid_pm=[int64]1;snapped_bounds_pm=[int64[]]@(0,0,0,0);footprint_count=[int64]0;footprints=@()}; d104_cells=@(); scope=[ordered]@{source_local_shadow_only=$true;solver_executed=$false;powersi_executed=$false;generic_four_layer_model=$false;c_res=$false}; membership=[ordered]@{evaluated=$true;passed=$false;violations=@();terminals=@()}; empty_ordinals=[int64[]]@(259); coverage_conflict=[ordered]@{empty_ordinals=[int64[]]@(259);required_ordinals=[int64[]]@(259,260,261,262,263,264,265,266);all_eight_evaluated=$true}; code=$ExpectedCode }
        $receiptFixture.d103.dielectric_point['layer_ordinal'] = [int64]57; $receiptFixture.d103.dielectric_point['loss_tangent'] = [decimal]0.0041; $receiptFixture.d103.dielectric_point['loss_tangent_origin'] = 'material_model'; $receiptFixture.d103.dielectric_point['loss_tangent_source_record_id'] = 'material:ABF-GL102'; $receiptFixture.d103.dielectric_point['ordinal'] = [int64]168; $receiptFixture.d103.dielectric_point['point_ordinal'] = [int64]0; $receiptFixture.d103.layers = @($d103LayerFixture); $receiptFixture.d103.stackup_layers = @( (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ rows = @($d103LayerFixture) })) 'self-check d103 clone').rows ); $receiptFixture.d115b.candidate.rail_rows = @($railRowFixture); $receiptFixture.rail_bindings = @( (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ rows = @($railRowFixture) })) 'self-check rail clone').rows ); Assert-ReceiptSchema $receiptFixture; $receiptFixture.d103.dielectric_point.frequency_hz = 'bad'; $nestedRejected = $false; try { Assert-ReceiptSchema $receiptFixture } catch [InvalidOperationException] { $nestedRejected = $true }; if (-not $nestedRejected) { Fail 'receipt nested type mutation accepted' }; $receiptFixture.d103.dielectric_point.frequency_hz = [decimal]1; $receiptFixture['extra'] = $true; $receiptRejected = $false; try { Assert-ReceiptSchema $receiptFixture } catch [InvalidOperationException] { $receiptRejected = $true }; if (-not $receiptRejected) { Fail 'receipt extra key accepted' }
        $atomic = Join-Path $temp 'atomic.bin'; Write-CreateNewBytes $atomic ([byte[]](1,2,3)); try { Write-CreateNewBytes $atomic ([byte[]](9,9)); Fail 'second CreateNew unexpectedly succeeded' } catch [IO.IOException] { }; $atomicBytes = [IO.File]::ReadAllBytes($atomic); if ($atomicBytes.Length -ne 3 -or $atomicBytes[0] -ne 1 -or $atomicBytes[1] -ne 2 -or $atomicBytes[2] -ne 3) { Fail 'CreateNew clobbered data' }
        $boundJsonPath = Join-Path $temp 'bound.json'; Write-CreateNewBytes $boundJsonPath ([Text.UTF8Encoding]::new($false).GetBytes('{"bound":true}')); $boundJson = Read-BoundJsonFile $boundJsonPath 'self-check bound JSON'; if ($boundJson.Identity.SizeBytes -ne $boundJson.Bytes.Length -or $boundJson.Identity.Sha256 -ine (([BitConverter]::ToString((Get-Sha256Bytes $boundJson.Bytes)) -replace '-', '').ToLowerInvariant()) -or $boundJson.Parsed.bound -ne $true) { Fail 'same-byte identity binding failed' }
        $probeArgv = [string[]]@('probe','a b','q"r\z'); $probeBytes = Get-CompactJsonBytes $probeArgv; $probeObject = [ordered]@{ argv = $probeArgv; count = $probeArgv.Count }; $probeParsed = Read-StrictJsonBytes (Get-CompactJsonBytes $probeObject) 'self-check token'; Assert-DeepEqual $probeParsed $probeObject 'self-check token serialization'
        $receiptFixture.Remove('extra'); $layerTypeRejected = $false; try { $receiptFixture.d103.layers[0].conductivity_s_per_m = 'bad'; Assert-ReceiptSchema $receiptFixture } catch [InvalidOperationException] { $layerTypeRejected = $true }; $receiptFixture.d103.layers[0].conductivity_s_per_m = [decimal]59590000; if (-not $layerTypeRejected) { Fail 'receipt D103 layer type mutation accepted' }; $railTypeRejected = $false; try { $receiptFixture.d115b.candidate.rail_rows[0].pair_evidence_sha256 = 'bad'; Assert-ReceiptSchema $receiptFixture } catch [InvalidOperationException] { $railTypeRejected = $true }; $receiptFixture.d115b.candidate.rail_rows[0].pair_evidence_sha256 = 'c594a8c61f5e85dd65407ec13bdb2817a0e3bb0b416a9ecdaed9635f0d6cf8c9'; if (-not $railTypeRejected) { Fail 'receipt rail provenance mutation accepted' }
        $d103Second = (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ row = $d103LayerFixture })) 'self-check d103 second clone').row; $d103Second.ordinal = [int64]57; $d103Second.raw_layer_ordinal = [int64]57; $d103Second.layer_kind = 'dielectric'; $d103Second.conductivity_s_per_m = $null; $d103Second.conductivity_source_record_id = $null; $d103Third = (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ row = $d103LayerFixture })) 'self-check d103 third clone').row; $d103Third.ordinal = [int64]58; $d103Third.raw_layer_ordinal = [int64]58; $d103Rows = @($d103LayerFixture,$d103Second,$d103Third); $d103StackupRows = @( (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ rows = $d103Rows })) 'self-check d103 distinct side').rows ); Assert-DeepEqual $d103Rows $d103StackupRows 'self-check d103 duplicate'; for ($i = 0; $i -lt 3; $i++) { Assert-ExactInt $d103Rows[$i].ordinal ([int64](56 + $i)) "self-check d103 ordinal[$i]" }; Assert-ExactString $d103Rows[0].layer_kind 'conductor' 'self-check d103 kind[0]'; Assert-ExactString $d103Rows[1].layer_kind 'dielectric' 'self-check d103 kind[1]'; Assert-ExactString $d103Rows[2].layer_kind 'conductor' 'self-check d103 kind[2]'; $d103DuplicateRejected = $false; try { $d103StackupRows[1].layer_kind = 'conductor'; Assert-DeepEqual $d103Rows $d103StackupRows } catch [InvalidOperationException] { $d103DuplicateRejected = $true }; $d103StackupRows[1].layer_kind = 'dielectric'; if (-not $d103DuplicateRejected) { Fail 'self-check d103 duplicate mutation accepted' }
        $railSecond = (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ row = $railRowFixture })) 'self-check rail second clone').row; $railSecond.ordinal = [int64]1; $railSecond.artwork_net = 'DGND'; $railSecond.layer = 'Signal$L29(DGND)'; $railSecond.logical_net = 'DGND'; $railSecond.rail_id = 'DGND'; $railSecond.role = 'ground'; $railRows = @($railRowFixture,$railSecond); $railBindingsRows = @( (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ rows = $railRows })) 'self-check rail distinct side').rows ); Assert-DeepEqual $railRows $railBindingsRows 'self-check rail duplicate'; Assert-ExactInt $railRows[0].ordinal 0 'self-check rail ordinal[0]'; Assert-ExactInt $railRows[1].ordinal 1 'self-check rail ordinal[1]'; Assert-ExactString $railRows[0].role 'power' 'self-check rail role[0]'; Assert-ExactString $railRows[1].role 'ground' 'self-check rail role[1]'; Assert-ExactString $railRows[0].state 'source_bound' 'self-check rail state[0]'; Assert-ExactString $railRows[1].state 'source_bound' 'self-check rail state[1]'; $railDuplicateRejected = $false; try { $railBindingsRows[1].role = 'power'; Assert-DeepEqual $railRows $railBindingsRows } catch [InvalidOperationException] { $railDuplicateRejected = $true }; $railBindingsRows[1].role = 'ground'; if (-not $railDuplicateRejected) { Fail 'self-check rail duplicate mutation accepted' }
        $terminalRolesSemantic = [string[]]@('ground','power','ground','power','ground','power'); $terminalRowsSemantic = @(); $selectedRowsSemantic = @(); $membershipRowsSemantic = @(); $footprintRowsSemantic = @(); for ($i = 0; $i -lt 6; $i++) { $role = $terminalRolesSemantic[$i]; $layer = 'Signal$L29(DGND)'; if (($i % 2) -eq 1) { $layer = 'Signal$L30(OTHER_POWER1)' }; $nodeSource = "node-source:$i"; $paddefSource = "paddef-source:$i"; $regularSource = "regular-source:$i"; $terminalRowsSemantic += ,([ordered]@{ ordinal = [int64]$i; terminal_id = "terminal:$i"; pin_id = "pin:$i"; role = $role; owner_kind = 'device'; status = 'complete'; issues_json = '[]'; via_record_id = "via:$i"; padstack_id = 'DR-0102_60'; layer = $layer; endpoint_node_id = "Node$i"; source_node_record_id = $nodeSource; paddef_source_record_id = $paddefSource; regular_source_record_id = $regularSource }); $selectedRowsSemantic += ,([ordered]@{ terminal_id = "terminal:$i"; pin_id = "pin:$i"; via_record_id = "via:$i"; padstack_id = 'DR-0102_60'; selected_plane_layer = $layer; source_endpoint_node_id = "Node$i" }); $membershipRowsSemantic += ,([ordered]@{ pin_id = "pin:$i"; role = $role }); $footprintRowsSemantic += ,([ordered]@{ pin_id = "pin:$i"; source_records = @([ordered]@{ record_id = $nodeSource },[ordered]@{ record_id = $paddefSource },[ordered]@{ record_id = $regularSource }) }) }; $terminalBindingsSemantic = @( (Read-StrictJsonBytes (Get-CompactJsonBytes ([ordered]@{ rows = $terminalRowsSemantic })) 'self-check terminal distinct side').rows ); Assert-DeepEqual $terminalRowsSemantic $terminalBindingsSemantic 'self-check terminal duplicate'; for ($i = 0; $i -lt 6; $i++) { Assert-ExactInt $terminalRowsSemantic[$i].ordinal ([int64]$i) "self-check terminal ordinal[$i]"; Assert-ExactString $terminalRowsSemantic[$i].role $terminalRolesSemantic[$i] "self-check terminal role[$i]"; Assert-ExactString $terminalRowsSemantic[$i].owner_kind 'device' "self-check terminal owner[$i]"; Assert-ExactString $terminalRowsSemantic[$i].status 'complete' "self-check terminal status[$i]"; Assert-ExactString $terminalRowsSemantic[$i].issues_json '[]' "self-check terminal issues[$i]"; Assert-ExactString $selectedRowsSemantic[$i].terminal_id $terminalRowsSemantic[$i].terminal_id "self-check terminal selected id[$i]"; Assert-ExactString $selectedRowsSemantic[$i].pin_id $terminalRowsSemantic[$i].pin_id "self-check terminal selected pin[$i]"; Assert-ExactString $selectedRowsSemantic[$i].via_record_id $terminalRowsSemantic[$i].via_record_id "self-check terminal selected via[$i]"; Assert-ExactString $selectedRowsSemantic[$i].padstack_id $terminalRowsSemantic[$i].padstack_id "self-check terminal selected padstack[$i]"; Assert-ExactString $selectedRowsSemantic[$i].selected_plane_layer $terminalRowsSemantic[$i].layer "self-check terminal selected layer[$i]"; Assert-ExactString $selectedRowsSemantic[$i].source_endpoint_node_id $terminalRowsSemantic[$i].endpoint_node_id "self-check terminal selected endpoint[$i]"; Assert-ExactString $membershipRowsSemantic[$i].pin_id $terminalRowsSemantic[$i].pin_id "self-check membership pin[$i]"; Assert-ExactString $membershipRowsSemantic[$i].role $terminalRowsSemantic[$i].role "self-check membership role[$i]"; Assert-ExactString $footprintRowsSemantic[$i].pin_id $terminalRowsSemantic[$i].pin_id "self-check footprint pin[$i]"; Assert-ExactString $footprintRowsSemantic[$i].source_records[0].record_id $terminalRowsSemantic[$i].source_node_record_id "self-check footprint node[$i]"; Assert-ExactString $footprintRowsSemantic[$i].source_records[1].record_id $terminalRowsSemantic[$i].paddef_source_record_id "self-check footprint paddef[$i]"; Assert-ExactString $footprintRowsSemantic[$i].source_records[2].record_id $terminalRowsSemantic[$i].regular_source_record_id "self-check footprint regular[$i]" }; $terminalDuplicateRejected = $false; try { $terminalBindingsSemantic[0].status = 'failed'; Assert-DeepEqual $terminalRowsSemantic $terminalBindingsSemantic } catch [InvalidOperationException] { $terminalDuplicateRejected = $true }; $terminalBindingsSemantic[0].status = 'complete'; if (-not $terminalDuplicateRejected) { Fail 'self-check terminal duplicate mutation accepted' }; $terminalIdentityRejected = $false; try { $selectedRowsSemantic[0].pin_id = 'wrong'; Assert-ExactString $selectedRowsSemantic[0].pin_id $terminalRowsSemantic[0].pin_id 'self-check terminal identity' } catch [InvalidOperationException] { $terminalIdentityRejected = $true }; $selectedRowsSemantic[0].pin_id = 'pin:0'; if (-not $terminalIdentityRejected) { Fail 'self-check terminal identity mutation accepted' }
        $sourceSemantic = [ordered]@{ sha256 = $parentSha; wkb_sha256 = $parentSha; size_bytes = [int64]1; wkb_size_bytes = [int64]1; bounds_um = [int64[]]@(0,0,1,1); bbox_um = [int64[]]@(0,0,1,1) }; $intersectionSemantic = [ordered]@{ wkb_sha256 = $parentSha; wkb_size_bytes = [int64]1; bbox_um = [int64[]]@(0,0,1,1); area_um2 = [decimal]1; nonempty = $true; boundary_contact = $true }; $cellSemantic = [ordered]@{ source_wkb = $sourceSemantic; intersection = $intersectionSemantic; intersection_wkb_sha256 = $parentSha; intersection_wkb_size_bytes = [int64]1; intersection_bbox_um = [int64[]]@(0,0,1,1); intersection_area_um2 = [decimal]1; boundary_contact = $true }; Assert-ExactString $cellSemantic.source_wkb.sha256 $cellSemantic.source_wkb.wkb_sha256 'self-check D104 source sha'; Assert-ExactInt $cellSemantic.source_wkb.size_bytes $cellSemantic.source_wkb.wkb_size_bytes 'self-check D104 source size'; Assert-DeepEqual $cellSemantic.source_wkb.bounds_um $cellSemantic.source_wkb.bbox_um 'self-check D104 source bbox'; Assert-ExactString $cellSemantic.intersection_wkb_sha256 $cellSemantic.intersection.wkb_sha256 'self-check D104 intersection sha'; Assert-ExactInt $cellSemantic.intersection_wkb_size_bytes $cellSemantic.intersection.wkb_size_bytes 'self-check D104 intersection size'; Assert-DeepEqual $cellSemantic.intersection_bbox_um $cellSemantic.intersection.bbox_um 'self-check D104 intersection bbox'; if ($cellSemantic.intersection_area_um2 -ne $cellSemantic.intersection.area_um2) { Fail 'self-check D104 area mismatch' }; Assert-ExactBoolean $cellSemantic.boundary_contact $cellSemantic.intersection.boundary_contact 'self-check D104 boundary'; $d104DuplicateRejected = $false; try { $cellSemantic.intersection_area_um2 = [decimal]2; if ($cellSemantic.intersection_area_um2 -ne $cellSemantic.intersection.area_um2) { Fail 'self-check D104 area mismatch caught' } } catch [InvalidOperationException] { $d104DuplicateRejected = $true }; $cellSemantic.intersection_area_um2 = [decimal]1; if (-not $d104DuplicateRejected) { Fail 'self-check D104 duplicate mutation accepted' }; $d104CoherenceRejected = $false; try { $cellSemantic.intersection.nonempty = $false; if ($null -ne $cellSemantic.intersection.wkb_sha256 -or $null -ne $cellSemantic.intersection_wkb_sha256 -or $cellSemantic.intersection.area_um2 -ne 0) { Fail 'self-check D104 empty coherence mutation accepted' } } catch [InvalidOperationException] { $d104CoherenceRejected = $true }; $cellSemantic.intersection.nonempty = $true; if (-not $d104CoherenceRejected) { Fail 'self-check D104 coherence mutation accepted' }
        $scalarSemantic = [ordered]@{ root_branch = 'main'; nested_branch = 'main'; section_bounds = [int64[]]@(10,20); section_start = [int64]10; section_end = [int64]20; d103_gap_pm = [int64]30; w0_gap_pm = [int64]30; d103_gap_um = [int64]30; w0_gap_um = [int64]30 }; Assert-ExactString $scalarSemantic.root_branch $scalarSemantic.nested_branch 'self-check git branch alias'; Assert-ExactInt $scalarSemantic.section_bounds[0] $scalarSemantic.section_start 'self-check section start alias'; Assert-ExactInt $scalarSemantic.section_bounds[1] $scalarSemantic.section_end 'self-check section end alias'; Assert-ExactInt $scalarSemantic.d103_gap_pm $scalarSemantic.w0_gap_pm 'self-check gap pm alias'; Assert-ExactInt $scalarSemantic.d103_gap_um $scalarSemantic.w0_gap_um 'self-check gap um alias'; $scalarRejected = $false; try { $scalarSemantic.nested_branch = 'other'; Assert-ExactString $scalarSemantic.root_branch $scalarSemantic.nested_branch 'self-check scalar alias' } catch [InvalidOperationException] { $scalarRejected = $true }; $scalarSemantic.nested_branch = 'main'; if (-not $scalarRejected) { Fail 'self-check scalar alias mutation accepted' }
        $footprintSemantic = [ordered]@{ pin_id = 'pin:0'; center_pm = [int64[]]@(10,20); radius_pm = [int64]3; diameter_pm = [int64]6; bbox_pm = [int64[]]@(7,17,13,23) }; $fpRadius = [int64]$footprintSemantic.radius_pm; if ($fpRadius -gt ([int64]::MaxValue / 2)) { Fail 'self-check footprint radius overflow' }; Assert-ExactInt $footprintSemantic.diameter_pm ([int64]($fpRadius + $fpRadius)) 'self-check footprint diameter'; Assert-ExactInt $footprintSemantic.bbox_pm[0] ([int64]($footprintSemantic.center_pm[0] - $fpRadius)) 'self-check footprint bbox left'; Assert-ExactInt $footprintSemantic.bbox_pm[1] ([int64]($footprintSemantic.center_pm[1] - $fpRadius)) 'self-check footprint bbox bottom'; Assert-ExactInt $footprintSemantic.bbox_pm[2] ([int64]($footprintSemantic.center_pm[0] + $fpRadius)) 'self-check footprint bbox right'; Assert-ExactInt $footprintSemantic.bbox_pm[3] ([int64]($footprintSemantic.center_pm[1] + $fpRadius)) 'self-check footprint bbox top'; $footprintRejected = $false; try { $footprintSemantic.diameter_pm = [int64]7; Assert-ExactInt $footprintSemantic.diameter_pm ([int64]($fpRadius + $fpRadius)) 'self-check footprint diameter mutation' } catch [InvalidOperationException] { $footprintRejected = $true }; $footprintSemantic.diameter_pm = [int64]6; if (-not $footprintRejected) { Fail 'self-check footprint geometry mutation accepted' }
        $pwsh = Get-CurrentPowerShellPath; $quotedA = "'a b'"; $quotedB = "'q`"r`\z'"
        $payload = '& { param($a,$b); if($a -cne ''a b'' -or $b -cne ''q"r\z''){exit 19}; [Console]::Out.Write(("ARGS=[{0}]|[{1}]|{2}" -f $a,$b,("O"*256))); [Console]::Error.Write("E"*256); Start-Sleep -Seconds 10 }'
        $child = New-ConfiguredProcess ([string[]]@($pwsh,'-NoProfile','-NonInteractive','-Command',$payload,$quotedA,$quotedB)) $temp $false; $runState = New-ProcessRunState
        $run = Invoke-BoundedProcess $child $runState 0.35 64; $orphanClean = $child.HasExited
        if (-not $orphanClean -or -not $run.TimedOut -or $run.TimeoutSignalElapsedSeconds -lt 0.20 -or $run.TimeoutSignalElapsedSeconds -gt 1.50 -or $run.Stdout.Total -le 64 -or $run.Stderr.Total -le 64 -or -not $run.Stdout.Truncated -or -not $run.Stderr.Truncated -or -not $run.Stdout.Text.ToString().StartsWith('ARGS=[a b]|[q"r\z]')) { $child.Dispose(); Fail 'bounded process SelfCheck failed' }
        $child.Dispose()
        $receiptExpectationFixture = [ordered]@{ receipt_size_bytes_exact = $RequiredReceiptSizeBytes; receipt_sha256_exact = $RequiredReceiptSha256 }; Assert-ExactInt $receiptExpectationFixture.receipt_size_bytes_exact $RequiredReceiptSizeBytes 'self-check expected receipt size'; Assert-ExactString $receiptExpectationFixture.receipt_sha256_exact $RequiredReceiptSha256 'self-check expected receipt sha'; $receiptDigestRejected = $false; try { $receiptExpectationFixture.receipt_sha256_exact = $parentSha; Assert-ExactString $receiptExpectationFixture.receipt_sha256_exact $RequiredReceiptSha256 'self-check expected receipt digest mutation' } catch [InvalidOperationException] { $receiptDigestRejected = $true }; $receiptExpectationFixture.receipt_sha256_exact = $RequiredReceiptSha256; if (-not $receiptDigestRejected) { Fail 'self-check receipt digest mutation accepted' }; $receiptSizeRejected = $false; try { $receiptExpectationFixture.receipt_size_bytes_exact = [int64]($RequiredReceiptSizeBytes + 1); Assert-ExactInt $receiptExpectationFixture.receipt_size_bytes_exact $RequiredReceiptSizeBytes 'self-check expected receipt size mutation' } catch [InvalidOperationException] { $receiptSizeRejected = $true }; $receiptExpectationFixture.receipt_size_bytes_exact = $RequiredReceiptSizeBytes; if (-not $receiptSizeRejected) { Fail 'self-check receipt size mutation accepted' }; $gitBindingFixture = [ordered]@{ branch = $RequiredBranch }; Assert-ExactString $gitBindingFixture.branch $RequiredBranch 'self-check git branch'; $branchRejected = $false; try { $gitBindingFixture.branch = 'other'; Assert-ExactString $gitBindingFixture.branch $RequiredBranch 'self-check git branch mutation' } catch [InvalidOperationException] { $branchRejected = $true }; $gitBindingFixture.branch = $RequiredBranch; if (-not $branchRejected) { Fail 'self-check git branch mutation accepted' }
        $evidence = Get-RunEvidence $run; if ($evidence.process.timeout_signal_elapsed_seconds -le 0 -or $evidence.stdout.stored -ne 64 -or $evidence.stderr.stored -ne 64) { Fail 'bounded evidence SelfCheck failed' }
        $astTokens = $null; $astErrors = $null; $ast = [Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$astTokens, [ref]$astErrors); if (@($astErrors).Count -ne 0) { Fail 'self-check parser found syntax errors' }; if (@($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ieq 'if' }, $true)).Count -ne 0) { Fail 'self-check parsed if as command' }
        [Console]::Out.WriteLine(([ordered]@{ banner = $Banner; result = 'PASS'; stdout_total = $run.Stdout.Total; stdout_stored = $run.Stdout.Stored; stdout_truncated = $run.Stdout.Truncated; stderr_total = $run.Stderr.Total; stderr_stored = $run.Stderr.Stored; stderr_truncated = $run.Stderr.Truncated; timed_out = $run.TimedOut; timeout_signal_elapsed_seconds = $run.TimeoutSignalElapsedSeconds; orphan_clean = $orphanClean } | ConvertTo-Json -Compress))
        return 0
    } finally {
        if ((Resolve-Absolute $temp).StartsWith($tempRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $temp)) { Remove-Item -LiteralPath $temp -Recurse -Force }
    }
}

function Invoke-Normal {
    if ([string]::IsNullOrWhiteSpace($ApprovalPath) -or [string]::IsNullOrWhiteSpace($HqAuthorizationPath)) { Fail 'normal mode requires -ApprovalPath and -HqAuthorizationPath' }
    $approvalCandidate = Resolve-Absolute $ApprovalPath; $hqCandidate = Resolve-Absolute $HqAuthorizationPath
    if ((Split-Path -Leaf $approvalCandidate) -cne $ApprovalLeaf -or (Split-Path -Leaf $hqCandidate) -cne $HqLeaf) { Fail 'anchor leaf name mismatch' }
    Assert-NotTerminalRoot (Split-Path -Parent $approvalCandidate); Assert-NotTerminalRoot (Split-Path -Parent $hqCandidate)
    $approvalFull = Assert-NoReparse $approvalCandidate $true; $hqFull = Assert-NoReparse $hqCandidate $true
    if ((Resolve-Absolute (Split-Path -Parent $approvalFull)) -cne (Resolve-Absolute (Split-Path -Parent $hqFull)) -or $approvalFull -ieq $hqFull) { Fail 'anchors must be distinct files in one root' }
    $root = Get-Directory (Split-Path -Parent $approvalFull); Assert-NotTerminalRoot $root; Assert-RootInventory $root $approvalFull $hqFull $false
    $repo = Resolve-Absolute (Join-Path $PSScriptRoot '..\..'); if ($repo -cne $RequiredRepo) { Fail 'purpose-specific repository path mismatch' }; $cwd = Resolve-Absolute (Get-Location).Path; if ($cwd -cne $RequiredRepo) { Fail 'current invocation cwd must be repository root' }; Assert-NoReparse $repo $true
    $approvalBound = Read-BoundJsonFile $approvalFull 'approval'; $approval = $approvalBound.Parsed; $approvalInfo = Assert-Approval $approval $approvalFull $root $repo $cwd $approvalBound.Identity
    $controllerPath = Resolve-Absolute $PSCommandPath; $controller = Get-FileIdentity $controllerPath; $git = Invoke-GitIdentity $repo $RequiredHead
    $hqBound = Read-BoundJsonFile $hqFull 'HQ authorization'; $hq = $hqBound.Parsed; $hqIdentity = Assert-Hq $hq $hqFull $approvalInfo $controllerPath $controller $root $hqBound.Identity
    $receiptPath = $approvalInfo.ReceiptPath; $tokenPath = Join-Path $root $TokenName
    $preTokenGit = Invoke-GitIdentity $repo $RequiredHead; if ($preTokenGit.Branch -cne $RequiredBranch) { Fail 'pre-token git branch mismatch' }
    if ((Resolve-Absolute $hq.attempt_token.absolute_path) -cne (Resolve-Absolute $tokenPath) -or (Test-Path -LiteralPath $receiptPath) -or (Test-Path -LiteralPath $tokenPath)) { Fail 'token/output must be absent or incorrectly bound before mutation' }
    $expected = New-ExpectedArgv $approval $root $repo $approvalInfo.Inputs $approvalInfo.Python $receiptPath
    if ($null -eq $approval.argv -or @($approval.argv).Count -ne 44 -or @($approval.argv | Where-Object { $_ -isnot [string] }).Count -ne 0) { Fail 'approval argv must contain exactly 44 string tokens' }
    Compare-Argv ([string[]]$approval.argv) $expected 'approval argv'
    $logicalArgvBytes = Get-CompactJsonBytes $expected; $logicalArgvSha = ([BitConverter]::ToString((Get-Sha256Bytes $logicalArgvBytes)) -replace '-', '').ToLowerInvariant()
    $process = New-ConfiguredProcess $expected $repo $true; $runState = New-ProcessRunState
    $approvalBinding = [ordered]@{ path = $approvalInfo.ApprovalIdentity.Path; size_bytes = $approvalInfo.ApprovalIdentity.SizeBytes; sha256 = $approvalInfo.ApprovalIdentity.Sha256 }
    $hqBinding = [ordered]@{ path = $hqIdentity.Path; size_bytes = $hqIdentity.SizeBytes; sha256 = $hqIdentity.Sha256 }
    $controllerBinding = [ordered]@{ path = $controller.Path; size_bytes = $controller.SizeBytes; sha256 = $controller.Sha256 }
    $tokenObject = [ordered]@{ schema = 'd117-wp3-selected-via-source-authority-attempt-v1'; product = [ordered]@{ name = $Product; version = $Version }; authorization_id = $approval.authorization.authorization_id; approval = $approvalBinding; hq_authorization = $hqBinding; controller = $controllerBinding; utc_timestamp = [DateTime]::UtcNow.ToString('O'); attempt = 1; retries = 0; argv_count = $expected.Count; argv = $expected; argv_sha256 = $logicalArgvSha; argv_hash_convention = 'System.Text.Json.JsonSerializer.Serialize(String[] argv), compact UTF-8 without BOM'; expected_auditor_exit = 1; expected_code = $ExpectedCode }
    $implementationBefore = Get-FileIdentity $approvalInfo.Implementation.Path; $pythonBefore = Get-FileIdentity $approvalInfo.Python.Path; if ($implementationBefore.Path -cne $approvalInfo.Implementation.Path -or $pythonBefore.Path -cne $approvalInfo.Python.Path -or (Get-FileVersion $pythonBefore.Path) -cne ([string]$approval.python_binding.file_version).Trim()) { Fail 'auditor/Python identity changed before token' }; Assert-SameIdentity $implementationBefore $approvalInfo.Implementation 'auditor before start'; Assert-SameIdentity $pythonBefore $approvalInfo.Python 'Python before start'
    $run = $null
    $tokenBytes = Get-CompactJsonBytes $tokenObject; [void](Read-StrictJsonBytes $tokenBytes 'attempt token'); Write-CreateNewBytes $tokenPath $tokenBytes
    try {
        $run = Invoke-BoundedProcess $process $runState $HardWallSeconds $CaptureCap
        $tokenAfter = $null; $receiptAfter = $null; $receipt = $null
        try {
            $approvalAfter = Get-FileIdentity $approvalFull; $hqAfter = Get-FileIdentity $hqFull; $controllerAfter = Get-FileIdentity $controllerPath
            if ($approvalAfter.Sha256 -ine $approvalInfo.ApprovalIdentity.Sha256 -or $hqAfter.Sha256 -ine $hqIdentity.Sha256 -or $controllerAfter.Sha256 -ine $controller.Sha256) { Fail 'postflight immutable anchor/controller identity mismatch' }
            $implementationAfter = Get-FileIdentity $approvalInfo.Implementation.Path; $pythonAfter = Get-FileIdentity $approvalInfo.Python.Path; if ((Get-FileVersion $pythonAfter.Path) -cne ([string]$approval.python_binding.file_version).Trim()) { Fail 'Python version changed after child' }; Assert-SameIdentity $implementationAfter $implementationBefore 'auditor after child'; Assert-SameIdentity $pythonAfter $pythonBefore 'Python after child'
            $tokenAfter = Assert-Token $tokenPath $tokenBytes $tokenObject
            Assert-RootInventory $root $approvalFull $hqFull $true
            if ($run.TimedOut -or $run.ExitCode -ne 1 -or $run.Stderr.Truncated -or $run.Stdout.Truncated) { Fail 'child orchestration did not meet expected wall/exit contract' }
            $receiptBound = Read-BoundJsonFile $receiptPath 'auditor receipt'; $receiptBytes = $receiptBound.Bytes; $receipt = $receiptBound.Parsed; $receiptAfter = $receiptBound.Identity; $approvedReceiptSize = [int64]$approval.expected_scientific_result.receipt_size_bytes_exact; $approvedReceiptSha = [string]$approval.expected_scientific_result.receipt_sha256_exact; if ($receiptAfter.SizeBytes -ne $approvedReceiptSize -or $receiptAfter.Sha256 -ine $approvedReceiptSha -or $receiptAfter.SizeBytes -ne [int64]$hq.execution.expected_result.receipt_size_bytes_exact -or $receiptAfter.Sha256 -ine [string]$hq.execution.expected_result.receipt_sha256_exact) { Fail 'auditor receipt bound identity mismatch exact approval/HQ digest' }; [void](Assert-Receipt $receipt $receiptPath $approval $approvalInfo.Inputs $repo $RequiredHead $approvalInfo.D104Root)
            # Raw auditor exit 1 is expected scientific evidence; controller exit 0 means that STOP was orchestrated and verified.
            Write-RunEvidence $run $tokenAfter $receiptAfter $receipt $false 'PASS_EXPECTED_STOP'; return 0
        } catch {
            if ($null -eq $run) { $exitAfter = $null; try { if ($process.HasExited) { $exitAfter = $process.ExitCode } } catch { }; $run = [pscustomobject]@{ ExitCode = $exitAfter; TimedOut = $null -ne $runState.TimeoutSignalElapsedSeconds; ElapsedSeconds = $runState.Stopwatch.Elapsed.TotalSeconds; TimeoutSignalElapsedSeconds = $runState.TimeoutSignalElapsedSeconds; Stdout = $runState.Stdout; Stderr = $runState.Stderr } }
            $evidenceToken = $tokenAfter; if ($null -eq $evidenceToken -and (Test-Path -LiteralPath $tokenPath)) { try { $evidenceToken = Get-FileIdentity $tokenPath } catch { } }
            $evidenceReceipt = $receiptAfter; if ($null -eq $evidenceReceipt -and (Test-Path -LiteralPath $receiptPath)) { try { $evidenceReceipt = Get-FileIdentity $receiptPath } catch { } }
            Write-RunEvidence $run $evidenceToken $evidenceReceipt $receipt $true; throw
        }
    } finally { $process.Dispose() }
}

try {
    if ($SelfCheck) { if ($ApprovalPath -or $HqAuthorizationPath) { Fail 'SelfCheck cannot receive normal-mode anchors' }; exit (Invoke-SelfCheck) }
    exit (Invoke-Normal)
} catch {
    # Controller exit 2 is refusal/orchestration failure, including timeout, mismatch, and missing receipt.
    [Console]::Error.WriteLine("$Banner REFUSED: $($_.Exception.Message)")
    exit 2
}
