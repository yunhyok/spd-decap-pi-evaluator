[CmdletBinding()]
param(
    [switch]$SelfCheck,
    [string]$HqAuthorizationPath,
    [int64]$HqAuthorizationSizeBytes,
    [string]$HqAuthorizationSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Product = 'SPD Decap PI Evaluator'
$Version = '0.23.1'
$Banner = "$Product v$Version"
$HqSchema = 'd117-wp3-selected-pin-source-path-absence-hq-authorization-v1'
$TokenName = 'd117_wp3_selected_pin_source_path_absence_attempt.json'
$CertificateName = 'd117_wp3_selected_pin_source_path_absence_certificate.json'
$ControllerReceiptName = 'd117_wp3_selected_pin_source_path_absence_controller_receipt.json'
$BuilderRelative = 'tools/research/build_d117_wp3_selected_pin_source_path_absence.py'
$TestRelative = 'tests/test_d117_wp3_selected_pin_source_path_absence.py'
$RequiredRepo = 'C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator'
$RequiredHead = 'e2f219e71d8c8a397009f72242cce10d78cfc7ab'
$RequiredBranch = 'main'
$InputRoot = 'D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-wp3-selected-pin-source-path-absence-02'
$InputName = 'd117_wp3_selected_pin_source_path_absence_input.json'
$InputSize = [int64]23049
$InputSha = '01b281f459da111ba908c1de9924b493881788a79eff3ef607036f7c1738ce56'
$BuilderSize = [int64]53470
$BuilderSha = '8961814ab1d8bd511be6d1fae56c405c26a8ea225d9d751c5491980f35342063'
$TestSize = [int64]29723
$TestSha = 'ed41fd65e2e3791a19cccddb0089c317d60188a07be7737cda32c91f08154cb6'
$CertificateSize = [int64]52161
$CertificateSha = '581ca0606b103b0ae6c3e4d25d410f31364f6fcfe659e0bcb82af7ba9ccb3310'
$RawSize = [int64]1116717287
$RawSha = '40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2'
$D115BSize = [int64]514208
$D115BSha = 'c69dce134ca02ff263b75f5df930106a7d8d75d05cccf2acd13b7d9b1919aa49'
$D115CSize = [int64]1063056
$D115CSha = '0c8daed46719b199ee50b1ec9b94dd5cac0fdedecbc7b58668b7665b5e9a2a80'
$Wp305Size = [int64]1321085
$Wp305Sha = '5800f801467680af8e21f8638650e238df0394296d3aa8d3caf6084a39183371'
$HardWallSeconds = [double]600
$CaptureCap = [int64]65536
$ChunkChars = 4096
$ExpectedPins = [string[]]@('SITE0:20612','SITE0:19973')
$ExpectedTargetLayers = [string[]]@('Signal$L29(DGND)','Signal$L30(OTHER_POWER1)')
$ExpectedTerminalLayers = [string[]]@('Signal$L18(DGND)','Signal$L20(DGND)')
  $ExpectedReversedTraces = [string[]]@('Trace1258306','Trace1258307','Trace1275750','Trace1275751')
$ProofNames = [string[]]@('three_dimensional_geometry_proven','barrel_proven','antipad_proven','land_proven','intermediate_access_proven','target_layer_traversal_proven','l29_l30_physical_pad_proven','physical_geometry_proven','physical_traversal_proven')
$SourceExpected = [ordered]@{
    raw_spd = [ordered]@{ path = 'D:\S4LB002-2Para_260729_1_injected.spd'; size_bytes = $RawSize; sha256 = $RawSha }
    d115b = [ordered]@{ path = 'D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\d115b_source_plane_ownership_materialization_receipt.json'; size_bytes = $D115BSize; sha256 = $D115BSha }
    d115c = [ordered]@{ path = 'D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_source_local_port_window_receipt.json'; size_bytes = $D115CSize; sha256 = $D115CSha }
    wp3_05 = [ordered]@{ path = 'D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp3-selected-via-source-authority-05\d117_wp3_selected_via_source_authority_receipt.json'; size_bytes = $Wp305Size; sha256 = $Wp305Sha }
}

try { $Host.UI.RawUI.WindowTitle = $Banner } catch { }

function Fail([string]$Message) { throw [InvalidOperationException]::new($Message) }

function Ensure-SystemTextJson {
    if ($null -ne ('System.Text.Json.JsonDocument' -as [type])) {
        try {
            $probe = [Text.Json.JsonDocument]::Parse('{"probe":true}'); $probe.Dispose()
            $probeOptions = [Text.Json.JsonSerializerOptions]::new(); [Text.Json.JsonSerializer]::Serialize([ordered]@{ probe = $true }, $probeOptions) | Out-Null
            return
        } catch { }
    }
    $roots = @($env:ProgramFiles, $env:ProgramW6432) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
    foreach ($base in $roots) {
        $sdkRoot = Join-Path $base 'dotnet\sdk'
        foreach ($sdk in @(Get-ChildItem -LiteralPath $sdkRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            $candidate = Join-Path $sdk.FullName 'Sdks\Microsoft.NET.Sdk\tools\net472\System.Text.Json.dll'
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
            $assemblyDir = Split-Path -Parent $candidate
            $loaded = [Collections.Generic.Dictionary[string,Reflection.Assembly]]::new([StringComparer]::OrdinalIgnoreCase)
            $resolverScript = {
                param($sender,$event)
                $simpleName = ([string]$event.Name).Split(',')[0]
                if ($loaded.ContainsKey($simpleName)) { return $loaded[$simpleName] }
                return $null
            }.GetNewClosure()
            $resolver = [ResolveEventHandler]$resolverScript
            [AppDomain]::CurrentDomain.add_AssemblyResolve($resolver)
            $ready = $false
            try {
                foreach ($name in @('System.Runtime.CompilerServices.Unsafe.dll','System.Memory.dll','System.Buffers.dll','System.Text.Encodings.Web.dll','Microsoft.Bcl.AsyncInterfaces.dll','System.Threading.Tasks.Extensions.dll','System.ValueTuple.dll','System.Numerics.Vectors.dll')) {
                    $dep = Join-Path $assemblyDir $name
                    if (Test-Path -LiteralPath $dep -PathType Leaf) {
                        try { $assembly = [Reflection.Assembly]::LoadFrom($dep); $loaded[$assembly.GetName().Name] = $assembly } catch { }
                    }
                }
                try { $jsonAssembly = [Reflection.Assembly]::LoadFrom($candidate); $loaded[$jsonAssembly.GetName().Name] = $jsonAssembly } catch { }
                if ($null -ne ('System.Text.Json.JsonDocument' -as [type])) {
                    $probe = [Text.Json.JsonDocument]::Parse('{"probe":true}'); $probe.Dispose()
                    $probeOptions = [Text.Json.JsonSerializerOptions]::new(); [Text.Json.JsonSerializer]::Serialize([ordered]@{ probe = $true }, $probeOptions) | Out-Null
                    $ready = $true
                }
            } catch { }
            finally { [AppDomain]::CurrentDomain.remove_AssemblyResolve($resolver) }
            if ($ready) { return }
        }
    }
    Fail 'System.Text.Json runtime unavailable'
}

try { Ensure-SystemTextJson } catch { [Console]::Error.WriteLine("$Banner REFUSED: $($_.Exception.Message)"); exit 2 }

try {
    if ($null -eq ('D117Wp3Capture' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
public sealed class D117Wp3Capture {
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
'@
    }
} catch { [Console]::Error.WriteLine("$Banner REFUSED: stream-capture runtime unavailable"); exit 2 }

try {
    if ($null -eq ('D117Wp3Native' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class D117Wp3Native {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateHardLink(string newFileName, string existingFileName, IntPtr securityAttributes);
    public static bool Link(string newFileName, string existingFileName) { return CreateHardLink(newFileName, existingFileName, IntPtr.Zero); }
}
'@
    }
} catch { [Console]::Error.WriteLine("$Banner REFUSED: atomic-link runtime unavailable"); exit 2 }

function Resolve-Absolute([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { Fail 'absolute path required' }
    try { $full = [IO.Path]::GetFullPath($Path) } catch { Fail "invalid path: $Path" }
    $root = [IO.Path]::GetPathRoot($full)
    if ([string]::IsNullOrEmpty($root) -or $root -eq '\' -or ($root.Length -eq 2 -and $root[1] -eq ':')) { Fail "absolute path required: $Path" }
    if ($full.Length -gt $root.Length) { $full = $full.TrimEnd('\','/') }
    return $full
}

function Assert-NoReparse([string]$Path, [bool]$RequireFinal = $true) {
    $full = Resolve-Absolute $Path; $root = [IO.Path]::GetPathRoot($full); $current = $root
    $tail = $full.Substring($root.Length).Trim('\','/')
    foreach ($part in @($tail -split '[\\/]' | Where-Object { $_ })) {
        $current = [IO.Path]::Combine($current, $part)
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail "reparse point rejected: $full" }
        } elseif ($RequireFinal -and ([IO.Path]::GetFullPath($current) -ieq $full)) { Fail "missing path: $full" }
    }
    if ($RequireFinal -and -not (Test-Path -LiteralPath $full)) { Fail "missing path: $full" }
    return $full
}

function Hash-Bytes([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create(); try { return ([BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace '-', '').ToLowerInvariant() } finally { $sha.Dispose() }
}

function Get-FileIdentity([string]$Path) {
    $full = Assert-NoReparse $Path $true; $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer) { Fail "ordinary file required: $full" }
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $sha = [Security.Cryptography.SHA256]::Create(); $buffer = [byte[]]::new(1048576); [int64]$count = 0
    try { while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) { [void]$sha.TransformBlock($buffer,0,$read,$null,0); $count += $read }; [void]$sha.TransformFinalBlock([byte[]]::new(0),0,0); return [pscustomobject]@{ Path = $full; SizeBytes = $count; Sha256 = ([BitConverter]::ToString($sha.Hash) -replace '-', '').ToLowerInvariant() } } finally { $sha.Dispose(); $stream.Dispose() }
}

function Get-FileVersion([string]$Path) {
    $full = Assert-NoReparse $Path $true; $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer) { Fail "ordinary file required: $full" }
    return ([string]$item.VersionInfo.FileVersion).Trim()
}

function New-IdentityRecord($Identity,[string]$FileVersion = $null) {
    $record = [ordered]@{ path = [string]$Identity.Path; size_bytes = [int64]$Identity.SizeBytes; sha256 = ([string]$Identity.Sha256).ToLowerInvariant() }
    if (-not [string]::IsNullOrWhiteSpace([string]$FileVersion)) { $record.file_version = [string]$FileVersion }
    return $record
}

function Read-BoundedFileBytes([string]$Path,[int64]$ExpectedSize,[string]$Label) {
    $full = Assert-NoReparse $Path $true; $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer) { Fail "$Label must be an ordinary file" }
    $stream = [IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($stream.Length -ne $ExpectedSize) { Fail "$Label size mismatch" }
        if ($stream.Length -gt [int32]::MaxValue) { Fail "$Label is too large" }
        $bytes = [byte[]]::new([int]$stream.Length); $offset = 0
        while ($offset -lt $bytes.Length) { $read = $stream.Read($bytes,$offset,$bytes.Length-$offset); if ($read -le 0) { Fail "$Label changed while reading" }; $offset += $read }
        return [pscustomobject]@{ Path = $full; Bytes = $bytes; SizeBytes = [int64]$bytes.Length; Sha256 = (Hash-Bytes $bytes); Stream = $stream }
    } catch { $stream.Dispose(); throw }
}

function Assert-BytesExact([string]$Path,[byte[]]$ExpectedBytes,[string]$Label) {
    $bound = Read-BoundedFileBytes $Path ([int64]$ExpectedBytes.Length) $Label
    try {
        $expectedSha = Hash-Bytes $ExpectedBytes
        if ($bound.Sha256 -cne $expectedSha) { Fail "$Label serialized bytes hash mismatch" }
        for ($i = 0; $i -lt $ExpectedBytes.Length; $i++) { if ($bound.Bytes[$i] -ne $ExpectedBytes[$i]) { Fail "$Label serialized bytes mismatch" } }
        return [pscustomobject]@{ Path = $bound.Path; SizeBytes = $bound.SizeBytes; Sha256 = $bound.Sha256 }
    } finally { $bound.Stream.Dispose() }
}

function Assert-RetainedBoundUnchanged($Bound,[string]$Label) {
    if ($null -eq $Bound -or $null -eq $Bound.Stream) { Fail "$Label retained handle missing" }
    $stream = $Bound.Stream; $position = $stream.Position
    try {
        if ($stream.Length -ne $Bound.Bytes.Length) { Fail "$Label changed while retained" }
        [void]$stream.Seek(0,[IO.SeekOrigin]::Begin); $bytes = [byte[]]::new($Bound.Bytes.Length); $offset = 0
        while ($offset -lt $bytes.Length) { $read = $stream.Read($bytes,$offset,$bytes.Length-$offset); if ($read -le 0) { Fail "$Label changed while retained" }; $offset += $read }
        if ((Hash-Bytes $bytes) -cne [string]$Bound.Identity.Sha256) { Fail "$Label changed while retained" }
        for ($i = 0; $i -lt $bytes.Length; $i++) { if ($bytes[$i] -ne $Bound.Bytes[$i]) { Fail "$Label changed while retained" } }
        return [pscustomobject]@{ Path = $Bound.Identity.Path; SizeBytes = [int64]$bytes.Length; Sha256 = ([string]$Bound.Identity.Sha256).ToLowerInvariant() }
    } finally { [void]$stream.Seek($position,[IO.SeekOrigin]::Begin) }
}

function Assert-IdentityUnchanged($Before,$After,[string]$Label) {
    if ($null -eq $Before -or $null -eq $After) { Fail "$Label identity missing" }
    foreach ($key in @('path','size_bytes','sha256')) {
        $beforeValue = if ($Before -is [Collections.IDictionary]) { $Before[$key] } else { $Before.PSObject.Properties[$key].Value }
        $afterValue = if ($After -is [Collections.IDictionary]) { $After[$key] } else { $After.PSObject.Properties[$key].Value }
        if ([string]$beforeValue -cne [string]$afterValue) { Fail "$Label $key changed" }
    }
    $beforeVersion = if ($Before -is [Collections.IDictionary]) { $Before['file_version'] } elseif ($Before.PSObject.Properties['file_version']) { $Before.PSObject.Properties['file_version'].Value } else { $null }
    $afterVersion = if ($After -is [Collections.IDictionary]) { $After['file_version'] } elseif ($After.PSObject.Properties['file_version']) { $After.PSObject.Properties['file_version'].Value } else { $null }
    if ($null -ne $beforeVersion -or $null -ne $afterVersion) {
        if ([string]$beforeVersion -cne [string]$afterVersion) { Fail "$Label file_version changed" }
    }
}

function Assert-SnapshotUnchanged($Before,$After) {
    foreach ($name in @('hq','controller','builder','test','input','python')) { Assert-IdentityUnchanged $Before[$name] $After[$name] "postflight.$name" }
    foreach ($name in @('raw_spd','d115b','d115c','wp3_05')) { Assert-IdentityUnchanged $Before.sources[$name] $After.sources[$name] "postflight.source_bindings.$name" }
    foreach ($name in @('root','head','branch')) { if ([string]$Before.git[$name] -cne [string]$After.git[$name]) { Fail "postflight.git $name changed" } }
    Assert-ExactArray $After.input_root_inventory ([string[]]$Before.input_root_inventory) 'postflight.input-root inventory'
}

function Convert-JsonElement($Element) {
    switch ($Element.ValueKind) {
        Object { $result = [Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal); $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal); foreach ($p in $Element.EnumerateObject()) { if (-not $seen.Add($p.Name)) { Fail "duplicate JSON key: $($p.Name)" }; $result[$p.Name] = Convert-JsonElement $p.Value }; return $result }
        Array { $items = [Collections.Generic.List[object]]::new(); foreach ($v in $Element.EnumerateArray()) { [void]$items.Add((Convert-JsonElement $v)) }; return ,([object[]]$items.ToArray()) }
        String { return $Element.GetString() }
        True { return $true }
        False { return $false }
        Null { return $null }
        Number { $raw = $Element.GetRawText(); [long]$i = 0; if ([long]::TryParse($raw,[Globalization.NumberStyles]::Integer,[Globalization.CultureInfo]::InvariantCulture,[ref]$i)) { return $i }; [decimal]$d = 0; if ([decimal]::TryParse($raw,[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$d)) { return $d }; Fail 'invalid JSON number' }
        default { Fail 'unsupported JSON value kind' }
    }
}

function Read-StrictJsonBytes([byte[]]$Bytes, [string]$Label) {
    if ($null -eq $Bytes -or $Bytes.Length -eq 0) { Fail "empty $Label JSON" }
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xef -and $Bytes[1] -eq 0xbb -and $Bytes[2] -eq 0xbf) { Fail "BOM rejected in $Label" }
    try { $text = [Text.UTF8Encoding]::new($false,$true).GetString($Bytes) } catch { Fail "invalid UTF-8 in $Label" }
    $options = [Text.Json.JsonDocumentOptions]::new(); $options.CommentHandling = [Text.Json.JsonCommentHandling]::Disallow; $options.AllowTrailingCommas = $false
    try { $document = [Text.Json.JsonDocument]::Parse($text,$options) } catch { Fail "invalid $Label JSON" }
    try { if ($document.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) { Fail "$Label JSON root must be an object" }; return Convert-JsonElement $document.RootElement } finally { $document.Dispose() }
}

function Read-StrictJsonFile([string]$Path, [string]$Label) { return Read-StrictJsonBytes ([IO.File]::ReadAllBytes((Assert-NoReparse $Path $true))) $Label }

function Get-CompactJsonBytes($Object) {
    $options = [Text.Json.JsonSerializerOptions]::new(); $options.WriteIndented = $false; $options.Encoder = [Text.Encodings.Web.JavaScriptEncoder]::UnsafeRelaxedJsonEscaping
    return [Text.UTF8Encoding]::new($false,$true).GetBytes([Text.Json.JsonSerializer]::Serialize($Object,$options))
}

function Assert-ExactKeys($Object, [string[]]$Keys, [string]$Label) {
    if ($null -eq $Object -or $Object -isnot [Collections.IDictionary]) { Fail "$Label must be an object" }
    $actual = @($Object.Keys | ForEach-Object { [string]$_ }); $wanted = @($Keys)
    if ($actual.Count -ne $wanted.Count) { Fail "$Label keys failed" }
    foreach ($key in $wanted) { if (@($actual | Where-Object { $_ -ceq $key }).Count -ne 1) { Fail "$Label key mismatch: $key" } }
}
function Assert-String($Value,[string]$Label) { if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) { Fail "$Label must be a non-empty string" } }
function Assert-Sha([string]$Value,[string]$Label) { if ($Value -notmatch '^[0-9a-fA-F]{64}$') { Fail "$Label must be SHA-256" } }
function Assert-Int($Value,[string]$Label,[int64]$Min = [int64]::MinValue) { if ($Value -isnot [int64] -and $Value -isnot [int32]) { Fail "$Label must be integer" }; if ([int64]$Value -lt $Min) { Fail "$Label out of range" } }
function Assert-Bool($Value,[string]$Label) { if ($Value -isnot [bool]) { Fail "$Label must be Boolean" } }
function Assert-ExactString($Value,[string]$Expected,[string]$Label) { Assert-String $Value $Label; if (-not [StringComparer]::Ordinal.Equals([string]$Value,$Expected)) { Fail "$Label mismatch" } }
function Assert-ExactInt($Value,[int64]$Expected,[string]$Label) { Assert-Int $Value $Label; if ([int64]$Value -ne $Expected) { Fail "$Label mismatch" } }
function Assert-ExactBool($Value,[bool]$Expected,[string]$Label) { Assert-Bool $Value $Label; if (-not [bool]::Equals($Value,$Expected)) { Fail "$Label mismatch" } }
function Assert-StringArray($Value,[string]$Label) { if ($null -eq $Value -or $Value -isnot [array]) { Fail "$Label must be an array" }; foreach($v in $Value){Assert-String $v "$Label item"} }
function Assert-ExactArray($Value,[string[]]$Expected,[string]$Label) { Assert-StringArray $Value $Label; if ($Value.Count -ne $Expected.Count) { Fail "$Label count mismatch" }; for($i=0;$i -lt $Expected.Count;$i++){Assert-ExactString $Value[$i] $Expected[$i] "$Label[$i]"} }
function Assert-ExactStringSet($Value,[string[]]$Expected,[string]$Label) { Assert-StringArray $Value $Label; if ($Value.Count -ne $Expected.Count) { Fail "$Label count mismatch" }; $actualSet=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal); foreach($v in $Value){if(-not $actualSet.Add([string]$v)){Fail "$Label duplicate item"}}; foreach($v in $Expected){if(-not $actualSet.Contains([string]$v)){Fail "$Label missing or unexpected item"}} }

function Open-BoundJson([string]$Path,[int64]$ExpectedSize,[string]$ExpectedSha) {
    $full = Assert-NoReparse $Path $true; $item = Get-Item -LiteralPath $full -Force; if ($item.PSIsContainer) { Fail 'HQ authorization must be an ordinary file' }
    $stream = [IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($stream.Length -ne $ExpectedSize) { Fail 'HQ authorization size pin mismatch' }
        if ($stream.Length -gt [int32]::MaxValue) { Fail 'HQ authorization is too large' }
        $bytes = [byte[]]::new([int]$stream.Length); $offset = 0
        while ($offset -lt $bytes.Length) { $read = $stream.Read($bytes,$offset,$bytes.Length-$offset); if ($read -le 0) { Fail 'HQ authorization changed while reading' }; $offset += $read }
        $actualSha = Hash-Bytes $bytes
        if ($actualSha -cne ([string]$ExpectedSha).ToLowerInvariant()) { Fail 'HQ authorization SHA-256 pin mismatch' }
        $parsed = Read-StrictJsonBytes $bytes 'HQ authorization'
        return [pscustomobject]@{ Path = $full; Bytes = $bytes; Identity = [pscustomobject]@{ Path = $full; SizeBytes = [int64]$bytes.Length; Sha256 = $actualSha }; Parsed = $parsed; Stream = $stream }
    } catch { $stream.Dispose(); throw }
}

function Assert-IdentityRecord($Record,[string]$Label,$Expected = $null) {
    Assert-ExactKeys $Record @('path','size_bytes','sha256') $Label; Assert-String $Record.path "$Label.path"; Assert-Int $Record.size_bytes "$Label.size_bytes" 1; Assert-Sha $Record.sha256 "$Label.sha256"
    $path = Resolve-Absolute $Record.path; if ($null -ne $Expected) { if ($path -cne (Resolve-Absolute $Expected.path) -or [int64]$Record.size_bytes -ne [int64]$Expected.size_bytes -or $Record.sha256 -ine [string]$Expected.sha256) { Fail "$Label differs from sealed identity" } }
    $actual = Get-FileIdentity $path; if ($actual.Path -cne $path -or $actual.SizeBytes -ne [int64]$Record.size_bytes -or $actual.Sha256 -ine [string]$Record.sha256) { Fail "$Label actual identity mismatch" }; return $actual
}

function Invoke-GitIdentity([string]$Repo) {
    $root = ((& git -C $Repo rev-parse --show-toplevel 2>$null)|Out-String).Trim(); if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) { Fail 'git root lookup failed' }
    $head = ((& git -C $Repo rev-parse HEAD 2>$null)|Out-String).Trim(); $branch = ((& git -C $Repo rev-parse --abbrev-ref HEAD 2>$null)|Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -ine $RequiredHead -or $branch -cne $RequiredBranch -or (Resolve-Absolute $root) -cne $Repo) { Fail 'git identity mismatch' }
    return [pscustomobject]@{ Root = $Repo; Head = $head.ToLowerInvariant(); Branch = $branch }
}

function Assert-Inventory([string]$Root,[string[]]$Expected,[string]$Label) {
    $items = @(Get-ChildItem -LiteralPath $Root -Force); $names = @()
    foreach ($item in $items) { [void](Assert-NoReparse $item.FullName $true); if ($item.PSIsContainer) { Fail "$Label contains a directory" }; $names += $item.Name }
    if ($names.Count -ne $Expected.Count) { Fail "$Label count mismatch" }
    foreach ($name in $Expected) { if (@($names | Where-Object { $_ -ceq $name }).Count -ne 1) { Fail "$Label missing or unexpected entry: $name" } }
    return ,([string[]]($names | Sort-Object))
}

function Get-SafeInventory([string]$Root) {
    if ([string]::IsNullOrWhiteSpace($Root) -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return ,([string[]]@()) }
    try { return ,([string[]](@(Get-ChildItem -LiteralPath $Root -Force | ForEach-Object { $_.Name } | Sort-Object))) } catch { return ,([string[]]@()) }
}

function Assert-ScientificScope($Scope,[string]$Label) {
    Assert-ExactKeys $Scope @('mode','pins','target_layers','selected_chain_only','global_claim_authorized','physical_nonconnection_authorized','raw_spd_graph_scan','counts','proof_flags') $Label
    Assert-ExactString $Scope.mode 'preparation_only' "$Label.mode"; Assert-ExactArray $Scope.pins $ExpectedPins "$Label.pins"; Assert-ExactArray $Scope.target_layers $ExpectedTargetLayers "$Label.target_layers"
    Assert-ExactBool $Scope.selected_chain_only $true "$Label.selected_chain_only"; Assert-ExactBool $Scope.global_claim_authorized $false "$Label.global_claim_authorized"; Assert-ExactBool $Scope.physical_nonconnection_authorized $false "$Label.physical_nonconnection_authorized"; Assert-ExactBool $Scope.raw_spd_graph_scan $false "$Label.raw_spd_graph_scan"
    Assert-ExactKeys $Scope.counts @('pins','nodes','edges','vias','traces','padstacks') "$Label.counts"; Assert-ExactInt $Scope.counts.pins 2 "$Label.counts.pins"; Assert-ExactInt $Scope.counts.nodes 44 "$Label.counts.nodes"; Assert-ExactInt $Scope.counts.edges 42 "$Label.counts.edges"; Assert-ExactInt $Scope.counts.vias 36 "$Label.counts.vias"; Assert-ExactInt $Scope.counts.traces 6 "$Label.counts.traces"; Assert-ExactInt $Scope.counts.padstacks 19 "$Label.counts.padstacks"
    Assert-ExactKeys $Scope.proof_flags $ProofNames "$Label.proof_flags"; foreach($n in $ProofNames){Assert-ExactBool $Scope.proof_flags.$n $false "$Label.proof_flags.$n"}
}

function Assert-ExpectedResult($Expected,[string]$Repo,[string]$Root) {
    Assert-ExactKeys $Expected @('exit_code','status','wp3_status','gate','certificate','counts','terminal_layers','reversed_traces','proof_flags','physical_nonconnection_claimed','fallback_geometry_used','logical_ownership_preserved') 'expected_result'
    Assert-ExactInt $Expected.exit_code 0 'expected_result.exit_code'; Assert-ExactString $Expected.status 'WP3=PARTIAL' 'expected_result.status'; Assert-ExactString $Expected.wp3_status 'PARTIAL' 'expected_result.wp3_status'; Assert-ExactString $Expected.gate 'STOP_NOT_REPRESENTED' 'expected_result.gate'
    Assert-ExactKeys $Expected.certificate @('path','size_bytes','sha256') 'expected_result.certificate'; Assert-String $Expected.certificate.path 'expected_result.certificate.path'; $certPath=Resolve-Absolute $Expected.certificate.path; if ((Split-Path -Parent $certPath) -cne $Root -or (Split-Path -Leaf $certPath) -cne $CertificateName) { Fail 'certificate output path mismatch' }; Assert-ExactInt $Expected.certificate.size_bytes $CertificateSize 'expected_result.certificate.size_bytes'; Assert-ExactString $Expected.certificate.sha256 $CertificateSha 'expected_result.certificate.sha256'
    Assert-ExactKeys $Expected.counts @('pins','nodes','edges','vias','padstacks','traces') 'expected_result.counts'; Assert-ExactInt $Expected.counts.pins 2 'expected_result.counts.pins'; Assert-ExactInt $Expected.counts.nodes 44 'expected_result.counts.nodes'; Assert-ExactInt $Expected.counts.edges 42 'expected_result.counts.edges'; Assert-ExactInt $Expected.counts.vias 36 'expected_result.counts.vias'; Assert-ExactInt $Expected.counts.traces 6 'expected_result.counts.traces'; Assert-ExactInt $Expected.counts.padstacks 19 'expected_result.counts.padstacks'; Assert-ExactArray $Expected.terminal_layers $ExpectedTerminalLayers 'expected_result.terminal_layers'; Assert-ExactArray $Expected.reversed_traces $ExpectedReversedTraces 'expected_result.reversed_traces'
    Assert-ExactKeys $Expected.proof_flags $ProofNames 'expected_result.proof_flags'; foreach($n in $ProofNames){Assert-ExactBool $Expected.proof_flags.$n $false "expected_result.proof_flags.$n"}; Assert-ExactBool $Expected.physical_nonconnection_claimed $false 'expected_result.physical_nonconnection_claimed'; Assert-ExactBool $Expected.fallback_geometry_used $false 'expected_result.fallback_geometry_used'; Assert-ExactBool $Expected.logical_ownership_preserved $true 'expected_result.logical_ownership_preserved'
}

function New-ExpectedArgv($Hq,[string]$PythonPath,[string]$BuilderPath,[string]$InputPath,[string]$OutputPath) {
    return [string[]]@($PythonPath,'-B',$BuilderPath,'--input',$InputPath,'--raw-spd',$Hq.source_bindings.raw_spd.path,'--d115b',$Hq.source_bindings.d115b.path,'--d115c',$Hq.source_bindings.d115c.path,'--wp3-05',$Hq.source_bindings.wp3_05.path,'--output',$OutputPath)
}

function New-ProcessRunState { return [pscustomobject]@{ Stdout = [D117Wp3Capture]::new(); Stderr = [D117Wp3Capture]::new(); Stopwatch = [Diagnostics.Stopwatch]::new(); TimeoutSignalElapsedSeconds = $null } }

function ConvertTo-WindowsArgument([string]$Argument) {
    if ($null -eq $Argument) { $Argument = '' }
    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s\"]') { return $Argument }
    $builder=[Text.StringBuilder]::new();[void]$builder.Append('"');$slashes=0
    foreach($character in $Argument.ToCharArray()) { if($character -eq [char]0x5c){$slashes++;continue};if($character -eq [char]0x22){for($i=0;$i -lt (2*$slashes+1);$i++){[void]$builder.Append([char]0x5c)};[void]$builder.Append([char]0x22);$slashes=0;continue};for($i=0;$i -lt $slashes;$i++){[void]$builder.Append([char]0x5c)};$slashes=0;[void]$builder.Append($character) }
    for($i=0;$i -lt (2*$slashes);$i++){[void]$builder.Append([char]0x5c)};[void]$builder.Append('"');return $builder.ToString()
}

function New-ConfiguredProcess([string[]]$Argv,[string]$WorkingDirectory) {
    $psi=[Diagnostics.ProcessStartInfo]::new(); $psi.FileName=$Argv[0]
    if ($null -ne $psi.GetType().GetProperty('ArgumentList')) { foreach($arg in $Argv[1..($Argv.Count-1)]){[void]$psi.ArgumentList.Add([string]$arg)} } else { $psi.Arguments=(($Argv[1..($Argv.Count-1)]|ForEach-Object { ConvertTo-WindowsArgument ([string]$_) }) -join ' ') }
    $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.WorkingDirectory=$WorkingDirectory; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
    if ($null -ne $psi.GetType().GetProperty('Environment')) { $psi.Environment['PYTHONDONTWRITEBYTECODE']='1' } else { $psi.EnvironmentVariables['PYTHONDONTWRITEBYTECODE']='1' }
    $p=[Diagnostics.Process]::new(); $p.StartInfo=$psi; return $p
}

function Stop-ProcessTree($Process) {
    try { if ($Process.HasExited) { return $true } } catch { }
    $kill=$Process.GetType().GetMethod('Kill',[Type[]]@([bool])); if ($null -ne $kill) { try{$kill.Invoke($Process,[object[]]@($true))|Out-Null}catch{Fail 'whole-tree Kill failed'}; if(-not $Process.WaitForExit(5000)){Fail 'process tree did not terminate'}; return $true }
    $taskkill=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::System)) 'taskkill.exe'; if(-not(Test-Path -LiteralPath $taskkill -PathType Leaf)){Fail 'taskkill.exe unavailable'}
    $psi=[Diagnostics.ProcessStartInfo]::new();$psi.FileName=$taskkill;$psi.Arguments="/PID $($Process.Id) /T /F";$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true
    $killer=[Diagnostics.Process]::new();$killer.StartInfo=$psi;try{if(-not$killer.Start()){Fail 'taskkill failed'};if(-not$killer.WaitForExit(5000)){Fail 'taskkill timeout'}}finally{$killer.Dispose()};if(-not$Process.WaitForExit(5000)){Fail 'taskkill tree did not terminate'};return $true
}

function Invoke-BoundedProcess($Process,$State,[double]$Wall,[int64]$Cap) {
    [void]$State.Stopwatch.Restart();$started=$false
    try {
        if(-not$Process.Start()){Fail 'direct child start failed'};$started=$true
        $outTask=$State.Stdout.DrainAsync($Process.StandardOutput,$Cap,$ChunkChars)
        $errTask=$State.Stderr.DrainAsync($Process.StandardError,$Cap,$ChunkChars)
        $timedOut=$false;$timeoutMs=[int][Math]::Ceiling($Wall*1000)
        if(-not$Process.WaitForExit($timeoutMs)){ $timedOut=$true;$State.TimeoutSignalElapsedSeconds=$State.Stopwatch.Elapsed.TotalSeconds;[void](Stop-ProcessTree $Process) }
        if(-not $outTask.Wait(5000) -or -not $errTask.Wait(5000)){Fail 'stream drain did not terminate'};if(-not$Process.HasExited){Fail 'child remained alive'}
        return [pscustomobject]@{ ExitCode=$Process.ExitCode;TimedOut=$timedOut;ElapsedSeconds=$State.Stopwatch.Elapsed.TotalSeconds;TimeoutSignalElapsedSeconds=$State.TimeoutSignalElapsedSeconds;StdoutTotal=$State.Stdout.Total;StderrTotal=$State.Stderr.Total;StdoutStored=$State.Stdout.Stored;StderrStored=$State.Stderr.Stored;StdoutTruncated=$State.Stdout.Truncated;StderrTruncated=$State.Stderr.Truncated;Cleaned=$true }
    } catch { if($started){try{[void](Stop-ProcessTree $Process)}catch{}}; throw } finally {[void]$State.Stopwatch.Stop()}
}

function Write-CreateNewBytes([string]$Path,[byte[]]$Bytes) {
    $full=Resolve-Absolute $Path;$stream=[IO.File]::Open($full,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

function Write-AtomicNoClobber([string]$Path,[byte[]]$Bytes) {
    $full=Resolve-Absolute $Path;if(Test-Path -LiteralPath $full){Fail 'output already exists'};$parent=Split-Path -Parent $full;if(-not(Test-Path -LiteralPath $parent -PathType Container)){Fail 'output parent missing'}
    $tmp=$null;try{$tmp=[IO.Path]::Combine($parent,".$([IO.Path]::GetFileName($full)).$([Guid]::NewGuid().ToString('N')).tmp");$stream=[IO.File]::Open($tmp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()};if(-not [D117Wp3Native]::Link($full,$tmp)){throw [ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())};[IO.File]::Delete($tmp);$tmp=$null}catch{if($null -ne $tmp -and(Test-Path -LiteralPath $tmp)){try{Remove-Item -LiteralPath $tmp -Force}catch{}};throw}
}

function Assert-Certificate([string]$Path,$Expected) {
    $bound = Read-BoundedFileBytes $Path $CertificateSize 'certificate'
    try {
        if($bound.Sha256 -ine $CertificateSha){Fail 'certificate identity mismatch'}
        $value=Read-StrictJsonBytes $bound.Bytes 'certificate';Assert-ExactString $value.product $Product 'certificate.product';Assert-ExactString $value.version $Version 'certificate.version';Assert-ExactString $value.status 'WP3=PARTIAL' 'certificate.status';Assert-ExactString $value.wp3_status 'PARTIAL' 'certificate.wp3_status';Assert-ExactString $value.gate 'STOP_NOT_REPRESENTED' 'certificate.gate';Assert-ExactBool $value.logical_ownership_preserved $true 'certificate.logical_ownership_preserved';Assert-ExactBool $value.physical_nonconnection_claimed $false 'certificate.physical_nonconnection_claimed';Assert-ExactBool $value.fallback_geometry_used $false 'certificate.fallback_geometry_used'
        if($value.pins.Count -ne 2 -or $value.padstacks.Count -ne 19){Fail 'certificate cardinality mismatch'};$nodes=0;$edges=0;$vias=0;$traces=0;$terminalLayers=@();$reversed=@()
        foreach($pin in $value.pins){$nodes += $pin.path.nodes.Count;$edges += $pin.path.edges.Count;$terminalLayers += [string]$pin.path.terminal.layer;foreach($edge in $pin.path.edges){if($edge.edge_kind -eq 'Via'){$vias++};if($edge.edge_kind -eq 'Trace'){$traces++;if($edge.traversal_reversed){$reversed += [string]$edge.trace_id}}}}
        if($nodes -ne 44 -or $edges -ne 42 -or $vias -ne 36 -or $traces -ne 6){Fail 'certificate path counts mismatch'};Assert-ExactArray $terminalLayers $ExpectedTerminalLayers 'certificate terminal layers';Assert-ExactStringSet $reversed $ExpectedReversedTraces 'certificate reversed traces';foreach($n in $ProofNames){if($value.proof_flags.$n -ne $false){Fail "certificate proof flag is true: $n"}}
        return [pscustomobject]@{Path=$bound.Path;SizeBytes=$bound.SizeBytes;Sha256=$bound.Sha256;Bytes=$bound.Bytes;Parsed=$value}
    } finally { $bound.Stream.Dispose() }
}

function New-Receipt($Phase,[string]$Reason,$State,$Run,$Pins,$Before,$After,$Inventories,$Token,$Certificate,$Argv,$Expected) {
    $argTokens = if($null -eq $Argv){[string[]]@()}else{[string[]]$Argv};$argHash=Hash-Bytes (Get-CompactJsonBytes $argTokens)
    $runExit=$null;$runElapsed=$null;$runTimedOut=$false;$runTimeoutSignal=$null;$runCleaned=$false;$stdoutTotal=0;$stdoutStored=0;$stdoutTruncated=$false;$stderrTotal=0;$stderrStored=0;$stderrTruncated=$false;$stdoutText='';$stderrText=''
    if($null -ne $Run){$runExit=$Run.ExitCode;$runElapsed=$Run.ElapsedSeconds;$runTimedOut=$Run.TimedOut;$runTimeoutSignal=$Run.TimeoutSignalElapsedSeconds;$runCleaned=$Run.Cleaned;$stdoutTotal=$Run.StdoutTotal;$stdoutStored=$Run.StdoutStored;$stdoutTruncated=$Run.StdoutTruncated;$stderrTotal=$Run.StderrTotal;$stderrStored=$Run.StderrStored;$stderrTruncated=$Run.StderrTruncated;if($null -ne $State){$stdoutText=$State.Stdout.Text.ToString();$stderrText=$State.Stderr.Text.ToString()}}
    $summary=if($null -eq $Expected){[ordered]@{}}else{$Expected};$counts=if($null -eq $Expected -or $null -eq $Expected.counts){[ordered]@{}}else{$Expected.counts};$beforeRecord=if($null -eq $Before){[ordered]@{}}else{$Before};$afterRecord=if($null -eq $After){[ordered]@{}}else{$After};$inventoryRecord=if($null -eq $Inventories){[ordered]@{}}else{$Inventories};$pinsRecord=if($null -eq $Pins){[ordered]@{}}else{$Pins};$tokenRecord=if($null -eq $Token){[ordered]@{}}else{$Token};$certificateRecord=if($null -eq $Certificate){[ordered]@{}}else{$Certificate}
    return [ordered]@{schema='d117-wp3-selected-pin-source-path-absence-controller-receipt-v1';product=$Product;version=$Version;status=$Phase;reason=$Reason;pins=$pinsRecord;identities=[ordered]@{before=$beforeRecord;after=$afterRecord};argv=[ordered]@{tokens=$argTokens;sha256=$argHash};counts=$counts;process=[ordered]@{exit_code=$runExit;elapsed_seconds=$runElapsed;wall_seconds=$HardWallSeconds;timed_out=$runTimedOut;timeout_signal_elapsed_seconds=$runTimeoutSignal;cleaned=$runCleaned;timeout_tree_cleanup=$true;attempts=1;retries=0};capture=[ordered]@{stdout_total=$stdoutTotal;stdout_stored=$stdoutStored;stdout_truncated=$stdoutTruncated;stdout_text=$stdoutText;stderr_total=$stderrTotal;stderr_stored=$stderrStored;stderr_truncated=$stderrTruncated;stderr_text=$stderrText;cap_chars=$CaptureCap};token=$tokenRecord;certificate=$certificateRecord;inventories=$inventoryRecord;expected_scientific_summary=$summary}
}

function Invoke-SelfCheck {
    $temp=Join-Path ([IO.Path]::GetTempPath()) "spd-d117-wp3-selfcheck-$([Guid]::NewGuid().ToString('N'))";New-Item -ItemType Directory -Path $temp -Force|Out-Null
    try {
        [void](Assert-NoReparse $temp $true);$hqPath=Join-Path $temp 'hq.json';$jsonBytes=[Text.UTF8Encoding]::new($false).GetBytes('{"schema":"fixture"}');Write-CreateNewBytes $hqPath $jsonBytes;$sha=Hash-Bytes $jsonBytes;$bound=Open-BoundJson $hqPath $jsonBytes.Length $sha;if($bound.Parsed.schema -cne 'fixture'){Fail 'self-check positive pin fixture failed'}; $bound.Stream.Dispose()
        $dupPath=Join-Path $temp 'duplicate.json';$dup=[Text.UTF8Encoding]::new($false).GetBytes('{"x":1,"x":2}');Write-CreateNewBytes $dupPath $dup;$dupRejected=$false;try{$null=Read-StrictJsonFile $dupPath 'duplicate'}catch{$dupRejected=$true};if(-not$dupRejected){Fail 'self-check duplicate JSON accepted'}
        $atomic=Join-Path $temp 'atomic.bin';Write-CreateNewBytes $atomic ([byte[]](1,2,3));$clobberRejected=$false;try{Write-CreateNewBytes $atomic ([byte[]](9,9))}catch [IO.IOException]{$clobberRejected=$true};if(-not$clobberRejected){Fail 'self-check CreateNew clobber'};$linked=Join-Path $temp 'linked.bin';Write-AtomicNoClobber $linked ([byte[]](4,5,6));if(-not([IO.File]::Exists($linked)) -or (([IO.File]::ReadAllBytes($linked)|ForEach-Object {[int]$_}) -join ',') -ne '4,5,6'){Fail 'self-check atomic hard-link failed'};$atomicLinkRejected=$false;try{Write-AtomicNoClobber $linked ([byte[]](8))}catch{$atomicLinkRejected=$true};if(-not$atomicLinkRejected){Fail 'self-check atomic clobber'}
        $pwsh=(Get-Command pwsh,powershell -ErrorAction SilentlyContinue|Select-Object -First 1).Source;if([string]::IsNullOrWhiteSpace($pwsh)){Fail 'self-check PowerShell unavailable'};$payload='[Console]::Out.Write(("O"*512));[Console]::Error.Write(("E"*512));Start-Sleep -Seconds 10';$child=New-ConfiguredProcess ([string[]]@($pwsh,'-NoProfile','-NonInteractive','-Command',$payload)) $temp;$state=New-ProcessRunState;try{$run=Invoke-BoundedProcess $child $state 0.35 64}finally{$child.Dispose()};if(-not$run.TimedOut -or -not$run.StdoutTruncated -or -not$run.StderrTruncated -or -not$run.Cleaned){Fail 'self-check timeout/capture failed'}
        [Console]::Out.WriteLine(([ordered]@{banner=$Banner;result='PASS';positive_pin_fixture=$true;duplicate_keys_rejected=$dupRejected;create_new_no_clobber=$clobberRejected;atomic_no_clobber=$atomicLinkRejected;timed_out=$run.TimedOut;stdout_truncated=$run.StdoutTruncated;stderr_truncated=$run.StderrTruncated}|ConvertTo-Json -Compress));return 0
    } finally {if(Test-Path -LiteralPath $temp){Remove-Item -LiteralPath $temp -Recurse -Force}}
}

function Invoke-Normal {
    $hasPath=$script:PSBoundParameters.ContainsKey('HqAuthorizationPath');$hasSize=$script:PSBoundParameters.ContainsKey('HqAuthorizationSizeBytes');$hasSha=$script:PSBoundParameters.ContainsKey('HqAuthorizationSha256')
    $hqBound=$null;$hq=$null;$repo=$null;$root=$null;$controllerPath=$null;$controllerIdentity=$null;$builder=$null;$test=$null;$input=$null;$python=$null;$pythonVersion=$null;$git=$null;$sources=[ordered]@{};$tokenPath=$null;$certPath=$null;$controllerReceiptPath=$null;$expectedArgv=[string[]]@();$expectedScientific=[ordered]@{};$before=[ordered]@{};$after=[ordered]@{};$preRunInventory=[string[]]@();$preReceiptInventory=[string[]]@();$successInventory=[string[]]@();$inventories=[ordered]@{};$pins=[ordered]@{hq_authorization_path=$HqAuthorizationPath;hq_authorization_size_bytes=$HqAuthorizationSizeBytes;hq_authorization_sha256=$HqAuthorizationSha256};$tokenBytes=$null;$tokenIdentity=$null;$certificateBytes=$null;$certificateIdentity=$null;$receiptBytes=$null;$receiptIdentity=$null;$process=$null;$state=New-ProcessRunState;$run=$null;$receiptPathAllowed=$false;$reason=''
    try {
        if(-not($hasPath -and $hasSize -and $hasSha)){Fail 'normal mode requires -HqAuthorizationPath, -HqAuthorizationSizeBytes, and -HqAuthorizationSha256 together'};if($HqAuthorizationSizeBytes -le 0){Fail 'HqAuthorizationSizeBytes must be positive'};if($HqAuthorizationSha256 -notmatch '^[0-9a-fA-F]{64}$'){Fail 'HqAuthorizationSha256 must be 64 hexadecimal characters'}
        $repo=Resolve-Absolute $RequiredRepo;$cwd=Resolve-Absolute (Get-Location).Path;if($cwd -cne $repo){Fail 'current invocation cwd must be repository root'};Assert-NoReparse $repo $true|Out-Null;$controllerPath=Resolve-Absolute $PSCommandPath;$controllerIdentity=Get-FileIdentity $controllerPath;$hqBound=Open-BoundJson $HqAuthorizationPath $HqAuthorizationSizeBytes $HqAuthorizationSha256;$hq=$hqBound.Parsed;$root=Resolve-Absolute (Split-Path -Parent $hqBound.Path)
        Assert-ExactKeys $hq @('schema','product','authorization','scientific_scope','git','controller_binding','builder_binding','test_binding','input_binding','source_bindings','python_binding','argv','expected_result','execution_policy','attempt_token_policy','artifact_policy','prohibitions') 'HQ authorization';Assert-ExactString $hq.schema $HqSchema 'HQ schema';Assert-ExactKeys $hq.product @('name','version') 'HQ product';Assert-ExactString $hq.product.name $Product 'HQ product.name';Assert-ExactString $hq.product.version $Version 'HQ product.version';Assert-ExactKeys $hq.authorization @('authorization_id','status','execution_authorized') 'HQ authorization';Assert-String $hq.authorization.authorization_id 'HQ authorization.authorization_id';Assert-ExactString $hq.authorization.status 'SOL_HQ_APPROVED_SINGLE_EXECUTION' 'HQ authorization.status';Assert-ExactBool $hq.authorization.execution_authorized $true 'HQ authorization.execution_authorized';Assert-ScientificScope $hq.scientific_scope 'HQ scientific_scope'
        Assert-ExactKeys $hq.git @('repo_root','head','branch') 'HQ git';Assert-ExactString $hq.git.repo_root $repo 'HQ git.repo_root';Assert-ExactString $hq.git.head $RequiredHead 'HQ git.head';Assert-ExactString $hq.git.branch $RequiredBranch 'HQ git.branch';$git=Invoke-GitIdentity $repo
        Assert-ExactKeys $hq.controller_binding @('path','size_bytes','sha256') 'HQ controller_binding';$null=Assert-IdentityRecord $hq.controller_binding 'HQ controller_binding' ([ordered]@{path=$controllerPath;size_bytes=$controllerIdentity.SizeBytes;sha256=$controllerIdentity.Sha256})
        $builderPath=Resolve-Absolute (Join-Path $repo $BuilderRelative);$testPath=Resolve-Absolute (Join-Path $repo $TestRelative);Assert-ExactKeys $hq.builder_binding @('path','size_bytes','sha256') 'HQ builder_binding';$builder=Assert-IdentityRecord $hq.builder_binding 'HQ builder_binding' ([ordered]@{path=$builderPath;size_bytes=$BuilderSize;sha256=$BuilderSha});Assert-ExactKeys $hq.test_binding @('path','size_bytes','sha256','passed') 'HQ test_binding';$testRecord=[ordered]@{path=$hq.test_binding.path;size_bytes=$hq.test_binding.size_bytes;sha256=$hq.test_binding.sha256};$test=Assert-IdentityRecord $testRecord 'HQ test_binding' ([ordered]@{path=$testPath;size_bytes=$TestSize;sha256=$TestSha});Assert-ExactInt $hq.test_binding.passed 22 'HQ test_binding.passed'
        Assert-ExactKeys $hq.input_binding @('path','size_bytes','sha256') 'HQ input_binding';Assert-ExactString $hq.input_binding.path (Join-Path $InputRoot $InputName) 'HQ input path';$input=Assert-IdentityRecord $hq.input_binding 'HQ input_binding' ([ordered]@{path=(Join-Path $InputRoot $InputName);size_bytes=$InputSize;sha256=$InputSha});if((Split-Path -Parent $input.Path) -cne (Resolve-Absolute $InputRoot)){Fail 'input root mismatch'};Assert-Inventory (Resolve-Absolute $InputRoot) @($InputName) 'input inventory'|Out-Null
        Assert-ExactKeys $hq.source_bindings @('raw_spd','d115b','d115c','wp3_05') 'HQ source_bindings';$sources=[ordered]@{};$sourceIdentities=[ordered]@{};foreach($name in $SourceExpected.Keys){$sources[$name]=$hq.source_bindings.$name;$sourceIdentities[$name]=Assert-IdentityRecord $sources[$name] "HQ source_bindings.$name" $SourceExpected[$name]}
        Assert-ExactKeys $hq.python_binding @('launcher_token','resolved_executable','file_version','size_bytes','sha256','working_directory','environment') 'HQ python_binding';Assert-ExactString $hq.python_binding.launcher_token 'python' 'HQ python launcher_token';Assert-ExactString $hq.python_binding.working_directory $repo 'HQ python working_directory';Assert-ExactKeys $hq.python_binding.environment @('PYTHONDONTWRITEBYTECODE') 'HQ python environment';Assert-ExactString $hq.python_binding.environment.PYTHONDONTWRITEBYTECODE '1' 'HQ python environment';$python=Assert-IdentityRecord ([ordered]@{path=$hq.python_binding.resolved_executable;size_bytes=$hq.python_binding.size_bytes;sha256=$hq.python_binding.sha256}) 'HQ python executable';Assert-String $hq.python_binding.file_version 'HQ python file_version'
        $tokenPath=Resolve-Absolute $hq.attempt_token_policy.path;$certPath=Resolve-Absolute $hq.expected_result.certificate.path;$controllerReceiptPath=Resolve-Absolute $hq.artifact_policy.controller_receipt_path;if((Split-Path -Parent $tokenPath)-cne$root -or(Split-Path -Parent $certPath)-cne$root -or(Split-Path -Parent $controllerReceiptPath)-cne$root){Fail 'artifact paths must be in HQ root'};Assert-ExactKeys $hq.argv @('tokens','sha256','hash_convention') 'HQ argv';Assert-StringArray $hq.argv.tokens 'HQ argv.tokens';$expectedArgv=New-ExpectedArgv $hq $python.Path $builder.Path $input.Path $certPath;if($hq.argv.tokens.Count -ne 15){Fail 'HQ argv must contain exactly 15 tokens'};for($i=0;$i -lt 15;$i++){if($hq.argv.tokens[$i] -cne $expectedArgv[$i]){Fail "HQ argv token $i mismatch"}};Assert-ExactString $hq.argv.hash_convention 'compact UTF-8 JSON array without BOM' 'HQ argv.hash_convention';Assert-ExactString $hq.argv.sha256 (Hash-Bytes (Get-CompactJsonBytes $expectedArgv)) 'HQ argv.sha256'
        Assert-ExpectedResult $hq.expected_result $repo $root;$expectedScientific=$hq.expected_result;Assert-ExactKeys $hq.execution_policy @('python_children','attempts','retries','wall_seconds','stdout_chars','stderr_chars','timeout_tree_cleanup') 'HQ execution_policy';Assert-ExactInt $hq.execution_policy.python_children 1 'HQ execution_policy.python_children';Assert-ExactInt $hq.execution_policy.attempts 1 'HQ execution_policy.attempts';Assert-ExactInt $hq.execution_policy.retries 0 'HQ execution_policy.retries';Assert-ExactInt $hq.execution_policy.wall_seconds 600 'HQ execution_policy.wall_seconds';Assert-ExactInt $hq.execution_policy.stdout_chars 65536 'HQ execution_policy.stdout_chars';Assert-ExactInt $hq.execution_policy.stderr_chars 65536 'HQ execution_policy.stderr_chars';Assert-ExactBool $hq.execution_policy.timeout_tree_cleanup $true 'HQ execution_policy.timeout_tree_cleanup'
        Assert-ExactKeys $hq.attempt_token_policy @('path','file_mode','creation_timing','permanent_retention') 'HQ attempt_token_policy';Assert-ExactString $hq.attempt_token_policy.file_mode 'System.IO.FileMode.CreateNew' 'HQ attempt_token_policy.file_mode';Assert-ExactString $hq.attempt_token_policy.creation_timing 'immediately_before_process_creation' 'HQ attempt_token_policy.creation_timing';Assert-ExactBool $hq.attempt_token_policy.permanent_retention $true 'HQ attempt_token_policy.permanent_retention'
        Assert-ExactKeys $hq.artifact_policy @('root','controller_receipt_path','before','pre_controller_receipt','success','prohibit_logs','prohibit_temp') 'HQ artifact_policy';Assert-ExactString $hq.artifact_policy.root $root 'HQ artifact_policy.root';Assert-ExactArray $hq.artifact_policy.before @('HQ') 'HQ artifact_policy.before';Assert-ExactArray $hq.artifact_policy.pre_controller_receipt @('HQ','TOKEN','CERTIFICATE') 'HQ artifact_policy.pre_controller_receipt';Assert-ExactArray $hq.artifact_policy.success @('HQ','TOKEN','CERTIFICATE','CONTROLLER_RECEIPT') 'HQ artifact_policy.success';Assert-ExactBool $hq.artifact_policy.prohibit_logs $true 'HQ artifact_policy.prohibit_logs';Assert-ExactBool $hq.artifact_policy.prohibit_temp $true 'HQ artifact_policy.prohibit_temp';Assert-Inventory $root @([IO.Path]::GetFileName($hqBound.Path)) 'pre-run inventory'|Out-Null
        Assert-StringArray $hq.prohibitions 'HQ prohibitions';if(@($hq.prohibitions|Where-Object{$_ -match 'solver|Triangle|FasterCap|PowerSI'}).Count -lt 4){Fail 'HQ prohibitions incomplete'}
    if((Test-Path -LiteralPath $tokenPath) -or(Test-Path -LiteralPath $certPath) -or(Test-Path -LiteralPath $controllerReceiptPath)){Fail 'token/certificate/controller receipt must be absent before run'}
    if([IO.Path]::GetFileName($tokenPath) -cne $TokenName){Fail 'attempt token leaf name mismatch'};if([IO.Path]::GetFileName($controllerReceiptPath) -cne $ControllerReceiptName){Fail 'controller receipt leaf name mismatch'};if([IO.Path]::GetFileName($certPath) -cne $CertificateName){Fail 'certificate leaf name mismatch'};$receiptPathAllowed=$true
    $preRunInventory=Assert-Inventory $root @([IO.Path]::GetFileName($hqBound.Path)) 'pre-run inventory';$preRunInventory=[string[]]$preRunInventory;$preRunInputRootInventory=Assert-Inventory (Resolve-Absolute $InputRoot) @($InputName) 'pre-run input-root inventory';$preRunInputRootInventory=[string[]]$preRunInputRootInventory
    $pythonVersion=Get-FileVersion $python.Path;if($pythonVersion -cne ([string]$hq.python_binding.file_version).Trim()){Fail 'Python file version mismatch'}
    $before=[ordered]@{hq=New-IdentityRecord $hqBound.Identity;controller=New-IdentityRecord $controllerIdentity;builder=New-IdentityRecord $builder;test=New-IdentityRecord $test;input=New-IdentityRecord $input;python=New-IdentityRecord $python $pythonVersion;sources=[ordered]@{raw_spd=(New-IdentityRecord $sourceIdentities.raw_spd);d115b=(New-IdentityRecord $sourceIdentities.d115b);d115c=(New-IdentityRecord $sourceIdentities.d115c);wp3_05=(New-IdentityRecord $sourceIdentities.wp3_05)};git=[ordered]@{root=$git.Root;head=$git.Head;branch=$git.Branch};input_root_inventory=$preRunInputRootInventory}
    $tokenObject=[ordered]@{schema='d117-wp3-selected-pin-source-path-absence-attempt-v1';product=$Product;version=$Version;authorization_id=$hq.authorization.authorization_id;attempt=1;retries=0;hq_pins=$pins;argv=$expectedArgv;argv_sha256=(Hash-Bytes (Get-CompactJsonBytes $expectedArgv));utc_timestamp=[DateTime]::UtcNow.ToString('O')};$tokenBytes=Get-CompactJsonBytes $tokenObject;Write-CreateNewBytes $tokenPath $tokenBytes;$tokenIdentity=Assert-BytesExact $tokenPath $tokenBytes 'attempt token'
    $process=New-ConfiguredProcess $expectedArgv $repo
    $run=Invoke-BoundedProcess $process $state $HardWallSeconds $CaptureCap;if($run.TimedOut -or $run.ExitCode -ne 0 -or $run.StdoutTruncated -or $run.StderrTruncated){Fail 'builder process failed execution contract'}
    $tokenIdentity=Assert-BytesExact $tokenPath $tokenBytes 'attempt token';$certificateRead=Assert-Certificate $certPath $hq.expected_result;$certificateBytes=$certificateRead.Bytes;$certificateIdentity=[pscustomobject]@{Path=$certificateRead.Path;SizeBytes=$certificateRead.SizeBytes;Sha256=$certificateRead.Sha256}
    $preReceiptInventory=Assert-Inventory $root @([IO.Path]::GetFileName($hqBound.Path),$TokenName,$CertificateName) 'pre-controller-receipt inventory';$preReceiptInventory=[string[]]$preReceiptInventory;$inventories=[ordered]@{pre_run=$preRunInventory;pre_controller_receipt=$preReceiptInventory;success=[string[]]@([IO.Path]::GetFileName($hqBound.Path),$TokenName,$CertificateName,$ControllerReceiptName)}
    $hqAfter=Assert-RetainedBoundUnchanged $hqBound 'HQ authorization';$postSourceIdentities=[ordered]@{};foreach($name in @('raw_spd','d115b','d115c','wp3_05')){$postSourceIdentities[$name]=Get-FileIdentity $sources[$name].path};$postGit=Invoke-GitIdentity $repo;$postInputRootInventory=Assert-Inventory (Resolve-Absolute $InputRoot) @($InputName) 'postflight input-root inventory';$postPythonVersion=Get-FileVersion $python.Path;$after=[ordered]@{hq=New-IdentityRecord $hqAfter;controller=New-IdentityRecord (Get-FileIdentity $controllerPath);builder=New-IdentityRecord (Get-FileIdentity $builder.Path);test=New-IdentityRecord (Get-FileIdentity $test.Path);input=New-IdentityRecord (Get-FileIdentity $input.Path);python=New-IdentityRecord (Get-FileIdentity $python.Path) $postPythonVersion;sources=[ordered]@{raw_spd=(New-IdentityRecord $postSourceIdentities.raw_spd);d115b=(New-IdentityRecord $postSourceIdentities.d115b);d115c=(New-IdentityRecord $postSourceIdentities.d115c);wp3_05=(New-IdentityRecord $postSourceIdentities.wp3_05)};git=[ordered]@{root=$postGit.Root;head=$postGit.Head;branch=$postGit.Branch};input_root_inventory=$postInputRootInventory};Assert-SnapshotUnchanged $before $after
    $inventories.after_postflight=$postInputRootInventory;$inventories.input_root_pre_run=$preRunInputRootInventory;$inventories.input_root_postflight=$postInputRootInventory;$receipt=New-Receipt 'PASS' '' $state $run $pins $before $after $inventories (New-IdentityRecord $tokenIdentity) (New-IdentityRecord $certificateIdentity) $expectedArgv $hq.expected_result;$receiptBytes=Get-CompactJsonBytes $receipt;Write-AtomicNoClobber $controllerReceiptPath $receiptBytes;$receiptIdentity=Assert-BytesExact $controllerReceiptPath $receiptBytes 'controller receipt';$successInventory=Assert-Inventory $root @([IO.Path]::GetFileName($hqBound.Path),$TokenName,$CertificateName,$ControllerReceiptName) 'final inventory';$inventories.success=$successInventory
    [Console]::Out.WriteLine("$Banner PASS controller_receipt=$($receiptIdentity.Path) size_bytes=$($receiptIdentity.SizeBytes) sha256=$($receiptIdentity.Sha256)");return 0
    } catch {
        $reason=$_.Exception.Message
        if($null -ne $tokenPath -and (Test-Path -LiteralPath $tokenPath)){try{$tokenIdentity=Assert-BytesExact $tokenPath ([byte[]]$tokenBytes) 'attempt token'}catch{try{$tokenIdentity=Get-FileIdentity $tokenPath}catch{}}}
        if($null -ne $root){try{$current=Get-SafeInventory $root;$inventories.current=$current;if($current -contains $TokenName -and $current -contains $CertificateName){$preReceiptInventory=$current}}catch{} }
        if($receiptPathAllowed -and $null -ne $controllerReceiptPath -and -not(Test-Path -LiteralPath $controllerReceiptPath)){
            try{$stopInventories=[ordered]@{pre_run=$preRunInventory;pre_controller_receipt=$preReceiptInventory;success=$successInventory;current=$inventories.current};$stopToken=[ordered]@{};$stopCertificate=[ordered]@{};if($null -ne $tokenIdentity){$stopToken=New-IdentityRecord $tokenIdentity};if($null -ne $certificateIdentity){$stopCertificate=New-IdentityRecord $certificateIdentity};$stopReceipt=New-Receipt 'STOP' $reason $state $run $pins $before $after $stopInventories $stopToken $stopCertificate $expectedArgv $expectedScientific;Write-AtomicNoClobber $controllerReceiptPath (Get-CompactJsonBytes $stopReceipt)}catch{}
        }
        throw
    } finally {
        if($null -ne $process){try{$process.Dispose()}catch{}}
        if($null -ne $hqBound -and $null -ne $hqBound.Stream){try{$hqBound.Stream.Dispose()}catch{}}
    }
}

try {
    if($SelfCheck){if($PSBoundParameters.ContainsKey('HqAuthorizationPath') -or $PSBoundParameters.ContainsKey('HqAuthorizationSizeBytes') -or $PSBoundParameters.ContainsKey('HqAuthorizationSha256')){Fail 'SelfCheck rejects normal-mode HQ pins'};exit (Invoke-SelfCheck)}
    exit (Invoke-Normal)
} catch { [Console]::Error.WriteLine("$Banner REFUSED: $($_.Exception.Message)"); exit 2 }
