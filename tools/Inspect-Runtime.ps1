# Read the live inspector without clicking the native debugger or modifying the game.
[CmdletBinding()]
param(
    [switch]$Json,
    [ValidateRange(0,4096)][int]$LogTail = 12,
    [string]$Path = (Join-Path (Split-Path $PSScriptRoot -Parent) 'PS2Recomp/out/build/ps2xRuntime/inspector.json')
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw 'No inspector report yet. Enable PS2_INSPECTOR_FILE in your game launcher and wait for a fresh report.'
}
# Allow the writer to replace the file while this reader holds the old version.
$stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
$reader = [IO.StreamReader]::new($stream)
try { $raw = $reader.ReadToEnd() } finally { $reader.Dispose() }
$report = $raw | ConvertFrom-Json
if ($report.schema_version -ne 1) { throw 'Unsupported inspector schema.' }
$sample = $report.snapshot
$process = Get-Process -Id $report.process_id -ErrorAction SilentlyContinue
$alive = $null -ne $process -and $process.ProcessName -eq 'ps2EntryRunner'
$age = if ($null -ne $sample) { ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $sample.captured_unix_ms) / 1000 } else { [double]::PositiveInfinity }
if (-not $alive -or $report.runtime_state -ne 'running') {
    Write-Warning 'The recorded runner is stopped. This is its last report, not live state.'
} elseif ($age -gt 5) {
    Write-Warning "No fresh EE sample for $([math]::Round($age,1)) seconds. The guest may not be returning to the dispatcher."
}
if ($Json) { Write-Output $raw; return }
if ($null -eq $sample) { Write-Output 'Inspector is waiting for the first EE dispatcher sample.'; return }
if ($sample.error) { throw "Inspector capture failed: $($sample.error)" }

$workspace = Join-Path (Split-Path $PSScriptRoot -Parent) 'Rumble Racing'
$map = $null
$discRoot = [IO.Path]::GetFullPath($sample.disc.root).TrimEnd('\','/')
if ($discRoot -eq (Join-Path $workspace 'Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)')) { $map = Join-Path $workspace 'CSV Map/map-ntsc.csv' }
if ($discRoot -eq (Join-Path $workspace 'Extracted_Assets/Rumble Racing (USA retail)')) { $map = Join-Path $workspace 'CSV Map/retail/map.csv' }
$functionName = 'unknown'
# Choose the map from the observed disc root, never the shared SLUS filename.
if ($map -and (Test-Path -LiteralPath $map)) {
    $pc = [Convert]::ToUInt32($sample.cpu.pc.Substring(2), 16)
    $rows = Import-Csv -LiteralPath $map
    foreach ($row in $rows) {
        $fields = @($row.PSObject.Properties.Value)
        $start = [Convert]::ToUInt32($fields[1].Substring(2), 16)
        $end = [Convert]::ToUInt32($fields[2].Substring(2), 16)
        if ($pc -ge $start -and $pc -lt $end) { $functionName = "$($fields[0])+0x$('{0:X}' -f ($pc - $start))"; break }
    }
}
Write-Output "Runner PID $($report.process_id): $($report.runtime_state); sample $($sample.sequence), age $([math]::Round($age,1))s"
Write-Output "PC $($sample.cpu.pc) ($functionName), RA $($sample.cpu.ra), SP $($sample.cpu.sp), GP $($sample.cpu.gp)"
$profile = if ($sample.iop.profile) { $sample.iop.profile } else { '(none)' }
$provider = if ($sample.iop.provider) { $sample.iop.provider } else { '(none)' }
Write-Output "IOP profile: $profile; provider: $provider"
Write-Output "CPU history: $(@($sample.cpu_history).Count)/$($sample.cpu_history_capacity) samples in this report"
Write-Output "Modules: $(($sample.iop.modules | ForEach-Object { $_.path }) -join ', ')"
Write-Output "RPC: $($sample.rpc.clients) clients, $($sample.rpc.servers) servers, $(@($sample.rpc.history).Count) recent events"
Write-Output "Disc: $($sample.disc.root); last error $($sample.disc.last_error)"
Write-Output "Graphics: has frame=$($sample.graphics.has_frame); DMA starts=$($sample.dma_io.dma_starts), GS writes=$($sample.dma_io.gs_writes)"
$sample.kernel.threads | Format-Table id, status, wait_reason, wait_id, pc, priority | Out-String | Write-Output
Write-Output "Runtime logs: $(@($sample.logs.entries).Count)/$($sample.logs.capacity), paused=$($sample.logs.paused)"
if ($LogTail -gt 0) { $sample.logs.entries | Select-Object -Last $LogTail | ForEach-Object { Write-Output ("[{0}] {1}" -f $_.sequence, $_.text.TrimEnd()) } }
