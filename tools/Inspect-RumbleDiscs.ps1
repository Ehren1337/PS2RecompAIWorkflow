param([switch]$ExtractFebruary, [switch]$InspectRetail, [switch]$ExtractRetail, [switch]$VerifyMarch, [Parameter(Mandatory=$true)][string]$DiscRoot)
$ErrorActionPreference = 'Stop'

function Read-IsoBytes($Stream, [long]$Lba, [int]$Count) {
    $result = [byte[]]::new($Count)
    $done = 0
    while ($done -lt $Count) {
        [void]$Stream.Seek(($Lba * 2352) + 24, [IO.SeekOrigin]::Begin)
        $take = [Math]::Min(2048, $Count - $done)
        $read = $Stream.Read($result, $done, $take)
        if ($read -ne $take) { throw 'Unexpected end of disc image' }
        $done += $take
        $Lba++
    }
    return ,$result
}

function Read-IsoDirectory($Stream, [long]$Lba, [int]$Size, [string]$Prefix, [int]$Depth = 0) {
    if ($Depth -gt 12) { throw 'Unexpected directory depth' }
    $bytes = Read-IsoBytes $Stream $Lba $Size
    $position = 0
    while ($position -lt $bytes.Length) {
        $length = [int]$bytes[$position]
        if ($length -eq 0) {
            $position = ([int][Math]::Floor($position / 2048) + 1) * 2048
            continue
        }
        if ($length -lt 34 -or ($position + $length) -gt $bytes.Length) { throw 'Invalid ISO directory record' }
        $nameLength = [int]$bytes[$position + 32]
        if ($nameLength -eq 1 -and $bytes[$position + 33] -le 1) { $position += $length; continue }
        $name = [Text.Encoding]::ASCII.GetString($bytes, $position + 33, $nameLength) -replace ';\d+$', ''
        $extent = [BitConverter]::ToUInt32($bytes, $position + 2)
        $fileSize = [BitConverter]::ToUInt32($bytes, $position + 10)
        $path = "$Prefix$name"
        if ($bytes[$position + 25] -band 2) {
            Read-IsoDirectory $Stream $extent $fileSize "$path/" ($Depth + 1)
        } else {
            [pscustomobject]@{Path=$path; Lba=$extent; Size=$fileSize}
        }
        $position += $length
    }
}

$retailMode = $InspectRetail -or $ExtractRetail
$builds = if ($VerifyMarch) { @('Mar 27, 2001') } elseif ($retailMode) { @('Retail USA') } else { @('Feb 7, 2001', 'Mar 27, 2001') }
foreach ($build in $builds) {
    if ($ExtractFebruary -and $build -ne 'Feb 7, 2001') { continue }
    $discPath = if ($retailMode) { (Join-Path $DiscRoot 'Retail/Rumble Racing (USA)/Rumble Racing (USA).bin') } else { (Join-Path $DiscRoot "Rumble Racing ($build prototype)/RUMBLE_RACING.bin") }
    $stream = [IO.File]::OpenRead($discPath)
    try {
        $pvd = Read-IsoBytes $stream 16 2048
        if ([Text.Encoding]::ASCII.GetString($pvd, 1, 5) -ne 'CD001') { throw 'Missing ISO9660 primary volume descriptor' }
        $files = @(Read-IsoDirectory $stream ([BitConverter]::ToUInt32($pvd,158)) ([BitConverter]::ToUInt32($pvd,166)) '')
        if ($VerifyMarch) {
            $root = Join-Path (Split-Path $PSScriptRoot -Parent) 'Extracted_Assets/Rumble Racing (Mar 27, 2001 prototype)'
            $verified = 0
            foreach ($file in $files) {
                $relative = if ($file.Path -match '^SLES_') { 'ELF/' + $file.Path } else { $file.Path }
                $local = Join-Path $root $relative
                if (-not (Test-Path -LiteralPath $local) -or (Get-Item -LiteralPath $local).Length -ne $file.Size) { throw "Missing/mismatched March extraction: $relative" }
                if ($file.Path -match '\.(TRK|PS2)$|^SLES_') {
                    $data = Read-IsoBytes $stream $file.Lba $file.Size
                    $sha = [Security.Cryptography.SHA256]::Create()
                    try { $hash = [BitConverter]::ToString($sha.ComputeHash($data)).Replace('-','') } finally { $sha.Dispose() }
                    if ($hash -ne (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash) { throw "March payload hash mismatch: $relative" }
                    $verified++
                }
            }
            Write-Output "March original ISO directory: $($files.Count) files, all extracted names/sizes matched; $verified ELF/asset containers SHA256 verified."
            Write-Output "March original ISO DA4 filenames: $(@($files | Where-Object Path -match '(?i)DA4').Count)"
            continue
        }
        if ($ExtractFebruary -or $ExtractRetail) {
            $expectedDiscHash = if ($ExtractRetail) { '6547381E6056C6CB9262A77E44A03BBA1AA067BB' } else { '64B7554240F42FFCA849787AD014C70EA4B642A5' }
            if ((Get-FileHash -LiteralPath $discPath -Algorithm SHA1).Hash -ne $expectedDiscHash) { throw 'Disc checksum mismatch' }
            $destination = if ($ExtractRetail) { (Join-Path (Split-Path $PSScriptRoot -Parent) 'Extracted_Assets/Rumble Racing (USA retail)') } else { (Join-Path (Split-Path $PSScriptRoot -Parent) 'Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)') }
            [void][IO.Directory]::CreateDirectory($destination)
            $destination = (Resolve-Path -LiteralPath $destination).Path
            $prefix = $destination.TrimEnd('\') + '\'
            $expected = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            foreach ($file in $files) {
                $target = [IO.Path]::GetFullPath((Join-Path $destination $file.Path))
                if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Disc path escaped extraction directory' }
                [void]$expected.Add($target)
                [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
                $data = Read-IsoBytes $stream $file.Lba $file.Size
                [IO.File]::WriteAllBytes($target, $data)
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $expectedHash = [BitConverter]::ToString($sha.ComputeHash($data)).Replace('-','') } finally { $sha.Dispose() }
                if ((Get-FileHash -LiteralPath $target).Hash -ne $expectedHash) { throw "Extraction verification failed: $target" }
            }
            $redumpPrefix = (Join-Path $destination 'Redump').TrimEnd('\') + '\'
            $stale = @(Get-ChildItem -LiteralPath $destination -File -Recurse | Where-Object { -not $_.FullName.StartsWith($redumpPrefix, [StringComparison]::OrdinalIgnoreCase) -and -not $expected.Contains($_.FullName) })
            foreach ($item in $stale) {
                if (-not $item.FullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Stale path escaped extraction directory' }
                Remove-Item -LiteralPath $item.FullName -Force
            }
            Get-ChildItem -LiteralPath $destination -Directory -Recurse | Sort-Object { $_.FullName.Length } -Descending | ForEach-Object {
                if (-not $_.FullName.StartsWith($redumpPrefix, [StringComparison]::OrdinalIgnoreCase) -and @(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0) {
                    if (-not $_.FullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Directory path escaped extraction directory' }
                    Remove-Item -LiteralPath $_.FullName -Force
                }
            }
            Write-Output "Extracted and SHA256-verified $($files.Count) $build disc files; removed $($stale.Count) stale files."
            continue
        }
        $selected = @($files | Where-Object { $_.Path -match '(?i)(SYSTEM\.CNF$|S[CL][EUJP][SE]_\d|\.ELF$|GLBLDATA\.PS2$)' })
        $details = foreach ($file in $selected) {
            $data = Read-IsoBytes $stream $file.Lba $file.Size
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $hash = [BitConverter]::ToString($sha.ComputeHash($data)).Replace('-','') } finally { $sha.Dispose() }
            $isElf = $data.Length -gt 52 -and [BitConverter]::ToUInt32($data,0) -eq 0x464C457F
            $patch = @()
            if ($isElf) {
                $phoff = [BitConverter]::ToUInt32($data,28)
                $phsize = [BitConverter]::ToUInt16($data,42)
                $phcount = [BitConverter]::ToUInt16($data,44)
                for ($i=0; $i -lt $phcount; $i++) {
                    $p = $phoff + $i*$phsize
                    $offset = [BitConverter]::ToUInt32($data,$p+4)
                    $va = [BitConverter]::ToUInt32($data,$p+8)
                    $size = [BitConverter]::ToUInt32($data,$p+16)
                    if ([BitConverter]::ToUInt32($data,$p) -eq 1 -and 0x1AC7C4 -ge $va -and 0x1AC7C4 -lt ($va+$size)) {
                        $target = 0x1AC7C4-$va+$offset
                        $patch += ('guest 0x001AC7C4 -> file 0x{0:X8}, word 0x{1:X8}' -f $target,[BitConverter]::ToUInt32($data,$target))
                    }
                }
            }
            [pscustomobject]@{
                Path=$file.Path; Size=$file.Size; SHA256=$hash; IsElf=$isElf
                BootConfig=$(if ($file.Path -match 'SYSTEM\.CNF$') { [Text.Encoding]::ASCII.GetString($data) } else { $null })
                DocumentedOffsetWord=$(if ($isElf -and $data.Length -ge 0xDAD50) { '0x{0:X8}' -f [BitConverter]::ToUInt32($data,0xDAD4C) } else { $null })
                PatchMapping=$patch
            }
        }
        [pscustomobject]@{Build=$build; DiscSHA1=(Get-FileHash -LiteralPath $discPath -Algorithm SHA1).Hash; FileCount=$files.Count; Files=$details} | ConvertTo-Json -Depth 5
    } finally { $stream.Dispose() }
}
