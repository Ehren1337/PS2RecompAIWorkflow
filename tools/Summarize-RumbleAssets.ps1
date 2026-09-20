[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$workspace=Split-Path $PSScriptRoot -Parent
$reportPath=Join-Path $workspace 'analysis/assets-investigation.json'
$report=Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
function Offset-Of($elf,[uint32]$address) {
    foreach($s in $elf.Segments) { if($address -ge $s.Address -and $address -lt $s.Address+$s.Size) { return [int]($s.Offset+$address-$s.Address) } }
    return -1
}
function Guest-String($elf,[uint32]$address,[switch]$AllowControls) {
    $p=Offset-Of $elf $address
    if($p -lt 0) { return $null }
    $end=$p
    while($end -lt $elf.Bytes.Length -and ($elf.Bytes[$end] -ge 32 -or ($AllowControls -and $elf.Bytes[$end] -gt 0)) -and $elf.Bytes[$end] -le 126 -and $end-$p -lt 200) { $end++ }
    if($end -eq $p -or $end -ge $elf.Bytes.Length -or $elf.Bytes[$end] -ne 0) { return $null }
    return [Text.Encoding]::ASCII.GetString($elf.Bytes,$p,$end-$p)
}
$inventories=foreach($game in $report.Builds) {
    $elfFile=Get-ChildItem -LiteralPath $game.Root -Recurse -File | Where-Object Name -match '^SL[EU]S_' | Select-Object -First 1
    $bytes=[IO.File]::ReadAllBytes($elfFile.FullName)
    $segments=@()
    $phoff=[BitConverter]::ToUInt32($bytes,28); $phsize=[BitConverter]::ToUInt16($bytes,42); $phcount=[BitConverter]::ToUInt16($bytes,44)
    for($i=0;$i -lt $phcount;$i++){ $p=$phoff+$i*$phsize; if([BitConverter]::ToUInt32($bytes,$p) -eq 1){$segments += [pscustomobject]@{Offset=[BitConverter]::ToUInt32($bytes,$p+4);Address=[BitConverter]::ToUInt32($bytes,$p+8);Size=[BitConverter]::ToUInt32($bytes,$p+16)}} }
    $elf=[pscustomobject]@{Bytes=$bytes;Segments=$segments}
    if($game.Build -eq 'February') { $prototypeElf=$elf }
    $banger=$game.ElfStrings | Where-Object Text -ceq 'The Banger' | Select-Object -First 1
    $address=0
    foreach($s in $segments){if($banger.Offset -ge $s.Offset -and $banger.Offset -lt $s.Offset+$s.Size){$address=$s.Address+$banger.Offset-$s.Offset}}
    $table=-1
    for($p=0;$p -lt $bytes.Length-1008;$p+=4) {
        if([BitConverter]::ToUInt32($bytes,$p) -ne $address){continue}
        if((Guest-String $elf ([BitConverter]::ToUInt32($bytes,$p+28))) -eq 'Silver Streak' -and
           (Guest-String $elf ([BitConverter]::ToUInt32($bytes,$p+56))) -eq 'Widow Maker') { $table=$p;break }
    }
    if($table -lt 0){throw "Cannot locate verified driver table in $($game.Build)"}
    $drivers=@(for($i=0;$i -lt 36;$i++) {
        $p=$table+$i*28; $name=Guest-String $elf ([BitConverter]::ToUInt32($bytes,$p))
        if(-not $name){throw "Invalid driver-table row $i"}
        [pscustomobject]@{Index=$i;Name=$name;ModelIndex=$bytes[$p+6];ResourceIds=@((10000+3*$bytes[$p+6]),(10001+3*$bytes[$p+6]),(10002+3*$bytes[$p+6]))}
    })
    $payloads=@(foreach($archive in $game.Archives){foreach($resource in $archive.Resources){
        [pscustomobject]@{Archive=$archive.Path;Type=$resource.Type;Id=$resource.Id;Name=$resource.ResourceName;Offset=$resource.Offset;Bytes=$resource.DecodedBytes;SHA256=$resource.SHA256}
    }})
    $models=@($payloads | Where-Object {$_.Type -eq 'o3d ' -and $_.Id -ge 10000} | Sort-Object Id -Unique)
    $unmapped=@($models | Where-Object { [int][math]::Floor(($_.Id-10000)/3) -notin $drivers.ModelIndex })
    $duplicateModels=@(foreach($model in $unmapped){ [pscustomobject]@{Id=$model.Id;Name=$model.Name;IdenticalOtherIds=@($models | Where-Object { $_.Id -ne $model.Id -and $_.SHA256 -eq $model.SHA256 } | ForEach-Object Id)} })
    $trackStrings=@($game.ElfStrings | Where-Object Text -match '^[A-Z0-9_]+\.TRK$' | ForEach-Object Text | Sort-Object -Unique)
    $trackFiles=@($game.Manifest | Where-Object Path -match '/LOC[^/]+/[^/]+\.TRK$' | ForEach-Object Path | Sort-Object)
    $missing=@($trackStrings | Where-Object { $leaf=$_; -not ($game.Manifest.Path | Where-Object { ($_ -split '/')[-1] -eq $leaf }) })
    [pscustomobject]@{Build=$game.Build;DriverTableFileOffset=$table;Drivers=$drivers;VehicleModels=$models;ModelsNotMappedByDriverTable=$unmapped;DuplicateUnusedModels=$duplicateModels;TrackFiles=$trackFiles;MissingTrackFileReferences=$missing;Payloads=$payloads}
}
$retail=$inventories | Where-Object Build -eq Retail
$retailNames=@{}; $retailHashes=@{}
foreach($p in $retail.Payloads){if($p.Name){$retailNames[$p.Name]=$true};if($p.SHA256){$retailHashes[$p.SHA256]=$true}}
$comparisons=foreach($build in $inventories | Where-Object Build -ne Retail) {
    $absent=@($build.Payloads | Where-Object {$_.Name -and -not $retailNames.ContainsKey($_.Name)} | Sort-Object Name,SHA256 -Unique)
    $different=@($absent | Where-Object {-not $retailHashes.ContainsKey($_.SHA256)})
    $carComparison=foreach($model in $build.VehicleModels) {
        $match=$retail.VehicleModels | Where-Object Id -eq $model.Id | Select-Object -First 1
        [pscustomobject]@{Id=$model.Id;PrototypeName=$model.Name;RetailName=$match.Name;IdenticalBytes=($model.SHA256 -eq $match.SHA256)}
    }
    [pscustomobject]@{Build=$build.Build;NamedPayloadsAbsentByRetailName=$absent;NamedPayloadsAbsentByRetailNameAndHash=$different;VehicleComparison=@($carComparison)}
}
$report | Add-Member -Force NoteProperty Inventories @($inventories)
$report | Add-Member -Force NoteProperty Comparisons @($comparisons)
$decompiled=Get-Content (Join-Path $workspace 'analysis/prototype-asset-functions.c') -Raw
$commands=@(foreach($m in [regex]::Matches($decompiled,'CO_vRegisterCommand\(0x([0-9a-f]+),0x([0-9a-f]+)')) {
    [pscustomobject]@{Name=(Guest-String $prototypeElf ([Convert]::ToUInt32($m.Groups[1].Value,16)));Callback=('0x'+$m.Groups[2].Value)}
})
$buttons=@(foreach($m in [regex]::Matches($decompiled,'CO_vRegisterButton\(([01]),(0x[0-9a-f]+|[0-9]+),0x([0-9a-f]+)')) {
    [pscustomobject]@{Device=[int]$m.Groups[1].Value;Code=$m.Groups[2].Value;Command=(Guest-String $prototypeElf ([Convert]::ToUInt32($m.Groups[3].Value,16)) -AllowControls)}
})
$report | Add-Member -Force NoteProperty PrototypeConsole ([pscustomobject]@{Commands=$commands;Buttons=$buttons;DefaultInputMask=2;Device0='controller';Device1='translated keyboard character code, not necessarily raw USB scancode'})
$report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $reportPath -Encoding utf8
foreach($item in $inventories) {
    "$($item.Build): $($item.Drivers.Count) driver-table rows; $($item.VehicleModels.Count) unique vehicle model IDs; missing tracks: $($item.MissingTrackFileReferences -join ', ')"
    $item.ModelsNotMappedByDriverTable | Select-Object Id,Name | Format-Table
}
foreach($item in $comparisons) { "$($item.Build): identical vehicle payloads $(@($item.VehicleComparison | Where-Object IdenticalBytes).Count)/$($item.VehicleComparison.Count); named payloads absent in retail by name and hash $($item.NamedPayloadsAbsentByRetailNameAndHash.Count)" }
