# Working with this toolkit

This is the portable companion to our personal workspace guide. It includes the actual tools through this repository and its patch; it does not embed game data or assume our private files exist. Run commands from this repository's root. Windows examples use PowerShell and `py -3 -B`; other hosts can use `python3 -B` for portable Python tools.

## Understand the layers

| Layer | What belongs here |
| --- | --- |
| Standard PS2Recomp | ELF analysis, Ghidra CSV/TOML exporter, C++ generation, guest memory/dispatch, runtime and debugger. |
| Our general additions | Structured inspector, bounded CPU history, frame capture/contact sheets, hidden window mode, renderer/scheduler/IOP/audio corrections and focused tests. |
| Development helpers | Python experiments, inspector reader, local browser viewer, rebuild and headless-analysis helpers. These are custom tools, not upstream commands. |
| Rumble-specific research | Exact-build hashes, verified names/addresses, asset formats, menu watches, normal-input routes and native picture/audio profiles. Adapt deliberately for another game. |
| Unfinished | Exhaustive race/results coverage, busy-scene slow motion, remaining visual defects, complete audio behavior, debug/content restoration and validation on other operating systems. Passing a test or rendering a menu is not proof of completion. |

The intended player-facing port is native C++. Python speeds up research and test orchestration; there is no embedded Python/pybind11 layer in this package.

## Dependencies and renderers

Configure `PS2X_ENABLE_DILIGENT_GS=ON` for the experimental GPU path. CMake fetches DiligentCore revision `b37336e5aac0c944a6d8f51b9f453ba3813738b5` and required sources; the whole engine/samples are not required. Select `PS2_GS_GPU=d3d11`, `d3d12` or `vulkan` at launch on supported hosts. Current live work uses D3D11. Shared OpenGL shader tests pass, but the game's OpenGL adapter and non-Windows operation remain unvalidated. DX10 is not provided. DX12 needs DXC/Shader Model 6 and a discoverable `dxcompiler.dll`.

`PS2X_ENABLE_FFMPEG=ON` builds movie support. Windows uses the configured shared FFmpeg SDK download; `-DPS2X_FFMPEG_ROOT=<SDK_DIRECTORY>` reuses an SDK with `include/` and `bin/`, import libraries and runtime DLLs. Other hosts need pkg-config development packages `libavcodec`, `libavformat`, `libavutil`, `libswresample`, `libswscale`. `ffmpeg.exe` alone does not satisfy library dependencies. Some Python offline decode comparisons separately need FFmpeg command-line tools. Movie decoding is not the PS2 audio-driver adapter.

CMake also resolves the configured raylib/ImGui dependencies. See patched runtime CMakeLists.txt for platform conditions. Retain third-party licenses when distributing binaries. This repository contains no SDK DLLs or game executables.

## Analyze and generate your own game

1. Complete README setup. Keep one `PS2Recomp/out/build` directory and reuse it.
2. Put your own executable and extracted assets under an ignored local directory such as `private/YOUR_GAME/`. Record region, revision, executable hash and module hashes. A shared SLUS/SLES filename does not prove identical code.
3. Import the ELF into Ghidra with a suitable PS2/R5900 processor definition. Ghidra extensions, their installation and compatibility are separate prerequisites. Confirm the language, entry point and function boundaries before export. See the pinned checkout's `ps2xAnalyzer/Readme.md`.
4. Add `PS2Recomp/ps2xRecomp/tools/ghidra` to Ghidra's script directories. Run `ExportPS2Functions.java` and save its TOML and CSV under your private game directory. The scripts in `tools/ghidra` add our research workflow; the Rumble imports are not generic imports.
5. Start from that exported TOML. Set `general.input`, `general.output` and `general.ghidra_output` relative to the working directory. [config.template.toml](examples/config.template.toml) illustrates the fields only. Preserve verified exporter classifications; fewer stubs alone is neither good nor bad.
6. Recompile, place the generated headers and `.cpp` files in the runtime's include/runner directories, reconfigure, then build the runner. Keep generated code local. The first-time example below assumes a fresh checkout with no generated game code or handwritten edits to those destination files.

```powershell
New-Item -ItemType Directory -Force output-ghidra | Out-Null
& .\PS2Recomp\out\build\ps2xRecomp\RelWithDebInfo\ps2_recomp.exe .\private\YOUR_GAME\config.toml
if ($LASTEXITCODE -ne 0) { throw 'Recompilation failed; do not stage partial output.' }
# First generation only. This also replaces the upstream placeholder registration file.
Copy-Item .\output-ghidra\*.h .\PS2Recomp\ps2xRuntime\include\
Copy-Item .\output-ghidra\*.cpp .\PS2Recomp\ps2xRuntime\src\runner\
cmake -S PS2Recomp -B PS2Recomp/out/build -DPS2X_ENABLE_RUNTIME_LOGS=ON
cmake --build PS2Recomp/out/build --config RelWithDebInfo --target ps2EntryRunner
```

The exported TOML's output must be `output-ghidra/` for these commands. Single-configuration generators may omit the configuration executable subdirectory. For repeated generation, track which files your generator owns, preserve handwritten edits and remove only verified obsolete generated files. Our Rumble rebuild script implements that policy for its two known builds; adapt it before using another game.

## Inspector, frames and viewer

After building a runner for your exact ELF, enable the custom inspector before launch:

```powershell
$runtime = Join-Path $PWD 'PS2Recomp/out/build/ps2xRuntime'
$env:PS2_INSPECTOR_FILE = Join-Path $runtime 'inspector.json'
$env:PS2_INSPECTOR_FRAME = '1'
$env:PS2_WINDOW_HIDDEN = '1' # Optional: keep rendering for the browser without a game window.
$elf = (Resolve-Path .\private\YOUR_GAME\YOUR_GAME.ELF).Path
Start-Process -FilePath (Join-Path $runtime 'RelWithDebInfo/ps2EntryRunner.exe') `
    -ArgumentList ('"' + $elf + '"') -WorkingDirectory (Join-Path $runtime 'RelWithDebInfo') `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'runner-stdout.log') `
    -RedirectStandardError (Join-Path $runtime 'runner-stderr.log')
py -3 -B tools/runtime_viewer.py
```

Use a separate terminal for commands while the viewer serves localhost. Do not launch a second runner over the same outputs. To use another capture directory: `py -3 -B tools/runtime_viewer.py --runtime-dir <DIRECTORY>`. To see a native window, clear `PS2_WINDOW_HIDDEN`; keep `-WindowStyle Hidden` to hide only the redirected console. Set `PS2_INSPECTOR_FRAME='0'` before launching to disable image capture while retaining text diagnostics.

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
.\tools\Rebuild-Game.ps1 -Build Retail -Configuration RelWithDebInfo
py -3 -B Scripts/rumble_dev.py launch --720p --no-frame-capture --no-ai --no-max-upgrades
py -3 -B tools/rumble_navigate.py status
py -3 -B tools/rumble_navigate.py route vehicle
py -3 -B tools/rumble_navigate.py wait track --timeout 60
```

`route` sends normal input through verified states; `wait` only waits. It checks readiness, transition state, PID and freshness, and stops on unsupported/unknown conditions. It does not directly call menu functions, patch game state, or automatically restart. Menu routing and the separate bounded driving/lap experiments are distinct; reaching a menu does not verify gameplay or race completion. Movie skipping uses normal input after recognizing playback. Confirm ordinary behavior before automating a route.

`Rebuild-Game.ps1` is a Windows/MSBuild helper. It overwrites the active config and verified generated files and reuses build/log paths. Its February-only instruction patch does not apply to retail. The native code's game guards do not make these helper scripts universal.

## Direct development race launch

For the exact supported NTSC retail build, repeated race setup can use original game functions instead of menu input. After preparing your assets/generated code and stopping the previous runner:

```powershell
$env:PS2_GS_GPU='d3d11'
$env:PS2_GS_THREADED='1'
$env:PS2_GS_GPU_PRESENT='1'
$env:PS2_GS_GPU_ASYNC_PRESENT='1'
$env:PS2_GS_GPU_PRESENT_COMPARE='1'
$env:PS2_GS_DEPTH_PREFETCH='1'
$env:PS2_GS_DEPTH_PREFETCH_COMPARE='1'
$env:PS2_RUMBLE_NATIVE_VU='1'
$env:PS2_RUMBLE_VU_COMPARE='1'
foreach ($suffix in 'CLIP','REFLECT','LIT','REFLECT_LIT','DUAL_BASIS','QUAD') {
    Set-Item -Path "Env:PS2_RUMBLE_NATIVE_VU_$suffix" -Value '1'
}
py -3 -B Scripts/rumble_dev.py launch --car Tiberius --track "True Grits" --720p --no-frame-capture --no-ai
```

Both arguments are required; names or catalog IDs work. Preset: one player, eight cars, three laps, Forgiving, power-ups active. `--car-class rookie|pro|elite`, `--max-upgrades` and `--ai` are separate choices. The adapter retains initialization/card polling/cleanup, skips intro presentation, invokes original race preparation and uses original loading. Ordinary launches retain menus. It does not change game files or saved unlock records. Another game requires its own verified interfaces.

Park first and run `py -3 -B Scripts/rumble_dev.py save-spot` to overwrite one ignored local `Scripts/rumble-benchmark-spot.json`; no pose is shipped. Add `--benchmark-spot` to the next launch for that same track. Three game seconds after GO, original recovery restores the pose once; restart rearms it. This is not a full save state: NPC/world state and lap progress are not restored, and recovery clears motion, active power-ups and skid history. AI plus teleport has not been live-validated. It is not deterministic whole-scene replay.

## Numeric slowdown profiling

`tools/rumble_profile.py` uses Windows/PDB counters. Enable `PS2_VU_PROFILE=1` before launch, then use `--trigger slowdown --pre 3 --seconds 15` or `--trigger now`. It overwrites `analysis/rumble-timing.json`. `--objects` requires `PS2_RUMBLE_OBJECT_PROFILE=1` and records bounded instance/model/producer attribution, not exact per-object GPU cost. `--stacks --stack-lines` adds measured thread suspensions; keep them off unless needed.

`tools/rumble_etw.py` needs Windows Performance Toolkit WPR/xperf and administrator rights for collection. It performs a bounded farm driving experiment and overwrites `analysis/rumble-cpu.etl` and `analysis/rumble-etw.json`; do not use it concurrently with manual driving. `--gpu` adds graphics events. Short combined traces can be several GB; reanalyze before collecting more. Queue completion latency is not exact shader execution time. No captures, PDBs or reports are distributed.

Record PID/build, game-clock delta versus monotonic time, pose/track/car, settings and measurement overhead. Host FPS differs from simulation speed. Inspector logs can show completion before buffered stdout. Farm slowdown still involves CPU geometry/submission and graphics transfer/completion dependencies; no single object explains every slowdown.

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
| `ExportRumbleDebug.java` | Focused local decompilation report; also accepts `- <FUNCTION_NAME>`, `- asm:0x<ADDRESS>:<COUNT>` (1–128 instructions), or `- refs:0x<ADDRESS>` (up to 128 references). |
| `ExportRumbleAudio.java` | Verified February/retail IOP audio analysis; argument is a private output C path. |
| `ExportRumbleAssets.java` | Selected resource/debug function analysis; argument is a private output C path. |

Exporters can produce decompiled game code locally. Those products belong in ignored `analysis/`, never in this public repository. Original linker maps and full exported symbol databases are not included.

## Other research tools

| Tool | Use and prerequisites |
| --- | --- |
| `rumble_research.py` | Function/runtime/movie/picture/music/bank/environment/model queries and explicit input. Use `--help`; decode comparisons require local FFmpeg tools. Some queries require private Ghidra or asset reports. `runtime --diff` overwrites one small state file. |
| `rumble_menu_research.py` | Read-only menu-label/function/caller research and `catalog` using private binaries/assets and exports. |
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

**Repeat a benchmark scene:**

> Prefer verified direct car/track launch and one saved pose for this retail build. Retain initialization/loading and reject unknown state. Measure game-clock progress and matching workloads. A pose is not a full save state. For another game, validate equivalent interfaces; never copy addresses.

**Test a menu route:**

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

## Retail developer controls

These are exact-build Rumble examples, not generic PS2 launch arguments. After building your matching retail runner:

```powershell
# Manual play, native 720p-sized window, no automatic images or upgrade changes:
py -3 -B Scripts/rumble_dev.py launch --720p --no-frame-capture --no-ai --no-max-upgrades
# Optional keyboard-only import; the source PCSX2 profile is read, never modified:
py -3 -B Scripts/rumble_dev.py launch --720p --keyboard-profile '<PROFILE>/Keyboard.ini'
# Optional original No Mercy NPC logic for the player, with player-only Elite upgrades:
py -3 -B Scripts/rumble_dev.py launch --720p --ai --max-upgrades
```

Choose one launch command; the helper refuses a second runner and does not stop an existing one. Defaults are manual driving and no upgrade override. `--window` shows a native window at its default size; without `--window` or `--720p` the helper uses viewer-only mode. `--no-frame-capture` disables images, not text inspection. `--glitch-visuals` is an intentional presentation effect, not a fix for texture corruption.

Developer upgrades apply at the next Single Race setup (Elite player, Rookie opponents), not to a race already running or saved progress. Normal manual driving and the separate Python driving experiment remain available. No Mercy driving does not guarantee a first-place finish.

The retail **Video Options** entry controls host window size, 4:3 presentation and Sharp/Smooth filtering. It does not raise internal rendering resolution. Its settings currently reset at launch. This UI is a build-scoped adapter calling the game's own menu/font routines; another game needs its own integration.

`prototype-candidates` audits local model and handling data using `analysis/assets-investigation.json`; it does not install extra vehicles. Prototype appearance/handling with retail audio is a restoration objective, not implemented content. There is no Lua runtime; `Scripts/` is a development-helper location.

`rumble_navigate.py race-status`, `camera-status` and `debug-status` inspect verified state. `wait-results` has a bounded timeout and checks original results readiness; it cannot make an unfinished race pass. `drive`/`lap` are opt-in Windows input experiments and depend on the current track, validated RAM state and expected keyboard bindings. Recheck bindings after importing another keyboard profile.

## Investigate the first actual failure

Use this loop: **verify identity → reproduce → find first unmet condition → test a hypothesis → fix → retest**.

For a stall, inspect the PC/return address, current thread, scheduler wait, and the operation that should release it. Follow request, receiver-owned data, completion, callback and acknowledgement separately. Check callback arguments/context and buffer lifetime. A repeated PC may be a valid wait; acknowledging work without implementing it hides the missing producer.

Read the whole rejected batch. Its first opcode need not be the failing operation. Validate before committing effects so a later unsupported command does not leave partially changed state.

For missing files, distinguish absent data from a wrong disc root, path, container parser, failed read or code that never requested it. Compare inventories, container contents and references before declaring content missing.

For warnings, distinguish compiler/linker warnings from unresolved guest targets, unsupported instructions and missing services. An executable can be produced despite an important runtime problem. Do not suppress everything to obtain a clean-looking log.

### Python first where it helps

Use Python for function matching, pointer/structure hypotheses, asset inventories, instruction comparisons, protocol decoding and experiment summaries. Share build identity and address definitions with other tools. A name match or normalized instruction similarity is a candidate until operands and control flow are checked.

Keep offline analysis in Python where appropriate. Move verified runtime behavior into portable C++ and test the compiled implementation. Python-only checks cannot establish native ownership, races, callbacks or rendering correctness. Embedded Python, pybind11 and nanobind are not required by this workflow.

### Test navigation without bypassing initialization

When testing the menus themselves, a separate development script should send normal input, wait for verified state, and stop safely on uncertainty. For repeated race benchmarks use direct launch above. A word such as “Continue” is a label, not automatically a callable menu function.

```text
PSEUDOCODE — requires game-specific adapters, not an installed command.

verify target executable, runner and fresh inspector state
wait until SOURCE is ready and its update counter advances
verify selection identity and completed animation
press normal input; always release it, even on failure
wait for a newer DESTINATION-ready or expected-ended observation
verify resulting selection and relevant visual output

on changed process, stale data, unknown state or unsupported work:
    release input and stop the route
on timeout:
    inspect the same process; timeout alone is not a crash
```

For a skippable movie, wait for playback and its normal input callback, press the normal skip button, release it, then verify playback ended. A movie without a skip callback must finish normally unless an explicitly separate bypass experiment is intended.

A direct-to-gameplay developer mode requires understanding the real initialization sequence, resource lifetime and game state. Merely calling a race function or changing a menu ID can skip required setup. Keep automated input, AI driving, upgrades and other experiments opt-in; normal launches retain normal controls.


## Rendering, controls and slow motion

A native C++ executable still needs implementations of graphics, audio, input, timing, IOP services and other PS2 behavior. Recompilation and a set of scripts do not supply complete compatibility.

**Inspect the backend actually in use.** In the current reference workspace, GS primitives are rasterized on the CPU; OpenGL uploads/displays the completed image. An OpenGL window therefore does not establish hardware-accelerated game rasterization. Another checkout may use a different backend.

Separate:

- Internal render size and original field/frame presentation.
- Host window size and aspect ratio.
- Final image filtering, such as Sharp/Smooth.
- Browser scaling or cached viewer images.

A 1280×720 window can display a lower-resolution 4:3 image. It does not create higher-resolution geometry or textures. A native in-game Video Options menu needs that game's own menu integration; generic presentation APIs alone do not install the menu.

For shaking or corruption, compare the same scene and time, field/frame mode, source/display buffers, scissor, depth, texture formats, VRAM addressing and actual packets. A CPU framebuffer matching the upload bytes narrows the fault; it does not prove the GPU's final output. Do not dismiss corruption as camera culling without evidence.

Keyboard D-pad and analog-stick bindings are different. A profile may use arrows for menus and WASD for driving. Importing an emulator's profile requires an implemented translation layer; it is not automatically a PS2Recomp feature. Verify confirmation/back, stick axes and release behavior before automating inputs.

### Performance checklist

1. Use the optimized build with symbols and confirm the running executable matches it.
2. Disable image capture and expensive optional tracing for comparison; keep the same scene, settings and manual/AI mode.
3. Measure game progression against wall time using a verified clock/counter. Presented FPS alone can hide slow simulation or duplicate frames.
4. Profile the busy thread: guest dispatch, VU execution, software rasterization, audio, waits and presentation are different costs.
5. Compare meaningful intervals and repeated scenes; rotating models and different visible geometry change workload.
6. Change one cause, preserve rendering/behavior, run native regressions and retest the live trigger.

Recent **local** work includes early rejection of depth-occluded triangles and compaction of GS swizzle lookup tables from **2.75 MiB to 88 KiB**. The latter removes repeated offsets; it does not shrink game textures, reduce image quality, or represent that reduction in total memory usage. Multi-format reads/writes and VRAM-boundary regression checks passed within a 563-test suite. This is the dated local validation result for the source snapshot included here, not a test count promised for upstream or every platform.

These changes have **not eliminated busy-scene slow motion** in the reference project. Memory savings do not imply a proportional speedup. Preserve measured limits, and do not alter game time or silently discard bad frames to label a port “full speed.”


## Compare revisions and restore content

Use the desired target build as the base. Compare prototypes and retail separately for symbols, function behavior and assets. A linker map does not contain missing source; a shared engine or intro movie does not make another game's port automatic.

Before transferring a feature, verify target interfaces, initialization, data layout and dependencies. Earlier vehicle variants may differ in models, tuning, physics logic and audio bindings. “Keep prototype driving behavior with target audio” is a multi-part integration requirement, not a model swap.

Distinguish confirmed absence, renamed/repacked data, dormant content and incomplete evidence. A discovered track or vehicle is not yet a playable selection. A `Scripts/` folder is an organizational choice; it does not create a Lua runtime or built-in game scripting interface.

Keep restoration, developer tools and normal player behavior clearly separated. Verify a full session, teardown and re-entry, not only first load.


## Project and handoff boilerplates

Copy only what is useful into an existing local record. No additional files are required by this guide.

```text
PROJECT
Target / region / revision: <IDENTITY>
ELF and relevant module hashes: <VERIFIED IDENTITIES>
Reference symbols/builds: <BUILD -> EVIDENCE>
Pinned source and custom patch: <REVISIONS>
Config / map / generated output / build: <RELATIVE PATHS>
Private asset root: <LOCAL PLACEHOLDER>
Installed custom tools: <COMPONENTS, DEPENDENCIES, SCHEMA>
Developer options: <DEFAULT OFF; VERIFIED SCOPE>
Restart permission: <ALREADY AGREED SCOPE>
Retention: <FIXED OUTPUTS, HISTORY CAPS, NO BACKUPS>
Platform evidence: <BUILT / RUN / PLAYED / UNTESTED PER PLATFORM>
```

```text
INVESTIGATION
Category: <STANDARD / CUSTOM RUNTIME / GAME-SPECIFIC / UNFINISHED>
Exact build/source: <IDENTITIES>
Trigger and expected result: <REPRODUCTION>
Last success / first failure: <FRESH EVIDENCE>
Hypothesis and discriminating check: <METHOD>
Python experiment: <RESULT AND LIMITS, OR NOT NEEDED>
Original behavior: <LOCALLY VERIFIED SEMANTICS>
Correction: <WHAT CHANGED AND WHY>
Native tests: <SCOPE AND RESULT>
Live retest: <STATE, INPUT, VISUAL/AUDIO EVIDENCE>
Performance: <SCENE, BUILD, SETTINGS, WALL TIME, VERIFIED GAME TIME>
Unverified: <CASES AND PLATFORMS>
Next action: <ONE CONCRETE STEP>
```

| Milestone | Evidence required | Result |
| --- | --- | --- |
| Startup to menu | Playback ended, ready state advances, fresh visual | `<PASS / FAIL / UNTESTED>` |
| Selection A to B | ID changed, animation complete, appropriate model/preview | `<RESULT>` |
| Back and forward | Selections retained, no new service rejection | `<RESULT>` |
| Load gameplay | Resources ready, actual gameplay state and live scene | `<RESULT>` |
| Manual controls | Input changes movement; release stops the input | `<RESULT>` |
| Performance | Verified game time versus wall time, representative scenes | `<RESULT>` |
| Complete session | End/results progression and resource teardown | `<RESULT>` |
| Re-entry | Another selection/session works without stale state | `<RESULT>` |
| Other platforms | Build and live validation on each intended platform | `<RESULT>` |

Record exact selections and modes tested. One successful pair does not cover all combinations.

```text
SHAREABLE HANDOFF
As of: <DATE>
Purpose: <TARGET PORT / FEATURE>
Verified: <OBSERVED MILESTONES; TESTS SEPARATE FROM LIVE RESULTS>
Current blocker: <FIRST UNMET CONDITION>
Implemented but not validated: <EXPLICIT PENDING WORK>
Custom components required separately: <TOOLS/PATCH VERSIONS>
Next: <ONE EXPERIMENT>
Unfinished: <GAMEPLAY, CONTENT, PERFORMANCE, PLATFORMS>
Excluded from shared material: game data, decompiled bodies,
  private paths, raw dumps and secrets
```
