# Patch contents and validation

Snapshot: September 20, 2026. Upstream: `14b1e5cb39b4af7e6fc12f9a29fdc751efde49d7` from ran-j/PS2Recomp.

## General runtime and analysis changes

- Structured inspector, read-only RAM watches, bounded history, sampled frames/contact sheets and hidden-window presentation.
- GS addressing/transfer/drawing corrections, depth rejection before unnecessary texture work, and compact swizzle lookup tables: **2.75 MiB to 88 KiB**, preserving image data and address behavior.
- VU instruction/readiness and DMA transfer handling, with opt-in bounded transfer diagnostics. The current experimental per-instruction clipping trace is excluded.
- Keyboard mappings, host output size/aspect/filter controls, frame latching and presentation work. Rendering remains CPU rasterization with OpenGL presentation; changing window size does not raise internal resolution.
- Scheduler/callback ownership, COP0 Count and blocked-state observations; SIF/RPC, CD/MPEG, pad/TTY and memory integration.
- Native PCM/ADPCM voice, looping/phase and reverb facilities, plus focused native regression tests.
- FPU translation and Ghidra exporter fixes. Windows RelWithDebInfo runtime inlining is enabled without fast-math or a new build tree.

These are development changes, not completed PS2 subsystem implementations or automatic compatibility.

## Rumble-specific adapters and tools

- `rumble_audio.cpp` and related reverb state implement handwritten, build-scoped AUDIO.IRX service behavior; unsupported cases remain explicit.
- `rumble_picture.cpp` orchestrates picture decode/upload using the user's generated guest functions.
- `rumble_video.cpp` integrates Video Options through retail menu/font/input routines and applies retail presentation policy. Host sizing/filter settings currently reset on launch.
- `rumble_dev.cpp` provides opt-in original No Mercy NPC player driving and Single Race upgrade overrides, retaining manual play. Intentional glitch presentation is separately opt-in.
- Hash-guarded Ghidra function repair/import scripts, Python model/audio/menu/camera research, state-checked input routes and developer controls in `Scripts/rumble_dev.py`.

Addresses, hashes, protocol constants and identified names are analysis metadata. No game executable, complete symbol database, original source or decompiled/generated game bodies are distributed. Prototype candidates remain an audit, not playable extra entries; debug/content restoration and Lua integration remain unfinished.

## Distribution adjustments

The publishing copy retains `GHIDRA_HOME`, `-DiscRoot`, `-VcVarsPath`, generic viewer title and `--runtime-dir`; Python examples use `py -3`. The launcher explicitly disables images when requested and hides its redirected console. The rebuild helper selects the matching recompiler configuration. README and WORKFLOW use relative paths/placeholders; personal guides, private reports and original workspace settings are excluded.

The integrated patch replaces the previous snapshot and must be applied to clean pinned upstream. It is not a delta on top of the earlier workflow patch. The installer intentionally refuses conflicting local edits.

## Validation and limits

- The 73-file source patch is checked against the pinned upstream tree using an isolated temporary Git index; the active source checkout is not changed.
- Python/PowerShell syntax and placeholder TOML are checked during publication.
- Historical validation of this source snapshot: **508/508 native tests passed** in the Windows development workspace after the compact-table change, including multi-format GS addresses/reads/writes and VRAM-boundary coverage. This publication does not rebuild the game or claim a fresh cross-platform test run.
- Menus, manual driving, different vehicles/tracks and lap progression have been observed locally. Substantial slow motion, camera-dependent missing road/water, complete race/results progression and some audio behavior remain unresolved or unverified.
- Generated registration/functions, game data/assets, builds, logs, captures, Ghidra databases, original linker maps and personal notes are excluded. Native tests use synthetic fixtures.
- Linux/macOS and a complete port from a clean public-only setup remain unverified. Users must analyze their own exact game and generate the missing game code.

## Files changed inside PS2Recomp

Apply together. `ps2xRuntime/src/runner/register_functions.cpp` is deliberately excluded: generate it from your own executable.

- `ps2xIOP/CMakeLists.txt`
- `ps2xIOP/include/ps2x/iop/iop_host.h`
- `ps2xIOP/include/ps2x/iop/iop_subsystem.h`
- `ps2xIOP/include/ps2x/iop/iop_types.h`
- `ps2xIOP/include/ps2x/iop/rumble_reverb.h`
- `ps2xIOP/src/builtin_profiles.cpp`
- `ps2xIOP/src/iop_service.h`
- `ps2xIOP/src/iop_subsystem.cpp`
- `ps2xIOP/src/module_factories.h`
- `ps2xIOP/src/modules/rumble_audio.cpp`
- `ps2xRecomp/src/lib/fpu_translator.cpp`
- `ps2xRecomp/tools/ghidra/ExportPS2Functions.java`
- `ps2xRuntime/CMakeLists.txt`
- `ps2xRuntime/include/game_overrides.h`
- `ps2xRuntime/include/ps2_call_list.h`
- `ps2xRuntime/include/ps2_inspector.h`
- `ps2xRuntime/include/ps2_runtime.h`
- `ps2xRuntime/include/ps2_runtime_macros.h`
- `ps2xRuntime/include/runtime/ee_scheduler.h`
- `ps2xRuntime/include/runtime/gs/gs_cpu_backend.h`
- `ps2xRuntime/include/runtime/gs/gs_frontend.h`
- `ps2xRuntime/include/runtime/gs/gs_types.h`
- `ps2xRuntime/include/runtime/gs/ps2_gs_memory.h`
- `ps2xRuntime/include/runtime/ps2_audio.h`
- `ps2xRuntime/include/runtime/ps2_audio_reverb.h`
- `ps2xRuntime/include/runtime/ps2_pad.h`
- `ps2xRuntime/include/runtime/ps2_vu1.h`
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
- `ps2xRuntime/src/lib/rumble_dev.cpp`
- `ps2xRuntime/src/lib/rumble_picture.cpp`
- `ps2xRuntime/src/lib/rumble_video.cpp`
- `ps2xRuntime/src/lib/vu/ps2_vu1_core.cpp`
- `ps2xRuntime/src/lib/vu/ps2_vu1_detail.h`
- `ps2xRuntime/src/lib/vu/ps2_vu1_upper.cpp`
- `ps2xTest/CMakeLists.txt`
- `ps2xTest/include/MiniTest.h`
- `ps2xTest/src/code_generator_tests.cpp`
- `ps2xTest/src/main.cpp`
- `ps2xTest/src/pad_input_tests.cpp`
- `ps2xTest/src/ps2_gs_tests.cpp`
- `ps2xTest/src/ps2_memory_tests.cpp`
- `ps2xTest/src/ps2_runtime_expansion_tests.cpp`
- `ps2xTest/src/ps2_runtime_io_tests.cpp`
- `ps2xTest/src/ps2_runtime_kernel_tests.cpp`
- `ps2xTest/src/ps2_sif_rpc_tests.cpp`
- `ps2xTest/src/ps2_vu1_tests.cpp`

Patch SHA-256: `44a78bf2ebb68785e066ee8d4c7bbab9060403d747c292b5e20a711b5b444500`.
