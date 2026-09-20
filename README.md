# PS2Recomp AI Workflow

Source patches and development tools from an ongoing Rumble Racing reversing project. Apply them to [PS2Recomp](https://github.com/ran-j/PS2Recomp), then analyze and generate code from your own game files.

**Experimental tools, not a finished game port or automatic compatibility layer.** No game assets, executables, decompiled/generated game code, build outputs, or private workspace files are included.

## What you get

- **Runtime additions:** JSON inspector, bounded history, frame contact sheets, hidden-window capture, and fixes to graphics, scheduling, audio, and IOP/RPC handling.
- **Development tools:** local browser viewer, Python research helpers, PowerShell rebuild/inspection scripts, and Ghidra analysis/export scripts.
- **Rumble-specific work:** build-guarded symbol/function repairs, menu automation, asset-format readers, and handwritten picture/audio compatibility code. These are not settings for other games.
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
cmake -S PS2Recomp -B PS2Recomp/out/build -DPS2X_ENABLE_RUNTIME_LOGS=ON
cmake --build PS2Recomp/out/build --config Debug --target ps2_recomp ps2x_tests
```

On Windows, use `py -3` if `python` is unavailable. The installer checks the pinned revision and refuses conflicting edits. It does not download games, generate game code, build, or launch anything. Newer upstream revisions require reviewing/rebasing the patch.

Next: follow [the game-analysis workflow](WORKFLOW.md#analyze-and-generate-your-own-game). Setup alone does not produce a playable runner.

Once a matching runner is built and inspector capture is enabled:

```sh
python -B tools/runtime_viewer.py
```

Open **http://127.0.0.1:8765/**. The viewer displays existing capture files; it has no AI connection, audio, controls, build watcher, or automatic runner restart.

## Status and scope

The original Windows workspace has rendered menus, tracks and a starting grid. Playable racing remains unverified: the current investigation includes premature race results and intermittent guest-state corruption. Some audio behavior, debug-feature restoration and missing-content restoration remain unfinished. Linux/macOS support for this patch set has not been validated; Windows input automation is a development helper.

The patch contains both general runtime changes and build-scoped Rumble code; it is one integrated patch, not independently selectable features. See [PATCH-CONTENTS.md](PATCH-CONTENTS.md) for scope and validation limits.

Based on PS2Recomp revision `14b1e5c`. Distributed under [GPL v3](LICENSE), with upstream notices retained. Not affiliated with the game's publisher. Supply game files locally and keep generated research outputs private.
