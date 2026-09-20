# Working with this toolkit

This is the portable companion to our personal workspace guide. It includes the actual tools through this repository and its patch; it does not embed game data or assume our private files exist. Run commands from this repository's root. Windows examples use PowerShell and `py -3 -B`; other hosts can use `python3 -B` for portable Python tools.

## Understand the layers

| Layer | What belongs here |
| --- | --- |
| Standard PS2Recomp | ELF analysis, Ghidra CSV/TOML exporter, C++ generation, guest memory/dispatch, runtime and debugger. |
| Our general additions | Structured inspector, bounded CPU history, frame capture/contact sheets, hidden window mode, renderer/scheduler/IOP/audio corrections and focused tests. |
| Development helpers | Python experiments, inspector reader, local browser viewer, rebuild and headless-analysis helpers. These are custom tools, not upstream commands. |
| Rumble-specific research | Exact-build hashes, verified names/addresses, asset formats, menu watches, normal-input routes and native picture/audio profiles. Adapt deliberately for another game. |
| Unfinished | Playable racing, complete audio behavior, debug/content restoration and validation on other operating systems. Passing a test or rendering a menu is not proof of completion. |

The intended player-facing port is native C++. Python speeds up research and test orchestration; there is no embedded Python/pybind11 layer in this package.

## Analyze and generate your own game

1. Complete README setup. Keep one `PS2Recomp/out/build` directory and reuse it.
2. Put your own executable and extracted assets under an ignored local directory such as `private/YOUR_GAME/`. Record region, revision, executable hash and module hashes. A shared SLUS/SLES filename does not prove identical code.
3. Import the ELF into Ghidra with a suitable PS2/R5900 processor definition. Ghidra extensions, their installation and compatibility are separate prerequisites. Confirm the language, entry point and function boundaries before export. See the pinned checkout's `ps2xAnalyzer/Readme.md`.
4. Add `PS2Recomp/ps2xRecomp/tools/ghidra` to Ghidra's script directories. Run `ExportPS2Functions.java` and save its TOML and CSV under your private game directory. The scripts in `tools/ghidra` add our research workflow; the Rumble imports are not generic imports.
5. Start from that exported TOML. Set `general.input`, `general.output` and `general.ghidra_output` relative to the working directory. [config.template.toml](examples/config.template.toml) illustrates the fields only. Preserve verified exporter classifications; fewer stubs alone is neither good nor bad.
6. Recompile, place the generated headers and `.cpp` files in the runtime's include/runner directories, reconfigure, then build the runner. Keep generated code local. The first-time example below assumes a fresh checkout with no generated game code or handwritten edits to those destination files.

```powershell
New-Item -ItemType Directory -Force output-ghidra | Out-Null
& .\PS2Recomp\out\build\ps2xRecomp\Debug\ps2_recomp.exe .\private\YOUR_GAME\config.toml
if ($LASTEXITCODE -ne 0) { throw 'Recompilation failed; do not stage partial output.' }
# First generation only. This also replaces the upstream placeholder registration file.
Copy-Item .\output-ghidra\*.h .\PS2Recomp\ps2xRuntime\include\
Copy-Item .\output-ghidra\*.cpp .\PS2Recomp\ps2xRuntime\src\runner\
cmake -S PS2Recomp -B PS2Recomp/out/build -DPS2X_ENABLE_RUNTIME_LOGS=ON
cmake --build PS2Recomp/out/build --config Debug --target ps2EntryRunner
```

The exported TOML's output must be `output-ghidra/` for these commands. Single-configuration generators may omit the `Debug` executable subdirectory. For repeated generation, track which files your generator owns, preserve handwritten edits and remove only verified obsolete generated files. Our Rumble rebuild script implements that policy for its two known builds; adapt it before using another game.

## Inspector, frames and viewer

After building a runner for your exact ELF, enable the custom inspector before launch:

```powershell
$runtime = Join-Path $PWD 'PS2Recomp/out/build/ps2xRuntime'
$env:PS2_INSPECTOR_FILE = Join-Path $runtime 'inspector.json'
$env:PS2_INSPECTOR_FRAME = '1'
$env:PS2_WINDOW_HIDDEN = '1' # Optional: keep rendering for the browser without a game window.
$elf = (Resolve-Path .\private\YOUR_GAME\YOUR_GAME.ELF).Path
Start-Process -FilePath (Join-Path $runtime 'Debug/ps2EntryRunner.exe') `
    -ArgumentList ('"' + $elf + '"') -WorkingDirectory (Join-Path $runtime 'Debug') `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'runner-stdout.log') `
    -RedirectStandardError (Join-Path $runtime 'runner-stderr.log')
py -3 -B tools/runtime_viewer.py
```

Use a separate terminal for commands while the viewer serves localhost. Do not launch a second runner over the same outputs. To use another capture directory: `py -3 -B tools/runtime_viewer.py --runtime-dir <DIRECTORY>`. To see a native window, omit hidden mode and use normal window visibility.

| Reused file | Meaning |
| --- | --- |
| `inspector.json` | Latest structured runtime report, with at most 60 CPU samples and bounded subsystem histories. |
| `inspector.png` | Latest sampled image from the runtime's presented pixel buffer. |
| `inspector.frames.png` | Contact sheet of eight recent presented frames with timing/buffer labels. |

Capture keeps eight recent frames in memory and writes the latest PNG/contact sheet at roughly one-second intervals. Files are replaced, with transient files used for atomic publication. This is not desktop recording or a separate image for every frame. The browser polls the existing files; it does not call an AI service. Only separately submitting an image to a model makes it model input. Start with one image for routine checks and use a contact sheet for flicker or frame-order problems.

```powershell
.\tools\Inspect-Runtime.ps1 -LogTail 20
.\tools\Inspect-Runtime.ps1 -Json
```

The reader accepts `-Path <INSPECTOR_JSON>`. Its automatic symbol lookup only knows the two Rumble folder conventions. For another game, adapt symbol resolution and verify schema version 1. Check PID, executable identity, capture time, sequence and relevant counters together. A recently written report can still contain an old CPU sample. A retained viewer image is not proof that the game is advancing.

Optional RAM watches use `PS2_INSPECTOR_WATCHES`, with entries such as `name=0xADDRESS:BYTES` or `name=*0xPOINTER_ADDRESS:BYTES`, separated by semicolons. Substitute only verified addresses for your exact build. Watches read memory; they are not menu actions.

## Rumble workspace conventions

These helpers expect the following private inputs relative to this repository:

| Input | Expected location |
| --- | --- |
| Retail executable/assets | `Extracted_Assets/Rumble Racing (USA retail)/SLUS_201.74` and sibling assets |
| February executable/assets | `Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)/SLUS_201.74` and sibling assets |
| March comparison assets | `Extracted_Assets/Rumble Racing (Mar 27, 2001 prototype)/` |
| Retail exports | `CSV Map/retail/config.toml` and `CSV Map/retail/map.csv` |
| February exports | `CSV Map/config-ntsc.toml` and `CSV Map/map-ntsc.csv` |
| Generated runtime code | `output-ghidra/` |
| Private research outputs | `analysis/` |

Create the output directories before the first export/rebuild. Supply your own inputs; none are distributed here. Research subcommands can require additional locally produced reports. Keep the identity guards: retail and prototype addresses must not be interchanged.

```powershell
# After your own Ghidra exports exist and the runner is stopped:
New-Item -ItemType Directory -Force output-ghidra, analysis | Out-Null
.\tools\Rebuild-Game.ps1 -Build Retail
.\run-ntsc.ps1 -Build Retail -ViewerOnly
py -3 -B tools/rumble_navigate.py status
py -3 -B tools/rumble_navigate.py route vehicle
py -3 -B tools/rumble_navigate.py wait track --timeout 60
```

`route` sends normal input through verified states; `wait` only waits. It checks readiness, transition state, PID and freshness, and stops on unsupported/unknown conditions. It does not directly call menu functions, patch game state, or automatically restart. Its verified route stops at track selection; do not treat that as an automated gameplay test. Movie skipping uses normal input after recognizing playback. Confirm ordinary behavior before automating a route.

`Rebuild-Game.ps1` is a Windows/MSBuild helper. It overwrites the active config and verified generated files and reuses build/log paths. Its February-only instruction patch does not apply to retail. The native code's game guards do not make these helper scripts universal.

## Ghidra helpers

Set `GHIDRA_HOME` to your installation; the wrapper uses that distribution's launcher and supported JDK. Example for an already imported, analyzed retail program:

```powershell
$env:GHIDRA_HOME = '<GHIDRA_INSTALLATION>'
New-Item -ItemType Directory -Force 'CSV Map/retail' | Out-Null
.\tools\Invoke-Ghidra.ps1 -HeadlessArguments @(
    'Ghidra Project', '<PROJECT_NAME>', '-process', 'SLUS_201.74', '-noanalysis',
    '-scriptPath', 'tools/ghidra;PS2Recomp/ps2xRecomp/tools/ghidra',
    '-postScript', 'ImportRumbleRetail.java',
    '-postScript', 'ExportPS2Functions.java',
    'CSV Map/retail/config.toml', 'CSV Map/retail/map.csv'
)
```

Use the project/program name you actually imported. This command edits its Ghidra analysis database and replaces exports. Close the same project in Ghidra before headless access. It neither imports an ELF nor substitutes for initial analysis.

| Script | Role / local inputs |
| --- | --- |
| `ConfigurePS2Analysis.java` | Our analysis-option preset; review options against your Ghidra version. |
| `ImportRumbleXmap.java` | February linker-map importer; arguments are your XMAP path and report path. |
| `ImportRumbleRetail.java` | Hash-guarded, individually verified retail function names and boundary repairs. |
| `ExportRumbleDebug.java` | Focused local decompilation report; also accepts `- <FUNCTION_NAME>` for a console query. |
| `ExportRumbleAudio.java` | Verified February/retail IOP audio analysis; argument is a private output C path. |
| `ExportRumbleAssets.java` | Selected resource/debug function analysis; argument is a private output C path. |

Exporters can produce decompiled game code locally. Those products belong in ignored `analysis/`, never in this public repository. Original linker maps and full exported symbol databases are not included.

## Other research tools

| Tool | Use and prerequisites |
| --- | --- |
| `rumble_research.py` | Function/runtime/movie/picture/music/bank queries and explicit input. Use `--help`; decode comparisons require local FFmpeg tools. Some queries require private Ghidra or asset reports. `runtime --diff` overwrites one small state file. |
| `rumble_menu_research.py` | Read-only menu-label/function/caller research using private binaries and exports. |
| `Inspect-RumbleDiscs.ps1` | `-DiscRoot <YOUR_DISC_DIRECTORY>` plus inspection/extraction switches. Expects the original per-build subfolder names from the script, and 2352-byte-sector images. Extraction overwrites matching files and removes stale files inside its designated extraction folder; use a dedicated folder. |
| `Compare-RumbleBuilds.ps1` | Compare the three locally supplied builds. |
| `Investigate-RumbleAssets.ps1`, `RumbleAssetReader.cs` | Parse local Rumble containers into a reusable private report. |
| `Summarize-RumbleAssets.ps1` | Summarize that report. |
| `Validate-RumbleDecoder.ps1` | Optional native validation harness; requires your generated February decoder and `-VcVarsPath <VCVARS64_BAT>`. Produces one reused validation executable in the existing build tree. No generated decoder is bundled. |

## Reusable AI prompts

**Start another game:**

> Read README.md and WORKFLOW.md. Use the pinned PS2Recomp patch and generic diagnostics. Identify my game/region/revision and hash its ELF/modules before analysis. Create a separately named game helper; do not reuse Rumble addresses or overwrite its tools. Prefer Ghidra exports for stripped code. Use small Python experiments to test hypotheses, then implement reliable runtime behavior in portable C++. Work toward a visible, interactive result and state what remains unverified. Keep game data and generated code private, reuse reports/build outputs, and create no backups.

**Debug one blocker:**

> Verify the runner and current evidence first. Find the first unmet condition using inspector state, caller/function analysis and one fresh image. Use a contact sheet if timing/flicker matters. Test a concrete hypothesis in Python when useful; use native tests for C++ ownership, scheduling, audio or rendering behavior. Do not hide unsupported behavior with success stubs. Confirm the visible result and stop broad testing once the relevant checks pass.

**Automate a repeated route:**

> Observe the ordinary flow first. Put test navigation in a separate game script. Wait for fresh, verified source readiness, send normal input, then wait for destination readiness or a clear failure. Use bounded timeouts and stop if PID/build/state changes. Skip a movie with normal input only after playback starts. Do not patch progression or equate a timeout with a stopped process.

**Handoff record boilerplate:**

```text
Game / region / revision / hashes:
Upstream revision and local source changes:
Config / map / generated output / build locations:
Last observed visible milestone:
Current runner identity and evidence freshness:
First unmet condition and supporting evidence:
Implemented and tested (exact scope):
Edited but not yet built or verified:
Next experiment and success condition:
Unverified gameplay / audio / platform behavior:
Reused report paths and bounded history:
```
