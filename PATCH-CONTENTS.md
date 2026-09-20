# Patch contents and validation

Snapshot: September 20, 2026. Upstream: `14b1e5cb39b4af7e6fc12f9a29fdc751efde49d7` from ran-j/PS2Recomp.

## General changes

- Inspector implementation and runtime/scheduler integration: structured snapshots, read-only RAM watches, bounded histories, hidden-window presentation captures and contact sheets.
- Scheduler and runtime work: callback stack ownership, COP0 Count propagation, observable blocked states and opt-in guest-write diagnostics.
- Graphics/presentation corrections: GS transfer/drawing handling and frame latching at the scheduler boundary.
- Kernel/IOP integration: SIF command/RPC transport, CD/MPEG callbacks and streaming, caller-owned MPEG workspace, pad/TTY helpers and memory operations.
- Native audio facilities: owned PCM voices, finite/looping playback, phase-preserving updates and host interfaces. These APIs also support the Rumble profile below.
- Ghidra exporter classification adjustment and focused native regression tests.

These are development changes, not a claim that every affected PS2 subsystem is complete or that every change is suitable upstream unchanged.

## Rumble-specific code

- `ps2xIOP/src/modules/rumble_audio.cpp`: handwritten AUDIO.IRX service behavior for verified builds, including streams, banks, listener/engine/road/emitter state and explicit unsupported cases. Selected through the registered game profile.
- `ps2xRuntime/src/lib/rumble_picture.cpp`: build-scoped native picture decode/upload orchestration; calls the user's generated guest functions through the scheduler. Registered through the game-override interface.
- Associated profile registration, native tests, Ghidra repair/import scripts and Python/PowerShell research helpers.

Addresses, hashes, format constants and identified function names are analysis metadata, not a bundled game executable or complete symbol database. These helpers depend on separately supplied exact-build inputs. Prototype debug features and missing content have not been restored by installing this package.

## Distribution adjustments

Only the publishing copy was changed for portability: Ghidra uses `GHIDRA_HOME`, disc inspection accepts `-DiscRoot`, decoder validation accepts `-VcVarsPath`, Python commands use `py -3`, and the viewer has a generic title plus `--runtime-dir` and handles an initial null snapshot. Existing private workspace scripts were not rewritten. The README/workflow use relative paths and placeholders; neither personal nor starter guide is included verbatim.

## Validation and limits

- The complete 55-file patch passed `git apply --cached --check` against the pinned upstream tree in an isolated temporary index. No active source checkout was changed by this check.
- The installer recognized the already patched development checkout without changing it.
- Packaged Python scripts and PowerShell scripts passed syntax checks; the placeholder TOML parsed successfully.
- The original development workspace previously passed 25 focused Rumble native tests plus finite/looping PCM checks. This publication task did not rebuild the runtime or rerun the complete suite. Those historical results are not a fresh platform matrix.
- Generated registration/function code, ELFs, IRX modules, disc images, assets, captures, reports, Ghidra databases and build products are excluded. The native tests use synthetic fixtures, including the explicitly hand-authored IPUM test image.
- Full gameplay and other operating systems remain unverified. The latest recovered vehicle callback still needs live verification; the game can prematurely show invalid race results.

## Files changed inside PS2Recomp

Apply these together. Four files are new native implementations/headers; the remaining 51 modify existing upstream source or tests. The patch intentionally excludes `ps2xRuntime/src/runner/register_functions.cpp`, which must be generated from the user's own executable.

- `ps2xIOP/CMakeLists.txt`
- `ps2xIOP/include/ps2x/iop/iop_host.h`
- `ps2xIOP/include/ps2x/iop/iop_subsystem.h`
- `ps2xIOP/include/ps2x/iop/iop_types.h`
- `ps2xIOP/src/builtin_profiles.cpp`
- `ps2xIOP/src/iop_service.h`
- `ps2xIOP/src/iop_subsystem.cpp`
- `ps2xIOP/src/module_factories.h`
- `ps2xIOP/src/modules/rumble_audio.cpp`
- `ps2xRecomp/tools/ghidra/ExportPS2Functions.java`
- `ps2xRuntime/CMakeLists.txt`
- `ps2xRuntime/include/ps2_call_list.h`
- `ps2xRuntime/include/ps2_inspector.h`
- `ps2xRuntime/include/ps2_runtime.h`
- `ps2xRuntime/include/runtime/ee_scheduler.h`
- `ps2xRuntime/include/runtime/gs/gs_frontend.h`
- `ps2xRuntime/include/runtime/ps2_audio.h`
- `ps2xRuntime/src/lib/Kernel/EeScheduler.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/CD.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/GS.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/Helpers/Support.h`
- `ps2xRuntime/src/lib/Kernel/Stubs/IPU.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/IPU.h`
- `ps2xRuntime/src/lib/Kernel/Stubs/LibC.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/Pad.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/SIF.cpp`
- `ps2xRuntime/src/lib/Kernel/Stubs/SIF.h`
- `ps2xRuntime/src/lib/Kernel/Stubs/TTY.cpp`
- `ps2xRuntime/src/lib/Kernel/Syscalls/Helpers/Runtime.h`
- `ps2xRuntime/src/lib/Kernel/Syscalls/RPC.cpp`
- `ps2xRuntime/src/lib/Kernel/Syscalls/RPC.h`
- `ps2xRuntime/src/lib/Kernel/Syscalls/System.cpp`
- `ps2xRuntime/src/lib/game_overrides.cpp`
- `ps2xRuntime/src/lib/gs/gs_cpu_backend.cpp`
- `ps2xRuntime/src/lib/gs/gs_frontend.cpp`
- `ps2xRuntime/src/lib/ps2_audio.cpp`
- `ps2xRuntime/src/lib/ps2_audio_vag.cpp`
- `ps2xRuntime/src/lib/ps2_inspector.cpp`
- `ps2xRuntime/src/lib/ps2_iop_host.cpp`
- `ps2xRuntime/src/lib/ps2_iop_host.h`
- `ps2xRuntime/src/lib/ps2_iop_transport.h`
- `ps2xRuntime/src/lib/ps2_memory.cpp`
- `ps2xRuntime/src/lib/ps2_pad.cpp`
- `ps2xRuntime/src/lib/ps2_runtime.cpp`
- `ps2xRuntime/src/lib/rumble_picture.cpp`
- `ps2xTest/CMakeLists.txt`
- `ps2xTest/include/MiniTest.h`
- `ps2xTest/src/main.cpp`
- `ps2xTest/src/ps2_gs_tests.cpp`
- `ps2xTest/src/ps2_memory_tests.cpp`
- `ps2xTest/src/ps2_runtime_expansion_tests.cpp`
- `ps2xTest/src/ps2_runtime_io_tests.cpp`
- `ps2xTest/src/ps2_runtime_kernel_tests.cpp`
- `ps2xTest/src/ps2_sif_rpc_tests.cpp`

Patch SHA-256: `9eb305b7e430ae92579ab725a1e22082615ab3d94e42b389aa6bd1bc8e8ff347`.
