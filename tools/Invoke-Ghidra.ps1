# Use the selected Ghidra distribution's launcher and supported JDK.
param(
    [Parameter(Mandatory=$true)][string[]]$HeadlessArguments,
    [string]$GhidraHome = $env:GHIDRA_HOME
)
$ErrorActionPreference = 'Stop'
if (-not $GhidraHome) { throw 'Set GHIDRA_HOME or pass -GhidraHome to your Ghidra installation.' }
$launcher = Join-Path $GhidraHome 'support/analyzeHeadless.bat'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    $launcher = Join-Path $GhidraHome 'support/analyzeHeadless'
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Ghidra headless launcher not found.' }
& $launcher @HeadlessArguments
if ($LASTEXITCODE -ne 0) { throw "Ghidra exited with code $LASTEXITCODE" }
