# Launch the selected NTSC build; retail is the port target. Reuse runtime outputs.
param([switch]$TraceBoot, [switch]$NoInspector, [switch]$NoFrameCapture, [switch]$ViewerOnly, [switch]$Window720p, [switch]$DevAI, [switch]$DevMaxUpgrades, [switch]$DevGlitchVisuals, [ValidateSet('Retail','February')][string]$Build = 'Retail', [ValidateSet('Debug','RelWithDebInfo')][string]$Configuration = 'Debug')
$ErrorActionPreference = 'Stop'
if ($DevAI -and $Build -ne 'Retail') { throw 'Dev AI is verified only for the USA retail build.' }
if ($DevMaxUpgrades -and $Build -ne 'Retail') { throw 'Dev upgrades are verified only for the USA retail build.' }
if ($DevGlitchVisuals -and $Build -ne 'Retail') { throw 'This development launcher enables glitch visuals only for retail.' }
$runtimeDirectory = Join-Path $PSScriptRoot 'PS2Recomp/out/build/ps2xRuntime'
$executable = Join-Path $runtimeDirectory (Join-Path $Configuration 'ps2EntryRunner.exe')
$relativeElf = if ($Build -eq 'Retail') { 'Extracted_Assets/Rumble Racing (USA retail)/SLUS_201.74' } else { 'Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)/SLUS_201.74' }
$gameElf = Join-Path $PSScriptRoot $relativeElf
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config-ghidra.toml') -Raw
if (-not $config.Contains('input = "' + $relativeElf + '"')) { throw 'Build selection differs from generated code. Run tools/Rebuild-Game.ps1 for this build first.' }
if ((Get-Item -LiteralPath $executable).LastWriteTimeUtc -lt (Get-Item -LiteralPath (Join-Path $PSScriptRoot 'config-ghidra.toml')).LastWriteTimeUtc) { throw 'Runner predates the selected build configuration. Rebuild before launching.' }
foreach ($path in @($executable, $gameElf)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required file: $path" }
}
$running = @(Get-Process -Name ps2EntryRunner -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) { throw 'A PS2 runner is already running. Close it before launching another instance.' }
$previousTrace = $env:PS2_BOOT_TRACE
$previousInspector = $env:PS2_INSPECTOR_FILE
$previousFrame = $env:PS2_INSPECTOR_FRAME
$previousHidden = $env:PS2_WINDOW_HIDDEN
$previousWindowWidth = $env:PS2_WINDOW_WIDTH
$previousWindowHeight = $env:PS2_WINDOW_HEIGHT
$previousDisplayAspect = $env:PS2_DISPLAY_ASPECT
$previousWatches = $env:PS2_INSPECTOR_WATCHES
$previousDevAI = $env:PS2_RUMBLE_DEV_AI
$previousDevUpgrades = $env:PS2_RUMBLE_DEV_UPGRADES
$previousDevGlitch = $env:PS2_RUMBLE_DEV_GLITCH
try {
$env:PS2_RUMBLE_DEV_AI = if ($DevAI) { 'no-mercy' } else { $null }
$env:PS2_RUMBLE_DEV_UPGRADES = if ($DevMaxUpgrades) { 'player-elite' } else { $null }
$env:PS2_RUMBLE_DEV_GLITCH = if ($DevGlitchVisuals) { '1' } else { $null }
if ($TraceBoot) { $env:PS2_BOOT_TRACE = '1' }
# Retail FE_Cycle watches: read at the executor boundary; no game-state writes.
if ($Build -eq 'Retail' -and -not $NoInspector) {
    $routeWatches = 'rumble_frontend=*0x1f28fc:36;rumble_transition=0x1f29f0:8;rumble_cycle=0x1f2900:4'
    $env:PS2_INSPECTOR_WATCHES = if ($previousWatches) { $routeWatches + ';' + $previousWatches } else { $routeWatches }
}
$env:PS2_WINDOW_HIDDEN = if ($ViewerOnly) { '1' } else { $null }
# Host output only: retain the game's internal framebuffer and use TV proportions.
if ($Window720p) {
    $env:PS2_WINDOW_WIDTH = '1280'
    $env:PS2_WINDOW_HEIGHT = '720'
    $env:PS2_DISPLAY_ASPECT = '4:3'
}
$env:PS2_INSPECTOR_FILE = if ($NoInspector) { $null } else { Join-Path $runtimeDirectory 'inspector.json' }
$env:PS2_INSPECTOR_FRAME = if ($NoInspector -or $NoFrameCapture) { '0' } else { '1' }
# Console output is redirected; native GLFW visibility is controlled above.
$windowStyle = 'Hidden'
$process = Start-Process -FilePath $executable -ArgumentList ('"' + $gameElf + '"') `
    -WorkingDirectory (Join-Path $runtimeDirectory $Configuration) -WindowStyle $windowStyle `
    -RedirectStandardOutput (Join-Path $runtimeDirectory 'ntsc-runner-stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDirectory 'ntsc-runner-stderr.log') -PassThru
} finally {
    $env:PS2_BOOT_TRACE = $previousTrace
    $env:PS2_INSPECTOR_FILE = $previousInspector
    $env:PS2_INSPECTOR_FRAME = $previousFrame
    $env:PS2_WINDOW_HIDDEN = $previousHidden
    $env:PS2_WINDOW_WIDTH = $previousWindowWidth
    $env:PS2_WINDOW_HEIGHT = $previousWindowHeight
    $env:PS2_DISPLAY_ASPECT = $previousDisplayAspect
    $env:PS2_INSPECTOR_WATCHES = $previousWatches
    $env:PS2_RUMBLE_DEV_AI = $previousDevAI
    $env:PS2_RUMBLE_DEV_UPGRADES = $previousDevUpgrades
    $env:PS2_RUMBLE_DEV_GLITCH = $previousDevGlitch
}
Write-Output "Started NTSC $Build $Configuration runner (PID $($process.Id)). Logs: $runtimeDirectory"
if (-not $NoInspector) { Write-Output 'Inspector enabled. Read it with .\tools\Inspect-Runtime.ps1' }
