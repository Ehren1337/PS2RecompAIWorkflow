[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
Add-Type -Path (Join-Path $PSScriptRoot 'RumbleAssetReader.cs')
$builds = @(
    @{Name='February';Folder='Rumble Racing (Feb 7, 2001 prototype)'},
    @{Name='March';Folder='Rumble Racing (Mar 27, 2001 prototype)'},
    @{Name='Retail';Folder='Rumble Racing (USA retail)'}
)
$results = foreach($build in $builds) {
    $root = Join-Path $workspace "Extracted_Assets/$($build.Folder)"
    $files = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.FullName -notmatch '\\Redump\\' })
    $manifest = foreach($file in $files) {
        $relative = $file.FullName.Substring($root.Length+1).Replace('\','/') -replace '^ELF/',''
        [pscustomobject]@{Path=$relative;Bytes=$file.Length;SHA256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash}
    }
    $elf = $files | Where-Object { $_.Name -match '^SL[EU]S_' } | Select-Object -First 1
    $strings = [RumbleAssetReader]::Strings([IO.File]::ReadAllBytes($elf.FullName))
    $archives = foreach($file in $files | Where-Object { $_.Extension -in @('.TRK','.PS2','.AV','.AV2','.FLM') }) {
        $archive = [RumbleAssetReader]::Read($file.FullName)
        $archive.Path = $file.FullName.Substring($root.Length+1).Replace('\','/')
        Write-Host "$($build.Name) $($archive.Path): $($archive.Resources.Count) resources, $(@($archive.Resources | Where-Object Error).Count) decode errors"
        $archive
    }
    [pscustomobject]@{Build=$build.Name;Root=$root;Manifest=$manifest;ElfStrings=$strings;Archives=@($archives)}
}
$report = [pscustomobject]@{Method='Read-only SHOC/SHDR/SDAT/Rdat parser derived from February game code. Resource lists contain fixed 24-byte stored name fields, often truncated source paths. Run Validate-RumbleDecoder.ps1 to independently compare with the generated guest decoder.';Builds=@($results)}
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $workspace 'analysis/assets-investigation.json') -Encoding utf8
