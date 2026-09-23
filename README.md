# PS2Recomp AI Workflow

Source patches and development tools from an ongoing Rumble Racing reversing project. Apply them to [PS2Recomp](https://github.com/ran-j/PS2Recomp), then analyze and generate code from your own game files.

**Experimental tools, not a finished game port or automatic compatibility layer.** No game assets, executables, decompiled/generated game code, build outputs, or private workspace files are included.

## What you get

- **Runtime additions:** JSON inspector, bounded history, frame contact sheets, hidden-window capture, keyboard mapping, presentation controls, and fixes to graphics, scheduling, audio, and IOP/RPC handling.
- **Development tools:** local browser viewer, Python research helpers, PowerShell rebuild/inspection scripts, and Ghidra analysis/export scripts.
- **Rumble-specific work:** build-guarded symbol/function repairs, state-checked menu automation, direct car/track launch, saved benchmark poses, optional developer driving/upgrades, validated native VU paths, native Video Options, asset-format readers, and handwritten picture/audio compatibility code. These are not settings for other games.
- **Boilerplates:** [workflow, commands and AI prompts](WORKFLOW.md), plus a [placeholder configuration](examples/config.template.toml).

## Setup

Install Git, Python 3.10+, CMake 3.21+, and a C++20 toolchain. On Windows use Visual Studio's **Desktop development with C++** tools. Ghidra and its supported JDK are needed for game analysis; they are not bundled.

```sh
git clone https://github.com/Ehren1337/PS2RecompAIWorkflow.git
cd PS2RecompAIWorkflow
git clone https://github.com/ran-j/PS2Recomp.git PS2Recomp
git -C PS2Recomp checkout 14b1e5cb39b4af7e6fc12f9a29fdc751efde49d7
git -C PS2Recomp submodule update --init --recursive
python -B apply.py --check
python -B apply.py
cmake -S PS2Recomp -B PS2Recomp/out/build -DPS2X_ENABLE_RUNTIME_LOGS=ON -DPS2X_ENABLE_DILIGENT_GS=ON -DPS2X_ENABLE_FFMPEG=ON
cmake --build PS2Recomp/out/build --config RelWithDebInfo --target ps2_recomp ps2x_tests
```

On Windows, use `py -3` if `python` is unavailable. The installer checks the pinned revision and refuses conflicting edits. It does not download games, generate game code, build, or launch anything. Newer upstream revisions require reviewing/rebasing the patch.

For a single-configuration generator, also configure `-DCMAKE_BUILD_TYPE=RelWithDebInfo`. Updating an older patched checkout requires reviewing its local changes; `apply.py` does not upgrade a previous patch in place. Use a clean pinned checkout for this snapshot and keep private/generated inputs separate.

Next: follow [the game-analysis workflow](WORKFLOW.md#analyze-and-generate-your-own-game). Setup alone does not produce a playable runner.

Once a matching runner is built and inspector capture is enabled:

```sh
python -B tools/runtime_viewer.py
```

Open **http://127.0.0.1:8765/**. The viewer displays existing capture files; it has no AI connection, audio, controls, build watcher, or automatic runner restart.

## Status and scope

The Windows workspace reaches menus, manual races, laps and results. Direct development launch was checked with two car/track combinations. Busy scenes still slow down; complete audio, prototype restoration and Linux/macOS validation remain unfinished.

The optional GPU backend uses pinned **DiligentCore** (downloaded by CMake), with shared Windows tests for D3D11, D3D12, Vulkan and OpenGL. Current game testing uses D3D11; shared shader tests do not establish playable support on every API. DirectX 10 is not implemented. FFmpeg development libraries handle supported movies: Windows CMake downloads the configured SDK unless `PS2X_FFMPEG_ROOT` supplies one; other hosts need the development packages listed in [WORKFLOW.md](WORKFLOW.md#dependencies-and-renderers).

This snapshot retains the GS lookup-table reduction from **2.75 MiB to 88 KiB** without reducing texture quality, and includes GPU/transfer, native geometry and ordered submission improvements. The local configured suite passed **563/563 tests**. This is not a full-game performance guarantee.

After building your matching retail runner, [direct launch](WORKFLOW.md#direct-development-race-launch) accepts a car and track without menu input:

```sh
python -B Scripts/rumble_dev.py launch --car Tiberius --track "True Grits" --720p --no-frame-capture
```

The patch contains both general runtime changes and build-scoped Rumble code; it is one integrated patch, not independently selectable features. See [PATCH-CONTENTS.md](PATCH-CONTENTS.md) for scope and validation limits.

Based on PS2Recomp revision `14b1e5c`. Distributed under [GPL v3](LICENSE), with upstream notices retained. Not affiliated with the game's publisher. Supply game files locally and keep generated research outputs private.
