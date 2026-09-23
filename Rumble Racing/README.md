# Rumble Racing helpers

These scripts target our verified Rumble Racing builds. They are examples for other ports, not a universal game launcher.

Shared setup is in [the main README](../README.md) and [WORKFLOW](../WORKFLOW.md). The patched source/build stays at `../PS2Recomp`; this folder contains the game-specific research workflow.

- `Scripts/rumble_dev.py`: direct car/track launch, saved benchmark pose, optional AI driving/upgrades.
- `tools/`: Rumble research, profiling, asset and rebuild helpers.
- `tools/ghidra/`: Rumble-specific analysis scripts.
- `run-ntsc.ps1`: launcher for matching generated Retail/February builds.

Supply your own private inputs here: `Extracted_Assets/`, `CSV Map/`, `Ghidra Project/`, `output-ghidra/`, `analysis/` and `config-ghidra.toml`. The saved pose is `Scripts/rumble-benchmark-spot.json`. None are bundled; ignore rules exclude them. Existing users should move those private inputs into this folder, while keeping the source/build at the repository root. Preserve independent edits to generated source.

After preparing your matching retail runner, run from the repository root:

```sh
python -B "Rumble Racing/Scripts/rumble_dev.py" launch --car Tiberius --track "True Grits" --720p --no-frame-capture --no-ai
```

To test teleport, park and use the same helper's `save-spot` command, then add `--benchmark-spot` on the next launch of that track. No pose is supplied. A local post-move check confirmed Tiberius / True Grits and the saved teleport three game seconds after GO; this is not validation of every combination.

Python/Ghidra outputs remain private. No original game or generated/decompiled game code is bundled. Native Rumble adapters remain inside the integrated C++ patch; they were not physically moved out of PS2Recomp.
