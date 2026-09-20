# Reuse one generated source directory and build tree; retail is the port target.
param([ValidateSet('Retail','February')][string]$Build = 'Retail')
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
Push-Location $workspace
try {
    if (@(Get-Process -Name ps2EntryRunner -ErrorAction SilentlyContinue).Count -gt 0) {
        throw 'Close the running PS2 runner before rebuilding.'
    }
    $retail = $Build -eq 'Retail'
    $elf = if ($retail) { 'Extracted_Assets/Rumble Racing (USA retail)/SLUS_201.74' } else { 'Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)/SLUS_201.74' }
    $expected = if ($retail) { 'E3C2C19B5FDEEAC9FB1F5A9B893346E7892E564796FA2F74FC40AE17A8ADE594' } else { '44E74A35DD123E3504F81EDED1DB68844E2E082EDAA908EA47A7EA1EC9A924A0' }
    if ((Get-FileHash -LiteralPath $elf).Hash -ne $expected) { throw 'Unexpected game ELF; refusing mismatched configuration' }
    $sourceConfig = if ($retail) { 'CSV Map/retail/config.toml' } else { 'CSV Map/config-ntsc.toml' }
    $map = if ($retail) { 'CSV Map/retail/map.csv' } else { 'CSV Map/map-ntsc.csv' }
    $config = Get-Content -LiteralPath $sourceConfig -Raw
    $config = [regex]::Replace($config, '(?m)^input = .*$', ('input = "' + $elf + '"'))
    $config = [regex]::Replace($config, '(?m)^output = .*$', 'output = "./output-ghidra/"')
    $config = [regex]::Replace($config, '(?m)^ghidra_output = .*$', ('ghidra_output = "' + $map + '"'))
    if (-not $retail) {
        $config += "`n# February-only playability fix; never apply to retail.`n[patches]`ninstructions = [{ address = 0x001AC7C4, value = 0x00000000 }]`n"
    }
    Set-Content -LiteralPath config-ghidra.toml -Value $config -Encoding utf8
    $outputRoot = (Resolve-Path -LiteralPath output-ghidra).Path
    if ($outputRoot -ne (Join-Path $workspace 'output-ghidra')) { throw 'Unexpected output path' }
    $oldFiles = @(Get-ChildItem -LiteralPath $outputRoot -File)
    $oldRuntimeFiles = @{}
    foreach ($item in $oldFiles) {
        if ($item.Extension -notin @('.cpp','.h')) { throw "Unexpected generated file: $($item.Name)" }
        $folder = if ($item.Extension -eq '.h') { 'PS2Recomp/ps2xRuntime/include' } else { 'PS2Recomp/ps2xRuntime/src/runner' }
        $target = Join-Path $workspace (Join-Path $folder $item.Name)
        if ((Test-Path -LiteralPath $target) -and (Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath $item.FullName).Hash) {
            throw "Preserving independently edited runtime file: $target"
        }
        $oldRuntimeFiles[$target] = (Get-FileHash -LiteralPath $item.FullName).Hash
    }
    foreach ($item in $oldFiles) {
        Remove-Item -LiteralPath $item.FullName -Force
    }
    & .\PS2Recomp\out\build\ps2xRecomp\Debug\ps2_recomp.exe .\config-ghidra.toml *> .\PS2Recomp\out\build\ntsc-recompile.log
    if ($LASTEXITCODE -ne 0) { throw 'Recompilation failed; see ntsc-recompile.log' }
    # Preserve unchanged timestamps so correcting one function does not rebuild
    # every generated translation unit. No alternate output tree or backup.
    $currentRuntimeFiles = @{}
    foreach ($item in @(Get-ChildItem -LiteralPath $outputRoot -File)) {
        if ($item.Extension -notin @('.cpp','.h')) { throw "Unexpected generated file: $($item.Name)" }
        $folder = if ($item.Extension -eq '.h') { 'PS2Recomp/ps2xRuntime/include' } else { 'PS2Recomp/ps2xRuntime/src/runner' }
        $target = Join-Path $workspace (Join-Path $folder $item.Name)
        $currentRuntimeFiles[$target] = $true
        $newHash = (Get-FileHash -LiteralPath $item.FullName).Hash
        if (-not (Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $target).Hash -ne $newHash) {
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
    foreach ($target in $oldRuntimeFiles.Keys) {
        if (-not $currentRuntimeFiles.ContainsKey($target) -and (Test-Path -LiteralPath $target)) {
            if ((Get-FileHash -LiteralPath $target).Hash -ne $oldRuntimeFiles[$target]) {
                throw "Preserving runtime file changed during regeneration: $target"
            }
            Remove-Item -LiteralPath $target -Force
        }
    }
    # Normalize PATH/Path for MSBuild and avoid persistent compiler nodes.
    $cmakeDriver = 'import os,subprocess,sys; e={k.upper():v for k,v in os.environ.items()}; e["MSBUILDDISABLENODEREUSE"]="1"; e["CL"]=e.get("CL","")+" /MP4"; f=open(sys.argv[1],"w"); r=subprocess.run(sys.argv[2:],env=e,stdout=f,stderr=subprocess.STDOUT); f.close(); sys.exit(r.returncode)'
    py -3 -B -c $cmakeDriver .\PS2Recomp\out\build\ntsc-configure.log cmake -S .\PS2Recomp -B .\PS2Recomp\out\build -DFETCHCONTENT_UPDATES_DISCONNECTED=ON -DPS2X_ENABLE_RUNTIME_LOGS=ON
    if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed; see ntsc-configure.log' }
    py -3 -B -c $cmakeDriver .\PS2Recomp\out\build\ntsc-build.log cmake --build .\PS2Recomp\out\build --config Debug --target ps2EntryRunner -- /nodeReuse:false
    if ($LASTEXITCODE -ne 0) { throw 'Build failed; see ntsc-build.log' }
    Write-Output "$Build regeneration and build completed successfully."
} finally { Pop-Location }
