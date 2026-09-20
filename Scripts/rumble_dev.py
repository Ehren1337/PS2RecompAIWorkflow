"""Local Rumble Racing development controls; no game code or assets included.

From the project root:
    py -3 -B Scripts/rumble_dev.py launch --max-upgrades --ai
    py -3 -B Scripts/rumble_dev.py launch --no-max-upgrades --no-ai
    py -3 -B Scripts/rumble_dev.py launch --720p --no-frame-capture
    py -3 -B Scripts/rumble_dev.py launch --720p --keyboard-profile "<PCSX2 profile path>/Keyboard.ini"
    py -3 -B Scripts/rumble_dev.py prototype-candidates

Launch uses the existing native runner and normal menus. Max upgrades means
Elite for the single human player and Rookie for opponents in Single Race.
It applies at the next race setup, not to a running race or memory-card saves.
AI is the separate opt-in No Mercy original NPC driver. Omit --ai for manual
driving. A running runner is never automatically stopped by this script.

Prototype candidates are an audit, NOT installed/playable menu entries yet.
Requested restoration policy: prototype appearance AND driving behavior,
retail audio; ordinary retail choices retain retail behavior. Model hashes
alone cannot establish physics differences or working restoration.

Future Lua integration boundary: expose named dev options and validated
vehicle identities through the native runtime. There is no Lua interpreter,
game-script execution, or arbitrary memory-write API in this helper.
"""
import argparse
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def keyboard_bindings(profile):
    """Import only Pad1 keyboard bindings; never modify the PCSX2 profile."""
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    with Path(profile).open(encoding="utf-8-sig") as source:
        config.read_file(source)
    if not config.has_section("Pad1") or config["Pad1"].get("Type") != "DualShock2":
        raise ValueError("Expected a PCSX2 DualShock2 Pad1 profile")
    names = ("Select", "L3", "R3", "Start", "Up", "Right", "Down", "Left",
             "L2", "R2", "L1", "R1", "Triangle", "Circle", "Cross", "Square",
             "LLeft", "LRight", "LUp", "LDown", "RLeft", "RRight", "RUp", "RDown")
    keys = {chr(n): str(n) for n in range(ord('A'), ord('Z') + 1)}
    keys.update({str(n): str(48 + n) for n in range(10)})
    keys.update({f"Numpad{n}": str(320 + n) for n in range(10)})
    keys.update({"Up": "265", "Down": "264", "Left": "263", "Right": "262",
                 "Return": "257", "Tab": "258", "Space": "32", "Escape": "256",
                 "Alt": "342,346", "Control": "341,345", "Shift": "340,344"})
    bindings = []
    for name in names:
        source = config["Pad1"].get(name, "")
        if not source:
            continue
        if source.startswith("SDL-") and "Keyboard/" not in source:
            continue  # Physical gamepad input remains handled by the runtime.
        if not source.startswith("Keyboard/") or source[9:] not in keys:
            raise ValueError(f"Unsupported profile binding for {name}: {source}")
        bindings.append(f"{name}={keys[source[9:]]}")
    if not bindings:
        raise ValueError("This profile contains no supported keyboard bindings")
    return ";".join(bindings)


def prototype_candidates():
    from rumble_menu_research import MenuResearch

    retail = MenuResearch("Retail")
    prototype = MenuResearch("February")
    report = json.loads((ROOT / "analysis/assets-investigation.json").read_text(encoding="utf-8-sig"))
    models = {}
    for build in ("February", "Retail"):
        entry = next(b for b in report["Builds"] if b["Build"] == build)
        archive = next(a for a in entry["Archives"] if a["Path"] == "GLBLDATA.PS2")
        selected = [r for r in archive["Resources"] if r["Type"] == "o3d " and 10000 <= r["Id"] <= 10110]
        if len(selected) != 111 or len({r["Id"] for r in selected}) != 111 or any(r["Error"] for r in selected):
            raise ValueError("Incomplete or ambiguous model inventory")
        models[build] = {r["Id"]: r for r in selected}
    inventory = next(i for i in report["Inventories"] if i["Build"] == "February")
    drivers = {d["Index"]: d for d in inventory["Drivers"]}
    if set(drivers) != set(range(36)):
        raise ValueError("Incomplete prototype driver inventory")
    rows = []
    for vehicle in retail.catalog()["vehicles"]:
        driver, model = vehicle["driver_id"], vehicle["model_index"]
        entry = drivers[driver]
        if entry["ModelIndex"] != model:
            raise ValueError("Prototype/retail model mapping differs from the audited layout")
        name = entry["Name"]
        old_row = prototype.read(0x1f2e00 + driver * 28, 28)
        new_row = retail.read(0x1eac80 + driver * 28, 28)
        if old_row[6] != model or old_row[11] >= 2 or new_row[11] >= 2 or old_row[12] >= 13 or new_row[12] >= 14:
            raise ValueError("Unrecognized driver physics/engine profile")
        physics_equal = (prototype.read(0x1f3840 + old_row[12] * 0x38, 0x38) ==
                         retail.read(0x1eb4d0 + new_row[12] * 0x38, 0x38))
        engine_equal = (prototype.read(0x1f3750 + old_row[11] * 0x74, 0x74) ==
                        retail.read(0x1eb3e0 + new_row[11] * 0x74, 0x74))
        changed = [level for level in range(3) if
                   models["February"][10000 + model * 3 + level]["SHA256"] !=
                   models["Retail"][10000 + model * 3 + level]["SHA256"]]
        if changed or not physics_equal or not engine_equal or name != vehicle["name"]:
            rows.append({"candidate_id": f"february-driver-{driver}",
                         "prototype_name": name, "retail_name": vehicle["name"],
                         "driver_id": driver, "model_index": model,
                         "changed_model_classes": [["Rookie", "Pro", "Elite"][i] for i in changed],
                         "physics_profile_bytes_equal": physics_equal,
                         "engine_simulation_profile_bytes_equal": engine_equal,
                         "behavior_note": "Profile equality alone does not prove identical physics code or handling",
                         "playable": False,
                         "status": "Pending texture, geometry and prototype physics compatibility validation",
                         "audio_policy": "Retail"})
    return {"source": "Existing local analysis inventory; no exports or game modifications",
            "candidates": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("launch", help="Launch the existing retail development runner")
    launch.add_argument("--max-upgrades", action=argparse.BooleanOptionalAction, default=False)
    launch.add_argument("--ai", action=argparse.BooleanOptionalAction, default=False)
    launch.add_argument("--window", action="store_true", help="Show native game window instead of viewer-only mode")
    launch.add_argument("--720p", dest="window_720p", action="store_true",
                        help="Show a 1280x720 window with a 4:3 picture; internal render resolution is unchanged")
    launch.add_argument("--keyboard-profile", type=Path,
                        help="Read keyboard controls from a PCSX2 input profile (no files copied)")
    launch.add_argument("--no-frame-capture", action="store_true", help="Disable screenshots/contact sheets; retain text diagnostics")
    launch.add_argument("--glitch-visuals", action=argparse.BooleanOptionalAction, default=False,
                        help="Opt-in scrambled-color/block effect on the displayed image only; restart to change")
    launch.add_argument("--configuration", choices=("Debug", "RelWithDebInfo"), default="RelWithDebInfo")
    commands.add_parser("prototype-candidates", help="Read-only audit; these are not playable entries yet")
    args = parser.parse_args()
    if args.command == "prototype-candidates":
        print(json.dumps(prototype_candidates(), indent=2))
        return 0
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("This local launcher needs PowerShell; the native dev hooks are C++")
    command = [shell, "-NoProfile", "-File", str(ROOT / "run-ntsc.ps1"),
               "-Build", "Retail", "-Configuration", args.configuration]
    if not args.window and not args.window_720p:
        command.append("-ViewerOnly")
    if args.window_720p:
        command.append("-Window720p")
    if args.max_upgrades:
        command.append("-DevMaxUpgrades")
    if args.ai:
        command.append("-DevAI")
    if args.no_frame_capture:
        command.append("-NoFrameCapture")
    if args.glitch_visuals:
        command.append("-DevGlitchVisuals")
    environment = os.environ.copy()
    if args.keyboard_profile:
        environment["PS2_PAD_KEYS"] = keyboard_bindings(args.keyboard_profile)
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration) as error:
        print(f"Rumble dev: {error}", file=sys.stderr)
        raise SystemExit(1)
