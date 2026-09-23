$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
function Load-Elf([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ([BitConverter]::ToUInt32($bytes,0) -ne 0x464C457F) { throw "Not an ELF: $Path" }
    $segments = @()
    $phoff = [BitConverter]::ToUInt32($bytes,28)
    $phsize = [BitConverter]::ToUInt16($bytes,42)
    $phcount = [BitConverter]::ToUInt16($bytes,44)
    for ($i=0; $i -lt $phcount; $i++) {
        $p = $phoff + $i*$phsize
        if ([BitConverter]::ToUInt32($bytes,$p) -eq 1) {
            $segments += [pscustomobject]@{Offset=[BitConverter]::ToUInt32($bytes,$p+4);Address=[BitConverter]::ToUInt32($bytes,$p+8);Size=[BitConverter]::ToUInt32($bytes,$p+16)}
        }
    }
    return [pscustomobject]@{Bytes=$bytes; Segments=$segments}
}
function Get-FunctionHashes($Elf, [string]$CsvPath) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($row in Import-Csv -LiteralPath $CsvPath) {
            $start = [Convert]::ToUInt32($row.Start.Substring(2),16)
            $end = [Convert]::ToUInt32($row.End.Substring(2),16)
            $size = $end-$start
            if ($size -lt 32) { continue }
            foreach ($segment in $Elf.Segments) {
                if ($start -ge $segment.Address -and $end -le ($segment.Address+$segment.Size)) {
                    $offset = $segment.Offset+$start-$segment.Address
                    $hash = [BitConverter]::ToString($sha.ComputeHash($Elf.Bytes, $offset, $size)).Replace('-','')
                    [pscustomobject]@{Name=$row.Name;Start=$row.Start;Size=$size;Hash=$hash}
                    break
                }
            }
        }
    } finally { $sha.Dispose() }
}
$prototype = Load-Elf (Join-Path $workspace 'Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)/SLUS_201.74')
$retail = Load-Elf (Join-Path $workspace 'Extracted_Assets/Rumble Racing (USA retail)/SLUS_201.74')
$prototypeFunctions = @(Get-FunctionHashes $prototype (Join-Path $workspace 'CSV Map/map-ntsc.csv'))
$retailFunctions = @(Get-FunctionHashes $retail (Join-Path $workspace 'CSV Map/retail/map.csv'))
$retailByHash = @{}
foreach ($group in ($retailFunctions | Group-Object Hash)) { if ($group.Count -eq 1) { $retailByHash[$group.Name] = $group.Group[0] } }
$matches = @(foreach ($group in ($prototypeFunctions | Group-Object Hash)) {
    if ($group.Count -ne 1 -or -not $retailByHash.ContainsKey($group.Name)) { continue }
    $p = $group.Group[0]; $r = $retailByHash[$group.Name]
    if ($p.Name -match '^(FUN_|sub_|LAB_|entry)') { continue }
    [pscustomobject]@{PrototypeName=$p.Name;PrototypeAddress=$p.Start;RetailAddress=$r.Start;Bytes=$p.Size;Method='Unique identical function bytes, minimum 32 bytes'}
})
$matches | Sort-Object PrototypeAddress | Export-Csv -LiteralPath (Join-Path $workspace 'analysis/retail-exact-function-matches.csv') -NoTypeInformation
$prototypeText = [Text.Encoding]::ASCII.GetString($prototype.Bytes)
$retailText = [Text.Encoding]::ASCII.GetString($retail.Bytes)
$strings = foreach ($command in @('FPS','POLYINFO','POLYCNT','VURCNT','OBJCNT','MEM','TRAILS','DUMP','CHKSUM','DISP','VU1','SHOW','SET','LINES','HELP')) {
    $pattern = '(?<![\x20-\x7e])' + [regex]::Escape($command) + '\x00'
    [pscustomobject]@{Command=$command;PrototypeStringCount=[regex]::Matches($prototypeText,$pattern).Count;RetailStringCount=[regex]::Matches($retailText,$pattern).Count}
}
$strings | Export-Csv -LiteralPath (Join-Path $workspace 'analysis/debug-command-strings.csv') -NoTypeInformation
Write-Output "Found $($matches.Count) uniquely matching named functions; matches are evidence of identical bytes, not complete semantic equivalence."
$strings | Format-Table -AutoSize
